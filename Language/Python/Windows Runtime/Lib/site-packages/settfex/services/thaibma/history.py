"""ThaiBMA yield-curve history — whole years of daily data, one request each.

Two bulk endpoints return an entire calendar year of business-day rows in a single call:

- ``GET /yieldcurve/getintpttm?year=YYYY`` (:attr:`HistoryKind.TENOR`) — the **constant-maturity**
  matrix: one row per business day, one column per standard tenor (``1M`` … ``51Y``). Verified
  identical to the point-in-time fitted curve: for 2026-08-10 every sampled column equalled
  ``Curve.Y`` at the same grid position to six decimals.
- ``GET /yieldcurve/getbyyear?year=YYYY`` (:attr:`HistoryKind.BOND`) — the **per-bond** matrix:
  one row per business day, one column per bond symbol. A superset of the daily ``Stat`` panel;
  it also carries inflation-linked (``ILB``) and amortizing (``LBA``) issues that are quoted but
  excluded from curve fitting.

**Why this module exists rather than a loop over dates.** The obvious way to build history is to
walk business days through ``/yieldcurve/gov/{date}`` — about 6,600 requests for the full record.
These endpoints cover a year per request, so the complete 1999→2026 history is 28 requests. They
are not linked from any API index or documentation, only from the ThaiBMA website's own JavaScript.

Three sharp edges are handled here:

1. **Columns change from year to year.** Tenors: 14 in 1999 (``1Y``…``14Y``, with **no sub-year
   tenors at all**), 20 in 2005, 53 in 2015, 54 in 2026. Bond symbols differ every year. Fixed
   Pydantic fields are impossible, so each row holds only its own year's columns in
   :attr:`HistoryRow.values`, and the union lives on the container.
2. **An absent column is not the same as a null value.** ``values`` omits a column the year never
   had; it maps to ``None`` for a column that existed but was not quoted that day.
   :meth:`HistoryRow.has` distinguishes them — a wide DataFrame flattens both to ``NaN``.
3. **An unserved year returns HTTP 200 with ``[]``, not an error.** Without the availability check
   a span of 1995-2001 would quietly yield only 1999-2001. The dropped years are reported in
   :attr:`YieldCurveHistory.unavailable_years`.

.. note::
    There is **no bulk history for the zero-coupon curve** (``getzerobyyear`` returns 404), which
    is why :meth:`YieldCurveHistoryService.fetch_history` deliberately takes no curve-type
    argument — accepting one and silently returning government data would be the worst outcome.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from settfex.exceptions import FetchError
from settfex.services.thaibma.availability import YieldCurveAvailabilityService
from settfex.services.thaibma.constants import (
    BANGKOK_TZ,
    THAIBMA_BASE_URL,
    THAIBMA_BOND_HISTORY_ENDPOINT,
    THAIBMA_TENOR_HISTORY_ENDPOINT,
)
from settfex.services.thaibma.utils import (
    fetch_thaibma_json,
    normalize_curve_date,
    normalize_year,
    sort_tenor_columns,
    stateless_config,
)
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError

if TYPE_CHECKING:
    import pandas as pd

# Default concurrency for the per-year fan-out. The host served 20 parallel requests in 0.34 s,
# so this is politeness rather than a limit we were pushed to.
DEFAULT_MAX_CONCURRENCY = 5


class HistoryKind(StrEnum):
    """Which history matrix to fetch."""

    TENOR = "tenor"
    """Constant-maturity yields by standard tenor (``getintpttm``)."""

    BOND = "bond"
    """Per-bond yields by ThaiBMA symbol (``getbyyear``)."""


# Endpoint per matrix kind. Both take a single ``?year=`` query parameter.
_ENDPOINTS: dict[HistoryKind, str] = {
    HistoryKind.TENOR: THAIBMA_TENOR_HISTORY_ENDPOINT,
    HistoryKind.BOND: THAIBMA_BOND_HISTORY_ENDPOINT,
}


def _coerce_kind(kind: HistoryKind | str) -> HistoryKind:
    """Accept the enum or its plain string value, raising a helpful error otherwise."""
    if isinstance(kind, HistoryKind):
        return kind
    try:
        return HistoryKind(str(kind).strip().lower())
    except ValueError as exc:
        valid = ", ".join(repr(member.value) for member in HistoryKind)
        error_msg = f"Unknown history kind {kind!r}; expected one of {valid}"
        logger.error(error_msg)
        raise ValueError(error_msg) from exc


class HistoryRow(BaseModel):
    """
    One business day of a wide history matrix.

    ``values`` holds only the columns that year's payload carried — see the module docstring on
    absent-versus-null.
    """

    as_of: date = Field(
        description="Row date. Note the wire key is lowercase 'asof' here, unlike the "
        "point-in-time payload's 'Asof'"
    )
    values: dict[str, float | None] = Field(
        default_factory=dict,
        description="Column label -> yield in percent; None means present but not quoted that day",
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _split_as_of(cls, data: Any) -> Any:
        """Route every non-``asof`` key of a wire row into ``values`` (columns are dynamic)."""
        if not isinstance(data, dict) or "values" in data:
            return data
        as_of = data.get("asof", data.get("as_of"))
        return {
            "as_of": as_of,
            "values": {k: v for k, v in data.items() if k.lower() not in {"asof", "as_of"}},
        }

    def get(self, column: str) -> float | None:
        """
        Value for one column, or None if it is absent or was not quoted.

        Args:
            column: Tenor label (``"10Y"``) or bond symbol (``"LB776A"``).

        Returns:
            The yield in percent, or None.
        """
        return self.values.get(column)

    def has(self, column: str) -> bool:
        """
        Whether this row's year carried the column at all.

        The distinction matters: ``has("1M")`` is False for a 1999 row because ThaiBMA published no
        sub-year tenors then, whereas ``has("51Y")`` is True for a 2026 row whose value is None
        because nothing was quoted at that tenor. ``to_dataframe()`` flattens both to ``NaN``.

        Args:
            column: Tenor label or bond symbol.

        Returns:
            True if the column exists in this row's payload.
        """
        return column in self.values


class YieldCurveHistory(BaseModel):
    """
    A span of daily ThaiBMA yield history as a wide matrix, assembled from whole-year fetches.

    ``columns`` is the ordered **union** across every year fetched, so rows from different eras
    line up. Any hole in the result is attributable: :attr:`unavailable_years` lists years ThaiBMA
    does not serve, :attr:`missing_years` lists years whose fetch failed and was skipped.
    """

    kind: HistoryKind = Field(description="Which matrix this is: constant-maturity or per-bond")
    rows: list[HistoryRow] = Field(default_factory=list, description="Ascending by as_of")
    columns: list[str] = Field(
        default_factory=list, description="Ordered union of columns across all fetched years"
    )
    start_date: date | None = Field(default=None, description="Requested start of the span")
    end_date: date | None = Field(default=None, description="Requested end of the span")
    unavailable_years: list[int] = Field(
        default_factory=list,
        description="Requested years ThaiBMA has no data for (the API returns [] silently)",
    )
    missing_years: list[int] = Field(
        default_factory=list,
        description="Years whose fetch failed and was skipped under continue_on_error=True",
    )

    model_config = ConfigDict(populate_by_name=True)

    @property
    def count(self) -> int:
        """Number of business-day rows."""
        return len(self.rows)

    @property
    def dates(self) -> list[date]:
        """Row dates, ascending."""
        return [row.as_of for row in self.rows]

    @property
    def latest(self) -> HistoryRow | None:
        """The most recent row, or None if empty."""
        return self.rows[-1] if self.rows else None

    def series(self, column: str, *, dropna: bool = True) -> list[tuple[date, float | None]]:
        """
        One column's time series.

        Args:
            column: Tenor label (``"10Y"``) or bond symbol (``"LB776A"``).
            dropna: If True (default), omit days with no value; if False, keep them as None.

        Returns:
            ``(date, value)`` pairs ascending by date.

        Example:
            >>> history.series("10Y")[-1]
            (datetime.date(2026, 8, 10), 2.060279)
        """
        out: list[tuple[date, float | None]] = []
        for row in self.rows:
            value = row.get(column)
            if value is None and dropna:
                continue
            out.append((row.as_of, value))
        return out

    def row_for(self, day: date | datetime | str) -> HistoryRow | None:
        """
        The row for an exact date.

        .. note::
            **No roll-back happens here**, deliberately. Unlike the point-in-time endpoint, this
            container is honest about which days exist: a Saturday returns None rather than
            quietly handing back Friday.

        Args:
            day: A ``date``, ``datetime`` or ISO ``"YYYY-MM-DD"`` string.

        Returns:
            The matching row, or None.
        """
        target = normalize_curve_date(day)
        for row in self.rows:
            if row.as_of == target:
                return row
        return None

    def slice(
        self,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> YieldCurveHistory:
        """
        A narrowed copy covering an inclusive date range.

        Args:
            start: New start bound, or None to keep the current one.
            end: New end bound, or None to keep the current one.

        Returns:
            A new :class:`YieldCurveHistory`; ``columns`` is recomputed from the surviving rows.

        Example:
            >>> history.slice("2026-01-01", "2026-06-30").count
            121
        """
        lower = normalize_curve_date(start) if start is not None else None
        upper = normalize_curve_date(end) if end is not None else None
        rows = [
            row
            for row in self.rows
            if (lower is None or row.as_of >= lower) and (upper is None or row.as_of <= upper)
        ]
        return YieldCurveHistory(
            kind=self.kind,
            rows=rows,
            columns=build_column_union(rows, self.kind),
            start_date=lower or self.start_date,
            end_date=upper or self.end_date,
            unavailable_years=list(self.unavailable_years),
            missing_years=list(self.missing_years),
        )

    def columns_by_year(self) -> dict[int, list[str]]:
        """
        The columns present in each calendar year, making the year-drift inspectable.

        Returns:
            ``{year: [column, ...]}`` with each year's columns in this matrix's canonical order.

        Example:
            >>> {y: len(c) for y, c in history.columns_by_year().items()}
            {1999: 14, 2026: 54}
        """
        per_year: dict[int, set[str]] = {}
        for row in self.rows:
            per_year.setdefault(row.as_of.year, set()).update(row.values.keys())
        order = {name: index for index, name in enumerate(self.columns)}
        return {
            year: sorted(names, key=lambda n: order.get(n, len(order)))
            for year, names in sorted(per_year.items())
        }

    def coverage(self) -> dict[str, int]:
        """
        Non-null observation count per column — spots a bond that stopped quoting.

        Returns:
            ``{column: count}`` in this matrix's canonical column order.
        """
        counts = dict.fromkeys(self.columns, 0)
        for row in self.rows:
            for name, value in row.values.items():
                if value is not None:
                    counts[name] = counts.get(name, 0) + 1
        return counts

    def to_long(self) -> list[tuple[date, str, float]]:
        """
        The matrix in long/tidy form, without pandas. Null cells are omitted.

        Returns:
            ``(date, column, value)`` triples.
        """
        out: list[tuple[date, str, float]] = []
        for row in self.rows:
            for name in self.columns:
                value = row.values.get(name)
                if value is not None:
                    out.append((row.as_of, name, value))
        return out

    def to_dataframe(self, *, layout: str = "wide") -> pd.DataFrame:
        """
        Render the history as a pandas DataFrame.

        pandas is an optional dependency, imported lazily so importing this service never needs it.

        Args:
            layout: ``"wide"`` (default) for one row per date and one column per tenor/bond,
                indexed by ``as_of``; or ``"long"`` for tidy ``as_of``/``column``/``value`` rows.

        Returns:
            The DataFrame. In wide layout, a cell is ``NaN`` both where the column did not exist
            that year and where it existed but was not quoted — use :meth:`columns_by_year` to
            tell the two apart.

            ``df.attrs`` carries ``missing_years``, ``unavailable_years``, ``kind``,
            ``start_date`` and ``end_date``. The two gap lists are there because a DataFrame is
            where this history usually *leaves* settfex, and a frame silently missing a year looks
            exactly like a frame whose year had no data — the same shape of loss this release
            exists to close. ``df.attrs`` survives copy, column selection and ``head()`` on both
            pandas majors, which is why the analyst-consensus frames already use it.

        Raises:
            ImportError: If pandas is not installed.
            ValueError: If ``layout`` is not recognized.

        Example:
            >>> df = history.to_dataframe()
            >>> df.shape
            (1608, 54)
            >>> df.attrs["missing_years"]        # years whose fetch failed and was skipped
            []
        """
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatched sys.modules
            raise ImportError(
                "pandas is required for YieldCurveHistory.to_dataframe(). Install it with "
                "'pip install settfex[dataframe]' (or 'uv add pandas')."
            ) from exc

        if layout == "long":
            long_frame = pd.DataFrame(self.to_long(), columns=["as_of", "column", "value"])
            return self._with_provenance(long_frame)
        if layout != "wide":
            error_msg = f"layout must be 'wide' or 'long', got {layout!r}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        frame = pd.DataFrame(
            [{name: row.values.get(name) for name in self.columns} for row in self.rows],
            columns=self.columns,
            index=pd.Index([row.as_of for row in self.rows], name="as_of"),
        )
        return self._with_provenance(frame)

    def _with_provenance(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Attach the gap lists to ``df.attrs`` so they survive the frame boundary.

        Both layouts get them: a long frame loses a year just as quietly as a wide one.
        """
        frame.attrs["kind"] = self.kind.value
        frame.attrs["start_date"] = self.start_date
        frame.attrs["end_date"] = self.end_date
        frame.attrs["missing_years"] = list(self.missing_years)
        frame.attrs["unavailable_years"] = list(self.unavailable_years)
        return frame


