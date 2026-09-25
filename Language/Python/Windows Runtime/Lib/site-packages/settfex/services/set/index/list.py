"""SET Market Index List Service - Fetch the directory of SET/mai market indices."""

from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.services.set.constants import SET_BASE_URL, SET_INDEX_LIST_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, validate_list_or_raise


class IndexSymbol(BaseModel):
    """Model for a single market index directory entry."""

    symbol: str = Field(description="Index symbol (e.g., 'SET50', 'sSET', 'AGRO')")
    market: str = Field(description="Market the index belongs to ('SET' or 'mai')")
    level: str = Field(description="Index level: 'INDEX', 'INDUSTRY', or 'SECTOR'")
    parent_index: str | None = Field(
        default=None,
        alias="parentIndex",
        description="Parent index symbol (None for top-level indices)",
    )
    query_symbol: str = Field(
        alias="querySymbol",
        description="Symbol to use in API paths (mai industries are suffixed, e.g. 'AGRO-m')",
    )
    name_en: str = Field(default="", alias="nameEN", description="Index name in English")
    name_th: str = Field(default="", alias="nameTH", description="Index name in Thai")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class IndexListResponse(BaseModel):
    """Response model wrapping the market index directory.

    The SET API returns three levels of entries:

    - ``INDEX``: the headline market indices (SET, SET50, SET50FF, SET100, SET100FF, sSET,
      SETCLMV, SETHD, SETESG, SETWB, mai)
    - ``INDUSTRY``: industry group indices per market (SET and mai share industry symbols;
      the mai ones are distinguished by a ``-m`` suffixed ``query_symbol``, e.g. 'AGRO-m')
    - ``SECTOR``: sector indices (SET only), each with its parent industry in ``parent_index``
    """

    # Required, with no default. An envelope whose only field defaults to an empty list validates
    # ANY JSON object -- an error payload included -- into a successful-looking empty result, which
    # is the second half of the silent-loss rule in issue #135. Required is not non-empty: a genuine
    # `{...: []}` still validates; only a missing key fails.
    indices: list[IndexSymbol] = Field(description="All index directory entries")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def count(self) -> int:
        """Get total count of index entries."""
        return len(self.indices)

    @property
    def market_indices(self) -> list[IndexSymbol]:
        """Top-level market indices (level == 'INDEX'): SET, SET50, ..., SETWB, mai."""
        return self.filter_by_level("INDEX")

    @property
    def industries(self) -> list[IndexSymbol]:
        """Industry group indices (level == 'INDUSTRY') across both SET and mai."""
        return self.filter_by_level("INDUSTRY")

    @property
    def sectors(self) -> list[IndexSymbol]:
        """Sector indices (level == 'SECTOR')."""
        return self.filter_by_level("SECTOR")

    def filter_by_market(self, market: str) -> list[IndexSymbol]:
        """
        Filter index entries by market.

        Args:
            market: Market name (e.g., 'SET', 'mai'); case-insensitive

        Returns:
            List of index entries for the specified market
        """
        return [ix for ix in self.indices if ix.market.upper() == market.strip().upper()]

    def filter_by_level(self, level: str) -> list[IndexSymbol]:
        """
        Filter index entries by level.

        Args:
            level: Index level ('INDEX', 'INDUSTRY', or 'SECTOR'); case-insensitive

        Returns:
            List of index entries at the specified level
        """
        return [ix for ix in self.indices if ix.level.upper() == level.strip().upper()]

    def get_index(self, symbol: str, market: str | None = None) -> IndexSymbol | None:
        """
        Get a specific index entry by symbol.

        Resolution order (case-insensitive): exact ``query_symbol`` match first (so 'AGRO-m'
        pins the mai industry), then ``symbol`` match. Because SET and mai industries share
        symbols (e.g. 'AGRO'), pass ``market`` (or use the ``query_symbol``) to disambiguate;
        an ambiguous bare-symbol match returns the first entry and logs a warning.

        Args:
            symbol: Index symbol or query symbol to find (e.g., 'SET50', 'sset', 'AGRO-m')
            market: Optional market to narrow the search ('SET' or 'mai')

        Returns:
            IndexSymbol if found, None otherwise
        """
        target = symbol.strip().upper()
        candidates = (
            self.indices
            if market is None
            else [ix for ix in self.indices if ix.market.upper() == market.strip().upper()]
        )
        for ix in candidates:
            if ix.query_symbol.upper() == target:
                return ix
        matches = [ix for ix in candidates if ix.symbol.upper() == target]
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                f"Index symbol '{symbol}' is ambiguous across markets "
                f"({', '.join(f'{ix.market}:{ix.query_symbol}' for ix in matches)}); "
                f"returning the first match — pass market= or use the query_symbol to pin one"
            )
        return matches[0]


