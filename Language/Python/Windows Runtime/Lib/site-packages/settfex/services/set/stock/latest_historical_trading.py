"""SET Stock Latest Historical Trading Service - Fetch latest trading day summary."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import (
    SET_BASE_URL,
    SET_STOCK_LATEST_HISTORICAL_TRADING_ENDPOINT,
)
from settfex.services.set.stock.utils import normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise


class LatestHistoricalTrading(BaseModel):
    """Model for latest historical trading data."""

    date: datetime = Field(description="Trading date")
    symbol: str = Field(description="Stock symbol/ticker")
    prior: float | None = Field(description="Prior closing price")
    open: float | None = Field(description="Opening price")
    high: float | None = Field(description="Highest price of the day")
    low: float | None = Field(description="Lowest price of the day")
    average: float | None = Field(description="Average price of the day")
    close: float | None = Field(description="Closing price")
    change: float | None = Field(description="Price change from prior close")
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
    nav: float | None = Field(description="Net asset value (for ETF/fund)")
    market_index: str | None = Field(alias="marketIndex", description="Market index name")
    market_percent_change: float | None = Field(
        alias="marketPercentChange", description="Market index percent change"
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class LatestHistoricalTradingService:
    """
    Service for fetching the latest historical trading summary from SET API.

    Returns OHLCV data plus valuation metrics (P/E, P/BV, dividend yield, market cap)
    for the most recent completed trading session.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"LatestHistoricalTradingService initialized with base_url={self.base_url}")

    async def fetch_latest_historical_trading(self, symbol: str) -> LatestHistoricalTrading:
        """
        Fetch latest historical trading data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")

        Returns:
            LatestHistoricalTrading with OHLCV and valuation data

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = LatestHistoricalTradingService()
            >>> data = await service.fetch_latest_historical_trading("CPALL")
            >>> print(f"Close: {data.close}, Change: {data.percent_change}%")
            >>> print(f"Volume: {data.total_volume:,.0f}, Market Cap: {data.market_cap:,.0f}")
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Stock symbol cannot be empty")

        endpoint = SET_STOCK_LATEST_HISTORICAL_TRADING_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}"

        logger.info(f"Fetching latest historical trading for '{symbol}' from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch latest historical trading for {symbol}: "
                    f"HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            data = decode_json(response.text, context=f"{symbol} (latest-historical-trading)")

            result = validate_or_raise(
                LatestHistoricalTrading, data, context=f"{symbol} (latest-historical-trading)"
            )
            logger.info(
                f"Successfully fetched latest historical trading for {symbol}: "
                f"close={result.close}, change={result.percent_change}%, "
                f"volume={result.total_volume}"
            )
            return result

    async def fetch_latest_historical_trading_raw(self, symbol: str) -> dict[str, Any]:
        """
        Fetch latest historical trading data as raw dictionary without Pydantic validation.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")

        Returns:
            Raw dictionary from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = LatestHistoricalTradingService()
            >>> raw = await service.fetch_latest_historical_trading_raw("CPALL")
            >>> print(raw.keys())
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Stock symbol cannot be empty")

        endpoint = SET_STOCK_LATEST_HISTORICAL_TRADING_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}"

        logger.info(f"Fetching raw latest historical trading for '{symbol}' from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch latest historical trading for {symbol}: "
                    f"HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            data = decode_json(response.text, context=f"{symbol} (latest-historical-trading)")

            logger.debug(f"Raw response keys: {list(data.keys())}")
            return data  # type: ignore[no-any-return]


async def get_latest_historical_trading(
    symbol: str,
    config: FetcherConfig | None = None,
) -> LatestHistoricalTrading:
    """
    Convenience function to fetch latest historical trading data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
        config: Optional fetcher configuration

    Returns:
        LatestHistoricalTrading with OHLCV and valuation data

    Raises:
        InvalidSymbolError: If the symbol is empty.
        SymbolNotFoundError: If the symbol is not found (HTTP 404).
        FetchError: On other HTTP or transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set.stock import get_latest_historical_trading
        >>> data = await get_latest_historical_trading("CPALL")
        >>> print(f"Close: {data.close}, P/E: {data.pe}, Market Cap: {data.market_cap:,.0f}")
    """
    service = LatestHistoricalTradingService(config=config)
    return await service.fetch_latest_historical_trading(symbol=symbol)
