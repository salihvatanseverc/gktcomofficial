"""Unified SetIndex class for accessing multiple market-index services."""

from datetime import datetime

from loguru import logger

from settfex.services.set.index.chart_quotation import IndexChartQuotationService
from settfex.services.set.index.composition import (
    IndexCompositionResponse,
    IndexCompositionService,
    IndexConstituent,
)
from settfex.services.set.index.info import IndexInfo, IndexInfoService
from settfex.services.set.index.utils import normalize_index_symbol
from settfex.services.set.stock.chart_quotation import ChartQuotation, PeriodType, Quotation
from settfex.services.set.stock.utils import Language
from settfex.utils.data_fetcher import FetcherConfig


class SetIndex:
    """
    Unified class for accessing all market-index services for a single index.

    Analogous to :class:`~settfex.services.set.stock.stock.Stock` for stocks: initialize
    with an index symbol and access its quotation, constituents, and intraday chart data.

    Example:
        >>> index = SetIndex("SET50")
        >>> info = await index.get_info()
        >>> print(f"{info.symbol}: {info.last} ({info.percent_change:+.2f}%)")
        >>>
        >>> composition = await index.get_composition()
        >>> print(f"Constituents: {composition.count}")
    """

    def __init__(
        self,
        symbol: str,
        config: FetcherConfig | None = None,
    ) -> None:
        """
        Initialize SetIndex instance for a specific index symbol.

        Args:
            symbol: Index symbol (e.g., "SET50", "sSET", "SETESG", "AGRO-m"). Casing is
                preserved (the API resolves paths case-insensitively).
            config: Optional fetcher configuration

        Example:
            >>> # Basic usage - SessionManager handles cookies automatically
            >>> index = SetIndex("SET50")
            >>>
            >>> # With custom configuration
            >>> config = FetcherConfig(timeout=60, max_retries=5)
            >>> index = SetIndex("SET50", config=config)
        """
        self.symbol = normalize_index_symbol(symbol)
        self.config = config

        # Initialize service instances (lazy initialization)
        self._info_service: IndexInfoService | None = None
        self._composition_service: IndexCompositionService | None = None
        self._chart_quotation_service: IndexChartQuotationService | None = None

        logger.info(f"SetIndex instance created for symbol '{self.symbol}'")

    @property
    def info_service(self) -> IndexInfoService:
        """Get or create index info service instance."""
        if self._info_service is None:
            self._info_service = IndexInfoService(config=self.config)
        return self._info_service

    async def get_info(self, lang: Language = "en") -> IndexInfo:
        """
        Fetch the quotation for this index (page-header data).

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            IndexInfo with last value, change, OHLC, volume, value, and market status

        Example:
            >>> index = SetIndex("SET50")
            >>> info = await index.get_info()
            >>> print(f"Last: {info.last} | Status: {info.market_status}")
        """
        logger.debug(f"Fetching index info for {self.symbol} (lang={lang})")
        return await self.info_service.fetch_index_info(symbol=self.symbol, lang=lang)

    @property
    def composition_service(self) -> IndexCompositionService:
        """Get or create index composition service instance."""
        if self._composition_service is None:
            self._composition_service = IndexCompositionService(config=self.config)
        return self._composition_service

    async def get_composition(self, lang: Language = "en") -> IndexCompositionResponse:
        """
        Fetch the constituents of this index with per-stock quote rows.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            IndexCompositionResponse with constituent quotes and the index's own quote

        Raises:
            SymbolNotFoundError: HTTP 404 for the whole-market indices 'SET' and 'mai'
                (query a sub-index, sector, or industry instead).
            FetchError: On other HTTP or transport failures.

        Example:
            >>> index = SetIndex("SET50")
            >>> composition = await index.get_composition()
            >>> for c in composition.constituents[:5]:
            ...     print(f"{c.symbol}: {c.last} (bid {c.best_bid} / offer {c.best_offer})")
        """
        logger.debug(f"Fetching index composition for {self.symbol} (lang={lang})")
        return await self.composition_service.fetch_composition(symbol=self.symbol, lang=lang)

    async def get_constituents(self, lang: Language = "en") -> list[IndexConstituent]:
        """
        Fetch just the constituent quote rows of this index.

        Shortcut for ``(await self.get_composition()).constituents``.

        Args:
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of IndexConstituent quote rows

        Example:
            >>> index = SetIndex("SETHD")
            >>> constituents = await index.get_constituents()
            >>> print([c.symbol for c in constituents])
        """
        composition = await self.get_composition(lang=lang)
        return composition.constituents

    @property
    def chart_quotation_service(self) -> IndexChartQuotationService:
        """Get or create index chart quotation service instance."""
        if self._chart_quotation_service is None:
            self._chart_quotation_service = IndexChartQuotationService(config=self.config)
        return self._chart_quotation_service

    async def get_chart_quotation(
        self,
        period: PeriodType = "1D",
        accumulated: bool = False,
    ) -> ChartQuotation:
        """
        Fetch chart quotation data for this index.

        Args:
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)

        Returns:
            ChartQuotation with prior value, intermissions, and quotation list

        Example:
            >>> index = SetIndex("SET50")
            >>> data = await index.get_chart_quotation(period="1D")
            >>> print(f"Prior: {data.prior}, Points: {len(data.quotations)}")
        """
        logger.debug(f"Fetching index chart quotation for {self.symbol} period={period}")
        return await self.chart_quotation_service.fetch_chart_quotation(
            symbol=self.symbol, period=period, accumulated=accumulated
        )

    async def get_latest_price(
        self,
        period: PeriodType = "1D",
        accumulated: bool = False,
        as_of: datetime | None = None,
    ) -> Quotation | None:
        """
        Fetch the latest *traded* index quotation relative to ``as_of``.

        Returns the most recent quotation with a non-null volume at or before ``as_of``
        (default: now in Asia/Bangkok), excluding the pre-populated future/no-trade
        buckets. Returns None if nothing has traded yet.

        Args:
            period: Time period — one of '1D','5D','1M','3M','6M','1Y','3Y','5Y','MAX'
            accumulated: Whether to return accumulated volume/value (default: False)
            as_of: Reference instant; naive values are treated as Asia/Bangkok local time.
                Defaults to now in Asia/Bangkok.

        Returns:
            The latest traded Quotation, or None if nothing has traded by ``as_of``

        Example:
            >>> index = SetIndex("SET50")
            >>> q = await index.get_latest_price()
            >>> if q:
            ...     print(f"{q.local_datetime}: {q.price}")
        """
        logger.debug(f"Fetching latest index value for {self.symbol} period={period}")
        data = await self.get_chart_quotation(period=period, accumulated=accumulated)
        return data.get_latest_quotation(as_of)

    def __repr__(self) -> str:
        """String representation of SetIndex instance."""
        return f"SetIndex(symbol='{self.symbol}')"

    def __str__(self) -> str:
        """String representation of SetIndex instance."""
        return self.symbol
