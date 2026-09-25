"""Unified Stock class for accessing multiple stock-related services."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from loguru import logger

from settfex.exceptions import SymbolNotFoundError
from settfex.services.set.asset_type import AssetType
from settfex.services.set.stock.chart_quotation import (
    ChartQuotation,
    ChartQuotationService,
    PeriodType,
    Quotation,
)
from settfex.services.set.stock.highlight_data import (
    StockHighlightData,
    StockHighlightDataService,
)
from settfex.services.set.stock.info import StockInfo, StockInfoService
from settfex.services.set.stock.latest_historical_trading import (
    LatestHistoricalTrading,
    LatestHistoricalTradingService,
)
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import FetcherConfig

if TYPE_CHECKING:
    from settfex.services.set.news import NewsSearchResponse, NewsService
    from settfex.services.set.stock.analyst_consensus import (
        AnalystConsensus,
        AnalystConsensusService,
        ConsensusOverallResponse,
    )
    from settfex.services.set.stock.dr_indicative_price import (
        DrIndicativePrice,
        DrIndicativePriceService,
    )
    from settfex.services.set.stock.profile_dr import DrProfile, DrProfileService
    from settfex.services.set.stock.profile_stock import StockProfile, StockProfileService
    from settfex.services.set.stock.shareholder import ShareholderData, ShareholderService


class Stock:
    """
    Unified class for accessing all stock-related services for a single symbol.

    This class provides a clean interface to fetch various types of data
    for a stock symbol, including highlight data, shareholders, financials, etc.

    Example:
        >>> stock = Stock("CPALL")
        >>> highlight = await stock.get_highlight_data()
        >>> print(f"Market Cap: {highlight.market_cap:,.0f}")
        >>>
        >>> # Future services (planned)
        >>> # shareholders = await stock.get_shareholders()
        >>> # financials = await stock.get_financials()
    """

    def __init__(
        self,
        symbol: str,
        config: FetcherConfig | None = None,
    ) -> None:
        """
        Initialize Stock instance for a specific symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            config: Optional fetcher configuration

        Example:
            >>> # Basic usage - SessionManager handles cookies automatically
            >>> stock = Stock("CPALL")
            >>>
            >>> # With custom configuration
            >>> config = FetcherConfig(timeout=60, max_retries=5)
            >>> stock = Stock("CPALL", config=config)
        """
        self.symbol = normalize_symbol(symbol)
        self.config = config

        # Initialize service instances (lazy initialization for future services)
        self._highlight_data_service: StockHighlightDataService | None = None
        self._chart_quotation_service: ChartQuotationService | None = None
        self._latest_historical_trading_service: LatestHistoricalTradingService | None = None
        self._info_service: StockInfoService | None = None
        self._profile_service: StockProfileService | None = None
        self._shareholder_service: ShareholderService | None = None
        self._news_service: NewsService | None = None
        self._dr_profile_service: DrProfileService | None = None
        self._dr_indicative_price_service: DrIndicativePriceService | None = None
        self._analyst_consensus_service: AnalystConsensusService | None = None

        # Instance caches (static per listing; a new Stock instance starts clean).
        self._asset_type: AssetType | None = None
        # Analyst consensus moves at most once a day, so one fetch per instance is plenty.
        self._analyst_consensus: AnalystConsensus | None = None
        self._dr_profiles: dict[str, DrProfile] = {}
        # Tri-state: None = not probed yet, True/False = known (a DR-profile 404 means
        # "not a DR" — the endpoint 404s for every non-DR symbol, even valid ones).
        self._is_dr: bool | None = None

        logger.info(f"Stock instance created for symbol '{self.symbol}'")

    @property
    def highlight_data_service(self) -> StockHighlightDataService:
        """
        Get or create highlight data service instance.

        Returns:
            StockHighlightDataService instance
        """
        if self._highlight_data_service is None:
            self._highlight_data_service = StockHighlightDataService(config=self.config)
        return self._highlight_data_service

    async def get_highlight_data(self, lang: Language = "en") -> StockHighlightData:
        """
        Fetch highlight data for this stock.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            StockHighlightData with metrics and statistics

        Raises:
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("CPALL")
            >>> data = await stock.get_highlight_data()
            >>> print(f"P/E: {data.pe_ratio}, P/B: {data.pb_ratio}")
            >>>
            >>> # In Thai
            >>> data = await stock.get_highlight_data(lang="th")
        """
        logger.debug(f"Fetching highlight data for {self.symbol} (lang={lang})")
        return await self.highlight_data_service.fetch_highlight_data(symbol=self.symbol, lang=lang)

    @property
    def chart_quotation_service(self) -> ChartQuotationService:
        if self._chart_quotation_service is None:
            self._chart_quotation_service = ChartQuotationService(config=self.config)
        return self._chart_quotation_service

    async def get_chart_quotation(
        self,
        period: PeriodType = "1D",
        accumulated: bool = False,
    ) -> ChartQuotation:
        """
        Fetch chart quotation data for this stock.

        Args:
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)

        Returns:
            ChartQuotation with prior price, intermissions, and quotation list

        Example:
            >>> stock = Stock("CPALL")
            >>> data = await stock.get_chart_quotation(period="1D")
            >>> print(f"Prior: {data.prior}, Points: {len(data.quotations)}")
            >>> for q in data.quotations[:5]:
            ...     print(f"{q.local_datetime}: {q.price}")
        """
        logger.debug(f"Fetching chart quotation for {self.symbol} period={period}")
        return await self.chart_quotation_service.fetch_chart_quotation(
            symbol=self.symbol, period=period, accumulated=accumulated
        )

    async def get_latest_price(
        self,
        period: PeriodType = "1D",
        accumulated: bool = False,
        as_of: datetime | None = None,
        prefer_dr_indicative: bool = True,
    ) -> Quotation | None:
        """
        Fetch the latest price quotation for this symbol relative to ``as_of``.

        For regular symbols this returns the most recent SET quotation with a non-null volume
        at or before ``as_of`` (default: now in Asia/Bangkok), excluding the pre-populated
        future/no-trade buckets — or None if nothing has traded yet.

        For **DR symbols** (e.g. GOOG80) this returns the TradingView **indicative price**
        instead — ``underlying x FX / conversion ratio`` in THB, the same number behind the
        "Indicative Price" menu on SET's DR pages. It keeps moving while SET is closed
        (underlying markets trade on their own hours; exchange legs are ~15-min delayed).
        The result is then a :class:`DrIndicativeQuotation` — a ``Quotation`` subclass whose
        ``price`` is the indicative price, whose ``volume``/``value``/``change``/
        ``percent_change`` are ``None`` (it is a fair value, not a SET trade), and whose
        ``.indicative`` field carries the full computation (legs, ratio, delay flags). Any
        failure on the TradingView path falls back gracefully to the SET chart data.

        The DR path is skipped when ``prefer_dr_indicative=False`` or when an explicit
        ``as_of`` is given (TradingView serves only "now" — it cannot answer a historical
        instant); ``period``/``accumulated`` only affect the SET chart path.

        Args:
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)
            as_of: Reference instant; naive values are treated as Asia/Bangkok local time.
                Defaults to now in Asia/Bangkok. Passing a value forces the SET chart path.
            prefer_dr_indicative: Use the TradingView indicative price for DRs (default:
                True). Pass False to always return the SET traded quotation.

        Returns:
            The latest Quotation (a DrIndicativeQuotation for DRs on the indicative path),
            or None if nothing has traded by ``as_of``

        Example:
            >>> stock = Stock("CPALL")
            >>> q = await stock.get_latest_price()
            >>> if q:
            ...     print(f"{q.local_datetime}: {q.price} (vol {q.volume})")
            >>>
            >>> dr = Stock("GOOG80")
            >>> q = await dr.get_latest_price()          # TradingView indicative price
            >>> q = await dr.get_latest_price(prefer_dr_indicative=False)  # SET traded price
        """
        if prefer_dr_indicative and as_of is None:
            quotation = await self._get_dr_indicative_quotation()
            if quotation is not None:
                return quotation

        logger.debug(f"Fetching latest price for {self.symbol} period={period}")
        data = await self.get_chart_quotation(period=period, accumulated=accumulated)
        return data.get_latest_quotation(as_of)

    async def _get_dr_indicative_quotation(self) -> Quotation | None:
        """DR auto-switch for :meth:`get_latest_price` — the only place with fallback logic.

        Returns the TradingView indicative price as a quotation, or ``None`` meaning "use the
        SET chart path": the symbol is a known non-DR (cached after one 404 probe per
        instance), or the indicative computation failed (any error → graceful fallback;
        DR-ness is never cached off a transient failure).
        """
        if self._is_dr is False:
            return None
        try:
            indicative = await self.get_indicative_price()
        except SymbolNotFoundError:
            # get_dr_profile already cached self._is_dr = False for this instance.
            logger.debug(f"{self.symbol} is not a DR; using SET chart data for latest price")
            return None
        except Exception as exc:
            logger.warning(
                f"DR indicative price unavailable for {self.symbol}; "
                f"falling back to SET chart data: {exc}"
            )
            return None
        return indicative.to_quotation()

    @property
    def latest_historical_trading_service(self) -> LatestHistoricalTradingService:
        if self._latest_historical_trading_service is None:
            self._latest_historical_trading_service = LatestHistoricalTradingService(
                config=self.config
            )
        return self._latest_historical_trading_service

    async def get_latest_historical_trading(self) -> LatestHistoricalTrading:
        """
        Fetch latest historical trading data for this stock.

        Returns:
            LatestHistoricalTrading with OHLCV and valuation data

        Example:
            >>> stock = Stock("CPALL")
            >>> data = await stock.get_latest_historical_trading()
            >>> print(f"Close: {data.close}, Change: {data.percent_change}%")
            >>> print(f"Volume: {data.total_volume:,.0f}")
        """
        logger.debug(f"Fetching latest historical trading for {self.symbol}")
        return await self.latest_historical_trading_service.fetch_latest_historical_trading(
            symbol=self.symbol
        )

    @property
    def info_service(self) -> StockInfoService:
        """
        Get or create stock info service instance.

        Returns:
            StockInfoService instance
        """
        if self._info_service is None:
            self._info_service = StockInfoService(config=self.config)
        return self._info_service

    async def get_info(self) -> StockInfo:
        """
        Fetch the live quote block for this symbol (sign, price, depth, reference data).

        Never cached: this is live trading state, unlike the profile-derived accessors.

        Returns:
            StockInfo with sign, price/OHLC, best bid/offer and reference data

        Raises:
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("INGRS")
            >>> info = await stock.get_info()
            >>> print(info.signs, info.last, info.market_status)
            ['SP'] None Suspend
        """
        logger.debug(f"Fetching stock info for {self.symbol}")
        return await self.info_service.fetch_stock_info(symbol=self.symbol)

    async def get_signs(self) -> list[str]:
        """
        Fetch this symbol's active trading signs (``['SP', 'NC']``; ``[]`` when untagged).

        Works for every security type, including warrants, DWs and DRs — the only route in
        this package that does.

        Returns:
            Sign codes in payload order (e.g. ``['SP', 'CB', 'CS']``)

        Raises:
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("GRAND")
            >>> await stock.get_signs()
            ['SP', 'CB', 'CS', 'CC']
        """
        return (await self.get_info()).signs

    async def is_suspended(self) -> bool:
        """
        Whether this symbol currently carries the ``SP`` (trading suspended) sign.

        Returns:
            True if SP is active

        Raises:
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("INGRS")
            >>> await stock.is_suspended()
            True
        """
        return (await self.get_info()).is_suspended

    @property
    def profile_service(self) -> StockProfileService:
        """
        Get or create profile service instance.

        Returns:
            StockProfileService instance
        """
        if self._profile_service is None:
            from settfex.services.set.stock.profile_stock import StockProfileService

            self._profile_service = StockProfileService(config=self.config)
        return self._profile_service

    async def get_profile(self, lang: Language = "en") -> StockProfile:
        """
        Fetch profile data for this stock.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            StockProfile with company and listing information

        Raises:
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("PTT")
            >>> profile = await stock.get_profile()
            >>> print(f"Company: {profile.name}")
            >>> print(f"Sector: {profile.sector_name}")
        """
        logger.debug(f"Fetching profile for {self.symbol} (lang={lang})")
        return await self.profile_service.fetch_profile(symbol=self.symbol, lang=lang)

    async def get_asset_type(self, refresh: bool = False) -> AssetType:
        """
        Classify this symbol's asset type (stock, ETF, DR, DW, warrant, ...).

        Derived from the stock profile's ``securityType`` code and cached on the instance —
        the first call fetches the profile once; later calls are network-free. Note there is
        no ``BOND`` type: bonds do not appear in SET's stock APIs at all.

        Args:
            refresh: Refetch the profile instead of using the cached classification

        Returns:
            AssetType (``AssetType.UNKNOWN`` when SET serves an unrecognized code)

        Raises:
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("GOOG80")
            >>> asset_type = await stock.get_asset_type()
            >>> print(asset_type)                      # 'dr' (StrEnum renders the bare value)
            >>> asset_type is AssetType.DEPOSITARY_RECEIPT
            True
        """
        if self._asset_type is None or refresh:
            profile = await self.get_profile()
            self._asset_type = profile.asset_type
        return self._asset_type

    @property
    def analyst_consensus_service(self) -> AnalystConsensusService:
        """
        Get or create analyst consensus service instance.

        Returns:
            AnalystConsensusService instance
        """
        if self._analyst_consensus_service is None:
            from settfex.services.set.stock.analyst_consensus import AnalystConsensusService

            self._analyst_consensus_service = AnalystConsensusService(config=self.config)
        return self._analyst_consensus_service

    async def get_analyst_consensus(self, refresh: bool = False) -> AnalystConsensus:
        """
        Fetch the IAA analyst-consensus table for this symbol (from settrade.com).

        Cached on the instance — the underlying data changes at most once a day.

        There is deliberately no ``lang`` argument: the endpoint has no language dimension
        (``?lang=`` is ignored) and ``recommend`` is broker-supplied English free text.

        Args:
            refresh: Refetch instead of using the instance cache

        Returns:
            AnalystConsensus with average/median/high/low aggregates, one row per covering
            broker, and each broker's research PDF URL. ``stats_to_dataframe()`` and
            ``to_dataframe()`` give the two pandas views.

        Raises:
            FetchError: If settrade has no consensus record for this symbol — it answers with
                HTTP 500 rather than 404, and does so for DRs, warrants and valid SET common
                stocks with no IAA coverage alike. Also on other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("GULF")
            >>> data = await stock.get_analyst_consensus()
            >>> print(f"{data.count} brokers, median target {data.median.target_price}")
            >>> brokers_df = data.to_dataframe()
        """
        if self._analyst_consensus is not None and not refresh:
            return self._analyst_consensus

        logger.debug(f"Fetching analyst consensus for {self.symbol}")
        consensus = await self.analyst_consensus_service.fetch_analyst_consensus(symbol=self.symbol)
        self._analyst_consensus = consensus
        return consensus

    async def get_consensus_overall(self, lang: Language = "en") -> ConsensusOverallResponse:
        """
        Fetch the buy/hold/sell consensus summary for this symbol (from settrade.com).

        Not cached: the row carries a live ``last_price``.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            ConsensusOverallResponse holding one row for this symbol — or ZERO rows when
            settrade does not know it, which it reports as HTTP 200 with an empty list rather
            than an error. Check ``count``, or use ``.get(symbol)`` which returns None.

        Raises:
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("GULF")
            >>> summary = await stock.get_consensus_overall()
            >>> row = summary.get("GULF")
            >>> print(f"{row.buy} buy / {row.hold} hold / {row.sell} sell")
        """
        logger.debug(f"Fetching consensus summary for {self.symbol} (lang={lang})")
        return await self.analyst_consensus_service.fetch_overall(symbol=self.symbol, lang=lang)

    @property
    def dr_profile_service(self) -> DrProfileService:
        """
        Get or create DR profile service instance.

        Returns:
            DrProfileService instance
        """
        if self._dr_profile_service is None:
            from settfex.services.set.stock.profile_dr import DrProfileService

            self._dr_profile_service = DrProfileService(config=self.config)
        return self._dr_profile_service

    async def get_dr_profile(self, lang: Language = "en", refresh: bool = False) -> DrProfile:
        """
        Fetch the DR (Depositary Receipt) profile for this symbol.

        Includes issuer/underlying details, the conversion ratio, and the TradingView
        "Indicative Price" link. Cached per language on the instance (the ratio and
        expression are static per listing).

        Args:
            lang: Language for response ('en' or 'th', default: 'en')
            refresh: Refetch instead of using the per-language cache

        Returns:
            DrProfile for this symbol

        Raises:
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If this symbol is not a DR — the endpoint answers every
                non-DR symbol (even valid listed ones) with HTTP 404 ``{"message":
                "Invalid DR"}``, so no "did you mean?" suggestion is attached.
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("GOOG80")
            >>> dr = await stock.get_dr_profile()
            >>> print(f"{dr.underlying} @ {dr.underlying_exchange}, ratio {dr.conversion_ratio}")
        """
        lang_key = normalize_language(lang)
        cached = self._dr_profiles.get(lang_key)
        if cached is not None and not refresh:
            return cached

        logger.debug(f"Fetching DR profile for {self.symbol} (lang={lang_key})")
        try:
            profile = await self.dr_profile_service.fetch_dr_profile(
                symbol=self.symbol, lang=lang_key
            )
        except SymbolNotFoundError:
            self._is_dr = False
            raise
        self._is_dr = True
        self._dr_profiles[lang_key] = profile
        return profile

    async def get_tradingview_url(self) -> str | None:
        """
        Get the TradingView "Indicative Price" chart URL for this symbol (DRs only).

        This is the link behind the "Indicative Price" menu on SET's DR pages, e.g.
        ``https://th.tradingview.com/chart/?symbol=NASDAQ%3AGOOG*FX_IDC%3AUSDTHB%2F2000.0``
        for GOOG80.

        Returns:
            The TradingView chart URL, or ``None`` when this symbol is not a DR (other
            failures propagate).

        Example:
            >>> stock = Stock("GOOG80")
            >>> url = await stock.get_tradingview_url()
            >>> print(url)
        """
        try:
            profile = await self.get_dr_profile()
        except SymbolNotFoundError:
            logger.debug(f"{self.symbol} is not a DR; no TradingView indicative URL")
            return None
        return profile.tradingview_url

    @property
    def dr_indicative_price_service(self) -> DrIndicativePriceService:
        """
        Get or create DR indicative price service instance.

        Returns:
            DrIndicativePriceService instance
        """
        if self._dr_indicative_price_service is None:
            from settfex.services.set.stock.dr_indicative_price import DrIndicativePriceService

            self._dr_indicative_price_service = DrIndicativePriceService(config=self.config)
        return self._dr_indicative_price_service

    async def get_indicative_price(self) -> DrIndicativePrice:
        """
        Compute this DR's indicative price (underlying x FX / ratio) from TradingView.

        Fetches the legs of SET's indicative-price expression from TradingView's scanner in
        one batch request and evaluates it. Exchange legs are ~15-minute delayed; the value
        moves while SET is closed, so it routinely diverges from the DR's own last traded
        price on SET.

        Returns:
            DrIndicativePrice with the THB fair value, per-leg quotes, and provenance

        Raises:
            SymbolNotFoundError: If this symbol is not a DR.
            FetchError: When no usable expression exists, a leg quote is missing/null, or on
                HTTP/transport failures.
            ResponseParseError: If a response cannot be parsed.

        Example:
            >>> stock = Stock("GOOG80")
            >>> price = await stock.get_indicative_price()
            >>> print(f"{price.indicative_price:.2f} THB (delayed={price.is_delayed})")
        """
        profile = await self.get_dr_profile()
        logger.debug(f"Computing indicative price for {self.symbol}")
        return await self.dr_indicative_price_service.fetch_indicative_price(
            self.symbol, profile=profile
        )

    @property
    def shareholder_service(self) -> ShareholderService:
        """
        Get or create shareholder service instance.

        Returns:
            ShareholderService instance
        """
        if self._shareholder_service is None:
            from settfex.services.set.stock.shareholder import ShareholderService

            self._shareholder_service = ShareholderService(config=self.config)
        return self._shareholder_service

    async def get_shareholder_data(self, lang: Language = "en") -> ShareholderData:
        """
        Fetch shareholder data for this stock.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            ShareholderData with major shareholders and free float information

        Raises:
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("MINT")
            >>> data = await stock.get_shareholder_data()
            >>> print(f"Total Shareholders: {data.total_shareholder:,}")
            >>> print(f"Free Float: {data.free_float.percent_free_float:.2f}%")
            >>> for sh in data.major_shareholders[:5]:
            ...     print(f"{sh.sequence}. {sh.name}: {sh.percent_of_share:.2f}%")
        """
        logger.debug(f"Fetching shareholder data for {self.symbol} (lang={lang})")
        return await self.shareholder_service.fetch_shareholder_data(symbol=self.symbol, lang=lang)

    @property
    def news_service(self) -> NewsService:
        """
        Get or create news service instance.

        Returns:
            NewsService instance
        """
        if self._news_service is None:
            from settfex.services.set.news import NewsService

            self._news_service = NewsService(config=self.config)
        return self._news_service

    async def get_news(
        self,
        lang: Language = "en",
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        keyword: str | None = None,
    ) -> NewsSearchResponse:
        """
        Fetch company news/disclosures for this stock.

        Args:
            lang: Language for headlines ('en' or 'th', default: 'en')
            from_date: Optional window start — ``datetime.date``/``datetime`` or a
                dd/MM/yyyy string (the SET news API rejects ISO dates); default:
                latest trading day only
            to_date: Optional window end (same formats as ``from_date``)
            keyword: Optional headline keyword filter

        Returns:
            NewsSearchResponse with this stock's news items

        Raises:
            InvalidDateError: If a date string is not a valid dd/MM/yyyy date.
            InvalidLanguageError: If the language is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> stock = Stock("CPALL")
            >>> news = await stock.get_news()
            >>> for item in news.news_info_list[:5]:
            ...     print(f"{item.news_datetime:%Y-%m-%d %H:%M} {item.headline}")
        """
        logger.debug(f"Fetching news for {self.symbol} (lang={lang})")
        return await self.news_service.fetch_news(
            lang=lang,
            symbol=self.symbol,
            from_date=from_date,
            to_date=to_date,
            keyword=keyword,
        )

    # Future service methods (placeholders for documentation)
    # async def get_financials(self, lang: Language = "en") -> FinancialsData:
    #     """Fetch financial statements for this stock."""
    #     pass

    def __repr__(self) -> str:
        """String representation of Stock instance."""
        return f"Stock(symbol='{self.symbol}')"

    def __str__(self) -> str:
        """String representation of Stock instance."""
        return self.symbol
