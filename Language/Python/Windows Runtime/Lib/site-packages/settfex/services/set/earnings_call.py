"""SET Earnings Call (Opportunity Day) Calendar Service.

Fetches the SET "Earnings Call (OPPDAY)" calendar from the opportunity-day backend
(``https://api.lcp.setgroup.or.th/api/v1``) and returns typed Pydantic models plus an
optional pandas DataFrame convenience.

Unlike the main SET API, this host is **stateless**: it sits behind no Incapsula bot wall,
needs no cookies and no auth header. The service therefore uses a plain (sessionless)
``AsyncDataFetcher`` — there is no cookie warm-up.

Note on the ``period`` field — it means two different things in the two endpoints:

- in the **list** response (used here for :attr:`EarningsCallItem.period`) it is the video
  **duration** as shown on the website card, e.g. ``"45:00"`` (MM:SS);
- in the **detail** response it is the meeting **clock-time range**, e.g. ``"16:15 - 17:00"``
  (exposed as :attr:`EarningsCallDetail.meeting_time`).

The list duration is authoritative for ``video_clip_time`` and is never overwritten by
enrichment.
"""

import asyncio
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from settfex.services.set.constants import (
    SET_EARNINGS_CALL_DETAIL_ENDPOINT,
    SET_EARNINGS_CALL_FILTER_ENDPOINT,
    SET_EARNINGS_CALL_SEARCH_ENDPOINT,
    SET_LCP_BASE_URL,
    SET_OPPDAY_ORIGIN,
    SET_OPPDAY_REFERER,
)
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import (
    ResponseParseError,
    validate_list_or_raise,
    validate_or_raise,
)
from settfex.utils.youtube_transcript import fetch_youtube_transcript

PresentationType = Literal[1, 2, 3]
"""Earnings-call presentation type: 1 = Earnings Call/OPPDAY, 2 = Digital Roadshow, 3 = C-Sign."""

if TYPE_CHECKING:
    import pandas as pd

# Extracts the YouTube video id from a thumbnail URL like
# ``https://img.youtube.com/vi/<VIDEO_ID>/mqdefault.jpg``.
_YOUTUBE_ID_RE = re.compile(r"/vi/([^/]+)/")


def _extract_youtube_id(image_path: str | None) -> str | None:
    """Extract the YouTube video id from a thumbnail URL.

    Args:
        image_path: Thumbnail URL from the API (e.g. ``.../vi/<id>/mqdefault.jpg``).

    Returns:
        The video id, or ``None`` if ``image_path`` is missing/empty or is not a
        YouTube thumbnail (e.g. an upcoming item with no recording).
    """
    if not image_path or "youtube.com" not in image_path:
        return None
    match = _YOUTUBE_ID_RE.search(image_path)
    return match.group(1) if match else None


def _build_earnings_call_headers(language: str) -> dict[str, str]:
    """Build browser-like headers for the opportunity-day API.

    No cookie/authorization header is required (the host is stateless). ``Content-Type`` is
    set automatically by curl_cffi when a JSON body is sent, so it is intentionally omitted.

    Args:
        language: Normalized language code (``"en"`` or ``"th"``) driving ``Accept-Language``.

    Returns:
        Header dictionary suitable for the search/detail/filter endpoints.
    """
    accept_language = "th,en-US;q=0.9,en;q=0.8" if language == "th" else "en-US,en;q=0.9,th;q=0.8"
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": accept_language,
        "Origin": SET_OPPDAY_ORIGIN,
        "Referer": SET_OPPDAY_REFERER,
        "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
    }


class _ProgressReporter:
    """Optional progress reporting for long fetches: a tqdm bar and/or a callback.

    ``show_bar`` renders a ``tqdm.auto`` bar (lazily imported from the optional ``progress``
    extra; auto-detects Jupyter vs terminal). If tqdm is missing, it logs a one-time hint and
    emits periodic loguru progress lines instead. ``callback`` (dependency-free) is invoked
    with ``(done, total)`` after every update. Used only from the asyncio event loop (single
    thread), so no locking is needed.
    """

    def __init__(
        self,
        total: int,
        *,
        show_bar: bool,
        callback: Callable[[int, int], None] | None,
        desc: str,
    ) -> None:
        self._total = total
        self._done = 0
        self._callback = callback
        self._desc = desc
        self._bar: Any = None
        self._log_fallback = False
        self._last_logged_pct = 0
        if show_bar:
            try:
                from tqdm.auto import tqdm

                self._bar = tqdm(total=total, desc=desc, unit="rec")
            except ImportError:
                logger.warning(
                    "progress=True but tqdm is not installed; install it with "
                    "'pip install settfex[progress]'. Falling back to log lines."
                )
                self._log_fallback = True

    def update(self, n: int) -> None:
        """Advance progress by ``n`` and fan out to the bar / callback / log fallback."""
        if n <= 0:
            return
        self._done = min(self._done + n, self._total) if self._total else self._done + n
        if self._bar is not None:
            self._bar.update(n)
        if self._callback is not None:
            self._callback(self._done, self._total)
        if self._log_fallback and self._total:
            pct = self._done * 100 // self._total
            if pct >= self._last_logged_pct + 10:
                self._last_logged_pct = pct - (pct % 10)
                logger.info(f"{self._desc}: {self._done}/{self._total} ({pct}%)")

    def close(self) -> None:
        """Close the underlying bar, if any."""
        if self._bar is not None:
            self._bar.close()


