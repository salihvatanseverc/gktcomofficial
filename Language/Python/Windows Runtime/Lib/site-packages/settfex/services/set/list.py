"""SET Stock List Service - Fetch list of stock details from SET API."""

import asyncio
import difflib
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import register_symbol_suggester
from settfex.services.set.asset_type import SECURITY_TYPE_TO_ASSET_TYPE, AssetType
from settfex.services.set.constants import SET_BASE_URL, SET_STOCK_LIST_ENDPOINT
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import validate_or_raise

# In-memory cache of the last-fetched SET stock symbols, powering network-free "did you mean?"
# suggestions on SymbolNotFoundError. Populated by fetch_stock_list; reading it never fetches.
_KNOWN_SYMBOLS: list[str] | None = None


class StockSymbol(BaseModel):
    """Model for individual stock symbol information."""

    symbol: str = Field(description="Stock symbol/ticker")
    name_th: str = Field(alias="nameTH", description="Company name in Thai")
    name_en: str = Field(alias="nameEN", description="Company name in English")
    market: str = Field(description="Market type (SET, mai, etc.)")
    security_type: str = Field(alias="securityType", description="Security type")
    type_sequence: int = Field(alias="typeSequence", description="Type sequence number")
    industry: str = Field(description="Industry classification")
    sector: str = Field(description="Sector classification")
    query_sector: str = Field(alias="querySector", description="Queryable sector name")
    is_iff: bool = Field(alias="isIFF", description="Is Infrastructure Fund Flag")
    is_foreign_listing: bool = Field(
        alias="isForeignListing", description="Is foreign listing flag"
    )
    remark: str = Field(default="", description="Additional remarks")
    indices: list[str] = Field(
        default_factory=list,
        description=(
            "Market index memberships (e.g. ['SET50', 'SET100', 'SETESG']); populated when "
            "fetching with include_indices=True (the default), empty otherwise"
        ),
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )

    @property
    def asset_type(self) -> AssetType:
        """Asset type derived from ``security_type`` (e.g. ``"X"`` → depositary receipt)."""
        return AssetType.from_security_type(self.security_type)


class StockListResponse(BaseModel):
    """Response model for stock list API."""

    security_symbols: list[StockSymbol] = Field(
        alias="securitySymbols", description="List of stock symbols"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
    )

    @property
    def count(self) -> int:
        """Get total count of securities."""
        return len(self.security_symbols)

    def filter_by_market(self, market: str) -> list[StockSymbol]:
        """
        Filter securities by market type.

        Args:
            market: Market type (e.g., 'SET', 'mai')

        Returns:
            List of stock symbols for the specified market
        """
        return [s for s in self.security_symbols if s.market.upper() == market.upper()]

    def filter_by_industry(self, industry: str) -> list[StockSymbol]:
        """
        Filter securities by industry.

        Args:
            industry: Industry classification

        Returns:
            List of stock symbols in the specified industry
        """
        return [s for s in self.security_symbols if s.industry.upper() == industry.upper()]

    def filter_by_index(self, index: str) -> list[StockSymbol]:
        """
        Filter securities by market index membership.

        Requires the list to have been fetched with ``include_indices=True`` (the default);
        otherwise every ``indices`` list is empty and this returns nothing.

        Args:
            index: Index symbol (e.g., 'SET50', 'SETESG'); case-insensitive ('sset' works)

        Returns:
            List of stock symbols that are members of the specified index
        """
        target = index.strip().upper()
        return [s for s in self.security_symbols if any(ix.upper() == target for ix in s.indices)]

    def filter_by_asset_type(self, asset_type: AssetType | str) -> list[StockSymbol]:
        """
        Filter securities by asset type (stock, ETF, DR, DW, warrant, ...).

        Args:
            asset_type: An :class:`AssetType`, its value (e.g. ``'dr'``, ``'etf'`` —
                case-insensitive), or a raw SET ``securityType`` code (e.g. ``'X'``)

        Returns:
            List of stock symbols of the specified asset type

        Raises:
            ValueError: If the value matches neither an AssetType nor a SET securityType code.

        Example:
            >>> stock_list = await get_stock_list(include_indices=False)
            >>> drs = stock_list.filter_by_asset_type(AssetType.DEPOSITARY_RECEIPT)
            >>> etfs = stock_list.filter_by_asset_type("etf")
        """
        if isinstance(asset_type, AssetType):
            target = asset_type
        else:
            text = asset_type.strip()
            try:
                target = AssetType(text.lower())
            except ValueError:
                code_match = SECURITY_TYPE_TO_ASSET_TYPE.get(text.upper())
                if code_match is None:
                    valid_values = ", ".join(t.value for t in AssetType)
                    valid_codes = ", ".join(SECURITY_TYPE_TO_ASSET_TYPE)
                    raise ValueError(
                        f"Unknown asset type {asset_type!r}; expected one of: {valid_values} "
                        f"(or a SET securityType code: {valid_codes})"
                    ) from None
                target = code_match
        return [s for s in self.security_symbols if s.asset_type is target]

    def get_symbol(self, symbol: str) -> StockSymbol | None:
        """
        Get a specific stock symbol.

        Args:
            symbol: Stock symbol to find

        Returns:
            StockSymbol if found, None otherwise
        """
        for s in self.security_symbols:
            if s.symbol.upper() == symbol.upper():
                return s
        return None


