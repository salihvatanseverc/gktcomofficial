"""SET Stock Price Performance Service.

Fetch price performance data for individual stock symbols.
"""

from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_PRICE_PERFORMANCE_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_or_raise


class PricePerformanceMetrics(BaseModel):
    """Model for price performance metrics (stock, sector, or market)."""

    symbol: str = Field(description="Symbol identifier (stock symbol, sector code, or market code)")
    five_day_percent_change: float | None = Field(
        alias="fiveDayPercentChange", description="5-day percentage price change"
    )
    one_month_percent_change: float | None = Field(
        alias="oneMonthPercentChange", description="1-month percentage price change"
    )
    three_month_percent_change: float | None = Field(
        alias="threeMonthPercentChange", description="3-month percentage price change"
    )
    six_month_percent_change: float | None = Field(
        alias="sixMonthPercentChange", description="6-month percentage price change"
    )
    ytd_percent_change: float | None = Field(
        alias="ytdPercentChange", description="Year-to-date percentage price change"
    )
    pe_ratio: float | None = Field(alias="peRatio", description="Price-to-Earnings ratio")
    pb_ratio: float | None = Field(alias="pbRatio", description="Price-to-Book ratio")
    turnover_ratio: float | None = Field(
        alias="turnoverRatio", description="Turnover ratio (trading volume / shares outstanding)"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class PricePerformanceData(BaseModel):
    """Model for complete price performance data including stock, sector, and market comparisons."""

    stock: PricePerformanceMetrics = Field(description="Stock-specific price performance metrics")
    sector: PricePerformanceMetrics = Field(
        description="Sector price performance metrics for comparison"
    )
    market: PricePerformanceMetrics = Field(
        description="Overall market (SET) price performance metrics for comparison"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class PricePerformanceService:
    """
    Service for fetching stock price performance data from SET API.

    This service provides async methods to fetch price performance data for individual
    stock symbols from the Stock Exchange of Thailand (SET), including stock-specific
    metrics and comparative data for sector and overall market performance.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the price performance service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = PricePerformanceService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"PricePerformanceService initialized with base_url={self.base_url}")

    async def fetch_price_performance(
        self, symbol: str, lang: Language = "en"
    ) -> PricePerformanceData:
        """
        Fetch price performance data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            PricePerformanceData object containing stock, sector, and market metrics

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = PricePerformanceService()
            >>> data = await service.fetch_price_performance("MINT", lang="en")
            >>> print(f"Stock: {data.stock.symbol}")
            >>> print(f"5-Day Change: {data.stock.five_day_percent_change:.2f}%")
            >>> print(f"YTD Change: {data.stock.ytd_percent_change:.2f}%")
            >>> print(f"P/E Ratio: {data.stock.pe_ratio}")
            >>> print(f"Sector: {data.sector.symbol}")
            >>> print(f"Sector YTD: {data.sector.ytd_percent_change:.2f}%")
            >>> print(f"Market YTD: {data.market.ytd_percent_change:.2f}%")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_PRICE_PERFORMANCE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching price performance for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/factsheet"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch price performance for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (price-performance)")

            # Validate response is a dict
            if not isinstance(data, dict):
                error_msg = f"Expected dict response, got {type(data).__name__}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            # Validate required keys
            required_keys = {"stock", "sector", "market"}
            if not required_keys.issubset(data.keys()):
                missing = required_keys - data.keys()
                error_msg = f"Missing required keys in response: {missing}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            # Validate using Pydantic (context-rich on failure)
            price_performance = validate_or_raise(
                PricePerformanceData, data, context=f"{symbol} (price-performance)"
            )

            logger.info(
                f"Successfully fetched price performance for {symbol}: "
                f"Stock={price_performance.stock.symbol}, "
                f"Sector={price_performance.sector.symbol}, "
                f"Market={price_performance.market.symbol}"
            )

            return price_performance

    async def fetch_price_performance_raw(
        self, symbol: str, lang: Language = "en"
    ) -> dict[str, Any]:
        """
        Fetch price performance as raw dictionary without Pydantic validation.

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
            >>> service = PricePerformanceService()
            >>> raw_data = await service.fetch_price_performance_raw("MINT")
            >>> print(f"Keys: {raw_data.keys()}")
            >>> print(f"Stock data: {raw_data['stock']}")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_PRICE_PERFORMANCE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw price performance for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/factsheet"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch price performance for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (price-performance)")

            # Validate response is a dict
            if not isinstance(data, dict):
                error_msg = f"Expected dict response, got {type(data).__name__}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            logger.debug(f"Raw response keys: {data.keys()}")
            return data


# Convenience function for quick access
async def get_price_performance(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> PricePerformanceData:
    """
    Convenience function to fetch stock price performance data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        PricePerformanceData object containing stock, sector, and market metrics

    Example:
        >>> from settfex.services.set.stock import get_price_performance
        >>> # Uses SessionManager for automatic cookie handling
        >>> data = await get_price_performance("MINT")
        >>> print(f"Stock Symbol: {data.stock.symbol}")
        >>> print(f"Stock 5-Day: {data.stock.five_day_percent_change:.2f}%")
        >>> print(f"Stock YTD: {data.stock.ytd_percent_change:.2f}%")
        >>> print(f"Stock P/E: {data.stock.pe_ratio}")
        >>> print(f"Sector: {data.sector.symbol}")
        >>> print(f"Sector YTD: {data.sector.ytd_percent_change:.2f}%")
        >>> print(f"Market YTD: {data.market.ytd_percent_change:.2f}%")
    """
    service = PricePerformanceService(config=config)
    return await service.fetch_price_performance(symbol=symbol, lang=lang)
