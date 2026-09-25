"""TFEX Trading Statistics Service - Fetch trading statistics for TFEX series."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.services.tfex.constants import (
    TFEX_BASE_URL,
    TFEX_TRADING_STATISTICS_ENDPOINT,
)
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, validate_or_raise


class TradingStatistics(BaseModel):
    """Model for TFEX trading statistics for a series."""

    symbol: str = Field(description="Series symbol/ticker")
    market_time: datetime = Field(
        alias="marketTime", description="Market time when data was captured"
    )
    last_trading_date: datetime = Field(
        alias="lastTradingDate", description="Last trading date of the series"
    )
    day_to_maturity: int = Field(
        alias="dayToMaturity", description="Number of days until maturity/expiration"
    )
    settlement_pattern: str = Field(
        alias="settlementPattern", description="Number format pattern for settlement price"
    )
    is_options: bool = Field(alias="isOptions", description="Whether series is an options contract")
    theoretical_price: float | None = Field(
        alias="theoreticalPrice", description="Theoretical price of the series"
    )
    prior_settlement_price: float | None = Field(
        alias="priorSettlementPrice", description="Previous settlement price"
    )
    settlement_price: float | None = Field(
        alias="settlementPrice", description="Current settlement price"
    )
    im: float | None = Field(description="Initial margin requirement")
    mm: float | None = Field(description="Maintenance margin requirement")
    has_theoretical_price: bool = Field(
        alias="hasTheoreticalPrice",
        description="Whether theoretical price is available",
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class TradingStatisticsService:
    """
    Service for fetching TFEX trading statistics from TFEX API.

    This service provides async methods to fetch trading statistics for individual
    TFEX series, including settlement prices, margin requirements, theoretical pricing,
    and days to maturity.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the TFEX trading statistics service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Uses SessionManager for automatic cookie handling
            >>> service = TradingStatisticsService()
        """
        self.config = config or FetcherConfig()
        self.base_url = TFEX_BASE_URL
        logger.info(f"TradingStatisticsService initialized with base_url={self.base_url}")

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol to uppercase.

        Args:
            symbol: Series symbol to normalize

        Returns:
            Normalized symbol in uppercase
        """
        return symbol.upper().strip()

    async def fetch_trading_statistics(self, symbol: str) -> TradingStatistics:
        """
        Fetch trading statistics for a specific TFEX series.

        Args:
            symbol: Series symbol (e.g., 'S50Z25')

        Returns:
            TradingStatistics with all trading statistics data

        Raises:
            Exception: If request fails or response cannot be parsed

        Example:
            >>> service = TradingStatisticsService()
            >>> stats = await service.fetch_trading_statistics("S50Z25")
            >>> print(f"Settlement Price: {stats.settlement_price}")
            >>> print(f"Days to Maturity: {stats.day_to_maturity}")
            >>> print(f"Initial Margin: {stats.im}")
        """
        # Normalize symbol
        normalized_symbol = self._normalize_symbol(symbol)

        # Build URL with symbol
        url = f"{self.base_url}{TFEX_TRADING_STATISTICS_ENDPOINT.format(symbol=normalized_symbol)}"

        logger.info(f"Fetching TFEX trading statistics for {normalized_symbol} from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for TFEX API
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": f"https://www.tfex.co.th/en/products/futures/{normalized_symbol}",
                "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                ),
            }

            # Fetch JSON data from API - SessionManager handles cookies automatically
            data = await fetcher.fetch_json(url, headers=headers)

            # Parse and validate response using Pydantic (context-rich on failure)
            statistics = validate_or_raise(
                TradingStatistics, data, context=f"{normalized_symbol} (tfex trading-statistics)"
            )

            logger.info(
                f"Successfully fetched trading statistics for {normalized_symbol}: "
                f"settlement_price={statistics.settlement_price}, "
                f"day_to_maturity={statistics.day_to_maturity}, "
                f"im={statistics.im}"
            )

            return statistics

    async def fetch_trading_statistics_raw(self, symbol: str) -> dict[str, Any]:
        """
        Fetch trading statistics as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Series symbol (e.g., 'S50Z25')

        Returns:
            Raw dictionary from API

        Raises:
            Exception: If request fails

        Example:
            >>> service = TradingStatisticsService()
            >>> raw_data = await service.fetch_trading_statistics_raw("S50Z25")
            >>> print(raw_data.keys())
        """
        # Normalize symbol
        normalized_symbol = self._normalize_symbol(symbol)

        # Build URL with symbol
        url = f"{self.base_url}{TFEX_TRADING_STATISTICS_ENDPOINT.format(symbol=normalized_symbol)}"

        logger.info(f"Fetching raw TFEX trading statistics for {normalized_symbol} from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for TFEX API
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": f"https://www.tfex.co.th/en/products/futures/{normalized_symbol}",
                "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                ),
            }

            # Fetch JSON data
            data: Any = await fetcher.fetch_json(url, headers=headers)
            # Guard the documented dict return type explicitly (asserts are stripped under -O).
            if not isinstance(data, dict):
                raise ResponseParseError(
                    f"Expected a JSON object for {normalized_symbol} "
                    f"(tfex trading-statistics), got {type(data).__name__}"
                )
            logger.debug(f"Raw response keys: {list(data.keys())}")
            return data


# Convenience function for quick access
async def get_trading_statistics(
    symbol: str, config: FetcherConfig | None = None
) -> TradingStatistics:
    """
    Convenience function to fetch TFEX trading statistics for a series.

    Args:
        symbol: Series symbol (e.g., 'S50Z25')
        config: Optional fetcher configuration

    Returns:
        TradingStatistics with all trading statistics data

    Example:
        >>> from settfex.services.tfex import get_trading_statistics
        >>> # Uses SessionManager for automatic cookie handling
        >>> stats = await get_trading_statistics("S50Z25")
        >>> print(f"Symbol: {stats.symbol}")
        >>> print(f"Settlement Price: {stats.settlement_price:.5f}")
        >>> print(f"Days to Maturity: {stats.day_to_maturity}")
        >>> print(f"Initial Margin: {stats.im:,.2f}")
        >>> print(f"Maintenance Margin: {stats.mm:,.2f}")
    """
    service = TradingStatisticsService(config=config)
    return await service.fetch_trading_statistics(symbol)
