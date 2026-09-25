"""Unified ThaiBMA facade — one entry point for Thai government bond yield curve data.

>>> tbma = ThaiBMA()
>>> curve = await tbma.get_yield_curve()                     # latest published curve
>>> history = await tbma.get_history("2020-01-01")           # 7 requests, not ~1,600
>>> availability = await tbma.get_availability()             # what history exists at all
"""

from __future__ import annotations

from datetime import date, datetime

from loguru import logger

from settfex.services.thaibma.availability import (
    YieldCurveAvailability,
    YieldCurveAvailabilityService,
)
from settfex.services.thaibma.history import (
    DEFAULT_MAX_CONCURRENCY,
    HistoryKind,
    YieldCurveHistory,
    YieldCurveHistoryService,
)
from settfex.services.thaibma.yield_curve import (
    RollbackPolicy,
    YieldCurve,
    YieldCurveService,
)
from settfex.utils.data_fetcher import FetcherConfig


class ThaiBMA:
    """
    Facade over the ThaiBMA yield-curve services.

    Unlike :class:`~settfex.services.set.Stock` or :class:`~settfex.services.sec.SecCompany`, this
    facade is not scoped to an entity — ThaiBMA publishes one national curve, so it takes no
    symbol. The three underlying services are lazily constructed and cached. All methods are async.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the facade.

        Args:
            config: Optional fetcher configuration shared by every underlying service.
                ``use_session`` is forced off for this host.

        Example:
            >>> tbma = ThaiBMA()
            >>> tbma = ThaiBMA(FetcherConfig(timeout=60))
        """
        self.config = config
        self._curve_service: YieldCurveService | None = None
        self._history_service: YieldCurveHistoryService | None = None
        self._availability_service: YieldCurveAvailabilityService | None = None
        logger.info("ThaiBMA facade initialized (host=www.thaibma.or.th)")

    @property
    def curve_service(self) -> YieldCurveService:
        """Lazily-constructed point-in-time curve service."""
        if self._curve_service is None:
            self._curve_service = YieldCurveService(config=self.config)
        return self._curve_service

    @property
    def history_service(self) -> YieldCurveHistoryService:
        """Lazily-constructed bulk-year history service."""
        if self._history_service is None:
            self._history_service = YieldCurveHistoryService(config=self.config)
        return self._history_service

    @property
    def availability_service(self) -> YieldCurveAvailabilityService:
        """Lazily-constructed availability service."""
        if self._availability_service is None:
            self._availability_service = YieldCurveAvailabilityService(config=self.config)
        return self._availability_service

    async def get_yield_curve(
        self,
        curve_date: date | datetime | str | None = None,
        *,
        on_rollback: RollbackPolicy = "warn",
    ) -> YieldCurve:
        """
        Fetch the government yield curve as at a date (default: the latest published).

        Args:
            curve_date: Date to fetch, or None for the latest curve.
            on_rollback: ``"warn"`` (default), ``"raise"`` or ``"allow"`` — what to do when
                ThaiBMA answers with an earlier date (weekend, holiday, or a future date).

        Returns:
            A :class:`YieldCurve`.

        Example:
            >>> curve = await ThaiBMA().get_yield_curve("2026-08-10")
            >>> curve.yield_at("10Y")
            2.060279
        """
        return await self.curve_service.fetch_curve(curve_date, on_rollback=on_rollback)

    async def get_history(
        self,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        *,
        kind: HistoryKind | str = HistoryKind.TENOR,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        check_availability: bool = True,
        progress: bool = False,
    ) -> YieldCurveHistory:
        """
        Fetch daily yield history across a span, one request per calendar year.

        Args:
            start_date: Inclusive start. Defaults to 1 January of the end year.
            end_date: Inclusive end. Defaults to today in Asia/Bangkok.
            kind: ``"tenor"`` (default) for constant-maturity yields, ``"bond"`` for per-bond.
            max_concurrency: Maximum simultaneous year requests.
            check_availability: Drop and report years ThaiBMA does not serve (default True).
            progress: Show a tqdm progress bar (needs the optional ``progress`` extra).

        Returns:
            A :class:`YieldCurveHistory`.

        Example:
            >>> history = await ThaiBMA().get_history("2020-01-01")
            >>> history.to_dataframe().shape
            (1608, 54)
        """
        return await self.history_service.fetch_history(
            start_date,
            end_date,
            kind=kind,
            max_concurrency=max_concurrency,
            check_availability=check_availability,
            progress=progress,
        )

    async def get_bond_history(
        self,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        check_availability: bool = True,
        progress: bool = False,
    ) -> YieldCurveHistory:
        """
        Fetch per-bond yield history across a span (columns are ThaiBMA bond symbols).

        Args:
            start_date: Inclusive start. Defaults to 1 January of the end year.
            end_date: Inclusive end. Defaults to today in Asia/Bangkok.
            max_concurrency: Maximum simultaneous year requests.
            check_availability: Drop and report years ThaiBMA does not serve (default True).
            progress: Show a tqdm progress bar.

        Returns:
            A :class:`YieldCurveHistory` keyed by bond symbol.

        Example:
            >>> history = await ThaiBMA().get_bond_history("2026-01-01")
            >>> history.series("LB776A")[-1][1]
            3.293888888888889
        """
        return await self.get_history(
            start_date,
            end_date,
            kind=HistoryKind.BOND,
            max_concurrency=max_concurrency,
            check_availability=check_availability,
            progress=progress,
        )

    async def get_availability(self, *, include_years: bool = True) -> YieldCurveAvailability:
        """
        Discover what yield-curve history ThaiBMA publishes.

        Args:
            include_years: Also fetch the list of years with data (default True).

        Returns:
            A :class:`YieldCurveAvailability`.

        Example:
            >>> availability = await ThaiBMA().get_availability()
            >>> availability.first_date
            datetime.date(1999, 9, 15)
        """
        return await self.availability_service.fetch_availability(include_years=include_years)
