"""SET Stock Chart Quotation Service - Fetch intraday/historical chart quotation data."""

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_STOCK_CHART_QUOTATION_ENDPOINT
from settfex.services.set.stock.utils import normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise

PeriodType = Literal["1D", "5D", "1M", "3M", "6M", "1Y", "3Y", "5Y", "MAX"]

# SET trades in Asia/Bangkok; quotation timestamps are tz-aware (+07:00) while ``as_of`` inputs may
# be naive or in any zone. Normalizing both sides to Bangkok keeps comparisons safe (never
# naive-vs-aware) and correct across timezones.
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def _to_bangkok(value: datetime) -> datetime:
    """Return ``value`` as an aware Asia/Bangkok datetime.

    A naive datetime is assumed to already be Bangkok local time; an aware datetime is converted.
    This guarantees safe comparisons (we never compare a naive and an aware datetime directly).
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=BANGKOK_TZ)
    return value.astimezone(BANGKOK_TZ)


class Intermission(BaseModel):
    """Model for trading intermission (lunch break) period."""

    begin: datetime = Field(description="Intermission start time")
    end: datetime = Field(description="Intermission end time")

    model_config = ConfigDict(populate_by_name=True)


class Quotation(BaseModel):
    """Model for a single quotation data point."""

    quote_datetime: datetime = Field(
        alias="datetime", description="Quotation datetime with timezone"
    )
    local_datetime: datetime = Field(
        alias="localDatetime", description="Quotation local datetime (no timezone)"
    )
    price: float | None = Field(description="Trade price")
    volume: float | None = Field(description="Trade volume (shares)")
    value: float | None = Field(description="Trade value (THB)")
    change: float | None = Field(description="Price change from prior close")
    percent_change: float | None = Field(
        alias="percentChange", description="Percentage price change from prior close"
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class ChartQuotation(BaseModel):
    """Model for chart quotation response."""

    prior: float | None = Field(description="Prior closing price")
    intermissions: list[Intermission] = Field(
        default_factory=list, description="Trading intermission periods (e.g. lunch break)"
    )
    quotations: list[Quotation] = Field(
        default_factory=list, description="List of quotation data points"
    )

    model_config = ConfigDict(populate_by_name=True)

    def get_latest_quotation(self, as_of: datetime | None = None) -> Quotation | None:
        """Return the most recent quotation that has actual trade data at or before ``as_of``.

        The API pre-populates the whole trading session with one bucket per minute; minutes that
        are in the future, fall in the lunch intermission, or simply had no trade carry
        ``volume=None`` (the ``price`` may still be carried forward). This returns the quotation
        with the **greatest** timestamp that is (a) at or before ``as_of`` **and** (b) has a
        non-null ``volume`` — i.e. the latest minute that actually traded.

        Args:
            as_of: Reference instant. A naive value is treated as Asia/Bangkok local time; an
                aware value is converted to Bangkok. Defaults to "now" in Asia/Bangkok.

        Returns:
            The latest traded ``Quotation``, or ``None`` if nothing has traded by ``as_of``
            (e.g. before the market opens, or an all-null/empty series).

        Example:
            >>> data = await get_chart_quotation("CPALL", period="1D")
            >>> q = data.get_latest_quotation()
            >>> if q:
            ...     print(f"{q.local_datetime}: {q.price} (vol {q.volume})")
        """
        cutoff = _to_bangkok(as_of) if as_of is not None else datetime.now(BANGKOK_TZ)

        latest: Quotation | None = None
        latest_dt: datetime | None = None
        for quotation in self.quotations:
            if quotation.volume is None:
                continue
            quote_dt = _to_bangkok(quotation.quote_datetime)
            if quote_dt <= cutoff and (latest_dt is None or quote_dt > latest_dt):
                latest = quotation
                latest_dt = quote_dt
        return latest

    def get_latest_price(self, as_of: datetime | None = None) -> float | None:
        """Return the latest traded price at or before ``as_of``, falling back to ``prior``.

        Convenience scalar accessor over :meth:`get_latest_quotation`. When nothing has traded yet
        (pre-open, or an all-null/empty series), this falls back to ``prior`` (the previous
        session's close), or ``None`` when ``prior`` is also unavailable.

        Args:
            as_of: Reference instant (see :meth:`get_latest_quotation`). Defaults to now in
                Asia/Bangkok.

        Returns:
            The latest traded ``price``, or ``prior`` as a fallback, or ``None``.
        """
        latest = self.get_latest_quotation(as_of)
        if latest is not None and latest.price is not None:
            return latest.price
        return self.prior


class ChartQuotationService:
    """
    Service for fetching stock chart quotation data from SET API.

    Provides intraday (1D) and historical chart data for individual stock symbols,
    including price, volume, value, and percent change per time interval.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"ChartQuotationService initialized with base_url={self.base_url}")

    async def fetch_chart_quotation(
        self,
        symbol: str,
        period: PeriodType = "1D",
        accumulated: bool = False,
    ) -> ChartQuotation:
        """
        Fetch chart quotation data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)

        Returns:
            ChartQuotation containing prior price, intermissions, and quotation list

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = ChartQuotationService()
            >>> data = await service.fetch_chart_quotation("CPALL", period="1D")
            >>> print(f"Prior: {data.prior}")
            >>> for q in data.quotations[:5]:
            ...     print(f"{q.local_datetime}: {q.price} ({q.percent_change}%)")
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Stock symbol cannot be empty")

        accumulated_str = str(accumulated).lower()
        endpoint = SET_STOCK_CHART_QUOTATION_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?period={period}&accumulated={accumulated_str}"

        logger.info(
            f"Fetching chart quotation for '{symbol}' period={period} accumulated={accumulated}"
        )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch chart quotation for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            data = decode_json(response.text, context=f"{symbol} (chart-quotation)")

            result = validate_or_raise(ChartQuotation, data, context=f"{symbol} (chart-quotation)")
            logger.info(
                f"Successfully fetched chart quotation for {symbol}: "
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
        Fetch chart quotation data as raw dictionary without Pydantic validation.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)

        Returns:
            Raw dictionary from API

        Example:
            >>> service = ChartQuotationService()
            >>> raw = await service.fetch_chart_quotation_raw("CPALL")
            >>> print(raw.keys())
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Stock symbol cannot be empty")

        accumulated_str = str(accumulated).lower()
        endpoint = SET_STOCK_CHART_QUOTATION_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?period={period}&accumulated={accumulated_str}"

        logger.info(
            f"Fetching raw chart quotation for '{symbol}' period={period} accumulated={accumulated}"
        )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch chart quotation for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            data = decode_json(response.text, context=f"{symbol} (chart-quotation)")

            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


async def get_chart_quotation(
    symbol: str,
    period: PeriodType = "1D",
    accumulated: bool = False,
    config: FetcherConfig | None = None,
) -> ChartQuotation:
    """
    Convenience function to fetch stock chart quotation data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
        period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
        accumulated: Whether to return accumulated volume/value (default: False)
        config: Optional fetcher configuration

    Returns:
        ChartQuotation with prior price, intermissions, and quotation list

    Example:
        >>> from settfex.services.set.stock import get_chart_quotation
        >>> data = await get_chart_quotation("CPALL", period="1D")
        >>> print(f"Prior: {data.prior}, Points: {len(data.quotations)}")
    """
    service = ChartQuotationService(config=config)
    return await service.fetch_chart_quotation(
        symbol=symbol, period=period, accumulated=accumulated
    )


async def get_latest_price(
    symbol: str,
    period: PeriodType = "1D",
    accumulated: bool = False,
    as_of: datetime | None = None,
    config: FetcherConfig | None = None,
) -> Quotation | None:
    """Fetch chart quotation and return the latest *traded* quotation relative to ``as_of``.

    One-line access to the most recent real price point — the quotation with the greatest
    timestamp at or before ``as_of`` that has a non-null ``volume``. The pre-populated future and
    no-trade buckets are excluded. Returns ``None`` when nothing has traded yet.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "JAS-W4"). Hyphens are preserved.
        period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX' (default '1D').
        accumulated: Whether to return accumulated volume/value (default: False).
        as_of: Reference instant. A naive value is treated as Asia/Bangkok local time; an aware
            value is converted. Defaults to "now" in Asia/Bangkok.
        config: Optional fetcher configuration.

    Returns:
        The latest traded ``Quotation``, or ``None`` if nothing has traded by ``as_of``.

    Example:
        >>> from settfex.services.set.stock import get_latest_price
        >>> q = await get_latest_price("CPALL")
        >>> if q:
        ...     print(f"{q.local_datetime}: {q.price} (vol {q.volume})")
    """
    data = await get_chart_quotation(
        symbol=symbol, period=period, accumulated=accumulated, config=config
    )
    return data.get_latest_quotation(as_of)