class IndexListService:
    """
    Service for fetching the market index directory from SET API.

    Provides async methods to fetch the complete list of market indices (SET50, SET100,
    sSET, SETESG, ...), industry group indices, and sector indices for both SET and mai.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the index list service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Uses SessionManager for automatic cookie handling
            >>> service = IndexListService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"IndexListService initialized with base_url={self.base_url}")

    async def fetch_index_list(self, lang: Language = "en") -> IndexListResponse:
        """
        Fetch the complete market index directory from SET API.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            IndexListResponse containing all index entries (INDEX/INDUSTRY/SECTOR levels)

        Raises:
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexListService()
            >>> response = await service.fetch_index_list()
            >>> print(f"Total indices: {response.count}")
            >>> for ix in response.market_indices:
            ...     print(f"{ix.symbol} ({ix.market})")
        """
        lang = normalize_language(lang)
        url = f"{self.base_url}{SET_INDEX_LIST_ENDPOINT}?language={lang}"

        logger.info(f"Fetching index list from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API (includes all Incapsula bypass headers)
            headers = AsyncDataFetcher.get_set_api_headers()

            # Fetch JSON data from API - SessionManager handles cookies automatically
            data = await fetcher.fetch_json(url, headers=headers)

            # The payload is a bare JSON array of index entries
            indices = validate_list_or_raise(IndexSymbol, data, context="set index-list")
            if not indices:
                # The market's index directory is never empty -- it is a static ~55-entry list of
                # SET/mai indices, industries and sectors. An empty array is therefore a broken or
                # substituted response, not "no indices", and returning it would be the silent
                # loss of issue #135 with nothing to cross-check it against.
                error_msg = (
                    "SET returned an empty index directory, which it never legitimately does"
                )
                logger.error(error_msg)
                raise ResponseParseError(error_msg)
            response = IndexListResponse(indices=indices)

            logger.info(f"Successfully fetched {response.count} index entries from SET API")

            return response

    async def fetch_index_list_raw(self, lang: Language = "en") -> list[dict[str, Any]]:
        """
        Fetch the index directory as a raw list without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw list of dictionaries from API

        Raises:
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexListService()
            >>> raw_data = await service.fetch_index_list_raw()
            >>> print(raw_data[0].keys())
        """
        lang = normalize_language(lang)
        url = f"{self.base_url}{SET_INDEX_LIST_ENDPOINT}?language={lang}"

        logger.info(f"Fetching raw index list from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            headers = AsyncDataFetcher.get_set_api_headers()

            # SessionManager handles cookies automatically — no manual cookie needed
            data = await fetcher.fetch_json(url, headers=headers)
            logger.debug(
                f"Raw response: {len(data) if isinstance(data, list) else type(data)} entries"
            )
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_index_list(
    lang: Language = "en", config: FetcherConfig | None = None
) -> IndexListResponse:
    """
    Convenience function to fetch the market index directory.

    Args:
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        IndexListResponse with all index entries

    Raises:
        InvalidLanguageError: If the language is not recognized.
        FetchError: On HTTP or transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_index_list
        >>> response = await get_index_list()
        >>> for ix in response.market_indices:
        ...     print(f"{ix.symbol}: {ix.name_en}")
    """
    service = IndexListService(config=config)
    return await service.fetch_index_list(lang=lang)