def _cache_known_symbols(response: StockListResponse) -> None:
    """Cache fetched symbols for network-free symbol suggestions (see :func:`suggest_symbol`)."""
    global _KNOWN_SYMBOLS
    _KNOWN_SYMBOLS = [s.symbol for s in response.security_symbols]


def suggest_symbol(symbol: str) -> str | None:
    """Return the closest known SET stock symbol to ``symbol``, or ``None``.

    Uses stdlib :func:`difflib.get_close_matches` against the symbols cached by the most recent
    :func:`get_stock_list` / ``fetch_stock_list`` call **in this process**. Returns ``None`` — and
    never performs a fetch — when no stock list has been fetched yet, or when no close match
    exists. Powers :attr:`settfex.exceptions.SymbolNotFoundError.suggestion`.

    Example:
        >>> await get_stock_list()          # populates the cache (once per session)
        >>> suggest_symbol("CPALLL")
        'CPALL'
    """
    if not symbol or _KNOWN_SYMBOLS is None:
        return None
    matches = difflib.get_close_matches(symbol.strip().upper(), _KNOWN_SYMBOLS, n=1, cutoff=0.6)
    return matches[0] if matches else None


class StockListService:
    """
    Service for fetching stock list from SET API.

    This service provides async methods to fetch the complete list of stocks
    traded on the Stock Exchange of Thailand (SET), including company names,
    market classifications, and industry sectors.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the stock list service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Uses SessionManager for automatic cookie handling
            >>> service = StockListService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"StockListService initialized with base_url={self.base_url}")

    async def fetch_stock_list(self, include_indices: bool = True) -> StockListResponse:
        """
        Fetch the complete list of stocks from SET API.

        By default each stock is enriched with its market index memberships (e.g. CPALL ->
        ['SET50', 'SET100', 'SETESG']) by fetching the constituents of every headline
        sub-index concurrently (~10 extra requests). Pass ``include_indices=False`` for the
        previous single-request behavior. Enrichment failures are logged and degrade to
        empty ``indices`` lists — they never fail the stock list itself.

        Args:
            include_indices: Whether to populate ``StockSymbol.indices`` with index
                memberships (default: True)

        Returns:
            StockListResponse containing all stock symbols and details

        Raises:
            Exception: If request fails or response cannot be parsed

        Example:
            >>> service = StockListService()
            >>> response = await service.fetch_stock_list()
            >>> print(f"Total stocks: {response.count}")
            >>> print(f"SET stocks: {len(response.filter_by_market('SET'))}")
            >>> cpall = response.get_symbol("CPALL")
            >>> print(f"CPALL indices: {cpall.indices}")
        """
        url = f"{self.base_url}{SET_STOCK_LIST_ENDPOINT}"

        logger.info(f"Fetching stock list from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API (includes all Incapsula bypass headers)
            headers = AsyncDataFetcher.get_set_api_headers()

            # Fetch JSON data from API - SessionManager handles cookies automatically
            data = await fetcher.fetch_json(url, headers=headers)

            # Validate response using Pydantic (context-rich on failure)
            response = validate_or_raise(StockListResponse, data, context="set stock-list")
            _cache_known_symbols(response)

            logger.info(f"Successfully fetched {response.count} stock symbols from SET API")

        if include_indices:
            try:
                membership = await self._fetch_index_memberships()
            except Exception as exc:
                logger.warning(
                    f"Index membership enrichment failed; "
                    f"returning stock list without indices: {exc}"
                )
                membership = {}
            for stock in response.security_symbols:
                stock.indices = membership.get(stock.symbol.upper(), [])

        return response

    async def _fetch_index_memberships(self) -> dict[str, list[str]]:
        """
        Build a stock-symbol -> index-memberships map from the sub-index compositions.

        Fetches the index directory, keeps the headline sub-indices (level 'INDEX' minus the
        whole markets 'SET' and 'mai', whose membership is already ``StockSymbol.market``),
        and fetches their compositions concurrently. A failed composition is logged and
        skipped so a single unavailable index never poisons the whole map.

        Returns:
            Mapping of uppercased stock symbol to canonical index symbols, in the index
            directory's order (e.g. {'CPALL': ['SET50', 'SET100', 'SETESG']})
        """
        # Lazy imports: keep the stock list module free of import-time coupling to the
        # index sub-package (and give tests a clean patch seam).
        from settfex.services.set.index.composition import IndexCompositionService
        from settfex.services.set.index.list import IndexListService

        index_list = await IndexListService(config=self.config).fetch_index_list()
        targets = [
            ix for ix in index_list.market_indices if ix.symbol.upper() not in {"SET", "MAI"}
        ]
        logger.info(
            f"Enriching stock list with index memberships from {len(targets)} indices: "
            f"{[ix.symbol for ix in targets]}"
        )

        service = IndexCompositionService(config=self.config)
        results = await asyncio.gather(
            *(service.fetch_composition(ix.query_symbol) for ix in targets),
            return_exceptions=True,
        )

        membership: dict[str, list[str]] = {}
        for ix, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(f"Skipping index '{ix.symbol}' in membership enrichment: {result}")
                continue
            for constituent in result.composition.stock_infos:
                membership.setdefault(constituent.symbol.upper(), []).append(ix.symbol)
        return membership

    async def fetch_stock_list_raw(self) -> dict[str, Any]:
        """
        Fetch stock list as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Returns:
            Raw dictionary from API

        Raises:
            Exception: If request fails

        Example:
            >>> service = StockListService()
            >>> raw_data = await service.fetch_stock_list_raw()
            >>> print(raw_data.keys())
        """
        url = f"{self.base_url}{SET_STOCK_LIST_ENDPOINT}"

        logger.info(f"Fetching raw stock list from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API
            headers = AsyncDataFetcher.get_set_api_headers()

            # SessionManager handles cookies automatically — no manual cookie needed
            data = await fetcher.fetch_json(url, headers=headers)
            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )  # noqa: E501
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_stock_list(
    config: FetcherConfig | None = None, include_indices: bool = True
) -> StockListResponse:
    """
    Convenience function to fetch stock list.

    By default each stock is enriched with its market index memberships (~10 extra
    concurrent requests); pass ``include_indices=False`` for the single-request behavior.
    Enrichment failures degrade to empty ``indices`` lists — they never raise.

    Args:
        config: Optional fetcher configuration
        include_indices: Whether to populate ``StockSymbol.indices`` with index
            memberships (default: True)

    Returns:
        StockListResponse with all stock symbols

    Example:
        >>> from settfex.services.set import get_stock_list
        >>> # Uses SessionManager for automatic cookie handling
        >>> response = await get_stock_list()
        >>> for stock in response.filter_by_index("SET50")[:5]:
        ...     print(f"{stock.symbol}: {stock.indices}")
    """
    service = StockListService(config=config)
    return await service.fetch_stock_list(include_indices=include_indices)


# Register the network-free symbol suggester so SymbolNotFoundError can offer "did you mean?"
# hints without this module and settfex.exceptions importing each other cyclically.
register_symbol_suggester(suggest_symbol)
