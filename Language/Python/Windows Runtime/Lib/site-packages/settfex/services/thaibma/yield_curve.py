"""ThaiBMA government bond yield curve — the curve as at one date.

Wraps ``GET /yieldcurve/gov/{YYYY-MM-DD}``, which returns two blocks:

- ``Curve`` — the **fitted par yield curve** on ThaiBMA's standard tenor grid (1M, 3M, 6M, then
  whole years). 53 points in 2026, 14 in 1999; the grid has grown as longer bonds were issued.
- ``Stat`` — the **underlying bond quotes** the curve was fitted to, with per-bond yield, day-on-day
  change, maturity and classification flags.

Four live-verified behaviours shape this module (probed 2026-08-10):

1. **The endpoint never 404s on a date — it rolls back silently.** It answers with the most recent
   curve *on or before* the request. A Saturday returns Friday's curve; a Thai holiday returns the
   previous business day's; and **any future date returns today's curve**, all with HTTP 200 and no
   marker. :class:`YieldCurve` therefore always carries both ``requested_date`` and ``as_of``, plus
   the serialized ``is_rolled_back`` / ``rollback_days`` fields, and ``on_rollback`` controls
   whether a mismatch warns (default), raises, or passes silently.
2. **``Yield`` is in percent but ``Change`` is in basis points.** Verified by differencing two
   consecutive business days: a move of ``-0.005534%`` is published as ``Change: -0.5534``. The
   field is named :attr:`BondQuote.change_bps` so the unit travels with it, and
   :attr:`BondQuote.change_percent` is the safe-to-add derived form. ``Change`` is null on
   1999-09-15 — the first curve ever published has no prior day to difference against — so it is
   the second nullable field alongside ``MaturityDate``.
3. **A date before 1999-09-15 returns a body of literal ``null``** under HTTP 200 — not ``{}``, not
   a 404. That is raised as :class:`~settfex.exceptions.FetchError` naming the first available date.
4. **The classification flags were never backfilled.** ``IsBenchmark`` is all-false before 2013 and
   ``IsSynthetic`` all-false before 2014, so :attr:`YieldCurve.benchmarks` is legitimately empty for
   the first ~14 years of history rather than broken. ``IsPlot`` was ``True`` on every row in every
   era sampled and is not a useful filter.

.. note::
    Unlike the SET services this module takes **no ``lang`` argument**. The endpoint has no
    language dimension at all — the payload is numeric plus bond symbols — so accepting a ``lang``
    that did nothing would be a lie rather than a convenience.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, computed_field

from settfex.exceptions import FetchError, StaleDataError
from settfex.services.thaibma.constants import (
    BANGKOK_TZ,
    THAIBMA_BASE_URL,
    THAIBMA_FIRST_CURVE_DATE,
    THAIBMA_GOV_CURVE_ENDPOINT,
)
from settfex.services.thaibma.utils import (
    fetch_thaibma_json,
    format_curve_date,
    normalize_curve_date,
    stateless_config,
    tenor_label,
)
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import validate_or_raise

if TYPE_CHECKING:
    import pandas as pd

# What to do when ThaiBMA answers with a different date than was requested.
RollbackPolicy = Literal["warn", "raise", "allow"]

# Default concurrency for multi-date fetches. The host served 20 parallel requests in 0.34 s, so
# this is politeness rather than necessity.
DEFAULT_MAX_CONCURRENCY = 5


class CurvePoint(BaseModel):
    """One fitted point on ThaiBMA's standard tenor grid."""

    as_of: date = Field(alias="Asof", description="Curve date (Bangkok calendar day)")
    tenor_years: float = Field(
        alias="X",
        description="Tenor in years: 0.076712=1M, 0.249315=3M, 0.498630=6M, then whole years",
    )
    yield_percent: float = Field(alias="Y", description="Fitted par yield, in PERCENT per annum")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def tenor_label(self) -> str:
        """
        This point's tenor as a history-matrix column label (``"1M"``, ``"6M"``, ``"10Y"``).

        This is the join key between the point-in-time curve (keyed by the float ``X``) and the
        history matrices (keyed by a string label); the mapping is exact, not approximate.
        """
        return tenor_label(self.tenor_years)


