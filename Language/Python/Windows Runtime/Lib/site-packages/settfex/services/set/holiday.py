"""SET Market Holiday Service - Fetch the official SET market holiday calendar for a year.

Wraps the SET CMS holiday endpoint, which returns a bare JSON array of ``{date, description}``
objects for one calendar year in English or Thai.

Three live-verified quirks drive the shape of this module:

1. The endpoint lives under ``/api/cms/v1/`` — every other ``www.set.or.th`` endpoint in this
   package is under ``/api/set/`` — and it takes ``?lang=`` (like the stock and news endpoints),
   not ``?language=`` (the index endpoints).
2. It answers anything it dislikes with a bare **HTTP 401 and an empty body**: an unrecognized
   ``lang``, a missing ``lang``, and a year it does not serve all look identical. It also returns
   401 *transiently* on perfectly valid requests, so :class:`HolidayService` retries before giving
   up (see ``FetcherConfig.max_retries``).
3. Descriptions are preserved **verbatim**. A trailing ``" *"`` is a SET footnote marker on
   additional special closures and is deliberately not stripped.

.. warning::
    **Only the current year is served.** Live-probed 2026-07-27: with 2026 returning 200 on every
    interleaved control request, 2024, 2025, 2027 and 2028 all returned HTTP 401. Any year other
    than the current one raises :class:`~settfex.exceptions.FetchError`. This endpoint therefore
    cannot supply history for backtests, nor next year's calendar for year-boundary arithmetic.

``MIN_YEAR``/``MAX_YEAR`` are only a client-side typo guard; they deliberately stay permissive
rather than hard-coding "current year only", since SET may begin publishing the following year's
calendar at some point and a narrow client-side check would reject it.
"""

import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import FetchError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_HOLIDAY_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig, FetchResponse
from settfex.utils.parsing import (
    ResponseParseError,
    decode_json,
    validate_list_or_raise,
)

# Thailand observes no DST, so the API's fixed +07:00 offset is equivalent to Asia/Bangkok. Kept as
# a ZoneInfo rather than a fixed offset so "the current year in Bangkok" comes from the real zone.
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

# Client-side bounds on the ``year`` argument. This is a typo guard, NOT a claim about what the API
# actually serves: the endpoint reports an unsupported year with the same bare HTTP 401 it uses for
# transient failures, so real coverage cannot be probed reliably and is deliberately not enforced.
MIN_YEAR = 1975  # SET began trading in 1975
MAX_YEAR = 2100

# Statuses worth retrying. AsyncDataFetcher.fetch() only retries exceptions, never a non-2xx status,
# so the transient 401 described in the module docstring has to be handled here.
_RETRYABLE_STATUS = frozenset({401, 403, 429})


def _as_bangkok_day(value: date | datetime) -> date:
    """
    Reduce a date or datetime to a Bangkok-local calendar day.

    Naive datetimes are assumed to already be Bangkok-local; aware ones are converted. Plain
    ``date`` objects pass through untouched.

    Args:
        value: A ``date`` or ``datetime`` to normalize

    Returns:
        The corresponding Bangkok-local calendar day

    Example:
        >>> _as_bangkok_day(datetime(2026, 1, 1, 23, 30, tzinfo=UTC))
        datetime.date(2026, 1, 2)
    """
    # datetime is a subclass of date, so this check must come first.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=BANGKOK_TZ).date()
        return value.astimezone(BANGKOK_TZ).date()
    return value


class Holiday(BaseModel):
    """Model for a single SET market holiday."""

    holiday_date: datetime = Field(
        alias="date",
        description=(
            "Holiday date as published by SET - timezone-aware, always +07:00 (Asia/Bangkok), "
            "with a 00:00:00 time component"
        ),
    )
    description: str = Field(
        description=(
            "Holiday name in the requested language, preserved verbatim. A trailing ' *' is a "
            "SET footnote marker on additional special closures and is not stripped"
        )
    )

    # NOTE: deliberately WITHOUT str_strip_whitespace (which the other SET models enable) - the
    # trailing ' *' footnote marker in some descriptions must survive verbatim.
    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
    )


