"""SET News Service - Fetch news/disclosures for all stocks from the SET news search API.

Two live-verified API gotchas (2026-07-19):

- ``fromDate``/``toDate`` accept **dd/MM/yyyy only** (e.g. ``15/07/2026``); ISO ``yyyy-MM-dd``
  is rejected with an opaque HTTP 400. Date filters are therefore converted from
  ``datetime.date``/``datetime`` objects automatically, and date strings are validated
  eagerly, raising :class:`~settfex.exceptions.InvalidDateError` before any request is made.
- ``sourceId`` is not validated by the API: any unrecognized value (including empty) is
  silently ignored and returns news from ALL sources. ``"company"`` is the only verified
  filter value; pass ``source_id=None`` for the explicit all-sources behavior.

Without date filters the API returns the latest-trading-day window only (~150-200 items).
No pagination has been observed (a 17-day window returned 2,804 items in one response), so
keep date windows modest.
"""

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from settfex.exceptions import InvalidDateError, InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_NEWS_SEARCH_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise

# The SET news API accepts dates ONLY as dd/MM/yyyy (ISO yyyy-MM-dd -> HTTP 400).
NEWS_DATE_FORMAT = "%d/%m/%Y"

# Page-specific referer for the news search API (part of the Incapsula bypass).
NEWS_REFERER = "https://www.set.or.th/en/market/news-and-alert/news"


def _format_date_param(value: date | str, param_name: str) -> str:
    """
    Convert/validate a date filter into the API's dd/MM/yyyy wire format.

    ``datetime.date``/``datetime`` objects are formatted directly; strings are parsed and
    re-formatted (canonicalizing e.g. '5/7/2026' to '05/07/2026').

    Args:
        value: A ``datetime.date``/``datetime`` object, or a dd/MM/yyyy string
        param_name: Parameter name used in the error message ('from_date'/'to_date')

    Returns:
        The date as a dd/MM/yyyy string

    Raises:
        InvalidDateError: If a string value is not a valid dd/MM/yyyy date
    """
    if isinstance(value, date):  # datetime subclasses date; one check covers both
        return value.strftime(NEWS_DATE_FORMAT)
    text = value.strip()
    try:
        parsed = datetime.strptime(text, NEWS_DATE_FORMAT)
    except ValueError as exc:
        error_msg = (
            f"Invalid {param_name} '{value}': expected dd/MM/yyyy (e.g. '15/07/2026') or a "
            f"datetime.date/datetime object. Note: the SET news API rejects ISO yyyy-MM-dd "
            f"dates with HTTP 400."
        )
        logger.error(error_msg)
        raise InvalidDateError(error_msg) from exc
    return parsed.strftime(NEWS_DATE_FORMAT)


class NewsItem(BaseModel):
    """Model for a single news/disclosure item from the SET news search API."""

    id: str = Field(
        description="News id (numeric string, e.g. '105467000'; ids differ per language)"
    )
    news_datetime: datetime = Field(
        alias="datetime",
        description="Publication timestamp (timezone-aware, Asia/Bangkok +07:00)",
    )
    symbol: str = Field(description="Security symbol the news belongs to")
    source: str = Field(description="Disclosing source code (usually the same symbol)")
    url: str = Field(description="Full URL of the news detail page on set.or.th")
    headline: str = Field(description="News headline (Thai or English, per requested lang)")
    is_today_news: bool = Field(alias="isTodayNews", description="Whether published today")
    view_clarification: bool | str | None = Field(
        default=None,
        alias="viewClarification",
        description="Trading-alert clarification marker; only null observed as of 2026-07",
    )
    market_alert_type_id: int | str | None = Field(
        default=None,
        alias="marketAlertTypeId",
        description="Market alert type id; only null observed as of 2026-07",
    )
    percent_price_change: float | None = Field(
        default=None,
        alias="percentPriceChange",
        description="Price change tied to a market alert; only null observed as of 2026-07",
    )
    tag: str = Field(
        default="",
        description=(
            "Category tag (observed: '', 'financial-statement', 'nav', 'ca', 'set-releases')"
        ),
    )
    product: str = Field(description="Product code (observed: 'S', 'L', 'U', 'V', 'X', 'TFEX')")
    lang: str = Field(description="Language of this item ('th' or 'en')")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class NewsSearchResponse(BaseModel):
    """Response model for the SET news search API."""

    total_count: int = Field(alias="totalCount", description="Total matches reported by the API")
    news_info_list: list[NewsItem] = Field(
        default_factory=list, alias="newsInfoList", description="News items"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
    )

    @field_validator("news_info_list", mode="before")
    @classmethod
    def _null_to_empty_list(cls, value: Any) -> Any:
        """Treat a ``null`` news list as an empty result set rather than failing validation."""
        return [] if value is None else value

    @property
    def count(self) -> int:
        """Number of items returned (matches ``total_count``; no pagination observed)."""
        return len(self.news_info_list)

    def filter_by_symbol(self, symbol: str) -> list[NewsItem]:
        """
        Filter news items by security symbol (case-insensitive).

        Args:
            symbol: Stock symbol to match (e.g., 'CPALL', 'cpall')

        Returns:
            List of news items for the specified symbol
        """
        target = symbol.strip().upper()
        return [n for n in self.news_info_list if n.symbol.upper() == target]

    def filter_today(self) -> list[NewsItem]:
        """
        Return only items flagged as published today.

        Returns:
            List of news items with ``is_today_news=True``
        """
        return [n for n in self.news_info_list if n.is_today_news]

    def filter_by_tag(self, tag: str) -> list[NewsItem]:
        """
        Filter news items by category tag (case-insensitive exact match).

        Args:
            tag: Category tag (e.g., 'financial-statement', 'nav', 'ca', 'set-releases')

        Returns:
            List of news items with the specified tag
        """
        target = tag.strip().lower()
        return [n for n in self.news_info_list if n.tag.lower() == target]


