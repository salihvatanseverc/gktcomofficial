"""SET Stock Trading Statistics Service - Fetch trading statistics for individual stock symbols."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_TRADING_STAT_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_list_or_raise


class TradingStat(BaseModel):
    """Model for individual trading statistics record."""

    date: datetime = Field(description="Trading statistics date")
    period: str = Field(description="Trading period (e.g., 'YTD', '1M', '3M', '6M', '1Y')")
    symbol: str = Field(description="Stock symbol/ticker")
    market: str = Field(description="Market (e.g., 'SET', 'mai')")
    industry: str = Field(description="Industry classification")
    sector: str = Field(description="Sector classification")
    prior: float | None = Field(description="Prior closing price")
    open: float | None = Field(description="Opening price for the period")
    high: float | None = Field(description="Highest price during the period")
    low: float | None = Field(description="Lowest price during the period")
    average: float | None = Field(description="Average price during the period")
    close: float | None = Field(description="Closing price for the period")
    change: float | None = Field(description="Price change (absolute)")
    percent_change: float | None = Field(
        alias="percentChange", description="Percentage price change"
    )
    total_volume: float | None = Field(
        alias="totalVolume", description="Total trading volume (shares)"
    )
    total_value: float | None = Field(alias="totalValue", description="Total trading value (THB)")
    pe: float | None = Field(description="Price-to-Earnings ratio")
    pbv: float | None = Field(description="Price-to-Book Value ratio")
    book_value_per_share: float | None = Field(
        alias="bookValuePerShare", description="Book value per share (THB)"
    )
    dividend_yield: float | None = Field(alias="dividendYield", description="Dividend yield (%)")
    market_cap: float | None = Field(alias="marketCap", description="Market capitalization (THB)")
    listed_share: float | None = Field(alias="listedShare", description="Number of listed shares")
    par: float | None = Field(description="Par value per share (THB)")
    financial_date: datetime | None = Field(
        alias="financialDate", description="Financial data reference date"
    )
    turnover_ratio: float | None = Field(alias="turnoverRatio", description="Turnover ratio (%)")
    beta: float | None = Field(description="Beta coefficient (volatility measure)")
    dividend_payout_ratio: float | None = Field(
        alias="dividendPayoutRatio", description="Dividend payout ratio"
    )
    average_value: float | None = Field(
        alias="averageValue", description="Average trading value per day (THB)"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class TradingStatService:
    """
    Service for fetching stock trading statistics from SET API.

    This service provides async methods to fetch trading statistics for individual
    stock symbols from the Stock Exchange of Thailand (SET), including price data,
    volume, valuation metrics, and financial ratios across various time periods.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the trading statistics service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = TradingStatService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"TradingStatService initialized with base_url={self.base_url}")

    async def fetch_trading_stats(self, symbol: str, lang: Language = "en") -> list[TradingStat]:
        """
        Fetch trading statistics for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of TradingStat objects for different time periods (YTD, 1M, 3M, 6M, 1Y)

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = TradingStatService()
            >>> stats = await service.fetch_trading_stats("MINT", lang="en")
            >>> for stat in stats:
            ...     print(f"{stat.period}: Close={stat.close}, Change={stat.percent_change:.2f}%")
            ...     print(f"  Volume: {stat.total_volume:,.0f}, P/E: {stat.pe}")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_TRADING_STAT_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching trading statistics for symbol '{symbol}' (lang={lang}) from {url}")

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
                    f"Failed to fetch trading statistics for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (trading-stat)")

            # Validate response is a list
            if not isinstance(data, list):
                error_msg = f"Expected list response, got {type(data).__name__}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            # Validate each record using Pydantic (context-rich on failure)
            trading_stats = validate_list_or_raise(
                TradingStat, data, context=f"{symbol} (trading-stat)"
            )

            logger.info(
                f"Successfully fetched {len(trading_stats)} trading statistics records for {symbol}"
            )

            return trading_stats

    async def fetch_trading_stats_raw(
        self, symbol: str, lang: Language = "en"
    ) -> list[dict[str, Any]]:
        """
        Fetch trading statistics as raw list of dictionaries without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw list of dictionaries from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = TradingStatService()
            >>> raw_stats = await service.fetch_trading_stats_raw("MINT")
            >>> print(f"Found {len(raw_stats)} periods")
            >>> print(raw_stats[0].keys())
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_TRADING_STAT_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw trading statistics for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/factsheet"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch trading statistics for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (trading-stat)")

            # Validate response is a list
            if not isinstance(data, list):
                error_msg = f"Expected list response, got {type(data).__name__}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            logger.debug(f"Raw response: {len(data)} records")
            return data


# Convenience function for quick access
async def get_trading_stats(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> list[TradingStat]:
    """
    Convenience function to fetch stock trading statistics.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "mint")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        List of TradingStat objects for different time periods

    Example:
        >>> from settfex.services.set.stock import get_trading_stats
        >>> # Uses SessionManager for automatic cookie handling
        >>> stats = await get_trading_stats("MINT")
        >>> ytd_stat = next(s for s in stats if s.period == "YTD")
        >>> print(f"YTD Performance: {ytd_stat.percent_change:.2f}%")
        >>> print(f"Current P/E: {ytd_stat.pe}, Market Cap: {ytd_stat.market_cap:,.0f} THB")
    """
    service = TradingStatService(config=config)
    return await service.fetch_trading_stats(symbol=symbol, lang=lang)
