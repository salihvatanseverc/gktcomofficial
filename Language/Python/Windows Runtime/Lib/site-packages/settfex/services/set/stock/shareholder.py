"""SET Stock Shareholder Service - Fetch shareholder data for individual stock symbols."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_STOCK_SHAREHOLDER_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise


class MajorShareholder(BaseModel):
    """Model for individual major shareholder information."""

    sequence: int = Field(description="Shareholder ranking sequence number")
    name: str = Field(description="Shareholder name (company or individual)")
    nationality: str | None = Field(description="Shareholder nationality")
    number_of_share: int = Field(alias="numberOfShare", description="Number of shares held")
    percent_of_share: float = Field(
        alias="percentOfShare", description="Percentage of total shares held"
    )
    is_thai_nvdr: bool = Field(alias="isThaiNVDR", description="Whether shareholder is Thai NVDR")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class FreeFloat(BaseModel):
    """Model for free float information."""

    book_close_date: datetime = Field(
        alias="bookCloseDate", description="Book close date for free float data"
    )
    ca_type: str = Field(alias="caType", description="Corporate action type")
    percent_free_float: float = Field(
        alias="percentFreeFloat", description="Percentage of free float shares"
    )
    number_of_holder: int = Field(
        alias="numberOfHolder", description="Number of free float holders"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
    )


class ShareholderData(BaseModel):
    """Model for complete shareholder data."""

    symbol: str = Field(description="Stock symbol/ticker")
    book_close_date: datetime = Field(
        alias="bookCloseDate", description="Book close date for shareholder data"
    )
    ca_type: str = Field(alias="caType", description="Corporate action type")
    total_shareholder: int = Field(
        alias="totalShareholder", description="Total number of shareholders"
    )
    percent_scriptless: float = Field(
        alias="percentScriptless", description="Percentage of scriptless shares"
    )
    major_shareholders: list[MajorShareholder] = Field(
        alias="majorShareholders", description="List of major shareholders"
    )
    free_float: FreeFloat = Field(alias="freeFloat", description="Free float information")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class ShareholderService:
    """
    Service for fetching stock shareholder data from SET API.

    This service provides async methods to fetch shareholder information for individual
    stock symbols from the Stock Exchange of Thailand (SET), including major shareholders,
    free float data, and ownership statistics.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the shareholder service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = ShareholderService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"ShareholderService initialized with base_url={self.base_url}")

    async def fetch_shareholder_data(self, symbol: str, lang: Language = "en") -> ShareholderData:
        """
        Fetch shareholder data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            ShareholderData containing major shareholders, free float, and ownership stats

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = ShareholderService()
            >>> data = await service.fetch_shareholder_data("MINT", lang="en")
            >>> print(f"Total Shareholders: {data.total_shareholder:,}")
            >>> print(f"Free Float: {data.free_float.percent_free_float:.2f}%")
            >>> for sh in data.major_shareholders[:5]:
            ...     print(f"{sh.sequence}. {sh.name}: {sh.percent_of_share:.2f}%")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_STOCK_SHAREHOLDER_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching shareholder data for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch shareholder data for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (shareholder)")

            # Validate response using Pydantic (context-rich on failure)
            shareholder_data = validate_or_raise(
                ShareholderData, data, context=f"{symbol} (shareholder)"
            )

            logger.info(
                f"Successfully fetched shareholder data for {symbol}: "
                f"{len(shareholder_data.major_shareholders)} major shareholders, "
                f"Free Float={shareholder_data.free_float.percent_free_float:.2f}%"
            )

            return shareholder_data

    async def fetch_shareholder_data_raw(
        self, symbol: str, lang: Language = "en"
    ) -> dict[str, Any]:
        """
        Fetch shareholder data as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw dictionary from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = ShareholderService()
            >>> raw_data = await service.fetch_shareholder_data_raw("MINT")
            >>> print(raw_data.keys())
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_STOCK_SHAREHOLDER_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw shareholder data for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch shareholder data for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (shareholder)")

            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_shareholder_data(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> ShareholderData:
    """
    Convenience function to fetch stock shareholder data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        ShareholderData with major shareholders, free float, and ownership stats

    Example:
        >>> from settfex.services.set.stock import get_shareholder_data
        >>> # Uses SessionManager for automatic cookie handling
        >>> data = await get_shareholder_data("MINT")
        >>> print(f"{data.symbol}: {len(data.major_shareholders)} major shareholders")
        >>> print(f"Free Float: {data.free_float.percent_free_float:.2f}%")
    """
    service = ShareholderService(config=config)
    return await service.fetch_shareholder_data(symbol=symbol, lang=lang)
