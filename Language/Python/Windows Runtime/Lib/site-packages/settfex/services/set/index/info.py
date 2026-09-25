"""SET Market Index Info Service - Fetch index quotations (last, change, OHLC, volume, value)."""

from datetime import datetime
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import (
    SET_BASE_URL,
    SET_INDEX_INFO_ENDPOINT,
    SET_INDEX_INFO_LIST_ENDPOINT,
)
from settfex.services.set.index.utils import normalize_index_symbol
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise

# The info/list endpoint accepts type=INDEX (the 11 headline indices) and type=INDUSTRY
# (industry AND sector indices for both markets). type=SECTOR returns HTTP 404 — sectors
# are already included in the INDUSTRY payload.
IndexInfoType = Literal["INDEX", "INDUSTRY"]


class IndexInfo(BaseModel):
    """Model for a market index quotation — the set.or.th index page header data."""

    symbol: str = Field(description="Index symbol (e.g., 'SET50', 'sSET')")
    name_en: str | None = Field(default=None, alias="nameEN", description="Index name in English")
    name_th: str | None = Field(default=None, alias="nameTH", description="Index name in Thai")
    prior: float | None = Field(default=None, description="Prior session's closing value")
    open: float | None = Field(default=None, description="Opening value")
    high: float | None = Field(default=None, description="Session high")
    low: float | None = Field(default=None, description="Session low")
    last: float | None = Field(default=None, description="Latest index value")
    change: float | None = Field(default=None, description="Change from prior close")
    percent_change: float | None = Field(
        default=None, alias="percentChange", description="Percentage change from prior close"
    )
    volume: float | None = Field(default=None, description="Total volume (shares)")
    value: float | None = Field(default=None, description="Total value (THB)")
    query_symbol: str | None = Field(
        default=None, alias="querySymbol", description="Symbol used in API paths (e.g. 'AGRO-m')"
    )
    market_status: str | None = Field(
        default=None, alias="marketStatus", description="Market status (e.g., 'Open2', 'Closed')"
    )
    market_date_time: datetime | None = Field(
        default=None,
        alias="marketDateTime",
        description="Timestamp of the data (tz-aware, Asia/Bangkok +07:00)",
    )
    market_name: str | None = Field(
        default=None, alias="marketName", description="Market name ('SET' or 'mai')"
    )
    industry_name: str | None = Field(
        default=None, alias="industryName", description="Industry name (industry/sector indices)"
    )
    sector_name: str | None = Field(
        default=None, alias="sectorName", description="Sector name (sector indices)"
    )
    level: str | None = Field(
        default=None, description="Index level: 'INDEX', 'INDUSTRY', or 'SECTOR'"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )

    @field_validator("market_date_time", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: Any) -> Any:
        """Treat an empty/blank datetime string as missing rather than failing validation."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class IndexInfoListResponse(BaseModel):
    """Envelope model for the index info/list endpoint."""

    # Required, with no default. An envelope whose only field defaults to an empty list validates
    # ANY JSON object -- an error payload included -- into a successful-looking empty result, which
    # is the second half of the silent-loss rule in issue #135. Required is not non-empty: a genuine
    # `{...: []}` still validates; only a missing key fails.
    index_industry_sectors: list[IndexInfo] = Field(
        alias="indexIndustrySectors",
        description="Quotations for all indices of the requested type",
    )

    model_config = ConfigDict(populate_by_name=True)


class IndexInfoService:
    """
    Service for fetching market index quotations from SET API.

    Provides the exact data shown in the set.or.th index page header: last value, change,
    percent change, open/high/low, volume, value, market status, and data timestamp —
    for a single index or for all indices of a type in one request.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the index info service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> service = IndexInfoService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"IndexInfoService initialized with base_url={self.base_url}")

    async def fetch_index_info(self, symbol: str, lang: Language = "en") -> IndexInfo:
        """
        Fetch the quotation for a specific market index.

        Args:
            symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m"). The API resolves the
                path case-insensitively, so "sset" also works.
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            IndexInfo with last value, change, OHLC, volume, value, and market status

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexInfoService()
            >>> info = await service.fetch_index_info("SET50")
            >>> print(f"{info.symbol}: {info.last} ({info.percent_change:+.2f}%)")
        """
        symbol = normalize_index_symbol(symbol)
        lang = normalize_language(lang)
        if not symbol:
            raise InvalidSymbolError("Index symbol cannot be empty")

        endpoint = SET_INDEX_INFO_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?language={lang}"

        logger.info(f"Fetching index info for '{symbol}' (lang={lang})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/index/{symbol.lower()}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = f"Failed to fetch index info for {symbol}: HTTP {response.status_code}"
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol, suggest=False)

            data = decode_json(response.text, context=f"{symbol} (index-info)")

            result = validate_or_raise(IndexInfo, data, context=f"{symbol} (index-info)")
            logger.info(
                f"Successfully fetched index info for {result.symbol}: "
                f"last={result.last} change={result.change} status={result.market_status}"
            )
            return result

    async def fetch_index_info_raw(self, symbol: str, lang: Language = "en") -> dict[str, Any]:
        """
        Fetch index quotation as raw dictionary without Pydantic validation.

        Args:
            symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw dictionary from API

        Example:
            >>> service = IndexInfoService()
            >>> raw = await service.fetch_index_info_raw("SET50")
            >>> print(raw.keys())
        """
        symbol = normalize_index_symbol(symbol)
        lang = normalize_language(lang)
        if not symbol:
            raise InvalidSymbolError("Index symbol cannot be empty")

        endpoint = SET_INDEX_INFO_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?language={lang}"

        logger.info(f"Fetching raw index info for '{symbol}' (lang={lang})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/index/{symbol.lower()}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = f"Failed to fetch index info for {symbol}: HTTP {response.status_code}"
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol, suggest=False)

            data = decode_json(response.text, context=f"{symbol} (index-info)")

            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]

    async def fetch_index_info_list(
        self, index_type: IndexInfoType = "INDEX", lang: Language = "en"
    ) -> list[IndexInfo]:
        """
        Fetch quotations for all indices of a type in one request.

        Args:
            index_type: 'INDEX' for the 11 headline market indices (SET, SET50, ..., mai),
                or 'INDUSTRY' for all industry AND sector indices (default: 'INDEX')
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of IndexInfo quotations

        Raises:
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexInfoService()
            >>> quotes = await service.fetch_index_info_list()
            >>> for q in quotes:
            ...     print(f"{q.symbol}: {q.last} ({q.percent_change:+.2f}%)")
        """
        lang = normalize_language(lang)
        url = f"{self.base_url}{SET_INDEX_INFO_LIST_ENDPOINT}?type={index_type}&language={lang}"

        logger.info(f"Fetching index info list (type={index_type}, lang={lang})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            headers = AsyncDataFetcher.get_set_api_headers()

            data = await fetcher.fetch_json(url, headers=headers)

            response = validate_or_raise(
                IndexInfoListResponse, data, context=f"set index-info-list ({index_type})"
            )
            quotes = response.index_industry_sectors

            logger.info(f"Successfully fetched {len(quotes)} index quotations (type={index_type})")

            return quotes

    async def fetch_index_info_list_raw(
        self, index_type: IndexInfoType = "INDEX", lang: Language = "en"
    ) -> dict[str, Any]:
        """
        Fetch the index info list as raw dictionary without Pydantic validation.

        Args:
            index_type: 'INDEX' or 'INDUSTRY' (default: 'INDEX')
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw dictionary from API (with 'indexIndustrySectors' key)

        Example:
            >>> service = IndexInfoService()
            >>> raw = await service.fetch_index_info_list_raw()
            >>> print(len(raw["indexIndustrySectors"]))
        """
        lang = normalize_language(lang)
        url = f"{self.base_url}{SET_INDEX_INFO_LIST_ENDPOINT}?type={index_type}&language={lang}"

        logger.info(f"Fetching raw index info list (type={index_type}, lang={lang})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            headers = AsyncDataFetcher.get_set_api_headers()

            data = await fetcher.fetch_json(url, headers=headers)
            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


# Convenience functions for quick access
async def get_index_info(
    symbol: str, lang: Language = "en", config: FetcherConfig | None = None
) -> IndexInfo:
    """
    Convenience function to fetch a market index quotation.

    Args:
        symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        IndexInfo with last value, change, OHLC, volume, value, and market status

    Example:
        >>> from settfex.services.set import get_index_info
        >>> info = await get_index_info("SET50")
        >>> print(f"{info.symbol}: {info.last} ({info.percent_change:+.2f}%)")
    """
    service = IndexInfoService(config=config)
    return await service.fetch_index_info(symbol=symbol, lang=lang)


async def get_index_info_list(
    index_type: IndexInfoType = "INDEX",
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> list[IndexInfo]:
    """
    Convenience function to fetch quotations for all indices of a type.

    Args:
        index_type: 'INDEX' for the 11 headline market indices, or 'INDUSTRY' for all
            industry and sector indices (default: 'INDEX')
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        List of IndexInfo quotations

    Example:
        >>> from settfex.services.set import get_index_info_list
        >>> quotes = await get_index_info_list()
        >>> for q in quotes:
        ...     print(f"{q.symbol}: {q.last}")
    """
    service = IndexInfoService(config=config)
    return await service.fetch_index_info_list(index_type=index_type, lang=lang)
