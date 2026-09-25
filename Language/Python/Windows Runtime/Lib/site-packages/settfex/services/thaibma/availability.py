"""ThaiBMA yield-curve data availability — what history actually exists.

Wraps two tiny discovery endpoints:

- ``GET /yieldcurve/avail`` → a two-element array ``["<first>", "<last>"]`` of ISO datetimes.
- ``GET /yieldcurve/availyear`` → a bare array of the calendar years with data.

These matter more than their size suggests. The per-year history endpoints answer a year they do
not serve with HTTP 200 and an empty list, so a request spanning 1995-2001 would otherwise return
only the 1999-2001 rows with no indication that six years were silently dropped. The history
service calls this module to clamp the requested span and report the gap explicitly.

Live-probed 2026-08-10: the government curve runs from **1999-09-15** to the current business day,
across 28 calendar years.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.services.thaibma.constants import (
    THAIBMA_AVAIL_ENDPOINT,
    THAIBMA_AVAIL_YEAR_ENDPOINT,
    THAIBMA_BASE_URL,
)
from settfex.services.thaibma.utils import (
    fetch_thaibma_json,
    normalize_curve_date,
    stateless_config,
)
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError


class YieldCurveAvailability(BaseModel):
    """The window of dates and years for which ThaiBMA publishes a government yield curve."""

    first_date: date = Field(description="Earliest date with a curve (live-probed: 1999-09-15)")
    last_date: date = Field(description="Most recent date with a curve")
    years: list[int] = Field(
        default_factory=list, description="Calendar years with data, ascending"
    )

    model_config = ConfigDict(populate_by_name=True)

    @property
    def span_days(self) -> int:
        """Calendar days between the first and last available dates."""
        return (self.last_date - self.first_date).days

    def covers(self, day: date | datetime | str) -> bool:
        """
        Check whether a day falls inside the availability window.

        This answers *"is this day within ThaiBMA's published range"* — not *"is there a curve
        stamped exactly that day"*. Weekends and public holidays fall inside the window but have
        no curve of their own; the endpoint rolls those back to the previous business day.

        Args:
            day: A ``date``, ``datetime`` or ISO ``"YYYY-MM-DD"`` string.

        Returns:
            True if ``first_date <= day <= last_date``.

        Example:
            >>> availability.covers("1999-09-14")
            False
        """
        target = normalize_curve_date(day)
        return self.first_date <= target <= self.last_date

    def clamp(self, start: date, end: date) -> tuple[date, date]:
        """
        Trim a requested date span to the available window.

        Args:
            start: Requested start date.
            end: Requested end date.

        Returns:
            ``(start, end)`` narrowed to the window. The result may be inverted (``start > end``)
            if the request lies entirely outside the window — callers should treat that as empty.
        """
        return max(start, self.first_date), min(end, self.last_date)


#: Labels for the two halves, used when one of them fails so the message names which endpoint.
_AVAILABILITY_HALVES = ("/yieldcurve/avail (window)", "/yieldcurve/availyear (years)")


def _raise_first_with_notes(outcomes: Sequence[Any]) -> None:
    """Raise the first failed half, carrying any other failure as a PEP 678 note.

    Deliberately not an ``ExceptionGroup`` despite the 3.11+ floor: ``except FetchError`` does not
    catch one, so it would silently break every existing handler. Same reasoning, and the same
    mechanism, as the SEC per-report-code isolation.
    """
    failures = [
        (label, outcome)
        for label, outcome in zip(_AVAILABILITY_HALVES, outcomes, strict=True)
        if isinstance(outcome, BaseException)
    ]
    if not failures:
        return
    first_label, first = failures[0]
    if not isinstance(first, Exception):  # pragma: no cover - BaseException never reaches here
        raise first
    for label, other in failures[1:]:
        first.add_note(
            f"The other half of the availability answer also failed: {label} raised "
            f"{type(other).__name__}: {other}"
        )
    logger.error(
        f"ThaiBMA availability failed on {first_label} "
        f"({type(first).__name__}: {first}); {len(failures)} of 2 half/halves failed"
    )
    raise first


class YieldCurveAvailabilityService:
    """Fetch the ThaiBMA yield-curve availability window and the list of years with data."""

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the availability service.

        Args:
            config: Optional fetcher configuration. ``use_session`` is always forced off.

        Example:
            >>> service = YieldCurveAvailabilityService()
        """
        self.config = stateless_config(config)
        self.base_url = THAIBMA_BASE_URL
        logger.info(f"YieldCurveAvailabilityService initialized with base_url={self.base_url}")

    async def fetch_availability(self, *, include_years: bool = True) -> YieldCurveAvailability:
        """
        Fetch the date window and, optionally, the list of years with data.

        Args:
            include_years: If True (default), also call ``/availyear`` — the two requests are
                issued concurrently, so this costs no extra wall-clock time.

        Returns:
            A :class:`YieldCurveAvailability`. ``years`` is empty when ``include_years=False``.

        Raises:
            FetchError: On a non-2xx status.
            ResponseParseError: If ``/avail`` does not return exactly two dates.

        Example:
            >>> availability = await YieldCurveAvailabilityService().fetch_availability()
            >>> availability.first_date, len(availability.years)
            (datetime.date(1999, 9, 15), 28)
        """
        logger.info(f"Fetching ThaiBMA yield-curve availability (include_years={include_years})")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            if include_years:
                # The two halves are ONE logical answer -- a window without its years, or years
                # without their window, is not a usable availability -- so they fail together and
                # loudly. That decision is why there is no partial mode here and no
                # `missing_half` field: the severity ladder used elsewhere in this release
                # ("a partial loss reports, a total loss raises") does not apply to a value whose
                # halves are not independently meaningful.
                #
                # `return_exceptions=True` is only so that a second failure is not discarded:
                # a bare gather raises the first cause and drops the other, which would leave a
                # caller debugging one broken endpoint while both were down. PEP 678 notes carry
                # it with no new type and no changed signature, exactly as the SEC report-code
                # isolation does.
                outcomes = await asyncio.gather(
                    fetch_thaibma_json(
                        fetcher,
                        f"{self.base_url}{THAIBMA_AVAIL_ENDPOINT}",
                        context="thaibma yield curve availability window",
                    ),
                    fetch_thaibma_json(
                        fetcher,
                        f"{self.base_url}{THAIBMA_AVAIL_YEAR_ENDPOINT}",
                        context="thaibma yield curve available years",
                    ),
                    return_exceptions=True,
                )
                _raise_first_with_notes(outcomes)
                window_data, years_data = outcomes
            else:
                window_data = await fetch_thaibma_json(
                    fetcher,
                    f"{self.base_url}{THAIBMA_AVAIL_ENDPOINT}",
                    context="thaibma yield curve availability window",
                )
                years_data = []

        first_date, last_date = self._parse_window(window_data)
        years = self._parse_years(years_data)

        availability = YieldCurveAvailability(
            first_date=first_date, last_date=last_date, years=years
        )
        logger.info(
            f"ThaiBMA yield-curve data spans {first_date} to {last_date} "
            f"({len(years)} year(s) listed)"
        )
        return availability

    @staticmethod
    def _parse_window(data: Any) -> tuple[date, date]:
        """Parse ``/avail``'s two-element array, refusing to index a differently-shaped body."""
        if not isinstance(data, list) or len(data) != 2:
            shape = f"{type(data).__name__}"
            if isinstance(data, list):
                shape += f" of length {len(data)}"
            error_msg = (
                f"Expected a 2-element [first, last] array from ThaiBMA /yieldcurve/avail, "
                f"got {shape}"
            )
            logger.error(error_msg)
            raise ResponseParseError(error_msg)
        return normalize_curve_date(str(data[0])), normalize_curve_date(str(data[1]))

    @staticmethod
    def _parse_years(data: Any) -> list[int]:
        """Parse ``/availyear``'s array of ints, ignoring any non-integer entry."""
        if not isinstance(data, list):
            return []
        years: list[int] = []
        for item in data:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                years.append(item)
            elif isinstance(item, str) and item.strip().isdigit():
                years.append(int(item.strip()))
            else:
                logger.warning(f"Ignoring non-integer entry in ThaiBMA available years: {item!r}")
        return sorted(years)

    async def fetch_availability_raw(self) -> dict[str, Any]:
        """
        Fetch both availability payloads unvalidated.

        Returns:
            ``{"avail": <2-element list>, "availyear": <list of ints>}``.

        Raises:
            FetchError: On a non-2xx status.

        Example:
            >>> raw = await YieldCurveAvailabilityService().fetch_availability_raw()
            >>> raw["avail"]
            ['1999-09-15T00:00:00', '2026-08-10T00:00:00']
        """
        async with AsyncDataFetcher(config=self.config) as fetcher:
            window_data, years_data = await asyncio.gather(
                fetch_thaibma_json(
                    fetcher,
                    f"{self.base_url}{THAIBMA_AVAIL_ENDPOINT}",
                    context="thaibma yield curve availability window",
                ),
                fetch_thaibma_json(
                    fetcher,
                    f"{self.base_url}{THAIBMA_AVAIL_YEAR_ENDPOINT}",
                    context="thaibma yield curve available years",
                ),
            )
        return {"avail": window_data, "availyear": years_data}


# Convenience function for quick access
async def get_yield_curve_availability(
    *,
    include_years: bool = True,
    config: FetcherConfig | None = None,
) -> YieldCurveAvailability:
    """
    Convenience function to discover what ThaiBMA yield-curve history exists.

    Args:
        include_years: If True (default), also fetch the list of years with data.
        config: Optional fetcher configuration.

    Returns:
        A :class:`YieldCurveAvailability`.

    Raises:
        FetchError: On a non-2xx status.
        ResponseParseError: If the availability window is not a 2-element array.

    Example:
        >>> from settfex.services.thaibma import get_yield_curve_availability
        >>> availability = await get_yield_curve_availability()
        >>> f"{availability.first_date} .. {availability.last_date}"
        '1999-09-15 .. 2026-08-10'
    """
    service = YieldCurveAvailabilityService(config=config)
    return await service.fetch_availability(include_years=include_years)