class HolidayCalendar(BaseModel):
    """
    The official SET market holiday calendar for one year.

    Holidays are kept in the order the API returned them (live-verified ascending, no duplicates);
    the query helpers below do not depend on that ordering.
    """

    year: int = Field(description="Calendar year these holidays belong to")
    lang: Language = Field(description="Language the descriptions were fetched in ('en' or 'th')")
    holidays: list[Holiday] = Field(
        default_factory=list, description="Holidays for the year, in API order"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
    )

    @property
    def count(self) -> int:
        """Get total count of holidays in the calendar."""
        return len(self.holidays)

    @property
    def dates(self) -> list[date]:
        """
        All holiday dates as plain Bangkok-local calendar days, ascending.

        Returns:
            Sorted list of ``datetime.date`` objects
        """
        return sorted(h.holiday_date.date() for h in self.holidays)

    def is_holiday(self, day: date | datetime) -> bool:
        """
        Check whether a day is a SET-published market holiday.

        .. warning::
            This answers *"is this day on SET's published holiday list"* - **not** *"is the market
            closed"*. Weekends are not in the payload, so a Saturday returns ``False``. Use a
            trading-calendar layer for market-open questions.

        Args:
            day: Day to check, as a ``date`` or ``datetime`` (naive datetimes are treated as
                Bangkok-local)

        Returns:
            True if the day appears in this calendar's holiday list

        Example:
            >>> calendar = await get_holidays(2026)
            >>> calendar.is_holiday(date(2026, 1, 1))
            True
        """
        return self.get_holiday(day) is not None

    def get_holiday(self, day: date | datetime) -> Holiday | None:
        """
        Get the holiday falling on a given day, if any.

        Args:
            day: Day to look up, as a ``date`` or ``datetime`` (naive datetimes are treated as
                Bangkok-local)

        Returns:
            The matching :class:`Holiday`, or None if the day is not a published holiday

        Example:
            >>> calendar = await get_holidays(2026)
            >>> holiday = calendar.get_holiday(date(2026, 1, 1))
            >>> holiday.description
            "New Year's Day"
        """
        target = _as_bangkok_day(day)
        for holiday in self.holidays:
            if holiday.holiday_date.date() == target:
                return holiday
        return None

    def filter_by_month(self, month: int) -> list[Holiday]:
        """
        Filter holidays by calendar month.

        Args:
            month: Month number, 1 (January) through 12 (December)

        Returns:
            List of holidays falling in that month, in API order

        Raises:
            ValueError: If month is outside 1-12.

        Example:
            >>> calendar = await get_holidays(2026)
            >>> len(calendar.filter_by_month(4))  # Songkran
            4
        """
        if not 1 <= month <= 12:
            error_msg = f"Month must be between 1 and 12, got {month}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        return [h for h in self.holidays if h.holiday_date.month == month]

    def next_holiday(self, after: date | datetime | None = None) -> Holiday | None:
        """
        Get the next holiday strictly after a given day.

        Args:
            after: Day to search from (exclusive). Defaults to today in Asia/Bangkok.

        Returns:
            The earliest holiday after ``after``, or None if this year has none left. Note the
            calendar only covers :attr:`year`, so December lookups usually return None.

        Example:
            >>> calendar = await get_holidays(2026)
            >>> upcoming = calendar.next_holiday(date(2026, 1, 1))
            >>> upcoming.description
            'Additional special holiday'
        """
        target = _as_bangkok_day(after) if after is not None else datetime.now(BANGKOK_TZ).date()
        upcoming = [h for h in self.holidays if h.holiday_date.date() > target]
        if not upcoming:
            return None
        # min() rather than [0] so the result is correct even if the API stops returning sorted data
        return min(upcoming, key=lambda h: h.holiday_date)