def build_column_union(rows: list[HistoryRow], kind: HistoryKind) -> list[str]:
    """
    Build the ordered union of columns across rows.

    Tenor matrices are sorted by maturity (so ``"2Y"`` precedes ``"10Y"``, which plain sorting gets
    wrong); bond matrices keep first-seen order walking rows chronologically, which matches the
    API's own ordering and appends new issues at the end.

    Args:
        rows: Rows to scan, ideally already sorted ascending.
        kind: Which matrix these rows belong to.

    Returns:
        The ordered column labels.
    """
    seen: dict[str, None] = {}
    for row in rows:
        for name in row.values:
            seen.setdefault(name, None)
    names = list(seen)
    if kind is HistoryKind.TENOR:
        return sort_tenor_columns(names)
    return names


class YieldCurveHistoryService:
    """
    Fetch whole years of ThaiBMA yield history and assemble them into a date-sliced matrix.

    Stateless host — ``use_session`` is forced off.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the history service.

        Args:
            config: Optional fetcher configuration. ``use_session`` is always forced off; every
                other setting is preserved. For very large pulls consider
                ``FetcherConfig(rate_limit_delay=...)``.

        Example:
            >>> service = YieldCurveHistoryService()
        """
        self.config = stateless_config(config)
        self.base_url = THAIBMA_BASE_URL
        logger.info(f"YieldCurveHistoryService initialized with base_url={self.base_url}")

    def _build_url(self, year: int, kind: HistoryKind) -> str:
        """Build the per-year history URL for a matrix kind."""
        return f"{self.base_url}{_ENDPOINTS[kind]}?year={year}"

    async def _fetch_year_payload(
        self, fetcher: AsyncDataFetcher, year: int, kind: HistoryKind
    ) -> list[dict[str, Any]]:
        """Fetch and shape-check one year of a matrix, sharing an already-open fetcher."""
        url = self._build_url(year, kind)
        data = await fetch_thaibma_json(
            fetcher, url, context=f"thaibma {kind.value} history ({year})"
        )
        if not isinstance(data, list):
            error_msg = (
                f"Expected a JSON array for thaibma {kind.value} history ({year}), "
                f"got {type(data).__name__}"
            )
            logger.error(error_msg)
            raise ResponseParseError(error_msg)
        return [row for row in data if isinstance(row, dict)]

    async def fetch_year(
        self, year: int, *, kind: HistoryKind | str = HistoryKind.TENOR
    ) -> YieldCurveHistory:
        """
        Fetch exactly one calendar year of history.

        Args:
            year: Calendar year.
            kind: ``"tenor"`` (default) for constant-maturity yields, or ``"bond"`` for per-bond.

        Returns:
            A :class:`YieldCurveHistory` for that year. A year ThaiBMA does not serve yields zero
            rows rather than raising.

        Raises:
            ValueError: If the year is out of range or ``kind`` is unrecognized.
            FetchError: On a non-2xx status.
            ResponseParseError: If the payload is not a JSON array.

        Example:
            >>> year = await service.fetch_year(2026)
            >>> year.count, len(year.columns)
            (145, 54)
        """
        resolved_kind = _coerce_kind(kind)
        resolved_year = normalize_year(year)
        logger.info(f"Fetching ThaiBMA {resolved_kind.value} history for {resolved_year}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            payload = await self._fetch_year_payload(fetcher, resolved_year, resolved_kind)

        rows = sorted((HistoryRow.model_validate(item) for item in payload), key=lambda r: r.as_of)
        history = YieldCurveHistory(
            kind=resolved_kind,
            rows=rows,
            columns=build_column_union(rows, resolved_kind),
            start_date=rows[0].as_of if rows else None,
            end_date=rows[-1].as_of if rows else None,
        )
        logger.info(
            f"Fetched {history.count} row(s) and {len(history.columns)} column(s) of ThaiBMA "
            f"{resolved_kind.value} history for {resolved_year}"
        )
        return history

    async def fetch_year_raw(
        self, year: int, *, kind: HistoryKind | str = HistoryKind.TENOR
    ) -> list[dict[str, Any]]:
        """
        Fetch one year of history as raw wide dicts, without validation.

        Args:
            year: Calendar year.
            kind: ``"tenor"`` (default) or ``"bond"``.

        Returns:
            The raw rows, each keeping its original lowercase ``asof`` key.

        Raises:
            ValueError: If the year is out of range or ``kind`` is unrecognized.
            FetchError: On a non-2xx status.

        Example:
            >>> raw = await service.fetch_year_raw(2026)
            >>> sorted(raw[0])[:3]
            ['10Y', '11Y', '12Y']
        """
        resolved_kind = _coerce_kind(kind)
        resolved_year = normalize_year(year)
        async with AsyncDataFetcher(config=self.config) as fetcher:
            return await self._fetch_year_payload(fetcher, resolved_year, resolved_kind)

    async def _resolve_span(
        self,
        start_date: date | datetime | str | None,
        end_date: date | datetime | str | None,
    ) -> tuple[date, date]:
        """Normalize the requested span, defaulting to year-to-date in Bangkok."""
        end = (
            normalize_curve_date(end_date)
            if end_date is not None
            else datetime.now(BANGKOK_TZ).date()
        )
        # Default to the start of the end year, NOT 1999: silently issuing a 28-request,
        # multi-megabyte full-history pull for a bare fetch_history() would be a footgun.
        start = normalize_curve_date(start_date) if start_date is not None else date(end.year, 1, 1)
        if start > end:
            error_msg = f"start_date {start} is after end_date {end}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        return start, end

    async def _select_years(
        self, start: date, end: date, *, check_availability: bool
    ) -> tuple[list[int], list[int]]:
        """Return (years to fetch, years ThaiBMA does not serve) for a span."""
        requested = list(range(start.year, end.year + 1))
        if not check_availability:
            return requested, []

        availability = await YieldCurveAvailabilityService(self.config).fetch_availability()
        served = set(availability.years)
        if not served:
            logger.warning(
                "ThaiBMA returned no available years; proceeding without availability clamping"
            )
            return requested, []

        wanted = [year for year in requested if year in served]
        unavailable = [year for year in requested if year not in served]
        if unavailable:
            logger.warning(
                f"ThaiBMA has no yield-curve data for {unavailable} - those years are excluded "
                f"(the per-year endpoints answer an unserved year with an empty list, so this "
                f"would otherwise be silent)"
            )
        return wanted, unavailable

    async def fetch_history(
        self,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        *,
        kind: HistoryKind | str = HistoryKind.TENOR,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        continue_on_error: bool = True,
        check_availability: bool = True,
        progress: bool = False,
    ) -> YieldCurveHistory:
        """
        Fetch daily yield history across a date span, one request per calendar year.

        Args:
            start_date: Inclusive start. Defaults to 1 January of the end year — **not** 1999, so
                a bare call never triggers a 28-request full-history pull by accident.
            end_date: Inclusive end. Defaults to today in Asia/Bangkok.
            kind: ``"tenor"`` (default) for the constant-maturity matrix, or ``"bond"`` for the
                per-bond matrix.
            max_concurrency: Maximum simultaneous year requests.
            continue_on_error: If True (default), a failed year is logged and recorded in
                ``missing_years``; if False, the first failure propagates.
            check_availability: If True (default), spend one request on ``/availyear`` to drop
                years ThaiBMA does not serve and report them in ``unavailable_years``.
            progress: Show a tqdm progress bar (requires the optional ``progress`` extra;
                degrades to a warning if tqdm is absent).

        Returns:
            A :class:`YieldCurveHistory` sliced to the requested span, with the union of columns
            across the fetched years.

        Raises:
            ValueError: If ``start_date`` is after ``end_date``, or ``kind`` is unrecognized.
            FetchError: On a non-2xx status when ``continue_on_error=False``.

        Example:
            >>> service = YieldCurveHistoryService()
            >>> history = await service.fetch_history("2020-01-01", "2026-08-10")
            >>> history.count, len(history.columns)     # 7 requests, not ~1,600
            (1608, 54)
            >>> history.series("10Y")[-1]
            (datetime.date(2026, 8, 10), 2.060279)
        """
        resolved_kind = _coerce_kind(kind)
        start, end = await self._resolve_span(start_date, end_date)
        years, unavailable = await self._select_years(
            start, end, check_availability=check_availability
        )

        rows: list[HistoryRow] = []
        missing: list[int] = []

        if years:
            logger.info(
                f"Fetching ThaiBMA {resolved_kind.value} history for {start}..{end} "
                f"({len(years)} year request(s), concurrency={max_concurrency})"
            )
            semaphore = asyncio.Semaphore(max(1, max_concurrency))
            bar = _make_progress_bar(len(years), resolved_kind) if progress else None

            async with AsyncDataFetcher(config=self.config) as fetcher:

                async def fetch_one(year: int) -> list[dict[str, Any]]:
                    async with semaphore:
                        try:
                            return await self._fetch_year_payload(fetcher, year, resolved_kind)
                        except Exception as exc:  # noqa: BLE001 - tolerant batch fetch
                            if not continue_on_error:
                                raise
                            logger.warning(f"Skipping ThaiBMA history for {year}: {exc}")
                            missing.append(year)
                            return []
                        finally:
                            if bar is not None:
                                bar.update(1)

                payloads = await asyncio.gather(*(fetch_one(year) for year in years))

            if bar is not None:
                bar.close()

            for payload in payloads:
                rows.extend(HistoryRow.model_validate(item) for item in payload)

        rows = [row for row in rows if start <= row.as_of <= end]
        rows.sort(key=lambda r: r.as_of)

        history = YieldCurveHistory(
            kind=resolved_kind,
            rows=rows,
            columns=build_column_union(rows, resolved_kind),
            start_date=start,
            end_date=end,
            unavailable_years=unavailable,
            missing_years=sorted(missing),
        )
        logger.info(
            f"Assembled {history.count} row(s) x {len(history.columns)} column(s) of ThaiBMA "
            f"{resolved_kind.value} history for {start}..{end}"
        )
        return history

    async def fetch_history_raw(
        self,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        *,
        kind: HistoryKind | str = HistoryKind.TENOR,
        check_availability: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Fetch a span of history as raw wide dicts, without validation.

        Args:
            start_date: Inclusive start (defaults to 1 January of the end year).
            end_date: Inclusive end (defaults to today in Asia/Bangkok).
            kind: ``"tenor"`` (default) or ``"bond"``.
            check_availability: If True (default), spend one request on ``/availyear`` so years
                ThaiBMA does not serve are reported rather than silently contributing nothing.

        Returns:
            Raw rows keeping their original lowercase ``asof`` key, ascending, sliced to the span.

            .. warning::
               A bare ``list`` has nowhere to carry an accounting, so unlike
               :meth:`fetch_history` this method reports a gap **only** through a WARNING —
               there is no ``unavailable_years`` to read afterwards. Use :meth:`fetch_history`
               when you need the gap in the return value.

        Raises:
            ValueError: If ``start_date`` is after ``end_date``.
            FetchError: On a non-2xx status, or when ThaiBMA serves **none** of the requested
                years — a total loss raises, because an empty list would otherwise be
                indistinguishable from "those years had no business days".

        Example:
            >>> raw = await service.fetch_history_raw("2026-01-01")
            >>> raw[0]["asof"]
            '2026-01-05T00:00:00'
        """
        resolved_kind = _coerce_kind(kind)
        start, end = await self._resolve_span(start_date, end_date)
        # Until 0.24.0 this went straight to `range(start.year, end.year + 1)` and never called
        # `_select_years`, so it skipped the availability clamp that `fetch_history` applies. The
        # per-year endpoints answer a year they do not serve with HTTP 200 and an EMPTY LIST, so a
        # 1995-2001 span quietly returned only the 1999-2001 rows -- no exception, no warning, and
        # nothing in the return value to say four years were dropped. That is precisely the case
        # `availability.py` was written to prevent, and this method was the one path that bypassed
        # it.
        years, unavailable = await self._select_years(
            start, end, check_availability=check_availability
        )
        if not years:
            error_msg = (
                f"ThaiBMA serves none of the years {unavailable} requested for {start}..{end}. "
                f"Returning an empty list here would be indistinguishable from a span that "
                f"genuinely holds no rows."
            )
            logger.error(error_msg)
            raise FetchError(error_msg)
        if unavailable:
            logger.warning(
                f"fetch_history_raw is returning rows for {years} only; ThaiBMA does not serve "
                f"{unavailable}. A raw list cannot carry that gap -- use fetch_history() if you "
                f"need `unavailable_years` in the return value."
            )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            payloads = await asyncio.gather(
                *(self._fetch_year_payload(fetcher, year, resolved_kind) for year in years)
            )

        out = [row for payload in payloads for row in payload]
        kept = [
            row
            for row in out
            if row.get("asof") and start <= normalize_curve_date(str(row["asof"])) <= end
        ]
        kept.sort(key=lambda r: str(r["asof"]))
        return kept


def _make_progress_bar(total: int, kind: HistoryKind) -> Any | None:
    """Return a tqdm bar if the optional 'progress' extra is installed, else None."""
    try:
        from tqdm.auto import tqdm
    except ImportError:
        logger.warning("progress=True but tqdm is not installed; install settfex[progress]")
        return None
    return tqdm(total=total, desc=f"ThaiBMA {kind.value} history", unit="year")


# Convenience function for quick access
async def get_yield_curve_history(
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    check_availability: bool = True,
    progress: bool = False,
    config: FetcherConfig | None = None,
) -> YieldCurveHistory:
    """
    Convenience function to fetch constant-maturity Thai government yield history.

    One request per calendar year, so a decade costs ten requests rather than ~2,400.

    Args:
        start_date: Inclusive start. Defaults to 1 January of the end year.
        end_date: Inclusive end. Defaults to today in Asia/Bangkok.
        max_concurrency: Maximum simultaneous year requests.
        check_availability: Drop and report years ThaiBMA does not serve (default True).
        progress: Show a tqdm progress bar (needs the optional ``progress`` extra).
        config: Optional fetcher configuration.

    Returns:
        A :class:`YieldCurveHistory` with one row per business day and one column per tenor.

    Raises:
        ValueError: If ``start_date`` is after ``end_date``.
        FetchError: On a non-2xx status.

    Example:
        >>> from settfex.services.thaibma import get_yield_curve_history
        >>> history = await get_yield_curve_history("1999-09-15")   # the whole record, 28 requests
        >>> history.count
        6574
        >>> history.to_dataframe()["10Y"].tail()
    """
    service = YieldCurveHistoryService(config=config)
    return await service.fetch_history(
        start_date,
        end_date,
        kind=HistoryKind.TENOR,
        max_concurrency=max_concurrency,
        check_availability=check_availability,
        progress=progress,
    )


# Convenience function for quick access
async def get_bond_yield_history(
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    check_availability: bool = True,
    progress: bool = False,
    config: FetcherConfig | None = None,
) -> YieldCurveHistory:
    """
    Convenience function to fetch per-bond Thai government yield history.

    Columns are ThaiBMA bond symbols rather than tenors. This matrix is a superset of the daily
    ``Stat`` panel — it also carries inflation-linked (``ILB``) and amortizing (``LBA``) issues
    excluded from curve fitting.

    Args:
        start_date: Inclusive start. Defaults to 1 January of the end year.
        end_date: Inclusive end. Defaults to today in Asia/Bangkok.
        max_concurrency: Maximum simultaneous year requests.
        check_availability: Drop and report years ThaiBMA does not serve (default True).
        progress: Show a tqdm progress bar (needs the optional ``progress`` extra).
        config: Optional fetcher configuration.

    Returns:
        A :class:`YieldCurveHistory` with one row per business day and one column per bond symbol.

    Raises:
        ValueError: If ``start_date`` is after ``end_date``.
        FetchError: On a non-2xx status.

    Example:
        >>> from settfex.services.thaibma import get_bond_yield_history
        >>> history = await get_bond_yield_history("2026-01-01")
        >>> history.series("LB776A")[-1]
        (datetime.date(2026, 8, 10), 3.293888888888889)
    """
    service = YieldCurveHistoryService(config=config)
    return await service.fetch_history(
        start_date,
        end_date,
        kind=HistoryKind.BOND,
        max_concurrency=max_concurrency,
        check_availability=check_availability,
        progress=progress,
    )
