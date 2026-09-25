"""SET Market Index Chart Quotation Service - Intraday/historical index value series.

The index chart-quotation endpoint returns the exact same payload shape as the stock one,
so the stock service's Pydantic models (``ChartQuotation``, ``Quotation``, ``Intermission``)
and their latest-traded-value scan logic are reused as-is — only the endpoint, referer, and
symbol normalization differ.
"""

from datetime import datetime
from typing import Any

from loguru import logger

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_INDEX_CHART_QUOTATION_ENDPOINT
from settfex.services.set.index.utils import normalize_index_symbol
from settfex.services.set.stock.chart_quotation import ChartQuotation, PeriodType, Quotation
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise

__all__ = [
    "IndexChartQuotationService",
    "get_index_chart_quotation",
    "get_index_latest_price",
]


class IndexChartQuotationService:
    """
    Service for fetching market index chart quotation data from SET API.

    Provides intraday (1D) and historical index value series (value, volume, value traded,
    percent change per time interval), sharing the stock chart-quotation models.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"IndexChartQuotationService initialized with base_url={self.base_url}")

    async def fetch_chart_quotation(
        self,
        symbol: str,
        period: PeriodType = "1D",
        accumulated: bool = False,
    ) -> ChartQuotation:
        """
        Fetch chart quotation data for a specific market index.

        Args:
            symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m")
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)

        Returns:
            ChartQuotation containing prior value, intermissions, and quotation list

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = IndexChartQuotationService()
            >>> data = await service.fetch_chart_quotation("SET50", period="1D")
            >>> print(f"Prior: {data.prior}")
            >>> latest = data.get_latest_quotation()
            >>> if latest:
            ...     print(f"{latest.local_datetime}: {latest.price}")
        """
        symbol = normalize_index_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Index symbol cannot be empty")

        accumulated_str = str(accumulated).lower()
        endpoint = SET_INDEX_CHART_QUOTATION_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?period={period}&accumulated={accumulated_str}"

        logger.info(
            f"Fetching index chart quotation for '{symbol}' "
            f"period={period} accumulated={accumulated}"
        )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/index/{symbol.lower()}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch index chart quotation for {symbol}: "
                    f"HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol, suggest=False)

            data = decode_json(response.text, context=f"{symbol} (index-chart-quotation)")

            result = validate_or_raise(
                ChartQuotation, data, context=f"{symbol} (index-chart-quotation)"
            )
            logger.info(
                f"Successfully fetched index chart quotation for {symbol}: "
                f"{len(result.quotations)} data points, prior={result.prior}"
            )
            return result

    async def fetch_chart_quotation_raw(
        self,
        symbol: str,
        period: PeriodType = "1D",
        accumulated: bool = False,
    ) -> dict[str, Any]:
        """
        Fetch index chart quotation data as raw dictionary without Pydantic validation.

        Args:
            symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m")
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)

        Returns:
            Raw dictionary from API

        Example:
            >>> service = IndexChartQuotationService()
            >>> raw = await service.fetch_chart_quotation_raw("SET50")
            >>> print(raw.keys())
        """
        symbol = normalize_index_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Index symbol cannot be empty")

        accumulated_str = str(accumulated).lower()
        endpoint = SET_INDEX_CHART_QUOTATION_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?period={period}&accumulated={accumulated_str}"

        logger.info(
            f"Fetching raw index chart quotation for '{symbol}' "
            f"period={period} accumulated={accumulated}"
        )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/index/{symbol.lower()}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch index chart quotation for {symbol}: "
                    f"HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol, suggest=False)

            data = decode_json(response.text, context=f"{symbol} (index-chart-quotation)")

            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


async def get_index_chart_quotation(
    symbol: str,
    period: PeriodType = "1D",
    accumulated: bool = False,
    config: FetcherConfig | None = None,
) -> ChartQuotation:
    """
    Convenience function to fetch market index chart quotation data.

    Args:
        symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m")
        period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
        accumulated: Whether to return accumulated volume/value (default: False)
        config: Optional fetcher configuration

    Returns:
        ChartQuotation with prior value, intermissions, and quotation list

    Example:
        >>> from settfex.services.set import get_index_chart_quotation
        >>> data = await get_index_chart_quotation("SET50", period="1D")
        >>> print(f"Prior: {data.prior}, Points: {len(data.quotations)}")
    """
    service = IndexChartQuotationService(config=config)
    return await service.fetch_chart_quotation(
        symbol=symbol, period=period, accumulated=accumulated
    )


async def get_index_latest_price(
    symbol: str,
    period: PeriodType = "1D",
    accumulated: bool = False,
    as_of: datetime | None = None,
    config: FetcherConfig | None = None,
) -> Quotation | None:
    """Fetch index chart quotation and return the latest *traded* quotation vs ``as_of``.

    One-line access to the most recent real index value point — the quotation with the
    greatest timestamp at or before ``as_of`` that has a non-null ``volume``. The
    pre-populated future and no-trade buckets are excluded. Returns ``None`` when nothing
    has traded yet.

    Args:
        symbol: Index symbol (e.g., "SET50", "sSET", "AGRO-m").
        period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'.
        accumulated: Whether to return accumulated volume/value (default: False).
        as_of: Reference instant. A naive value is treated as Asia/Bangkok local time; an
            aware value is converted. Defaults to "now" in Asia/Bangkok.
        config: Optional fetcher configuration.

    Returns:
        The latest traded ``Quotation``, or ``None`` if nothing has traded by ``as_of``.

    Example:
        >>> from settfex.services.set import get_index_latest_price
        >>> q = await get_index_latest_price("SET50")
        >>> if q:
        ...     print(f"{q.local_datetime}: {q.price}")
    """
    data = await get_index_chart_quotation(
        symbol=symbol, period=period, accumulated=accumulated, config=config
    )
    return data.get_latest_quotation(as_of)