class HolidayService:
    """
    Service for fetching the official SET market holiday calendar.

    Market-level: takes a year rather than a symbol, and supports English and Thai. Because the
    endpoint returns HTTP 401 transiently on valid requests, fetches are retried according to
    ``FetcherConfig.max_retries`` / ``retry_delay``.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the holiday service.

        Args:
            config: Optional fetcher configuration (uses defaults if None). Raise
                ``max_retries`` if you hit repeated HTTP 401s from the endpoint.

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = HolidayService()
            >>> # More patient, for when the endpoint is throwing transient 401s
            >>> service = HolidayService(FetcherConfig(max_retries=6, retry_delay=2.0))
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"HolidayService initialized with base_url={self.base_url}")

    @staticmethod
    def _normalize_year(year: int | None) -> int:
        """Resolve None to the current Bangkok year and range-check an explicit year."""
        if year is None:
            resolved = datetime.now(BANGKOK_TZ).year
            logger.debug(f"No year supplied; defaulting to the current Bangkok year {resolved}")
            return resolved

        # bool is a subclass of int, so exclude it explicitly rather than formatting True into a URL
        if isinstance(year, bool) or not isinstance(year, int):
            error_msg = f"Year must be an integer, got {type(year).__name__}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if not MIN_YEAR <= year <= MAX_YEAR:
            error_msg = f"Year {year} is out of range; must be between {MIN_YEAR} and {MAX_YEAR}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        return year

    async def _fetch_with_retry(
        self, fetcher: AsyncDataFetcher, url: str, headers: dict[str, str], year: int
    ) -> FetchResponse:
        """Fetch ``url``, retrying the endpoint's transient 401/403/429 before raising."""
        attempts = self.config.max_retries + 1

        for attempt in range(attempts):
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code == 200:
                return response

            if response.status_code in _RETRYABLE_STATUS and attempt < self.config.max_retries:
                delay = self.config.retry_delay * (2**attempt)
                logger.warning(
                    f"HTTP {response.status_code} from the holiday API "
                    f"(attempt {attempt + 1}/{attempts}); retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue

            error_msg = f"Failed to fetch holidays for {year}: HTTP {response.status_code}"
            if response.status_code == 401:
                # 401 is this endpoint's only failure code, so spell out both causes rather than
                # leaving the caller with a bare status.
                error_msg += (
                    " - the API returns 401 both for years it does not serve (live-probed"
                    " 2026-07-27: only the current year is available) and transiently under"
                    " load; check the year, or retry later"
                )
            logger.error(error_msg)
            raise_for_status(response.status_code, error_msg, suggest=False)

        # Not reachable: the final attempt above always returns or raises. Present so the function
        # is total for mypy without an assert.
        error_msg = f"Failed to fetch holidays for {year} after {attempts} attempts"
        logger.error(error_msg)
        raise FetchError(error_msg)

    async def _fetch_payload(self, year: int, lang: Language) -> Any:
        """Fetch and JSON-decode the holiday payload for a resolved year and language."""
        endpoint = SET_HOLIDAY_ENDPOINT.format(year=year)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching SET holidays for {year} (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API (includes all Incapsula bypass headers)
            headers = AsyncDataFetcher.get_set_api_headers()

            # SessionManager handles cookies automatically - no manual cookie needed
            response = await self._fetch_with_retry(fetcher, url, headers, year)

            return decode_json(response.text, context=f"set holidays {year} ({lang})")

    async def fetch_holidays(
        self, year: int | None = None, lang: Language = "en"
    ) -> HolidayCalendar:
        """
        Fetch the SET market holiday calendar for a year.

        Args:
            year: Calendar year (defaults to the current year in Asia/Bangkok). The API only
                serves the current year - any other year raises FetchError.
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            HolidayCalendar containing every published holiday for the year

        Raises:
            ValueError: If the year is not an integer within MIN_YEAR..MAX_YEAR.
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures - including the endpoint's bare HTTP 401,
                which it returns both for unsupported years and transiently for valid requests.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = HolidayService()
            >>> calendar = await service.fetch_holidays(2026)
            >>> print(f"{calendar.count} holidays in {calendar.year}")
            >>> for holiday in calendar.holidays:
            ...     print(holiday.holiday_date.date(), holiday.description)
        """
        resolved_year = self._normalize_year(year)
        lang = normalize_language(lang)

        data = await self._fetch_payload(resolved_year, lang)

        # The payload is a bare JSON array of holiday entries
        holidays = validate_list_or_raise(
            Holiday, data, context=f"set holidays {resolved_year} ({lang})"
        )
        if not holidays:
            # A year this endpoint serves always has holidays -- it answers an unserved year with
            # 401, not with an empty list. Returning an empty calendar would make `is_holiday()`
            # False for every date, i.e. silently assert that the market never closes. Guarded in
            # the service rather than with a model constraint, so the message can say why.
            error_msg = (
                f"SET returned an empty holiday calendar for {resolved_year}, which a served year "
                f"never has -- an empty calendar would make every day look like a trading day"
            )
            logger.error(error_msg)
            raise ResponseParseError(error_msg)
        calendar = HolidayCalendar(year=resolved_year, lang=lang, holidays=holidays)

        logger.info(f"Successfully fetched {calendar.count} holiday(s) for {resolved_year}")

        return calendar

    async def fetch_holidays_raw(
        self, year: int | None = None, lang: Language = "en"
    ) -> list[dict[str, Any]]:
        """
        Fetch the holiday calendar as a raw list without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            year: Calendar year (defaults to the current year in Asia/Bangkok). The API only
                serves the current year - any other year raises FetchError.
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw list of dictionaries from API

        Raises:
            ValueError: If the year is not an integer within MIN_YEAR..MAX_YEAR.
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = HolidayService()
            >>> raw_data = await service.fetch_holidays_raw(2026)
            >>> print(raw_data[0])
            {'date': '2026-01-01T00:00:00+07:00', 'description': "New Year's Day"}
        """
        resolved_year = self._normalize_year(year)
        lang = normalize_language(lang)

        data = await self._fetch_payload(resolved_year, lang)
        logger.debug(f"Raw response: {len(data) if isinstance(data, list) else type(data)} entries")

        return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_holidays(
    year: int | None = None,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> HolidayCalendar:
    """
    Convenience function to fetch the SET market holiday calendar for a year.

    Args:
        year: Calendar year (defaults to the current year in Asia/Bangkok). The API only serves
            the current year - any other year raises FetchError.
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        HolidayCalendar containing every published holiday for the year

    Raises:
        ValueError: If the year is not an integer within MIN_YEAR..MAX_YEAR.
        InvalidLanguageError: If the language is not recognized.
        FetchError: On HTTP or transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_holidays
        >>> calendar = await get_holidays()  # current year, English
        >>> calendar.is_holiday(date(2026, 1, 1))
        True
        >>> thai = await get_holidays(2026, lang="th")
        >>> thai.holidays[0].description
        'วันขึ้นปีใหม่'
    """
    service = HolidayService(config=config)
    return await service.fetch_holidays(year=year, lang=lang)