class NewsService:
    """
    Service for fetching news/disclosures from the SET news search API.

    Market-wide by default: one call returns the latest-trading-day news for ALL stocks
    (company disclosures via ``sourceId=company``). Optional filters narrow the result by
    symbol, date window (dd/MM/yyyy), headline keyword, and source.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the news service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Uses SessionManager for automatic cookie handling
            >>> service = NewsService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"NewsService initialized with base_url={self.base_url}")

    @staticmethod
    def _normalize_inputs(lang: Language, symbol: str | None) -> tuple[Language, str | None]:
        """Normalize the language and (optional) symbol, raising typed errors when invalid."""
        lang = normalize_language(lang)
        if symbol is not None:
            symbol = normalize_symbol(symbol)
            if not symbol:
                error_msg = "Stock symbol cannot be empty; pass symbol=None for all stocks"
                logger.error(error_msg)
                raise InvalidSymbolError(error_msg)
        return lang, symbol

    def _build_url(
        self,
        lang: str,
        symbol: str | None,
        from_date: date | str | None,
        to_date: date | str | None,
        keyword: str | None,
        source_id: str | None,
    ) -> str:
        """Build the news search URL, encoding only the parameters actually provided."""
        params: list[tuple[str, str]] = []
        if source_id:
            if source_id != "company":
                logger.warning(
                    f"sourceId '{source_id}' is not a verified value; the SET API silently "
                    f"ignores unrecognized sourceId values and returns ALL sources"
                )
            params.append(("sourceId", source_id))
        if symbol:
            params.append(("symbol", symbol))
        if from_date is not None:
            params.append(("fromDate", _format_date_param(from_date, "from_date")))
        if to_date is not None:
            params.append(("toDate", _format_date_param(to_date, "to_date")))
        if keyword and keyword.strip():
            params.append(("keyword", keyword.strip()))
        params.append(("lang", lang))
        # safe="/" keeps dd/MM/yyyy dates byte-identical to the live-verified wire format
        # while still percent-encoding free-text keywords (spaces, Thai text).
        query = urlencode(params, safe="/")
        return f"{self.base_url}{SET_NEWS_SEARCH_ENDPOINT}?{query}"

    async def _request_json(self, url: str, symbol: str | None, context: str) -> Any:
        """Fetch ``url`` and return the decoded JSON body, raising typed errors on failure."""
        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Page-specific referer is part of the Incapsula bot-detection bypass
            headers = AsyncDataFetcher.get_set_api_headers(referer=NEWS_REFERER)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch news (symbol={symbol or 'all'}): HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            return decode_json(response.text, context=context)

    async def fetch_news(
        self,
        lang: Language = "en",
        symbol: str | None = None,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        keyword: str | None = None,
        source_id: str | None = "company",
    ) -> NewsSearchResponse:
        """
        Fetch news/disclosures — market-wide (all stocks) by default.

        Without date filters the API returns the latest-trading-day window only. No
        pagination has been observed; keep date windows modest (a 17-day window already
        returns ~2,800 items).

        Args:
            lang: Language for headlines ('en' or 'th', default: 'en')
            symbol: Optional stock symbol filter (e.g., "CPALL"); None = all stocks
            from_date: Optional window start — ``datetime.date``/``datetime`` or a
                dd/MM/yyyy string (the API rejects ISO dates with HTTP 400)
            to_date: Optional window end (same formats as ``from_date``)
            keyword: Optional headline keyword filter (e.g., "dividend")
            source_id: News source filter; ``"company"`` (default) = company disclosures,
                ``None`` = all sources. Unrecognized values are silently ignored by the API
                (equivalent to all sources) — a warning is logged for them.

        Returns:
            NewsSearchResponse with total count and the list of news items

        Raises:
            InvalidSymbolError: If an explicitly passed symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            InvalidDateError: If a date string is not a valid dd/MM/yyyy date.
            SymbolNotFoundError: If the API returns HTTP 404.
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = NewsService()
            >>> news = await service.fetch_news()
            >>> print(f"Items: {news.count}")
            >>> for item in news.news_info_list[:5]:
            ...     print(f"{item.news_datetime:%Y-%m-%d %H:%M} {item.symbol}: {item.headline}")
        """
        lang, symbol = self._normalize_inputs(lang, symbol)
        url = self._build_url(
            lang=lang,
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            keyword=keyword,
            source_id=source_id,
        )
        context = f"{symbol} (news-search)" if symbol else "set news-search"

        logger.info(f"Fetching news (lang={lang}) from {url}")

        data = await self._request_json(url, symbol=symbol, context=context)

        # Validate response using Pydantic (context-rich on failure)
        result = validate_or_raise(NewsSearchResponse, data, context=context)

        if result.total_count != result.count:
            logger.warning(
                f"news-search totalCount={result.total_count} != items returned="
                f"{result.count}; the API may have introduced pagination"
            )

        logger.info(f"Successfully fetched {result.count} news items")
        return result

    async def fetch_news_raw(
        self,
        lang: Language = "en",
        symbol: str | None = None,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        keyword: str | None = None,
        source_id: str | None = "company",
    ) -> dict[str, Any]:
        """
        Fetch news as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            lang: Language for headlines ('en' or 'th', default: 'en')
            symbol: Optional stock symbol filter (e.g., "CPALL"); None = all stocks
            from_date: Optional window start — ``datetime.date``/``datetime`` or a
                dd/MM/yyyy string (the API rejects ISO dates with HTTP 400)
            to_date: Optional window end (same formats as ``from_date``)
            keyword: Optional headline keyword filter (e.g., "dividend")
            source_id: News source filter; ``"company"`` (default) = company disclosures,
                ``None`` = all sources

        Returns:
            Raw dictionary from API (keys: ``totalCount``, ``newsInfoList``)

        Raises:
            InvalidSymbolError: If an explicitly passed symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            InvalidDateError: If a date string is not a valid dd/MM/yyyy date.
            SymbolNotFoundError: If the API returns HTTP 404.
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = NewsService()
            >>> raw_data = await service.fetch_news_raw()
            >>> print(raw_data.keys())
        """
        lang, symbol = self._normalize_inputs(lang, symbol)
        url = self._build_url(
            lang=lang,
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            keyword=keyword,
            source_id=source_id,
        )
        context = f"{symbol} (news-search)" if symbol else "set news-search"

        logger.info(f"Fetching raw news (lang={lang}) from {url}")

        data = await self._request_json(url, symbol=symbol, context=context)
        logger.debug(
            f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        )
        return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_news(
    lang: Language = "en",
    symbol: str | None = None,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    keyword: str | None = None,
    source_id: str | None = "company",
    config: FetcherConfig | None = None,
) -> NewsSearchResponse:
    """
    Convenience function to fetch news/disclosures — all stocks by default.

    Args:
        lang: Language for headlines ('en' or 'th', default: 'en')
        symbol: Optional stock symbol filter (e.g., "CPALL"); None = all stocks
        from_date: Optional window start — ``datetime.date``/``datetime`` or a dd/MM/yyyy
            string (the SET news API rejects ISO dates with HTTP 400)
        to_date: Optional window end (same formats as ``from_date``)
        keyword: Optional headline keyword filter (e.g., "dividend")
        source_id: News source filter; ``"company"`` (default) = company disclosures,
            ``None`` = all sources
        config: Optional fetcher configuration

    Returns:
        NewsSearchResponse with total count and the list of news items

    Raises:
        InvalidSymbolError: If an explicitly passed symbol is empty.
        InvalidLanguageError: If the language is not recognized.
        InvalidDateError: If a date string is not a valid dd/MM/yyyy date.
        SymbolNotFoundError: If the API returns HTTP 404.
        FetchError: On other HTTP or transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_news
        >>> # Latest trading day, all stocks, English headlines
        >>> news = await get_news()
        >>> print(f"Items: {news.count}")
        >>>
        >>> # Thai headlines for one symbol over a date window
        >>> from datetime import date
        >>> news = await get_news(
        ...     lang="th", symbol="CPALL", from_date=date(2026, 7, 1), to_date=date(2026, 7, 17)
        ... )
    """
    service = NewsService(config=config)
    return await service.fetch_news(
        lang=lang,
        symbol=symbol,
        from_date=from_date,
        to_date=to_date,
        keyword=keyword,
        source_id=source_id,
    )
