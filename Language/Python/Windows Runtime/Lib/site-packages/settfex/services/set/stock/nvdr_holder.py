"""SET Stock NVDR Holder Service - Fetch NVDR holder data for individual stock symbols."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_NVDR_HOLDER_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise


class NVDRHolder(BaseModel):
    """Model for individual NVDR (Non-Voting Depository Receipt) holder information."""

    sequence: int = Field(description="Holder ranking sequence number")
    name: str = Field(description="Holder name (company or individual)")
    nationality: str | None = Field(description="Holder nationality")
    number_of_share: int = Field(alias="numberOfShare", description="Number of NVDR shares held")
    percent_of_share: float = Field(
        alias="percentOfShare", description="Percentage of total NVDR shares held"
    )
    is_thai_nvdr: bool = Field(alias="isThaiNVDR", description="Whether holder is Thai NVDR")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class NVDRHolderData(BaseModel):
    """Model for complete NVDR holder data."""

    symbol: str = Field(description="Stock symbol/ticker (may include -R suffix)")
    book_close_date: datetime = Field(
        alias="bookCloseDate", description="Book close date for NVDR holder data"
    )
    ca_type: str = Field(alias="caType", description="Corporate action type")
    total_shareholder: int = Field(
        alias="totalShareholder", description="Total number of NVDR holders"
    )
    percent_scriptless: float = Field(
        alias="percentScriptless", description="Percentage of scriptless NVDR shares"
    )
    major_shareholders: list[NVDRHolder] = Field(
        alias="majorShareholders", description="List of major NVDR holders"
    )
    free_float: Any | None = Field(
        alias="freeFloat",
        description="Free float information (typically null for NVDR)",
        default=None,
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class NVDRHolderService:
    """
    Service for fetching stock NVDR holder data from SET API.

    This service provides async methods to fetch NVDR (Non-Voting Depository Receipt)
    holder information for individual stock symbols from the Stock Exchange of Thailand (SET),
    including major NVDR holders and ownership statistics.

    NVDR shares are depository receipts that carry the same rights as ordinary shares
    except for voting rights in shareholder meetings.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the NVDR holder service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = NVDRHolderService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"NVDRHolderService initialized with base_url={self.base_url}")

    async def fetch_nvdr_holder_data(self, symbol: str, lang: Language = "en") -> NVDRHolderData:
        """
        Fetch NVDR holder data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "MINT", "PTT", "cpall")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            NVDRHolderData containing major NVDR holders and ownership stats

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = NVDRHolderService()
            >>> data = await service.fetch_nvdr_holder_data("MINT", lang="en")
            >>> print(f"Total NVDR Holders: {data.total_shareholder:,}")
            >>> print(f"Symbol: {data.symbol}")  # May include -R suffix
            >>> for holder in data.major_shareholders[:5]:
            ...     print(f"{holder.sequence}. {holder.name}: {holder.percent_of_share:.2f}%")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_NVDR_HOLDER_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching NVDR holder data for symbol '{symbol}' (lang={lang}) from {url}")

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
                    f"Failed to fetch NVDR holder data for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (nvdr-holder)")

            # Validate response using Pydantic (context-rich on failure)
            nvdr_holder_data = validate_or_raise(
                NVDRHolderData, data, context=f"{symbol} (nvdr-holder)"
            )

            logger.info(
                f"Successfully fetched NVDR holder data for {symbol}: "
                f"{len(nvdr_holder_data.major_shareholders)} major NVDR holders, "
                f"Total holders={nvdr_holder_data.total_shareholder:,}"
            )

            return nvdr_holder_data

    async def fetch_nvdr_holder_data_raw(
        self, symbol: str, lang: Language = "en"
    ) -> dict[str, Any]:
        """
        Fetch NVDR holder data as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "MINT", "PTT", "cpall")
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
            >>> service = NVDRHolderService()
            >>> raw_data = await service.fetch_nvdr_holder_data_raw("MINT")
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
        endpoint = SET_NVDR_HOLDER_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw NVDR holder data for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch NVDR holder data for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (nvdr-holder)")

            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_nvdr_holder_data(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> NVDRHolderData:
    """
    Convenience function to fetch stock NVDR holder data.

    Args:
        symbol: Stock symbol (e.g., "MINT", "PTT", "cpall")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        NVDRHolderData with major NVDR holders and ownership stats

    Example:
        >>> from settfex.services.set.stock import get_nvdr_holder_data
        >>> # Uses SessionManager for automatic cookie handling
        >>> data = await get_nvdr_holder_data("MINT")
        >>> print(f"{data.symbol}: {len(data.major_shareholders)} major NVDR holders")
        >>> print(f"Total holders: {data.total_shareholder:,}")
    """
    service = NVDRHolderService(config=config)
    return await service.fetch_nvdr_holder_data(symbol=symbol, lang=lang)
