"""Shared helpers for the ThaiBMA yield-curve services.

Three concerns live here:

1. **Statelessness.** ``session_manager.get_session_for_url()`` routes every non-``tfex.co.th``
   host to the SET warm-up, so an unguarded ThaiBMA URL would be mis-warmed against set.or.th.
   :func:`stateless_config` forces ``use_session=False`` while preserving the caller's timeout and
   retry settings.
2. **Date normalization.** The API is strict about zero-padding but permissive about nonsense:
   ``2026-8-10`` returns an HTML 404 while ``2026-02-30`` returns HTTP 200 with the *latest*
   curve. :func:`normalize_curve_date` neutralizes both client-side — it re-emits every date
   through :data:`~settfex.services.thaibma.constants.THAIBMA_DATE_FORMAT`, and an impossible
   calendar date fails during ``date`` construction before any request is made.
3. **Tenor labels.** The point-in-time curve is keyed by a float ``X`` and the history matrices by
   a string label (``"10Y"``). :func:`tenor_label` and :func:`parse_tenor` are the exact,
   verified bridge between the two.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from loguru import logger

from settfex.exceptions import InvalidDateError, raise_for_status
from settfex.services.thaibma.constants import (
    THAIBMA_DATE_FORMAT,
    THAIBMA_MAX_YEAR,
    THAIBMA_MIN_YEAR,
    THAIBMA_SUBYEAR_TENORS,
)
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json

# Tolerance for matching a wire ``X`` value to a sub-year tenor label. The grid values carry ~15
# significant digits (0.076712328767123), so anything this close is the same point.
_TENOR_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Fetcher configuration and headers
# ---------------------------------------------------------------------------


def stateless_config(config: FetcherConfig | None) -> FetcherConfig:
    """
    Force ``use_session=False`` for the ThaiBMA host, preserving every other setting.

    Args:
        config: Caller-supplied configuration, or None for defaults.

    Returns:
        A copy with ``use_session`` disabled; the caller's ``timeout``, ``max_retries``,
        ``retry_delay`` and ``rate_limit_delay`` survive untouched.

    Example:
        >>> stateless_config(FetcherConfig(timeout=99)).timeout
        99
    """
    base = config or FetcherConfig()
    return base.model_copy(update={"use_session": False})


def build_thaibma_headers() -> dict[str, str]:
    """
    Request headers for www.thaibma.or.th.

    Deliberately minimal. The host was live-probed 2026-08-10 with no ``User-Agent``, no cookies
    and no referer and answered every request, so there is no bot-detection posture to mirror —
    and inventing a ``Referer`` for a host that provably needs none would be a guess baked into
    every request.

    Returns:
        Headers requesting JSON.
    """
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


# ---------------------------------------------------------------------------
# Date and year normalization
# ---------------------------------------------------------------------------


def normalize_curve_date(value: date | datetime | str) -> date:
    """
    Coerce a user-supplied date to a plain calendar day, rejecting anything ThaiBMA would
    mishandle.

    Args:
        value: A ``date``, a ``datetime`` (the time component is dropped), or a string. Strings
            must be ISO ``YYYY-MM-DD`` (a full ISO timestamp is also accepted).

    Returns:
        The corresponding ``datetime.date``.

    Raises:
        InvalidDateError: If the value is not a date/datetime/str, or the string is not ISO
            ``YYYY-MM-DD``, or it names an impossible calendar day such as ``2026-02-30``.

    Example:
        >>> normalize_curve_date("2026-8-10")   # accepted, re-emitted zero-padded downstream
        datetime.date(2026, 8, 10)
        >>> normalize_curve_date("2026-02-30")
        Traceback (most recent call last):
        InvalidDateError: ...
    """
    # datetime is a subclass of date, so this check must come first.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        error_msg = (
            f"Curve date must be a date, datetime or 'YYYY-MM-DD' string, "
            f"got {type(value).__name__}"
        )
        logger.error(error_msg)
        raise InvalidDateError(error_msg)

    text = value.strip()
    try:
        # Accept a bare day first; fall back to a full ISO timestamp. Both reject impossible
        # calendar days (2026-02-30) during date construction, before any request is made.
        return datetime.strptime(text, THAIBMA_DATE_FORMAT).date()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:
        error_msg = (
            f"Invalid ThaiBMA curve date {value!r} - expected ISO 'YYYY-MM-DD'. Note the API "
            f"answers a malformed date with an HTML 404 and an impossible one (e.g. '2026-02-30') "
            f"with HTTP 200 and the LATEST curve, so this is rejected client-side."
        )
        logger.error(error_msg)
        raise InvalidDateError(error_msg) from exc


def format_curve_date(day: date) -> str:
    """
    Render a calendar day as the zero-padded ``YYYY-MM-DD`` the API requires.

    Args:
        day: The day to format.

    Returns:
        e.g. ``"2026-08-10"``.
    """
    return day.strftime(THAIBMA_DATE_FORMAT)


def normalize_year(year: int) -> int:
    """
    Range-check a calendar year for the per-year history endpoints.

    Args:
        year: Calendar year.

    Returns:
        The year, unchanged.

    Raises:
        ValueError: If the year is not an int, or falls outside
            ``THAIBMA_MIN_YEAR..THAIBMA_MAX_YEAR``.
    """
    # bool is a subclass of int, so exclude it explicitly rather than formatting True into a URL.
    if isinstance(year, bool) or not isinstance(year, int):
        error_msg = f"Year must be an integer, got {type(year).__name__}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    if not THAIBMA_MIN_YEAR <= year <= THAIBMA_MAX_YEAR:
        error_msg = (
            f"Year {year} is out of range; must be between {THAIBMA_MIN_YEAR} and "
            f"{THAIBMA_MAX_YEAR}"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
    return year


# ---------------------------------------------------------------------------
# Tenor labels — the bridge between the float grid and the history columns
# ---------------------------------------------------------------------------


def tenor_label(tenor_years: float) -> str:
    """
    Convert a curve ``X`` value to the label the history matrices use.

    Args:
        tenor_years: Tenor in years, as published in ``Curve[].X``.

    Returns:
        ``"1M"``/``"3M"``/``"6M"`` for the three sub-year grid points, ``"{N}Y"`` for whole years,
        and a compact ``"{value}Y"`` for anything off-grid (no ThaiBMA payload has produced one).

    Example:
        >>> tenor_label(0.076712328767123)
        '1M'
        >>> tenor_label(10.0)
        '10Y'
    """
    for label, value in THAIBMA_SUBYEAR_TENORS.items():
        if abs(tenor_years - value) < _TENOR_TOLERANCE:
            return label
    if abs(tenor_years - round(tenor_years)) < _TENOR_TOLERANCE:
        return f"{round(tenor_years)}Y"
    return f"{tenor_years:g}Y"


def parse_tenor(label: str) -> float:
    """
    Convert a history column label to its exact tenor in years.

    The mapping is exact, not a day-count approximation: for 2026-08-10 every ``getintpttm``
    column equalled the point-in-time ``Curve.Y`` at the position this function returns, to six
    decimals.

    Args:
        label: A tenor label such as ``"1M"``, ``"6M"`` or ``"10Y"`` (case-insensitive).

    Returns:
        Tenor in years — ``28/365`` for ``"1M"``, ``91/365`` for ``"3M"``, ``182/365`` for
        ``"6M"``, and ``float(N)`` for ``"{N}Y"``.

    Raises:
        ValueError: If the label is not a recognized tenor.

    Example:
        >>> parse_tenor("6M")
        0.4986301369863014
        >>> parse_tenor("10Y")
        10.0
    """
    text = str(label).strip().upper()
    if text in THAIBMA_SUBYEAR_TENORS:
        return THAIBMA_SUBYEAR_TENORS[text]
    if text.endswith("Y"):
        try:
            return float(text[:-1])
        except ValueError:
            pass
    error_msg = f"Unrecognized ThaiBMA tenor label {label!r} (expected '1M'/'3M'/'6M' or '<N>Y')"
    logger.error(error_msg)
    raise ValueError(error_msg)


def tenor_sort_key(label: str) -> float:
    """
    Sort key placing tenor labels in maturity order.

    Plain ``sorted()`` is wrong here — lexicographically ``"10Y"`` precedes ``"2Y"``. Unrecognized
    labels sort last rather than raising, so a new ThaiBMA column never breaks a history pull.

    Args:
        label: A tenor label.

    Returns:
        The tenor in years, or ``inf`` for an unrecognized label.
    """
    try:
        return parse_tenor(label)
    except ValueError:
        return float("inf")


def sort_tenor_columns(labels: list[str]) -> list[str]:
    """
    Sort tenor column labels by maturity, shortest first.

    Args:
        labels: Column labels in any order.

    Returns:
        A new list ordered ``1M, 3M, 6M, 1Y, 2Y, ... 50Y``; unrecognized labels keep their
        relative order at the end.

    Example:
        >>> sort_tenor_columns(["10Y", "2Y", "1M"])
        ['1M', '2Y', '10Y']
    """
    return sorted(labels, key=tenor_sort_key)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


async def fetch_thaibma_json(fetcher: AsyncDataFetcher, url: str, *, context: str) -> Any:
    """
    GET a ThaiBMA endpoint, checking the status explicitly, and decode the JSON body.

    ``AsyncDataFetcher.fetch()`` retries **exceptions only** and never raises on a non-2xx status,
    so the status check has to live here. Error bodies are deliberately never parsed: ThaiBMA
    answers some bad routes with an ASP.NET ``{"Message": ...}`` JSON and others with a plain HTML
    404 page, so the status code is the only trustworthy signal.

    Args:
        fetcher: An open fetcher (created with :func:`stateless_config`).
        url: Fully-qualified request URL.
        context: Human-readable origin used in error messages and logs.

    Returns:
        The decoded JSON value. May legitimately be ``None`` — the curve endpoint answers dates
        before its first available date with a body of literal ``null``; callers must handle it.

    Raises:
        FetchError: On any non-2xx status.
        ResponseParseError: If the body is not valid JSON, or contains NaN/Infinity.
    """
    response = await fetcher.fetch(url, headers=build_thaibma_headers())

    if response.status_code != 200:
        error_msg = f"ThaiBMA request failed for {context}: HTTP {response.status_code} ({url})"
        logger.error(error_msg)
        # suggest=False: a 404 here means a bad route or a malformed date, never an unknown
        # stock symbol, so the symbol suggester must not run.
        raise_for_status(response.status_code, error_msg, suggest=False)

    return decode_json(response.text, context=context)
