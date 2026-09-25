"""SET Market Index Composition Service - Fetch index constituents with per-stock quotes."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from settfex.exceptions import FetchError, InvalidSymbolError, SymbolNotFoundError
from settfex.services.set.constants import SET_BASE_URL, SET_INDEX_COMPOSITION_ENDPOINT
from settfex.services.set.index.info import IndexInfo
from settfex.services.set.index.utils import normalize_index_symbol
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise


class BidOffer(BaseModel):
    """Model for one bid/offer ladder level.

    The API sends ``price`` as a string (e.g. ``"374.00"``); it is coerced to float, with
    blank/placeholder strings treated as missing.
    """

    volume: float | None = Field(default=None, description="Volume at this price level (shares)")
    price: float | None = Field(default=None, description="Price at this level (THB)")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("volume", "price", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Map blank/placeholder strings ('', '-') to None; numeric strings coerce to float."""
        if isinstance(value, str) and value.strip() in {"", "-"}:
            return None
        return value


class IndexConstituent(BaseModel):
    """Model for one constituent stock row of an index composition.

    Mirrors the per-stock quote table on the set.or.th index page ("หลักทรัพย์ที่ใช้คำนวณดัชนี"):
    open/high/low/last, change, best bid/offer, volume, value — plus valuation extras
    (market cap, P/E, P/B, dividend yield, 52-week range, NVDR net volume).
    """

    symbol: str = Field(description="Stock symbol/ticker")
    sign: str | None = Field(default=None, description="Trading sign/notation (e.g., 'XD', 'NP')")
    prior: float | None = Field(default=None, description="Prior session's closing price")
    last: float | None = Field(default=None, description="Latest trade price")
    open: float | None = Field(default=None, description="Opening price")
    high: float | None = Field(default=None, description="Session high")
    low: float | None = Field(default=None, description="Session low")
    average: float | None = Field(default=None, description="Volume-weighted average price")
    floor: float | None = Field(default=None, description="Floor price limit")
    ceiling: float | None = Field(default=None, description="Ceiling price limit")
    change: float | None = Field(default=None, description="Price change from prior close")
    percent_change: float | None = Field(
        default=None, alias="percentChange", description="Percentage change from prior close"
    )
    total_volume: float | None = Field(
        default=None, alias="totalVolume", description="Total traded volume (shares)"
    )
    total_value: float | None = Field(
        default=None, alias="totalValue", description="Total traded value (THB)"
    )
    tr_volume: float | None = Field(
        default=None, alias="trVolume", description="Trade-report volume (shares)"
    )
    tr_value: float | None = Field(
        default=None, alias="trValue", description="Trade-report value (THB)"
    )
    aom_volume: float | None = Field(
        default=None, alias="aomVolume", description="Automatic order matching volume (shares)"
    )
    aom_value: float | None = Field(
        default=None, alias="aomValue", description="Automatic order matching value (THB)"
    )
    bids: list[BidOffer] = Field(
        default_factory=list, description="Best bid levels (best bid first)"
    )
    offers: list[BidOffer] = Field(
        default_factory=list, description="Best offer levels (best offer first)"
    )
    market_status: str | None = Field(
        default=None, alias="marketStatus", description="Market status (e.g., 'Open2')"
    )
    market_date_time: datetime | None = Field(
        default=None,
        alias="marketDateTime",
        description="Timestamp of the data (tz-aware, Asia/Bangkok +07:00)",
    )
    market_name: str | None = Field(
        default=None, alias="marketName", description="Market name ('SET' or 'mai')"
    )
    security_type: str | None = Field(
        default=None, alias="securityType", description="Security type (e.g., 'S' for stock)"
    )
    tick_size: float | None = Field(default=None, alias="tickSize", description="Price tick size")
    name_en: str | None = Field(default=None, alias="nameEN", description="Company name (English)")
    name_th: str | None = Field(default=None, alias="nameTH", description="Company name (Thai)")
    industry_name: str | None = Field(
        default=None, alias="industryName", description="Industry symbol (e.g., 'TECH')"
    )
    sector_name: str | None = Field(
        default=None, alias="sectorName", description="Sector symbol (e.g., 'ICT')"
    )
    is_npg: bool | None = Field(default=None, alias="isNPG", description="Is non-performing group")
    high_52_weeks: float | None = Field(
        default=None, alias="high52Weeks", description="52-week high price"
    )
    low_52_weeks: float | None = Field(
        default=None, alias="low52Weeks", description="52-week low price"
    )
    par: float | None = Field(default=None, description="Par value")
    inav: float | None = Field(default=None, description="Indicative NAV (ETFs)")
    multiplier: float | None = Field(default=None, description="Contract multiplier (DWs)")
    exercise_ratio: float | str | None = Field(
        default=None, alias="exerciseRatio", description="Exercise ratio (e.g., '1 : 1')"
    )
    exercise_price: float | None = Field(
        default=None, alias="exercisePrice", description="Exercise price (warrants/DWs)"
    )
    exercise_price_unit: str | None = Field(
        default=None, alias="exercisePriceUnit", description="Exercise price currency unit"
    )
    maturity_date: datetime | None = Field(
        default=None, alias="maturityDate", description="Maturity date (warrants/DWs)"
    )
    last_trading_date: datetime | None = Field(
        default=None, alias="lastTradingDate", description="Last trading date (warrants/DWs)"
    )
    underlying: str | None = Field(default=None, description="Underlying symbol (warrants/DWs)")
    is_iff: bool | None = Field(
        default=None, alias="isIFF", description="Is Infrastructure Fund Flag"
    )
    is_pfund: bool | None = Field(
        default=None, alias="isPFUND", description="Is property fund flag"
    )
    statistics_as_of: datetime | None = Field(
        default=None, alias="statisticsAsOf", description="Statistics as-of date"
    )
    market_cap: float | None = Field(
        default=None, alias="marketCap", description="Market capitalization (THB)"
    )
    pe_ratio: float | None = Field(default=None, alias="peRatio", description="P/E ratio")
    pb_ratio: float | None = Field(default=None, alias="pbRatio", description="P/B ratio")
    dividend_yield: float | None = Field(
        default=None, alias="dividendYield", description="Dividend yield (%)"
    )
    nvdr_net_volume: float | None = Field(
        default=None, alias="nvdrNetVolume", description="NVDR net buy/sell volume (shares)"
    )
    listed_share: float | None = Field(
        default=None, alias="listedShare", description="Listed shares outstanding"
    )
    ttm: float | str | None = Field(default=None, description="Trailing twelve months marker")
    moneyness_status: str | None = Field(
        default=None, alias="moneynessStatus", description="Moneyness status (DWs)"
    )
    moneyness_percent: float | None = Field(
        default=None, alias="moneynessPercent", description="Moneyness percentage (DWs)"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )

    @field_validator("bids", "offers", mode="before")
    @classmethod
    def _null_to_empty_list(cls, value: Any) -> Any:
        """The API sends ``null`` (not ``[]``) when a ladder side is empty."""
        return [] if value is None else value

    @field_validator(
        "market_date_time", "maturity_date", "last_trading_date", "statistics_as_of", mode="before"
    )
    @classmethod
    def _empty_str_to_none(cls, value: Any) -> Any:
        """Treat an empty/blank datetime string as missing rather than failing validation."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def best_bid(self) -> float | None:
        """Best (highest) bid price, or None if the bid ladder is empty."""
        return self.bids[0].price if self.bids else None

    @property
    def best_offer(self) -> float | None:
        """Best (lowest) offer price, or None if the offer ladder is empty."""
        return self.offers[0].price if self.offers else None


class IndexComposition(BaseModel):
    """Model for the index composition: constituent stocks and/or sub-index drilldown.

    SET industry indices (e.g. 'AGRO') carry no direct constituents — they return an empty
    ``stock_infos`` plus ``sub_indices`` with their sector quotes instead. All headline
    sub-indices (SET50, ..., SETWB), sectors, and mai industries return stocks directly.
    """

    symbol: str = Field(description="Index symbol")
    name_en: str | None = Field(default=None, alias="nameEN", description="Index name (English)")
    name_th: str | None = Field(default=None, alias="nameTH", description="Index name (Thai)")
    stock_infos: list[IndexConstituent] = Field(
        default_factory=list, alias="stockInfos", description="Constituent stock quote rows"
    )
    sub_indices: list[IndexInfo] | None = Field(
        default=None,
        alias="subIndices",
        description="Sub-index quotes (SET industry drilldown into sectors), if any",
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @field_validator("stock_infos", mode="before")
    @classmethod
    def _null_to_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value


class IndexCompositionResponse(BaseModel):
    """Response model for the index composition endpoint."""

    composition: IndexComposition = Field(description="Index composition data")
    index_infos: list[IndexInfo] = Field(
        default_factory=list,
        alias="indexInfos",
        description="Quotation(s) of the queried index itself",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("index_infos", mode="before")
    @classmethod
    def _null_to_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @property
    def constituents(self) -> list[IndexConstituent]:
        """Shortcut to ``composition.stock_infos``."""
        return self.composition.stock_infos

    @property
    def symbols(self) -> list[str]:
        """Constituent stock symbols."""
        return [c.symbol for c in self.constituents]

    @property
    def count(self) -> int:
        """Number of constituent stocks."""
        return len(self.constituents)

    @property
    def index_info(self) -> IndexInfo | None:
        """Quotation of the queried index itself (first ``index_infos`` entry), if present."""
        return self.index_infos[0] if self.index_infos else None

    def get_constituent(self, symbol: str) -> IndexConstituent | None:
        """
        Get a specific constituent by stock symbol (case-insensitive).

        Args:
            symbol: Stock symbol to find (e.g., 'CPALL')

        Returns:
            IndexConstituent if found, None otherwise
        """
        target = symbol.strip().upper()
        for constituent in self.constituents:
            if constituent.symbol.upper() == target:
                return constituent
        return None


class IndexCompositionService:
    """
    Service for fetching index constituents from SET API.

    Fetches the securities used to calculate an index, each with a full quote row
    (OHLC, change, best bid/offer, volume, value, market cap, P/E, ...). Works for the
    headline sub-indices (SET50, SET100, SET50FF, SET100FF, sSET, SETCLMV, SETHD, SETESG,
    SETWB), sector indices, and mai industries. The whole-market indices 'SET' and 'mai'
    have no composition endpoint (HTTP 404).
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the index composition service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> service = IndexCompositionService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"IndexCompositionService initialized with base_url={self.base_url}")

    def _build_url(self, symbol: str, lang: str) -> str:
        endpoint = SET_INDEX_COMPOSITION_ENDPOINT.format(symbol=symbol)
        return f"{self.base_url}{endpoint}?language={lang}"

    @staticmethod
    def _raise_for_status(symbol: str, status_code: int) -> None:
        if status_code == 200:
            return
        if status_code == 404:
            error_msg = (
                f"Failed to fetch index composition for {symbol}: HTTP 404 — the whole-market "
                f"indices 'SET' and 'mai' have no composition endpoint; query a sub-index "
                f"(e.g. 'SET50'), a sector, or an industry instead"
            )
            logger.error(error_msg)
            raise SymbolNotFoundError(error_msg, status_code=status_code, symbol=symbol)
        error_msg = f"Failed to fetch index composition for {symbol}: HTTP {status_code}"
        logger.error(error_msg)
        raise FetchError(error_msg, status_code=status_code, symbol=symbol)

    async def fetch_composition(
        self, symbol: str, lang: Language = "en"
    ) -> IndexCompositionResponse:
        """
        Fetch the constituents of a specific market index.

        Args:
            symbol: Index symbol (e.g., "SET50", "SETESG", "ICT", "AGRO-m"). Note that the
                SET industry symbols (e.g. "AGRO") return a sector drilldown in
                ``composition.sub_indices`` instead of stocks; the mai variants use the
                ``-m`` suffixed query symbol (e.g. "AGRO-m") and return stocks directly.
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            IndexCompositionResponse with constituent quote rows and the index's own quote

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the index is not found (HTTP 404; incl. 'SET'/'mai').
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexCompositionService()
            >>> response = await service.fetch_composition("SET50")
            >>> print(f"Constituents: {response.count}")
            >>> for c in response.constituents[:5]:
            ...     print(f"{c.symbol}: {c.last} ({c.percent_change:+.2f}%)")
        """
        symbol = normalize_index_symbol(symbol)
        lang = normalize_language(lang)
        if not symbol:
            raise InvalidSymbolError("Index symbol cannot be empty")

        url = self._build_url(symbol, lang)

        logger.info(f"Fetching index composition for '{symbol}' (lang={lang})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/index/{symbol.lower()}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            self._raise_for_status(symbol, response.status_code)

            data = decode_json(response.text, context=f"{symbol} (index-composition)")

            result = validate_or_raise(
                IndexCompositionResponse, data, context=f"{symbol} (index-composition)"
            )
            sub_count = len(result.composition.sub_indices or [])
            logger.info(
                f"Successfully fetched composition for {result.composition.symbol}: "
                f"{result.count} constituents, {sub_count} sub-indices"
            )
            return result

    async def fetch_composition_raw(self, symbol: str, lang: Language = "en") -> dict[str, Any]:
        """
        Fetch index composition as raw dictionary without Pydantic validation.

        Args:
            symbol: Index symbol (e.g., "SET50", "SETESG", "ICT", "AGRO-m")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw dictionary from API (with 'composition' and 'indexInfos' keys)

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the index is not found (HTTP 404; incl. 'SET'/'mai').
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexCompositionService()
            >>> raw = await service.fetch_composition_raw("SET50")
            >>> print(raw.keys())
        """
        symbol = normalize_index_symbol(symbol)
        lang = normalize_language(lang)
        if not symbol:
            raise InvalidSymbolError("Index symbol cannot be empty")

        url = self._build_url(symbol, lang)

        logger.info(f"Fetching raw index composition for '{symbol}' (lang={lang})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/index/{symbol.lower()}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            self._raise_for_status(symbol, response.status_code)

            data = decode_json(response.text, context=f"{symbol} (index-composition)")

            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_index_composition(
    symbol: str, lang: Language = "en", config: FetcherConfig | None = None
) -> IndexCompositionResponse:
    """
    Convenience function to fetch the constituents of a market index.

    Args:
        symbol: Index symbol (e.g., "SET50", "SETESG", "ICT", "AGRO-m")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        IndexCompositionResponse with constituent quote rows and the index's own quote

    Example:
        >>> from settfex.services.set import get_index_composition
        >>> response = await get_index_composition("SET50")
        >>> print(f"{response.composition.symbol}: {response.count} constituents")
        >>> print(response.symbols[:10])
    """
    service = IndexCompositionService(config=config)
    return await service.fetch_composition(symbol=symbol, lang=lang)
