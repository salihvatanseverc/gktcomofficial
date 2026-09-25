"""TFEX Underlying Price Service - Fetch the underlying instrument price for a TFEX series."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.services.tfex.constants import (
    TFEX_BASE_URL,
    TFEX_UNDERLYING_PRICE_ENDPOINT,
)
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, validate_or_raise


class UnderlyingPrice(BaseModel):
    """Model for the underlying instrument price of a TFEX series.

    For SET50 index options/futures the underlying instrument is the SET50 index, so the
    ``symbol`` here is the underlying (e.g. ``"SET50"``), not the series that was queried.
    Financial fields are ``float | None`` and reject NaN/Infinity at decode time (the JSON
    decoder used by ``AsyncDataFetcher`` rejects non-finite literals before validation).
    """

    symbol: str = Field(description="Underlying instrument symbol (e.g. 'SET50')")
    sign: str = Field(description="Price movement sign indicator (e.g. '+', '-', or empty)")
    prior: float | None = Field(description="Prior day's closing price of the underlying")
    high: float | None = Field(description="Intraday high price of the underlying")
    low: float | None = Field(description="Intraday low price of the underlying")
    last: float | None = Field(description="Last traded price of the underlying")
    change: float | None = Field(description="Absolute price change from prior close")
    percent_change: float | None = Field(
        alias="percentChange", description="Percentage price change from prior close"
    )
    total_volume: float | None = Field(
        alias="totalVolume", description="Total traded volume of the underlying"
    )
    total_value: float | None = Field(
        alias="totalValue", description="Total traded value of the underlying"
    )
    market_status: str = Field(
        alias="marketStatus", description="Market status (e.g. 'Open', 'Closed')"
    )
    market_time: datetime = Field(
        alias="marketTime", description="Market time when the underlying data was captured"
    )
    underlying_type: str = Field(
        alias="underlyingType", description="Underlying instrument type (e.g. 'I' for index)"
    )
    statistics_as_of: datetime = Field(
        alias="statisticsAsOf", description="Timestamp the statistics are reported as of"
    )
    pe: float | None = Field(description="Price-to-earnings ratio of the underlying")
    pbv: float | None = Field(description="Price-to-book-value ratio of the underlying")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class TFEXUnderlyingPriceService:
    """
    Service for fetching the underlying instrument price for a TFEX series.

    This service provides async methods to fetch the price of the instrument underlying a
    TFEX series (for SET50 index options/futures, that is the SET50 index), including the
    last price, intraday high/low, change, traded volume/value, and valuation ratios.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the TFEX underlying price service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Uses SessionManager for automatic cookie handling
            >>> service = TFEXUnderlyingPriceService()
        """
        self.config = config or FetcherConfig()
        self.base_url = TFEX_BASE_URL
        logger.info(f"TFEXUnderlyingPriceService initialized with base_url={self.base_url}")

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol to uppercase.

        Args:
            symbol: Series symbol to normalize

        Returns:
            Normalized symbol in uppercase
        """
        return symbol.upper().strip()

    async def get_underlying_price(self, symbol: str) -> UnderlyingPrice:
        """
        Fetch the underlying instrument price for a specific TFEX series.

        Args:
            symbol: Series symbol (e.g., 'S50M26C880')

        Returns:
            UnderlyingPrice with the underlying instrument's price data

        Raises:
            ResponseParseError: If the response is not valid JSON or contains a
                NaN/Infinity literal (rejected to avoid silent financial-data corruption).
            pydantic.ValidationError: If the response does not satisfy the model.
            Exception: If the request itself fails.

        Example:
            >>> service = TFEXUnderlyingPriceService()
            >>> price = await service.get_underlying_price("S50M26C880")
            >>> print(f"Underlying: {price.symbol}")
            >>> print(f"Last: {price.last}")
            >>> print(f"Change: {price.change} ({price.percent_change}%)")
        """
        # Normalize symbol
        normalized_symbol = self._normalize_symbol(symbol)

        # Build URL with symbol
        url = f"{self.base_url}{TFEX_UNDERLYING_PRICE_ENDPOINT.format(symbol=normalized_symbol)}"

        logger.info(f"Fetching TFEX underlying price for {normalized_symbol} from {url}")

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

            # Fetch JSON data from API - SessionManager handles cookies automatically.
            # fetch_json routes the body through decode_json, which rejects NaN/Infinity
            # literals (the silent financial-data-corruption guard) with URL context.
            data = await fetcher.fetch_json(url, headers=headers)

            # Parse and validate response using Pydantic (context-rich on failure)
            underlying_price = validate_or_raise(
                UnderlyingPrice, data, context=f"{normalized_symbol} (tfex underlying-price)"
            )

            logger.info(
                f"Successfully fetched underlying price for {normalized_symbol}: "
                f"underlying={underlying_price.symbol}, "
                f"last={underlying_price.last}, "
                f"change={underlying_price.change}"
            )

            return underlying_price

    async def get_underlying_price_raw(self, symbol: str) -> dict[str, Any]:
        """
        Fetch the underlying price as a raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Series symbol (e.g., 'S50M26C880')

        Returns:
            Raw dictionary from API

        Raises:
            ResponseParseError: If the request fails or the response is not a JSON object.

        Example:
            >>> service = TFEXUnderlyingPriceService()
            >>> raw_data = await service.get_underlying_price_raw("S50M26C880")
            >>> print(raw_data.keys())
        """
        # Normalize symbol
        normalized_symbol = self._normalize_symbol(symbol)

        # Build URL with symbol
        url = f"{self.base_url}{TFEX_UNDERLYING_PRICE_ENDPOINT.format(symbol=normalized_symbol)}"

        logger.info(f"Fetching raw TFEX underlying price for {normalized_symbol} from {url}")

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
                    f"(tfex underlying-price), got {type(data).__name__}"
                )
            logger.debug(f"Raw response keys: {list(data.keys())}")
            return data


# Convenience function for quick access
async def get_underlying_price(symbol: str, config: FetcherConfig | None = None) -> UnderlyingPrice:
    """
    Convenience function to fetch the underlying instrument price for a TFEX series.

    Args:
        symbol: Series symbol (e.g., 'S50M26C880')
        config: Optional fetcher configuration

    Returns:
        UnderlyingPrice with the underlying instrument's price data

    Example:
        >>> from settfex.services.tfex import get_underlying_price
        >>> # Uses SessionManager for automatic cookie handling
        >>> price = await get_underlying_price("S50M26C880")
        >>> print(f"Underlying: {price.symbol}")
        >>> print(f"Last: {price.last:.2f}")
        >>> print(f"Change: {price.change:.2f} ({price.percent_change:.2f}%)")
        >>> print(f"P/E: {price.pe}, P/BV: {price.pbv}")
    """
    service = TFEXUnderlyingPriceService(config=config)
    return await service.get_underlying_price(symbol)