class BondQuote(BaseModel):
    """One bond or T-Bill quote underlying the fitted curve (a row of the ``Stat`` block)."""

    as_of: date = Field(alias="Asof", description="Quote date (Bangkok calendar day)")
    symbol: str = Field(alias="Symbol", description="ThaiBMA symbol, e.g. 'LB776A' or 'T-BILL1M'")
    maturity_date: date | None = Field(
        default=None,
        alias="MaturityDate",
        description=(
            "Maturity, or None for the four synthetic T-BILL tenor rows - the ONLY nullable "
            "field in this payload"
        ),
    )
    ttm_years: float = Field(alias="Ttm", description="Time to maturity, in years")
    yield_percent: float = Field(alias="Yield", description="Quoted yield, in PERCENT per annum")
    change_bps: float | None = Field(
        default=None,
        alias="Change",
        description=(
            "Day-on-day yield change in BASIS POINTS - note yield_percent is in PERCENT. "
            "Verified by differencing consecutive business days: a -0.005534% move is "
            "published here as -0.5534. None on 1999-09-15, the first curve ever published, "
            "which has no prior business day to difference against"
        ),
    )
    spread: float = Field(
        alias="Spread",
        description=(
            "Spread as published by ThaiBMA. What it is a spread *to* was not independently "
            "verified, so the unit is deliberately not baked into the field name"
        ),
    )
    group_order: int = Field(
        alias="GroupOrder", description="1 = T-Bill, 2 = government bond (LB/ESGLB/SLB/ILB)"
    )
    is_synthetic: bool = Field(
        alias="IsSynthetic",
        description="Interpolated rather than directly quoted. Always False before 2014",
    )
    is_plot: bool = Field(
        alias="IsPlot",
        description="ThaiBMA's plot flag - True on every row observed across 27 years",
    )
    is_benchmark: bool = Field(
        alias="IsBenchmark",
        description="An on-the-run benchmark bond. Always False before 2013",
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @property
    def change_percent(self) -> float | None:
        """
        Day-on-day change expressed in percent, so it can be added to :attr:`yield_percent`.

        None when :attr:`change_bps` is None (the 1999-09-15 payload).
        """
        return None if self.change_bps is None else self.change_bps / 100

    @property
    def is_tbill(self) -> bool:
        """True for the T-Bill rows (``group_order == 1``)."""
        return self.group_order == 1


class _CurvePayload(BaseModel):
    """Private wire envelope: ``{"Curve": [...], "Stat": [...]}``."""

    curve: list[CurvePoint] = Field(default_factory=list, alias="Curve")
    stat: list[BondQuote] = Field(default_factory=list, alias="Stat")

    model_config = ConfigDict(populate_by_name=True)


class YieldCurve(BaseModel):
    """
    The Thai government bond yield curve as at one date, plus the quotes behind it.

    ``requested_date`` and ``as_of`` are both retained because they routinely differ: see the
    module docstring on silent roll-back. :attr:`is_rolled_back` and :attr:`rollback_days` are
    computed fields, so they survive ``model_dump()`` — a curve persisted to disk keeps the audit
    trail of what was asked for versus what was served.
    """

    requested_date: date | None = Field(
        default=None, description="The date the caller asked for; None means 'latest'"
    )
    as_of: date = Field(description="The date ThaiBMA actually answered with")
    points: list[CurvePoint] = Field(
        default_factory=list, description="Fitted curve, ascending by tenor"
    )
    quotes: list[BondQuote] = Field(
        default_factory=list, description="Underlying bond and T-Bill quotes"
    )

    model_config = ConfigDict(populate_by_name=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_rolled_back(self) -> bool:
        """True when ThaiBMA answered with an earlier date than was requested."""
        return self.requested_date is not None and self.as_of != self.requested_date

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rollback_days(self) -> int | None:
        """
        Calendar days between the requested date and the date served, or None if none was asked.

        Useful as a diagnostic: 1-4 days is an ordinary weekend or public holiday, while a large
        value means a future date was requested and today's curve came back instead.
        """
        if self.requested_date is None:
            return None
        return (self.requested_date - self.as_of).days

    @property
    def count(self) -> int:
        """Number of points on the fitted curve."""
        return len(self.points)

    @property
    def tenors(self) -> list[float]:
        """Curve tenors in years, ascending."""
        return sorted(point.tenor_years for point in self.points)

    @property
    def tenor_labels(self) -> list[str]:
        """Curve tenors as history-matrix labels (``["1M", "3M", "6M", "1Y", ...]``)."""
        return [point.tenor_label for point in sorted(self.points, key=lambda p: p.tenor_years)]

    @property
    def benchmarks(self) -> list[BondQuote]:
        """
        The on-the-run benchmark bonds.

        .. warning::
            Legitimately **empty before 2013** — ThaiBMA never backfilled ``IsBenchmark`` into
            older data. An empty list for a 2005 curve is the data, not a failure.
        """
        return [quote for quote in self.quotes if quote.is_benchmark]

    @property
    def bills(self) -> list[BondQuote]:
        """The T-Bill quotes (``group_order == 1``)."""
        return [quote for quote in self.quotes if quote.group_order == 1]

    @property
    def bonds(self) -> list[BondQuote]:
        """The government bond quotes (``group_order == 2``)."""
        return [quote for quote in self.quotes if quote.group_order == 2]

    def to_dict(self) -> dict[str, float]:
        """
        The fitted curve as ``{tenor_label: yield_percent}``, in maturity order.

        Example:
            >>> curve.to_dict()["10Y"]
            2.060279
        """
        return {
            point.tenor_label: point.yield_percent
            for point in sorted(self.points, key=lambda p: p.tenor_years)
        }

    def yield_at(self, tenor: str | float) -> float | None:
        """
        Look up the fitted yield at an exact grid tenor.

        Args:
            tenor: A label (``"10Y"``, ``"6M"``) or a tenor in years (``10``, ``10.0``).

        Returns:
            The yield in percent, or None if that tenor is not on this curve's grid. The grid is
            era-dependent: a 1999 curve has no sub-year tenors and stops at 14Y.

        Example:
            >>> curve.yield_at("10Y") == curve.yield_at(10)
            True
        """
        from settfex.services.thaibma.utils import parse_tenor

        target = parse_tenor(tenor) if isinstance(tenor, str) else float(tenor)
        for point in self.points:
            if abs(point.tenor_years - target) < 1e-6:
                return point.yield_percent
        return None

    def interpolate(self, tenor_years: float) -> float:
        """
        Linearly interpolate the yield at an arbitrary tenor, in yield space.

        This is settfex's own interpolation between ThaiBMA's published grid points — **not**
        ThaiBMA's curve-fitting model. It never extrapolates.

        Args:
            tenor_years: Tenor in years, within the curve's grid range.

        Returns:
            The interpolated yield in percent.

        Raises:
            ValueError: If the curve is empty, or the tenor lies outside the grid.

        Example:
            >>> curve.interpolate(7.5)
            1.8555...
        """
        if not self.points:
            error_msg = "Cannot interpolate an empty yield curve"
            logger.error(error_msg)
            raise ValueError(error_msg)

        ordered = sorted(self.points, key=lambda p: p.tenor_years)
        low, high = ordered[0].tenor_years, ordered[-1].tenor_years
        if not low <= tenor_years <= high:
            error_msg = (
                f"Tenor {tenor_years} years is outside this curve's grid ({low:g}..{high:g} "
                f"years, as of {self.as_of}); extrapolation is deliberately not supported"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        for left, right in zip(ordered, ordered[1:], strict=False):
            if left.tenor_years <= tenor_years <= right.tenor_years:
                span = right.tenor_years - left.tenor_years
                if span == 0:
                    return left.yield_percent
                weight = (tenor_years - left.tenor_years) / span
                return left.yield_percent + weight * (right.yield_percent - left.yield_percent)
        return ordered[-1].yield_percent

    def slope_bps(self, short_tenor: str | float, long_tenor: str | float) -> float:
        """
        Curve slope between two grid tenors, in basis points.

        Args:
            short_tenor: The near leg, e.g. ``"2Y"``.
            long_tenor: The far leg, e.g. ``"10Y"``.

        Returns:
            ``(long_yield - short_yield) * 100``, in basis points.

        Raises:
            ValueError: If either tenor is not on this curve's grid.

        Example:
            >>> curve.slope_bps("2Y", "10Y")   # the 2s10s
            92.73
        """
        near, far = self.yield_at(short_tenor), self.yield_at(long_tenor)
        if near is None or far is None:
            missing = short_tenor if near is None else long_tenor
            error_msg = f"Tenor {missing!r} is not on the curve grid for {self.as_of}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        return (far - near) * 100

    def quote(self, symbol: str) -> BondQuote | None:
        """
        Find one underlying quote by symbol, case-insensitively.

        Args:
            symbol: e.g. ``"LB776A"`` or ``"t-bill1m"``.

        Returns:
            The matching :class:`BondQuote`, or None.
        """
        target = symbol.strip().upper()
        for quote in self.quotes:
            if quote.symbol.upper() == target:
                return quote
        return None

    def to_dataframe(self, kind: Literal["curve", "quotes"] = "curve") -> pd.DataFrame:
        """
        Render the curve or its underlying quotes as a pandas DataFrame.

        pandas is an optional dependency, imported lazily so that importing this service never
        requires it.

        Args:
            kind: ``"curve"`` for the fitted grid (tenor/tenor_label/yield_percent), or
                ``"quotes"`` for the underlying bond rows.

        Returns:
            A DataFrame with one row per point or per quote.

        Raises:
            ImportError: If pandas is not installed.
            ValueError: If ``kind`` is not recognized.
        """
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatched sys.modules
            raise ImportError(
                "pandas is required for YieldCurve.to_dataframe(). Install it with "
                "'pip install settfex[dataframe]' (or 'uv add pandas')."
            ) from exc

        if kind == "curve":
            ordered = sorted(self.points, key=lambda p: p.tenor_years)
            return pd.DataFrame(
                [
                    {
                        "as_of": point.as_of,
                        "tenor_years": point.tenor_years,
                        "tenor": point.tenor_label,
                        "yield_percent": point.yield_percent,
                    }
                    for point in ordered
                ]
            )
        if kind == "quotes":
            return pd.DataFrame([quote.model_dump() for quote in self.quotes])

        error_msg = f"kind must be 'curve' or 'quotes', got {kind!r}"
        logger.error(error_msg)
        raise ValueError(error_msg)


class YieldCurveService:
    """
    Fetch the Thai government bond yield curve for a date.

    The ThaiBMA host is stateless — no cookies, no warm-up — so ``use_session`` is forced off
    (routing it through SessionManager would warm a ThaiBMA URL against set.or.th).
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the yield curve service.

        Args:
            config: Optional fetcher configuration. ``use_session`` is always forced off; every
                other setting (timeout, retries, rate limiting) is preserved.

        Example:
            >>> service = YieldCurveService()
            >>> service = YieldCurveService(FetcherConfig(timeout=60))
        """
        self.config = stateless_config(config)
        self.base_url = THAIBMA_BASE_URL
        logger.info(f"YieldCurveService initialized with base_url={self.base_url}")

    def _build_url(self, day: date | None) -> str:
        """Build the curve URL; omitting the date segment asks for the latest curve."""
        endpoint = THAIBMA_GOV_CURVE_ENDPOINT
        if day is not None:
            endpoint = f"{endpoint}/{format_curve_date(day)}"
        return f"{self.base_url}{endpoint}"

    @staticmethod
    def _resolve_request_date(curve_date: date | datetime | str | None) -> date | None:
        """Normalize the requested date and reject anything before ThaiBMA's first curve."""
        if curve_date is None:
            return None
        day = normalize_curve_date(curve_date)
        if day < THAIBMA_FIRST_CURVE_DATE:
            error_msg = (
                f"ThaiBMA has no yield curve for {day} - the first available curve is "
                f"{THAIBMA_FIRST_CURVE_DATE}. (The API answers earlier dates with HTTP 200 and a "
                f"body of literal 'null', so this is rejected client-side.)"
            )
            logger.error(error_msg)
            from settfex.exceptions import InvalidDateError

            raise InvalidDateError(error_msg)
        return day

    def _check_rollback(
        self, requested: date | None, as_of: date, on_rollback: RollbackPolicy
    ) -> None:
        """Warn, raise, or stay silent when ThaiBMA answered with a different date."""
        if requested is None or as_of == requested:
            return

        gap = (requested - as_of).days
        if gap < 0:
            reason = "the requested date precedes ThaiBMA's coverage for this instrument"
        elif gap <= 4:
            reason = "the requested date was a weekend or a Thai public holiday"
        else:
            reason = "the requested date is in the future, or a long market closure intervened"

        message = (
            f"ThaiBMA rolled back: requested {requested} but the curve returned is as of "
            f"{as_of} ({gap} day(s) earlier) - {reason}. The endpoint never 404s on a date, it "
            f"serves the most recent curve on or before it."
        )

        if on_rollback == "raise":
            logger.error(message)
            raise StaleDataError(message, requested_date=requested, as_of=as_of, rollback_days=gap)
        if on_rollback == "warn":
            logger.warning(message)

    async def _fetch_payload(self, day: date | None) -> Any:
        """Fetch and decode the raw ``{"Curve", "Stat"}`` envelope for a date."""
        url = self._build_url(day)
        label = format_curve_date(day) if day is not None else "latest"
        logger.info(f"Fetching ThaiBMA government yield curve for {label} from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            return await fetch_thaibma_json(
                fetcher, url, context=f"thaibma government yield curve ({label})"
            )

    async def fetch_curve(
        self,
        curve_date: date | datetime | str | None = None,
        *,
        on_rollback: RollbackPolicy = "warn",
    ) -> YieldCurve:
        """
        Fetch the government yield curve as at a date.

        Args:
            curve_date: The date to fetch, as a ``date``, ``datetime`` or ISO ``"YYYY-MM-DD"``
                string. Defaults to None, meaning the latest published curve.
            on_rollback: What to do when ThaiBMA answers with an earlier date than requested
                (a weekend, a public holiday, or a future date):

                - ``"warn"`` (default) - log a warning and return the curve, flagged.
                - ``"raise"`` - raise :class:`~settfex.exceptions.StaleDataError`.
                - ``"allow"`` - return it silently; the model flags are still set.

        Returns:
            A :class:`YieldCurve` carrying both ``requested_date`` and ``as_of``.

        Raises:
            InvalidDateError: If the date is malformed, impossible, or before 1999-09-15.
            StaleDataError: On a rolled-back date when ``on_rollback="raise"``.
            FetchError: On a non-2xx status, or when the API returns a body of ``null``.
            ResponseParseError: If the response cannot be decoded.

        Example:
            >>> service = YieldCurveService()
            >>> curve = await service.fetch_curve()             # latest
            >>> curve = await service.fetch_curve("2026-08-10")
            >>> curve.yield_at("10Y")
            2.060279
            >>> saturday = await service.fetch_curve("2026-08-08")
            >>> saturday.is_rolled_back, saturday.as_of
            (True, datetime.date(2026, 8, 7))
        """
        requested = self._resolve_request_date(curve_date)
        data = await self._fetch_payload(requested)

        label = format_curve_date(requested) if requested is not None else "latest"
        if data is None:
            error_msg = (
                f"ThaiBMA returned JSON null for the government yield curve ({label}) - no curve "
                f"exists on or before that date (the first available curve is "
                f"{THAIBMA_FIRST_CURVE_DATE})."
            )
            logger.error(error_msg)
            raise FetchError(error_msg)

        payload = validate_or_raise(
            _CurvePayload, data, context=f"thaibma government yield curve ({label})"
        )

        as_of = self._extract_as_of(payload, label)
        self._check_rollback(requested, as_of, on_rollback)

        curve = YieldCurve(
            requested_date=requested,
            as_of=as_of,
            points=payload.curve,
            quotes=payload.stat,
        )
        logger.info(
            f"Fetched ThaiBMA government yield curve as of {as_of}: "
            f"{curve.count} curve point(s), {len(curve.quotes)} quote(s)"
        )
        return curve

    @staticmethod
    def _extract_as_of(payload: _CurvePayload, label: str) -> date:
        """Read the served date from the payload, preferring Curve and falling back to Stat."""
        if payload.curve:
            return payload.curve[0].as_of
        if payload.stat:
            return payload.stat[0].as_of
        error_msg = (
            f"ThaiBMA returned an empty government yield curve for {label} - neither 'Curve' "
            f"nor 'Stat' contained a row, so the effective date cannot be determined."
        )
        logger.error(error_msg)
        raise FetchError(error_msg)

    async def fetch_curve_raw(
        self, curve_date: date | datetime | str | None = None
    ) -> dict[str, Any]:
        """
        Fetch the raw ``{"Curve", "Stat"}`` dict without Pydantic validation.

        Useful for debugging or for fields not yet modelled. Note this escape hatch performs **no**
        roll-back check — the returned dict's ``Asof`` may not be the date you asked for.

        Args:
            curve_date: The date to fetch, or None for the latest curve.

        Returns:
            The raw response dictionary with its original ``Curve``/``Stat`` keys.

        Raises:
            InvalidDateError: If the date is malformed, impossible, or before 1999-09-15.
            FetchError: On a non-2xx status, or when the API returns a body of ``null``.

        Example:
            >>> raw = await YieldCurveService().fetch_curve_raw("2026-08-10")
            >>> raw["Curve"][0]
            {'Asof': '2026-08-10T00:00:00', 'X': 0.076712328767123, 'Y': 0.856643}
        """
        requested = self._resolve_request_date(curve_date)
        data = await self._fetch_payload(requested)

        label = format_curve_date(requested) if requested is not None else "latest"
        if not isinstance(data, dict):
            error_msg = (
                f"ThaiBMA returned {type(data).__name__} rather than an object for the government "
                f"yield curve ({label})"
                + (
                    " - a body of literal 'null' means no curve exists on or before that date"
                    if data is None
                    else ""
                )
            )
            logger.error(error_msg)
            raise FetchError(error_msg)
        return data

    async def fetch_curves(
        self,
        curve_dates: list[date | datetime | str],
        *,
        on_rollback: RollbackPolicy = "allow",
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        continue_on_error: bool = True,
    ) -> list[YieldCurve]:
        """
        Fetch several dates concurrently, one request per date.

        .. warning::
            **For yields alone, use the history service instead** —
            :meth:`~settfex.services.thaibma.history.YieldCurveHistoryService.fetch_history`
            covers a whole year in a single request, where this issues one per business day
            (~245 requests per year). Use this method only when you need the per-date ``Stat``
            block (benchmark flags, spreads, per-bond changes), which has no bulk endpoint.

        Args:
            curve_dates: Dates to fetch. Duplicates are collapsed.
            on_rollback: Roll-back policy per date. Defaults to ``"allow"`` here, because walking a
                calendar range hits every weekend and would otherwise emit a warning per Saturday.
            max_concurrency: Maximum simultaneous requests.
            continue_on_error: If True (default), a failed date is logged and skipped; if False,
                the first failure propagates.

        Returns:
            Curves ascending by ``as_of``. Rolled-back duplicates are **not** collapsed — check
            ``is_rolled_back`` if you need distinct trading days.

        Example:
            >>> curves = await service.fetch_curves(["2026-08-06", "2026-08-07"])
            >>> [c.as_of for c in curves]
            [datetime.date(2026, 8, 6), datetime.date(2026, 8, 7)]
        """
        unique = list(dict.fromkeys(self._resolve_request_date(day) for day in curve_dates))
        if not unique:
            return []

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def fetch_one(day: date | None) -> YieldCurve | None:
            async with semaphore:
                try:
                    return await self.fetch_curve(day, on_rollback=on_rollback)
                except Exception as exc:  # noqa: BLE001 - tolerant batch fetch
                    if not continue_on_error:
                        raise
                    logger.warning(f"Skipping ThaiBMA curve for {day}: {exc}")
                    return None

        logger.info(
            f"Fetching {len(unique)} ThaiBMA yield curve(s) (concurrency={max_concurrency})"
        )
        results = await asyncio.gather(*(fetch_one(day) for day in unique))
        curves = [curve for curve in results if curve is not None]
        curves.sort(key=lambda c: c.as_of)
        logger.info(f"Fetched {len(curves)} of {len(unique)} requested ThaiBMA yield curve(s)")
        return curves


# Convenience function for quick access
async def get_government_yield_curve(
    curve_date: date | datetime | str | None = None,
    *,
    on_rollback: RollbackPolicy = "warn",
    config: FetcherConfig | None = None,
) -> YieldCurve:
    """
    Convenience function to fetch the Thai government bond yield curve for a date.

    Args:
        curve_date: The date to fetch, as a ``date``, ``datetime`` or ISO ``"YYYY-MM-DD"`` string.
            Defaults to None, meaning the latest published curve.
        on_rollback: ``"warn"`` (default), ``"raise"`` or ``"allow"`` - what to do when ThaiBMA
            answers with an earlier date than requested (weekend, holiday, or a future date).
        config: Optional fetcher configuration.

    Returns:
        A :class:`YieldCurve` carrying both ``requested_date`` and ``as_of``.

    Raises:
        InvalidDateError: If the date is malformed, impossible, or before 1999-09-15.
        StaleDataError: On a rolled-back date when ``on_rollback="raise"``.
        FetchError: On a non-2xx status, or when the API returns a body of ``null``.

    Example:
        >>> from settfex.services.thaibma import get_government_yield_curve
        >>> curve = await get_government_yield_curve()
        >>> curve.as_of, curve.yield_at("10Y")
        (datetime.date(2026, 8, 10), 2.060279)
        >>> strict = await get_government_yield_curve("2026-08-08", on_rollback="raise")
        Traceback (most recent call last):
        StaleDataError: ThaiBMA rolled back: requested 2026-08-08 ...
    """
    service = YieldCurveService(config=config)
    return await service.fetch_curve(curve_date, on_rollback=on_rollback)


def bangkok_today() -> date:
    """Today's calendar day in Asia/Bangkok — the market's own clock."""
    return datetime.now(BANGKOK_TZ).date()
