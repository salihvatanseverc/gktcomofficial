"""TFEX Series List Service - Fetch list of futures/options series from TFEX API."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.services.tfex.constants import TFEX_BASE_URL, TFEX_SERIES_LIST_ENDPOINT
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, validate_or_raise


class TFEXSeries(BaseModel):
    """Model for individual TFEX series information."""

    symbol: str = Field(description="Series symbol/ticker")
    instrument_id: str = Field(
        alias="instrumentId", description="Instrument identifier (e.g., SET50_FC)"
    )
    instrument_name: str = Field(
        alias="instrumentName", description="Instrument name (e.g., SET50 Futures)"
    )
    market_list_id: str = Field(
        alias="marketListId", description="Market list identifier (e.g., TXI_F)"
    )
    market_list_name: str = Field(
        alias="marketListName", description="Market list name (e.g., Equity Index Futures)"
    )
    first_trading_date: datetime = Field(
        alias="firstTradingDate", description="First trading date of the series"
    )
    last_trading_date: datetime = Field(
        alias="lastTradingDate", description="Last trading date of the series"
    )
    contract_month: str = Field(alias="contractMonth", description="Contract month (e.g., 10/2025)")
    options_type: str = Field(
        alias="optionsType", description="Options type (empty for futures, C/P for options)"
    )
    strike_price: float | None = Field(
        alias="strikePrice", description="Strike price (for options only)"
    )
    has_night_session: bool = Field(
        alias="hasNightSession", description="Whether series has night trading session"
    )
    underlying: str = Field(description="Underlying asset (e.g., SET50, GOLD)")
    active: bool = Field(description="Whether series is currently active for trading")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class TFEXSeriesListResponse(BaseModel):
    """Response model for TFEX series list API."""

    series: list[TFEXSeries] = Field(description="List of TFEX series")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
    )

    @property
    def count(self) -> int:
        """Get total count of series."""
        return len(self.series)

    def filter_by_instrument(self, instrument_id: str) -> list[TFEXSeries]:
        """
        Filter series by instrument ID.

        Args:
            instrument_id: Instrument identifier (e.g., 'SET50_FC', 'GOLD_FC')

        Returns:
            List of series for the specified instrument
        """
        return [s for s in self.series if s.instrument_id.upper() == instrument_id.upper()]

    def filter_by_market(self, market_list_id: str) -> list[TFEXSeries]:
        """
        Filter series by market list ID.

        Args:
            market_list_id: Market list identifier (e.g., 'TXI_F' for Equity Index Futures)

        Returns:
            List of series in the specified market
        """
        return [s for s in self.series if s.market_list_id.upper() == market_list_id.upper()]

    def filter_by_underlying(self, underlying: str) -> list[TFEXSeries]:
        """
        Filter series by underlying asset.

        Args:
            underlying: Underlying asset (e.g., 'SET50', 'GOLD')

        Returns:
            List of series with the specified underlying
        """
        return [s for s in self.series if s.underlying.upper() == underlying.upper()]

    def filter_active_only(self) -> list[TFEXSeries]:
        """
        Get only active series.

        Returns:
            List of active series
        """
        return [s for s in self.series if s.active]

    def get_futures(self) -> list[TFEXSeries]:
        """
        Get only futures contracts (not options).

        Returns:
            List of futures series (options_type is empty)
        """
        return [s for s in self.series if not s.options_type]

    def get_options(self) -> list[TFEXSeries]:
        """
        Get only options contracts.

        Returns:
            List of options series (options_type is C or P)
        """
        return [s for s in self.series if s.options_type]

    def get_symbol(self, symbol: str) -> TFEXSeries | None:
        """
        Get a specific series by symbol.

        Args:
            symbol: Series symbol to find

        Returns:
            TFEXSeries if found, None otherwise
        """
        for s in self.series:
            if s.symbol.upper() == symbol.upper():
                return s
        return None


class TFEXSeriesListService:
    """
    Service for fetching TFEX series list from TFEX API.

    This service provides async methods to fetch the complete list of futures
    and options series traded on the Thailand Futures Exchange (TFEX), including
    contract details, trading dates, and underlying assets.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the TFEX series list service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Uses SessionManager for automatic cookie handling
            >>> service = TFEXSeriesListService()
        """
        self.config = config or FetcherConfig()
        self.base_url = TFEX_BASE_URL
        logger.info(f"TFEXSeriesListService initialized with base_url={self.base_url}")

    async def fetch_series_list(self) -> TFEXSeriesListResponse:
        """
        Fetch the complete list of TFEX series from TFEX API.

        Returns:
            TFEXSeriesListResponse containing all series details

        Raises:
            Exception: If request fails or response cannot be parsed

        Example:
            >>> service = TFEXSeriesListService()
            >>> response = await service.fetch_series_list()
            >>> print(f"Total series: {response.count}")
            >>> print(f"Active series: {len(response.filter_active_only())}")
            >>> print(f"Futures only: {len(response.get_futures())}")
        """
        url = f"{self.base_url}{TFEX_SERIES_LIST_ENDPOINT}"

        logger.info(f"Fetching TFEX series list from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for TFEX API
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://www.tfex.co.th/en/products/futures",
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
            response = validate_or_raise(TFEXSeriesListResponse, data, context="tfex series-list")

            logger.info(
                f"Successfully fetched {response.count} TFEX series from TFEX API "
                f"({len(response.filter_active_only())} active, "
                f"{len(response.get_futures())} futures, "
                f"{len(response.get_options())} options)"
            )

            return response

    async def fetch_series_list_raw(self) -> dict[str, Any]:
        """
        Fetch series list as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Returns:
            Raw dictionary from API

        Raises:
            Exception: If request fails

        Example:
            >>> service = TFEXSeriesListService()
            >>> raw_data = await service.fetch_series_list_raw()
            >>> print(raw_data.keys())
        """
        url = f"{self.base_url}{TFEX_SERIES_LIST_ENDPOINT}"

        logger.info(f"Fetching raw TFEX series list from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for TFEX API
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://www.tfex.co.th/en/products/futures",
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
                    f"Expected a JSON object for tfex series-list, got {type(data).__name__}"
                )
            logger.debug(f"Raw response keys: {list(data.keys())}")
            return data


# Convenience function for quick access
async def get_series_list(config: FetcherConfig | None = None) -> TFEXSeriesListResponse:
    """
    Convenience function to fetch TFEX series list.

    Args:
        config: Optional fetcher configuration

    Returns:
        TFEXSeriesListResponse with all series

    Example:
        >>> from settfex.services.tfex import get_series_list
        >>> # Uses SessionManager for automatic cookie handling
        >>> response = await get_series_list()
        >>> for series in response.filter_active_only()[:5]:
        ...     print(f"{series.symbol}: {series.instrument_name}")
    """
    service = TFEXSeriesListService(config=config)
    return await service.fetch_series_list()