class FilterOption(BaseModel):
    """A single filter option from ``/investor/filter/{name}``.

    ``id`` is an ``int`` for the ``types``/``years``/``trusts``/``stages`` filters and a
    ``str`` code for ``industries``/``markets``/``themes``.
    """

    id: int | str = Field(description="Option id (int or string code, depends on the filter)")
    name: str = Field(description="Human-readable option name")

    model_config = ConfigDict(str_strip_whitespace=True)


class EarningsCallDetail(BaseModel):
    """Enrichment fields from ``GET /investor/vdo/{id}`` (fetched only when ``enrich=True``)."""

    id: int = Field(description="Presentation/event id")
    symbol: str | None = Field(default=None, description="Stock symbol/ticker")
    type_id: int | None = Field(default=None, description="Presentation type id (1 = OPPDAY)")
    type: str | None = Field(default=None, description="Presentation type label")
    year: int | None = Field(default=None, description="Fiscal year")
    round: int | None = Field(default=None, description="Round number within the year")
    round_name: str | None = Field(default=None, description='Round name, e.g. "Q1/2026"')
    company_name: str | None = Field(default=None, description="Company name (English, raw)")
    company_name_th: str | None = Field(default=None, description="Company name (Thai, raw)")
    description: str | None = Field(default=None, description="Presentation description")
    meeting_time: str | None = Field(
        default=None,
        alias="period",
        description=(
            'Meeting clock-time range, e.g. "16:15 - 17:00". Distinct from the list '
            "endpoint's MM:SS video duration — see the module docstring."
        ),
    )
    video_link: str | None = Field(default=None, description="YouTube embed URL")
    document_link: str | None = Field(default=None, description="Presentation document path")
    snapshot_link: str | None = Field(default=None, description="Listed-company snapshot URL")
    company_link: str | None = Field(default=None, description="SET company profile URL")
    logo_path: str | None = Field(default=None, description="Company logo URL")
    image_path: str | None = Field(default=None, description="YouTube thumbnail URL")
    has_qa: bool | None = Field(default=None, description="Whether a Q&A is available")

    model_config = ConfigDict(populate_by_name=True, extra="ignore", str_strip_whitespace=True)

    @field_validator("video_link")
    @classmethod
    def _clean_video_link(cls, value: str | None) -> str | None:
        """Remove stray internal whitespace (some old records embed a newline mid-URL)."""
        if value is None:
            return None
        return "".join(value.split())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def youtube_video_id(self) -> str | None:
        """YouTube video id derived from the (clean) thumbnail ``image_path``.

        Preferred over ``video_link``, which a few legacy records return malformed.
        """
        return _extract_youtube_id(self.image_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def youtube_url(self) -> str | None:
        """Canonical ``https://www.youtube.com/watch?v=<id>`` URL, or ``None``."""
        video_id = self.youtube_video_id
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


class EarningsCallItem(BaseModel):
    """A single Earnings Call (Opportunity Day) calendar entry from the list endpoint."""

    id: int = Field(description="Unique presentation/event id")
    name: str = Field(description="Presentation title (Thai, as shown on the card)")
    company_name: str = Field(description='Raw company name, prefixed "<SYMBOL>: ..."')
    industry: str | None = Field(
        default=None,
        description="Industry classification (None for newly-listed companies not yet classified)",
    )
    symbol: str = Field(description="Stock symbol/ticker")
    image_path: str | None = Field(
        default=None, description="YouTube thumbnail URL (present only when recorded)"
    )
    view_mode: bool | None = Field(default=None, description="Whether the recording is viewable")
    meeting_date: datetime = Field(
        description="Meeting date (tz-aware UTC; the API reports midnight UTC)"
    )
    period: str | None = Field(
        default=None,
        description='Video clip duration as shown on the card, e.g. "45:00" (MM:SS)',
    )
    detail: EarningsCallDetail | None = Field(
        default=None,
        description="Enrichment from the detail endpoint, populated only when enrich=True",
    )
    transcript: str | None = Field(
        default=None,
        description=(
            "YouTube transcript text (Thai by default), populated only by fetch_transcripts() "
            "or get_earnings_call_transcript() — handy as raw text for AI/LLM use"
        ),
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @field_validator("meeting_date")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        """Guarantee a tz-aware datetime (assume UTC if the API ever omits the offset)."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def company_name_clean(self) -> str:
        """Company name with the leading ``"<SYMBOL>: "`` prefix removed.

        Uses a literal prefix check (not a regex) so symbols containing regex-special
        characters (e.g. ``88TH``) are handled safely.
        """
        prefix = f"{self.symbol}:"
        if self.company_name.startswith(prefix):
            return self.company_name[len(prefix) :].strip()
        return self.company_name.strip()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def youtube_video_id(self) -> str | None:
        """YouTube video id derived from ``image_path`` (``None`` if not a YouTube thumb)."""
        return _extract_youtube_id(self.image_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def youtube_url(self) -> str | None:
        """Canonical ``https://www.youtube.com/watch?v=<id>`` URL, or ``None``."""
        video_id = self.youtube_video_id
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


# DataFrame column name -> accessor. The first five are the user-facing default columns; the
# rest are opt-in extras selectable via ``to_dataframe(columns=[...])``.
_DATAFRAME_ACCESSORS: dict[str, Callable[[EarningsCallItem], Any]] = {
    "stock_name": lambda item: item.symbol,
    "company_name": lambda item: item.company_name_clean,
    "earnings_call_date": lambda item: item.meeting_date.date(),
    "video_clip_time": lambda item: item.period,
    "youtube_url": lambda item: item.youtube_url,
    "company_name_raw": lambda item: item.company_name,
    "earnings_call_datetime": lambda item: item.meeting_date,
    "youtube_video_id": lambda item: item.youtube_video_id,
    "id": lambda item: item.id,
    "name": lambda item: item.name,
    "industry": lambda item: item.industry,
    "view_mode": lambda item: item.view_mode,
}
_DEFAULT_DATAFRAME_COLUMNS: tuple[str, ...] = (
    "stock_name",
    "company_name",
    "earnings_call_date",
    "video_clip_time",
    "youtube_url",
)


class EarningsCallResponse(BaseModel):
    """Response from the earnings-call list endpoint (``no_records`` + ``items``)."""

    no_records: int = Field(description="Total number of records across all pages")
    items: list[EarningsCallItem] = Field(
        default_factory=list, description="Calendar entries for this page (or accumulated)"
    )

    model_config = ConfigDict(populate_by_name=True)

    @property
    def count(self) -> int:
        """Number of items currently held (this page, or accumulated by ``fetch_all``)."""
        return len(self.items)

    def to_dataframe(self, columns: list[str] | None = None) -> "pd.DataFrame":
        """Render the items as a pandas DataFrame.

        pandas is an optional dependency; it is imported lazily here so that importing this
        service never requires pandas.

        Args:
            columns: Column names to include, in order. Defaults to
                ``["stock_name", "company_name", "earnings_call_date", "video_clip_time",
                "youtube_url"]``. See ``_DATAFRAME_ACCESSORS`` for the full set of selectable
                columns (e.g. ``"company_name_raw"``, ``"youtube_video_id"``,
                ``"earnings_call_datetime"``, ``"industry"``, ``"id"``).

        Returns:
            A DataFrame with one row per item (empty — but with the requested columns — when
            there are no items).

        Raises:
            ImportError: If pandas is not installed.
            ValueError: If an unknown column name is requested.
        """
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatched sys.modules
            raise ImportError(
                "pandas is required for EarningsCallResponse.to_dataframe(). Install it with "
                "'pip install settfex[dataframe]' (or 'uv add pandas')."
            ) from exc

        cols = list(columns) if columns is not None else list(_DEFAULT_DATAFRAME_COLUMNS)
        unknown = [c for c in cols if c not in _DATAFRAME_ACCESSORS]
        if unknown:
            raise ValueError(
                f"Unknown DataFrame column(s): {unknown}. "
                f"Available columns: {sorted(_DATAFRAME_ACCESSORS)}"
            )

        rows = [{col: _DATAFRAME_ACCESSORS[col](item) for col in cols} for item in self.items]
        return pd.DataFrame(rows, columns=cols)


class EarningsCallService:
    """Service for fetching the SET Earnings Call (Opportunity Day) calendar.

    The opportunity-day host is stateless, so this service always uses a sessionless
    ``AsyncDataFetcher`` (no cookie warm-up). Any injected :class:`FetcherConfig` is honored
    for everything except ``use_session``, which is forced to ``False``.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """Initialize the service.

        Args:
            config: Optional fetcher configuration. ``use_session`` is always coerced to
                ``False`` for this stateless host; all other settings (timeout, retries,
                browser impersonation, rate limiting) are preserved.
        """
        base = config or FetcherConfig()
        # This host needs no cookies; never warm up a SET session for it.
        self.config = base.model_copy(update={"use_session": False})
        self.base_url = SET_LCP_BASE_URL
        logger.info(f"EarningsCallService initialized with base_url={self.base_url}")

    @staticmethod
    def _build_search_body(
        *,
        type_id: int,
        quarter_id: int,
        keyword: str | None,
        industries_id: str | None,
        composition_id: int | None,
        start: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Build (and validate) the JSON body for the search endpoint.

        Raises:
            ValueError: If ``start`` or ``page_size`` is out of range.
        """
        if start < 1:
            raise ValueError(f"start must be >= 1 (1-based page number), got {start}")
        if page_size < 1:
            raise ValueError(f"page_size must be >= 1, got {page_size}")

        return {
            "start": start,
            "page_size": page_size,
            "keyword": normalize_symbol(keyword) if keyword else None,
            "quarter_id": quarter_id,
            "industries_id": industries_id,
            "composition_id": composition_id,
            "type_id": type_id,
        }

    async def _search_page(
        self, fetcher: AsyncDataFetcher, body: dict[str, Any], language: str
    ) -> EarningsCallResponse:
        """POST a single search page and validate the response."""
        url = f"{self.base_url}{SET_EARNINGS_CALL_SEARCH_ENDPOINT}"
        headers = _build_earnings_call_headers(language)
        logger.debug(
            f"POST earnings-call search: start={body['start']} page_size={body['page_size']} "
            f"type_id={body['type_id']} quarter_id={body['quarter_id']} "
            f"keyword={body['keyword']!r}"
        )
        data = await fetcher.fetch_json(url, headers=headers, method="POST", json_body=body)
        response = validate_or_raise(EarningsCallResponse, data, context="set earnings-call search")
        logger.info(
            f"Fetched {response.count} earnings-call item(s) (no_records={response.no_records})"
        )
        return response

    async def fetch_earnings_calls(
        self,
        *,
        type_id: PresentationType = 1,
        quarter_id: int = 0,
        keyword: str | None = None,
        industries_id: str | None = None,
        composition_id: int | None = None,
        start: int = 1,
        page_size: int = 12,
        language: Language = "en",
        enrich: bool = False,
    ) -> EarningsCallResponse:
        """Fetch one page of earnings-call entries.

        Args:
            type_id: Presentation type (1 = Earnings Call/OPPDAY, 2 = Digital Roadshow,
                3 = C-Sign Public Presentation).
            quarter_id: Quarter filter id (0 = all quarters); see ``fetch_filter_years()``.
            keyword: Free-text symbol/company search (normalized via ``normalize_symbol``).
            industries_id: Industry filter code (see ``fetch_filter_industries()``).
            composition_id: Optional composition/theme filter id.
            start: 1-based page number.
            page_size: Items per page.
            language: ``"en"`` or ``"th"`` (drives ``Accept-Language``).
            enrich: If True, also fetch per-item detail concurrently (bounded). Off by
                default — all five required columns come from the list endpoint.

        Returns:
            An :class:`EarningsCallResponse` for the requested page.

        Raises:
            ValueError: If ``start``/``page_size`` are out of range or ``language`` is invalid.
            ResponseParseError: If the response cannot be decoded.

        Example:
            >>> service = EarningsCallService()
            >>> response = await service.fetch_earnings_calls(keyword="HANN")
            >>> response.items[0].youtube_url
        """
        language = normalize_language(language)
        body = self._build_search_body(
            type_id=type_id,
            quarter_id=quarter_id,
            keyword=keyword,
            industries_id=industries_id,
            composition_id=composition_id,
            start=start,
            page_size=page_size,
        )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            response = await self._search_page(fetcher, body, language)
            if enrich:
                await self._enrich_items(fetcher, response.items, language)
            return response

    async def fetch_earnings_calls_raw(
        self,
        *,
        type_id: PresentationType = 1,
        quarter_id: int = 0,
        keyword: str | None = None,
        industries_id: str | None = None,
        composition_id: int | None = None,
        start: int = 1,
        page_size: int = 12,
        language: Language = "en",
    ) -> dict[str, Any]:
        """Fetch one page as a raw dict (no Pydantic validation).

        Returns:
            The raw JSON object from the API.

        Raises:
            ValueError: If ``start``/``page_size`` are out of range or ``language`` is invalid.
            ResponseParseError: If the response is not a JSON object.
        """
        language = normalize_language(language)
        body = self._build_search_body(
            type_id=type_id,
            quarter_id=quarter_id,
            keyword=keyword,
            industries_id=industries_id,
            composition_id=composition_id,
            start=start,
            page_size=page_size,
        )
        url = f"{self.base_url}{SET_EARNINGS_CALL_SEARCH_ENDPOINT}"
        headers = _build_earnings_call_headers(language)

        async with AsyncDataFetcher(config=self.config) as fetcher:
            data: Any = await fetcher.fetch_json(
                url, headers=headers, method="POST", json_body=body
            )
            # Guard the documented dict return type explicitly (asserts are stripped under -O).
            if not isinstance(data, dict):
                raise ResponseParseError(
                    f"Expected a JSON object for set earnings-call search, "
                    f"got {type(data).__name__}"
                )
            logger.debug(f"Raw response keys: {list(data.keys())}")
            return data

    async def fetch_all_earnings_calls(
        self,
        *,
        type_id: PresentationType = 1,
        quarter_id: int = 0,
        keyword: str | None = None,
        industries_id: str | None = None,
        composition_id: int | None = None,
        start: int = 1,
        page_size: int = 200,
        language: Language = "en",
        enrich: bool = False,
        max_records: int | None = None,
        max_pages: int | None = None,
        max_concurrency: int = 5,
        progress: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
        throttle: float = 0.0,
    ) -> EarningsCallResponse:
        """Auto-paginate across all pages **concurrently**, bounded and polite.

        Fetches the first page to learn ``no_records``, then fetches the remaining pages
        concurrently under ``max_concurrency`` and reassembles them in page order. This is far
        faster than fetching pages one at a time. ``page_size`` is uncapped by the API, so a
        larger value also means fewer requests.

        Args:
            page_size: Records per request (default 200).
            max_records: Stop once this many items are collected (then truncate).
            max_pages: Fetch at most this many pages.
            max_concurrency: Max simultaneous page requests (politeness bound, default 5).
            progress: Show a ``tqdm`` progress bar (needs ``pip install settfex[progress]``).
            progress_callback: Optional ``(done, total)`` callback, fired as pages complete.
            throttle: Optional per-request delay in seconds (default 0; concurrency bounds load).
            (Other args mirror :meth:`fetch_earnings_calls`.)

        Returns:
            An :class:`EarningsCallResponse` with all collected items (in order) and the API's
            ``no_records`` total.

        Raises:
            ValueError: If ``max_records``/``max_pages`` are set but < 1, ``max_concurrency`` < 1,
                or other inputs are invalid.
        """
        language = normalize_language(language)
        if max_records is not None and max_records < 1:
            raise ValueError(f"max_records must be >= 1 when set, got {max_records}")
        if max_pages is not None and max_pages < 1:
            raise ValueError(f"max_pages must be >= 1 when set, got {max_pages}")
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")

        def build(page: int) -> dict[str, Any]:
            return self._build_search_body(
                type_id=type_id,
                quarter_id=quarter_id,
                keyword=keyword,
                industries_id=industries_id,
                composition_id=composition_id,
                start=page,
                page_size=page_size,
            )

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # 1. The first page reveals the total, so the rest can fan out concurrently.
            first = await self._search_page(fetcher, build(start), language)
            no_records = first.no_records
            target = no_records if max_records is None else min(no_records, max_records)
            total_pages = (target + page_size - 1) // page_size if target > 0 else 1
            if max_pages is not None:
                total_pages = min(total_pages, max_pages)

            reporter = _ProgressReporter(
                total=target,
                show_bar=progress,
                callback=progress_callback,
                desc="Fetching OPPDAY",
            )
            items: list[EarningsCallItem] = list(first.items)
            reporter.update(len(first.items))

            # 2. Fetch the remaining pages concurrently, bounded by a semaphore.
            if total_pages > 1:
                semaphore = asyncio.Semaphore(max_concurrency)

                async def fetch_page(page: int) -> tuple[int, list[EarningsCallItem]]:
                    async with semaphore:
                        if throttle > 0:
                            await asyncio.sleep(throttle)
                        resp = await self._search_page(fetcher, build(page), language)
                        return page, resp.items

                tasks = [
                    asyncio.create_task(fetch_page(p))
                    for p in range(start + 1, start + total_pages)
                ]
                pages: dict[int, list[EarningsCallItem]] = {}
                for coro in asyncio.as_completed(tasks):
                    page_no, page_items = await coro
                    pages[page_no] = page_items
                    reporter.update(len(page_items))
                for page_no in sorted(pages):
                    items.extend(pages[page_no])

            reporter.close()

            # 3. Truncate to the record cap, then optionally enrich.
            if max_records is not None:
                items = items[:max_records]
            if enrich:
                await self._enrich_items(
                    fetcher,
                    items,
                    language,
                    max_concurrency=max_concurrency,
                    progress=progress,
                    progress_callback=progress_callback,
                )

        logger.info(
            f"Fetched {len(items)} earnings-call item(s) across {total_pages} page(s) "
            f"(no_records={no_records}, concurrency={max_concurrency})"
        )
        return EarningsCallResponse(no_records=no_records, items=items)

    async def _fetch_detail(
        self, fetcher: AsyncDataFetcher, item_id: int, language: str
    ) -> EarningsCallDetail:
        """Fetch and validate the detail record for a single item."""
        endpoint = SET_EARNINGS_CALL_DETAIL_ENDPOINT.format(id=item_id)
        url = f"{self.base_url}{endpoint}"
        headers = _build_earnings_call_headers(language)
        data = await fetcher.fetch_json(url, headers=headers)
        return validate_or_raise(
            EarningsCallDetail, data, context=f"set earnings-call detail id={item_id}"
        )

    async def fetch_earnings_call_detail(
        self, item_id: int, language: Language = "en"
    ) -> EarningsCallDetail:
        """Fetch a single OPPDAY presentation's detail by its id.

        This is the typed detail behind an
        ``https://opportunity-day.setgroup.or.th/<lang>/vdo/{id}`` page
        (``GET /investor/vdo/{id}``).

        Args:
            item_id: The presentation/event id (e.g. ``6319``).
            language: ``"en"`` or ``"th"``.

        Returns:
            The :class:`EarningsCallDetail` for ``item_id``.

        Raises:
            ValueError: If ``language`` is invalid.
            ResponseParseError: If the response cannot be decoded.

        Example:
            >>> service = EarningsCallService()
            >>> detail = await service.fetch_earnings_call_detail(6319)
            >>> detail.symbol, detail.round_name, detail.youtube_url
        """
        language = normalize_language(language)
        async with AsyncDataFetcher(config=self.config) as fetcher:
            return await self._fetch_detail(fetcher, item_id, language)

    async def _enrich_items(
        self,
        fetcher: AsyncDataFetcher,
        items: list[EarningsCallItem],
        language: str,
        *,
        max_concurrency: int = 5,
        progress: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Populate ``item.detail`` for each item via bounded-concurrent detail fetches.

        A failed detail fetch is logged and tolerated (that item keeps ``detail=None``); it
        never fails the whole batch. Progress (bar/callback) advances once per item.
        """
        if not items:
            return

        semaphore = asyncio.Semaphore(max_concurrency)
        reporter = _ProgressReporter(
            total=len(items),
            show_bar=progress,
            callback=progress_callback,
            desc="Enriching OPPDAY",
        )

        async def _attach(item: EarningsCallItem) -> None:
            async with semaphore:
                try:
                    item.detail = await self._fetch_detail(fetcher, item.id, language)
                except Exception as exc:  # noqa: BLE001 - tolerate one bad detail, keep the row
                    logger.warning(
                        f"Failed to enrich earnings-call item id={item.id} ({item.symbol}): {exc}"
                    )
                finally:
                    reporter.update(1)

        await asyncio.gather(*(_attach(item) for item in items))
        reporter.close()

    async def _fetch_filter(self, name: str, language: Language = "en") -> list[FilterOption]:
        """Fetch a single filter endpoint and validate it into a list of options."""
        language = normalize_language(language)
        endpoint = SET_EARNINGS_CALL_FILTER_ENDPOINT.format(name=name)
        url = f"{self.base_url}{endpoint}"
        headers = _build_earnings_call_headers(language)

        async with AsyncDataFetcher(config=self.config) as fetcher:
            data = await fetcher.fetch_json(url, headers=headers)
            return validate_list_or_raise(
                FilterOption, data, context=f"set earnings-call filter/{name}"
            )

    async def fetch_filter_types(self, language: Language = "en") -> list[FilterOption]:
        """Fetch presentation types (e.g. 1 = Earnings Call/OPPDAY)."""
        return await self._fetch_filter("types", language)

    async def fetch_filter_years(self, language: Language = "en") -> list[FilterOption]:
        """Fetch quarter/year filter options (ids usable as ``quarter_id``)."""
        return await self._fetch_filter("years", language)

    async def fetch_filter_industries(self, language: Language = "en") -> list[FilterOption]:
        """Fetch industry filter options (string codes usable as ``industries_id``)."""
        return await self._fetch_filter("industries", language)

    async def fetch_filter_markets(self, language: Language = "en") -> list[FilterOption]:
        """Fetch market filter options (e.g. SET / mai / LiVEx)."""
        return await self._fetch_filter("markets", language)

    async def fetch_filter_themes(self, language: Language = "en") -> list[FilterOption]:
        """Fetch theme filter options (e.g. SET50, SET100, SETESG)."""
        return await self._fetch_filter("themes", language)

    async def fetch_filter_trusts(self, language: Language = "en") -> list[FilterOption]:
        """Fetch trust/security-kind filter options (e.g. Common Stock, REIT)."""
        return await self._fetch_filter("trusts", language)

    async def fetch_filter_stages(self, language: Language = "en") -> list[FilterOption]:
        """Fetch stage filter options (e.g. Upcoming, Live, Video)."""
        return await self._fetch_filter("stages", language)


async def get_earnings_calls(
    *,
    type_id: PresentationType = 1,
    quarter_id: int = 0,
    keyword: str | None = None,
    industries_id: str | None = None,
    composition_id: int | None = None,
    start: int = 1,
    page_size: int = 12,
    language: Language = "en",
    enrich: bool = False,
    config: FetcherConfig | None = None,
) -> EarningsCallResponse:
    """Convenience: fetch one page of earnings-call entries.

    Thin wrapper over :meth:`EarningsCallService.fetch_earnings_calls`.

    Args:
        type_id: Presentation type (1 = Earnings Call/OPPDAY, 2 = Digital Roadshow,
            3 = C-Sign Public Presentation).
        quarter_id: Quarter filter id (0 = all quarters); see ``fetch_filter_years()``.
        keyword: Free-text symbol/company search (normalized via ``normalize_symbol``).
        industries_id: Industry filter code (see ``fetch_filter_industries()``).
        composition_id: Optional composition/theme filter id.
        start: 1-based page number.
        page_size: Items per page.
        language: ``"en"`` or ``"th"``.
        enrich: If True, also fetch per-item detail concurrently (bounded).
        config: Optional :class:`FetcherConfig` override.

    Returns:
        An :class:`EarningsCallResponse` for the requested page.

    Raises:
        ValueError: If ``start``/``page_size`` are out of range or ``language`` is invalid.
        ResponseParseError: If the response cannot be decoded.

    Example:
        >>> from settfex.services.set import get_earnings_calls
        >>> response = await get_earnings_calls(keyword="HANN")
        >>> for item in response.items:
        ...     print(item.symbol, item.company_name_clean, item.youtube_url)
    """
    service = EarningsCallService(config=config)
    return await service.fetch_earnings_calls(
        type_id=type_id,
        quarter_id=quarter_id,
        keyword=keyword,
        industries_id=industries_id,
        composition_id=composition_id,
        start=start,
        page_size=page_size,
        language=language,
        enrich=enrich,
    )


async def get_earnings_calls_dataframe(
    *,
    type_id: PresentationType = 1,
    quarter_id: int = 0,
    keyword: str | None = None,
    industries_id: str | None = None,
    composition_id: int | None = None,
    start: int = 1,
    page_size: int = 12,
    language: Language = "en",
    columns: list[str] | None = None,
    config: FetcherConfig | None = None,
) -> "pd.DataFrame":
    """Convenience: fetch one page and return it as a pandas DataFrame.

    Defaults to the five columns ``stock_name, company_name, earnings_call_date,
    video_clip_time, youtube_url``. For the full calendar, use
    :meth:`EarningsCallService.fetch_all_earnings_calls` then :meth:`to_dataframe`.

    Requires pandas (``pip install settfex[dataframe]``).

    Args:
        type_id: Presentation type (1 = Earnings Call/OPPDAY, 2 = Digital Roadshow,
            3 = C-Sign Public Presentation).
        quarter_id: Quarter filter id (0 = all quarters).
        keyword: Free-text symbol/company search.
        industries_id: Industry filter code (see ``fetch_filter_industries()``).
        composition_id: Optional composition/theme filter id.
        start: 1-based page number.
        page_size: Items per page.
        language: ``"en"`` or ``"th"``.
        columns: Column subset/order for the DataFrame (defaults to the five above).
        config: Optional :class:`FetcherConfig` override.

    Returns:
        A :class:`pandas.DataFrame` with one row per earnings-call entry.

    Raises:
        ImportError: If pandas is not installed (``pip install settfex[dataframe]``).
        ValueError: If a requested column is unknown, or ``start``/``page_size``/``language``
            is invalid.

    Example:
        >>> from settfex.services.set import get_earnings_calls_dataframe
        >>> df = await get_earnings_calls_dataframe()
        >>> list(df.columns)
        ['stock_name', 'company_name', 'earnings_call_date', 'video_clip_time', 'youtube_url']
    """
    response = await get_earnings_calls(
        type_id=type_id,
        quarter_id=quarter_id,
        keyword=keyword,
        industries_id=industries_id,
        composition_id=composition_id,
        start=start,
        page_size=page_size,
        language=language,
        config=config,
    )
    return response.to_dataframe(columns=columns)


async def get_earnings_call_detail(
    item_id: int,
    language: Language = "en",
    config: FetcherConfig | None = None,
) -> EarningsCallDetail:
    """Convenience: fetch a single OPPDAY presentation's detail by id.

    The ``id`` is the number in an opportunity-day ``/vdo/{id}`` URL.

    Args:
        item_id: The presentation id (the number in a ``/vdo/{id}`` URL).
        language: ``"en"`` or ``"th"``.
        config: Optional :class:`FetcherConfig` override.

    Returns:
        An :class:`EarningsCallDetail` for the presentation.

    Raises:
        ValueError: If ``language`` is invalid.
        ResponseParseError: If the response cannot be decoded.

    Example:
        >>> from settfex.services.set import get_earnings_call_detail
        >>> detail = await get_earnings_call_detail(6319)
        >>> print(detail.symbol, detail.round_name, detail.youtube_url)
    """
    service = EarningsCallService(config=config)
    return await service.fetch_earnings_call_detail(item_id, language=language)


async def get_all_earnings_calls(
    *,
    type_id: PresentationType = 1,
    quarter_id: int = 0,
    keyword: str | None = None,
    industries_id: str | None = None,
    composition_id: int | None = None,
    start: int = 1,
    page_size: int = 200,
    language: Language = "en",
    enrich: bool = False,
    max_records: int | None = None,
    max_pages: int | None = None,
    max_concurrency: int = 5,
    progress: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
    config: FetcherConfig | None = None,
) -> EarningsCallResponse:
    """Convenience: fetch the **entire** OPPDAY calendar, concurrently.

    Mirrors :meth:`EarningsCallService.fetch_all_earnings_calls` as a one-liner. Pass
    ``progress=True`` for a tqdm bar (``pip install "settfex[progress]"``), or a
    ``progress_callback(done, total)`` for a dependency-free hook. Bound the crawl with
    ``max_records`` / ``max_pages`` / ``max_concurrency``.

    Args:
        type_id: Presentation type (1 = Earnings Call/OPPDAY, 2 = Digital Roadshow,
            3 = C-Sign Public Presentation).
        quarter_id: Quarter filter id (0 = all quarters).
        keyword: Free-text symbol/company search.
        industries_id: Industry filter code (see ``fetch_filter_industries()``).
        composition_id: Optional composition/theme filter id.
        start: 1-based starting page number.
        page_size: Records per request (default 200; larger = fewer requests).
        language: ``"en"`` or ``"th"``.
        enrich: If True, also fetch per-item detail concurrently (bounded).
        max_records: Stop once this many items are collected (then truncate).
        max_pages: Fetch at most this many pages.
        max_concurrency: Max simultaneous page requests (politeness bound, default 5).
        progress: Show a ``tqdm`` progress bar (needs ``settfex[progress]``).
        progress_callback: Optional ``(done, total)`` hook, fired as pages complete.
        config: Optional :class:`FetcherConfig` override.

    Returns:
        An :class:`EarningsCallResponse` with all collected items (in page order) and the API's
        ``no_records`` total.

    Raises:
        ValueError: If ``max_records``/``max_pages`` are set but < 1, ``max_concurrency`` < 1,
            or ``language`` is invalid.

    Example:
        >>> from settfex.services.set import get_all_earnings_calls
        >>> response = await get_all_earnings_calls(progress=True)
        >>> df = response.to_dataframe()
    """
    service = EarningsCallService(config=config)
    return await service.fetch_all_earnings_calls(
        type_id=type_id,
        quarter_id=quarter_id,
        keyword=keyword,
        industries_id=industries_id,
        composition_id=composition_id,
        start=start,
        page_size=page_size,
        language=language,
        enrich=enrich,
        max_records=max_records,
        max_pages=max_pages,
        max_concurrency=max_concurrency,
        progress=progress,
        progress_callback=progress_callback,
    )


async def fetch_transcripts(
    items: list[EarningsCallItem],
    *,
    languages: Sequence[str] = ("th",),
    max_concurrency: int = 3,
    progress: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
    proxies: dict[str, str] | None = None,
) -> list[EarningsCallItem]:
    """Fetch the YouTube transcript for each item that has a video, in place.

    For every item with a ``youtube_video_id``, fetches the transcript (Thai by default) and
    stores it on ``item.transcript`` as a plain string (raw text, ready for AI/LLM use); items
    without a video are left ``transcript=None``. Fetches run with bounded concurrency and are
    individually tolerant — a blocked/missing transcript logs a warning and leaves that item
    ``None`` rather than failing the batch.

    YouTube rate-limits / IP-blocks aggressively, so the default concurrency is low (3) and this
    is meant for a **filtered** set of items, not the full archive. Pass ``proxies`` if the host
    IP is blocked. Requires the ``transcript`` extra (``pip install "settfex[transcript]"``).

    Args:
        items: The items to annotate (e.g. ``response.items``).
        languages: Transcript language priority (default Thai).
        max_concurrency: Max simultaneous YouTube requests.
        progress: Show a tqdm bar (needs ``settfex[progress]``).
        progress_callback: Optional ``(done, total)`` hook.
        proxies: Optional ``{"http": url, "https": url}`` proxy mapping.

    Returns:
        The same ``items`` list (for chaining), with ``transcript`` populated where available.

    Example:
        >>> from settfex.services.set import get_earnings_calls, fetch_transcripts
        >>> resp = await get_earnings_calls(keyword="SCB")
        >>> await fetch_transcripts(resp.items)
        >>> [it.transcript[:40] for it in resp.items if it.transcript]
    """
    targets = [item for item in items if item.youtube_video_id]
    if not targets:
        return items

    semaphore = asyncio.Semaphore(max_concurrency)
    reporter = _ProgressReporter(
        total=len(targets), show_bar=progress, callback=progress_callback, desc="Transcripts"
    )

    async def _attach(item: EarningsCallItem) -> None:
        async with semaphore:
            try:
                video_id = item.youtube_video_id
                if video_id is not None:
                    item.transcript = await fetch_youtube_transcript(
                        video_id, languages=languages, proxies=proxies
                    )
            except Exception as exc:  # noqa: BLE001 - tolerate one bad transcript, keep the batch
                logger.warning(
                    f"Failed to fetch transcript for item id={item.id} ({item.symbol}): {exc}"
                )
            finally:
                reporter.update(1)

    await asyncio.gather(*(_attach(item) for item in targets))
    reporter.close()
    return items


async def get_earnings_call_transcript(
    item_id: int,
    *,
    languages: Sequence[str] = ("th",),
    proxies: dict[str, str] | None = None,
    config: FetcherConfig | None = None,
) -> str | None:
    """Convenience: fetch one OPPDAY presentation's YouTube transcript by id.

    Resolves the presentation's video via the detail endpoint, then fetches its transcript (Thai
    by default). Returns ``None`` if the presentation has no YouTube video or no matching
    captions. Requires the ``transcript`` extra (``pip install "settfex[transcript]"``).

    Args:
        item_id: The presentation id (the number in a ``/vdo/{id}`` URL).
        languages: Transcript language priority (default Thai, ``("th",)``).
        proxies: Optional ``{"http": url, "https": url}`` proxy mapping.
        config: Optional :class:`FetcherConfig` override.

    Returns:
        The transcript as a plain string (raw text, ready for AI/LLM use), or ``None`` if the
        presentation has no YouTube video or no matching captions.

    Raises:
        ImportError: If the ``transcript`` extra is not installed
            (``pip install "settfex[transcript]"``).

    Example:
        >>> from settfex.services.set import get_earnings_call_transcript
        >>> text = await get_earnings_call_transcript(6319)   # SCB, YE/2021
        >>> print((text or "")[:200])
    """
    detail = await get_earnings_call_detail(item_id, config=config)
    if not detail.youtube_video_id:
        return None
    return await fetch_youtube_transcript(
        detail.youtube_video_id, languages=languages, proxies=proxies
    )
