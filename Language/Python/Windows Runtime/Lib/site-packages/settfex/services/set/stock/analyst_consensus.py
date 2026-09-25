"""SET Analyst Consensus (IAA) Service - broker target prices, forecasts and research PDFs.

Serves the "Analyst Consensus" table behind
``https://www.settrade.com/th/equities/quote/{SYMBOL}/analyst-consensus`` (HTML table id
``tableAnalystConcensus``). That page is a client-rendered Nuxt app - the table is **not** in the
server HTML - so this service calls the JSON endpoints the page's own bundle calls. No HTML
parsing is involved, and the JSON carries full float precision plus the broker/research ids that
the rendered table rounds away.

Two endpoints, kept as two methods:

- ``GET /api/set-fund/consensus/stock/{symbol}/consensus`` -> :meth:`fetch_analyst_consensus`.
  The table: four aggregate rows (average/median/high/low) plus one row per covering broker.
  Both use the SAME wire shape; on the aggregate rows every identity field (``id``, ``symbol``,
  ``brokerName``, ``brokerURL``, ``analystName``, ``recommend``, ``recommendType``,
  ``lastUpdateDate``, ``lastResearchURL``, ``fullResearchURL``, ``lastResearchId``,
  ``fullResearchId``) is null - only the numbers are populated.
- ``GET /api/set-fund/consensus/stock/overall?lang=&symbol=`` -> :meth:`fetch_overall`. The
  buy/hold/sell summary above the table. Omit the symbol and it returns EVERY covered SET stock
  in one response - a market-wide consensus screener.

Host specifics (live-probed 2026-08-16):

- **Bot protection needs BOTH a warmed www.settrade.com cookie jar AND a Referer on
  www.settrade.com.** A warmed session without a Referer is 403; a session warmed on
  www.set.or.th is 403 (Incapsula cookies are per-domain). Cookies come from
  ``SessionManager(warmup_site="settrade")``, auto-detected from the URL; the Referer comes from
  :func:`_build_settrade_headers`. Never bypass either.
- **No language dimension on the table endpoint.** ``?lang=`` is ignored - the th and en
  responses are byte-identical, and ``recommend`` is broker-supplied English free text ("Buy",
  "Outperform Market"). :meth:`fetch_analyst_consensus` therefore takes NO ``lang`` argument, on
  purpose; do not "restore" one. The *overall* endpoint does honour ``lang``.
- **An uncovered symbol is HTTP 500**, not 404 - and "uncovered" includes perfectly valid SET
  symbols (ABICO), DRs (GOOG80) and warrants (JAS-W4). It is therefore mapped to
  :class:`~settfex.exceptions.FetchError`, never ``SymbolNotFoundError``.
- **A covered-but-unrated symbol returns zeros, not nulls.** Low-profile stocks (TCC, MORE,
  PROUD) answer HTTP 200 with ``"consensuses": []`` and every aggregate row filled with ``0.0``.
  Those zeros are kept verbatim; :attr:`AnalystConsensus.has_coverage` is the flag, and the
  service logs a warning. Never treat a 0.0 target price as a real estimate.
- **Every numeric field is nullable on real rows** - ``targetPriceChange``, ``nextYearPe``,
  ``currentYearPbv`` and ``nextYearDiv`` were all observed null on live CPALL rows - and
  ``lastResearchURL`` is null for several covering brokers (no PDF published).
"""

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from settfex.exceptions import FetchError, InvalidSymbolError, raise_for_status
from settfex.services.set.constants import (
    SETTRADE_ANALYST_CONSENSUS_ENDPOINT,
    SETTRADE_BASE_URL,
    SETTRADE_CONSENSUS_OVERALL_ENDPOINT,
    SETTRADE_QUOTE_REFERER,
)
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_or_raise

if TYPE_CHECKING:
    import pandas as pd

StatisticName = Literal["average", "median", "high", "low"]
"""The four aggregate rows settrade publishes above the per-broker table."""

STATISTIC_NAMES: tuple[StatisticName, ...] = ("average", "median", "high", "low")
"""Canonical order - matches the website's row order; used for the statistics DataFrame."""


def _build_settrade_headers(symbol: str) -> dict[str, str]:
    """Build the headers for a www.settrade.com JSON request.

    The base header block (Accept / User-Agent / sec-ch-ua / Sec-Fetch-*) is host-agnostic and
    shared with the SET API - the request really is same-origin from settrade's point of view -
    so this delegates rather than duplicating the UA string. Its job is to own the
    symbol -> Referer mapping and to be the single documented place where the mandatory-Referer
    invariant lives.

    The Referer is **mandatory**: a warmed session that omits it gets an HTTP 403 Incapsula
    challenge (proved by a 2x3 warm-url/referer matrix on 2026-08-16). It only has to point at
    some www.settrade.com page; the symbol's own quote page is used so the request is
    indistinguishable from the page's own XHR.

    Args:
        symbol: Normalized (uppercase) stock symbol, used to build the Referer

    Returns:
        Dictionary of HTTP headers for a settrade API request
    """
    return AsyncDataFetcher.get_set_api_headers(
        referer=SETTRADE_QUOTE_REFERER.format(symbol=symbol)
    )


class AnalystConsensusRow(BaseModel):
    """One row of the analyst-consensus table.

    The SAME wire shape serves both the per-broker rows and the four aggregate rows, which is
    why every field is optional: on an aggregate row every identity field is null, and settrade
    nulls individual forecasts a broker did not publish (observed live on CPALL for
    ``targetPriceChange``, ``nextYearPe``, ``currentYearPbv`` and ``nextYearDiv``).

    The ``current_year_*`` / ``next_year_*`` fields are relative to
    :attr:`AnalystConsensus.current_year` / :attr:`AnalystConsensus.next_year` - the calendar
    years live on the container, so these field names stay stable from year to year.
    """

    # --- Identity: null on every aggregate row ---
    id: int | None = Field(default=None, description="Settrade research row id")
    symbol: str | None = Field(default=None, description="Stock symbol")
    broker_name: str | None = Field(
        default=None, alias="brokerName", description="Covering broker's short name, e.g. 'ASPS'"
    )
    broker_url: str | None = Field(
        default=None, alias="brokerURL", description="Broker's own website URL"
    )
    analyst_name: str | None = Field(
        default=None, alias="analystName", description="Analyst who published the estimate"
    )

    # --- Estimates: EVERY numeric is nullable on real broker rows ---
    current_year_eps: float | None = Field(
        default=None, alias="currentYearEps", description="Forecast EPS for the current year (THB)"
    )
    next_year_eps: float | None = Field(
        default=None, alias="nextYearEps", description="Forecast EPS for next year (THB)"
    )
    current_year_net_profit: float | None = Field(
        default=None,
        alias="currentYearNetProfit",
        description="Forecast net profit for the current year, in MILLION baht "
        "(the site's column header is 'กำไรสุทธิ (ล้านบาท)')",
    )
    next_year_net_profit: float | None = Field(
        default=None,
        alias="nextYearNetProfit",
        description="Forecast net profit for next year, in MILLION baht",
    )
    current_year_pe: float | None = Field(
        default=None, alias="currentYearPe", description="Forecast P/E for the current year"
    )
    next_year_pe: float | None = Field(
        default=None, alias="nextYearPe", description="Forecast P/E for next year"
    )
    current_year_pbv: float | None = Field(
        default=None, alias="currentYearPbv", description="Forecast P/BV for the current year"
    )
    next_year_pbv: float | None = Field(
        default=None, alias="nextYearPbv", description="Forecast P/BV for next year"
    )
    current_year_div: float | None = Field(
        default=None,
        alias="currentYearDiv",
        description="Forecast dividend yield for the current year, in PERCENT "
        "(the site's column header is 'DIV (%)') - a yield, not baht per share",
    )
    next_year_div: float | None = Field(
        default=None,
        alias="nextYearDiv",
        description="Forecast dividend yield for next year, in PERCENT",
    )
    target_price: float | None = Field(
        default=None, alias="targetPrice", description="Target price (THB)"
    )
    target_price_change: float | None = Field(
        default=None,
        alias="targetPriceChange",
        description="Target price minus the broker's PREVIOUS target price (THB). Null or 0.0 "
        "when the broker has not revised it",
    )
    target_price_percent_change: float | None = Field(
        default=None,
        alias="targetPricePercentChange",
        description="target_price_change as a percentage of the broker's previous target price",
    )

    # --- Recommendation: null on every aggregate row ---
    recommend: str | None = Field(
        default=None,
        description="Broker-supplied recommendation, free text and English-only "
        "(e.g. 'Buy', 'Outperform Market')",
    )
    recommend_type: str | None = Field(
        default=None,
        alias="recommendType",
        description="Short recommendation code, e.g. 'B' for Buy",
    )
    last_update_date: datetime | None = Field(
        default=None,
        alias="lastUpdateDate",
        description="When the broker last updated this row (timezone-aware, +07:00)",
    )
    last_research_url: str | None = Field(
        default=None,
        alias="lastResearchURL",
        description="URL of the broker's latest research PDF (null when none is published)",
    )
    full_research_url: str | None = Field(
        default=None,
        alias="fullResearchURL",
        description="URL of the broker's full research PDF (rarely populated)",
    )
    last_research_id: int | None = Field(
        default=None, alias="lastResearchId", description="Settrade id of the latest research"
    )
    full_research_id: int | None = Field(
        default=None, alias="fullResearchId", description="Settrade id of the full research"
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @property
    def research_url(self) -> str | None:
        """Best available research PDF URL - the latest report first, then the full report."""
        return self.last_research_url or self.full_research_url

    @property
    def has_research(self) -> bool:
        """True when this row links a downloadable research PDF."""
        return self.research_url is not None

    @property
    def recommend_group(self) -> Literal["buy", "hold", "sell"] | None:
        """Coarse bucket derived from :attr:`recommend_type`'s leading letter.

        Only ``'B'`` has been live-observed; ``'H'``/``'S'`` are inferred from the buy/hold/sell
        counters on the overall endpoint. Anything unmapped returns ``None`` rather than guessing.
        """
        groups: dict[str, Literal["buy", "hold", "sell"]] = {"B": "buy", "H": "hold", "S": "sell"}
        return groups.get((self.recommend_type or "").strip().upper()[:1])


class ConsensusStatistic(AnalystConsensusRow):
    """An aggregate row (average / median / high / low) - the same shape, plus its label.

    The label is not on the wire: it is the payload KEY. :class:`AnalystConsensus` stamps it
    during validation so an aggregate row still identifies itself when passed around alone or
    dumped to Parquet. ``isinstance(row, ConsensusStatistic)`` is the discriminator - deliberately
    not an inference like "``broker_name`` is None", which would misfire the day settrade
    publishes a broker row with a null broker name.

    **Every column is aggregated INDEPENDENTLY - an aggregate row is not any one broker's row.**
    Verified on GULF (2026-08-16): ``high.target_price`` was 91.0 (from one broker) while
    ``high.target_price_change`` was 12.0 (from a different broker, whose target was 79.0). The
    two never have to be mutually consistent, so never read a ``high``/``low`` row as a single
    analyst's view, and never reconstruct one field from another across a row.

    The change columns compound this: they aggregate over only the brokers who actually revised
    their target (2 of GULF's 16), so ``average.target_price_change`` is NOT
    ``average.target_price`` minus a previous average.
    """

    statistic: StatisticName = Field(
        description="Which aggregate this row is: 'average', 'median', 'high' or 'low'"
    )


class AnalystConsensus(BaseModel):
    """The full analyst-consensus table for one symbol: aggregates plus per-broker rows.

    Use :meth:`stats_to_dataframe` for the four aggregate rows and :meth:`to_dataframe` for the
    per-broker rows (including each broker's research PDF URL).
    """

    symbol: str = Field(
        description="Stock symbol this consensus is for (injected by the service - the payload "
        "itself carries no top-level symbol)"
    )
    current_year: int | None = Field(
        default=None,
        alias="currentYear",
        description="Calendar year the 'current year' columns refer to",
    )
    next_year: int | None = Field(
        default=None, alias="nextYear", description="Calendar year the 'next year' columns refer to"
    )
    target_price_year: int | None = Field(
        default=None, alias="targetPriceYear", description="Year the target prices refer to"
    )
    # Required, no default. A real payload ALWAYS carries the four aggregate rows -- an
    # uncovered symbol gets them zero-filled rather than omitted (see the CLAUDE.md gotcha), so
    # their absence means the body is not a consensus payload at all. `symbol` cannot serve as the
    # guard here because the service injects it rather than reading it from the response.
    average: ConsensusStatistic | None = Field(description="Average across covering brokers")
    median: ConsensusStatistic | None = Field(description="Median across covering brokers")
    high: ConsensusStatistic | None = Field(description="Highest broker estimate")
    low: ConsensusStatistic | None = Field(description="Lowest broker estimate")
    consensuses: list[AnalystConsensusRow] = Field(
        default_factory=list,
        description="One row per covering broker (settrade's wire name, kept verbatim)",
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _label_statistics(cls, data: Any) -> Any:
        """Stamp each aggregate row with its payload key so ConsensusStatistic can carry it."""
        if not isinstance(data, dict):
            return data
        patched = dict(data)
        for name in STATISTIC_NAMES:
            row = patched.get(name)
            if isinstance(row, dict) and "statistic" not in row:
                patched[name] = {**row, "statistic": name}
        return patched

    @field_validator("current_year", "next_year", "target_price_year", mode="before")
    @classmethod
    def _coerce_year(cls, value: Any) -> int | None:
        """Settrade sends years as strings ('2026'); a cosmetic label must never fail a fetch."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning(f"Unparseable consensus year {value!r}; treating as None")
            return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_coverage(self) -> bool:
        """False when no broker covers this symbol.

        Settrade fills the aggregate rows with ``0.0`` rather than nulls for an uncovered
        symbol, so a 0.0 target price is indistinguishable from a real one without this flag.
        It is a computed field so the warning survives ``model_dump()`` into Parquet/JSON.
        """
        return bool(self.consensuses)

    @property
    def count(self) -> int:
        """Number of covering brokers."""
        return len(self.consensuses)

    @property
    def brokers(self) -> list[AnalystConsensusRow]:
        """Readable alias for :attr:`consensuses` - one row per covering broker."""
        return self.consensuses

    @property
    def statistics(self) -> list[ConsensusStatistic]:
        """The aggregate rows present, in website order (average, median, high, low)."""
        return [row for row in (self.average, self.median, self.high, self.low) if row is not None]

    @property
    def broker_names(self) -> list[str]:
        """Short names of the covering brokers, in payload order."""
        return [row.broker_name for row in self.consensuses if row.broker_name]

    @property
    def with_research(self) -> list[AnalystConsensusRow]:
        """Broker rows that link a research PDF."""
        return [row for row in self.consensuses if row.has_research]

    @property
    def research_urls(self) -> list[tuple[str, str]]:
        """``(broker_name, url)`` pairs for every broker row that links a research PDF."""
        return [
            (row.broker_name or "", url)
            for row in self.consensuses
            if (url := row.research_url) is not None
        ]

    @property
    def latest_update(self) -> datetime | None:
        """Most recent ``last_update_date`` across the broker rows (None when unknown)."""
        dates = [row.last_update_date for row in self.consensuses if row.last_update_date]
        return max(dates) if dates else None

    def broker(self, broker_name: str) -> AnalystConsensusRow | None:
        """Look up one broker's row, case-insensitively.

        Args:
            broker_name: Broker short name, e.g. "ASPS" or "asps"

        Returns:
            The broker's row, or None when that broker does not cover this symbol

        Example:
            >>> data = await get_analyst_consensus("GULF")
            >>> row = data.broker("asps")
            >>> print(row.target_price if row else "not covered by ASPS")
        """
        wanted = broker_name.strip().upper()
        return next(
            (row for row in self.consensuses if (row.broker_name or "").upper() == wanted), None
        )

    def _frame_attrs(self) -> dict[str, Any]:
        """Metadata attached to both DataFrames via ``df.attrs``."""
        return {
            "symbol": self.symbol,
            "current_year": self.current_year,
            "next_year": self.next_year,
            "target_price_year": self.target_price_year,
            "has_coverage": self.has_coverage,
        }

    def to_dataframe(self, columns: list[str] | None = None) -> "pd.DataFrame":
        """Render the per-broker rows as a pandas DataFrame (one row per covering broker).

        pandas is an optional dependency, imported lazily so importing this service never
        needs it.

        Args:
            columns: Column names to include, in order. Defaults to broker_name, analyst_name,
                recommend, recommend_type, target_price, target_price_change,
                target_price_percent_change, the eight current/next-year forecast columns,
                last_update_date and research_url. Also selectable: broker_url,
                last_research_url, full_research_url, has_research, recommend_group, id, symbol,
                last_research_id, full_research_id.

        Returns:
            A DataFrame with one row per broker - empty, but with the requested columns, when no
            broker covers the symbol. ``df.attrs`` carries symbol, current_year, next_year,
            target_price_year and has_coverage, because the ``current_year_*`` column names are
            deliberately year-agnostic and stable.

            ``last_update_date`` comes back as a tz-aware ``datetime64`` column (all SET
            timestamps share the +07:00 offset), with ``NaT`` for brokers that have no update
            date — not the ``object`` column an earlier version of this docstring described.
            Resolution differs by pandas major: ``datetime64[ns, +07:00]`` on pandas 2,
            ``datetime64[us, +07:00]`` on pandas 3.

            Missing values in the string columns (``research_url``, ``recommend``, ...) are
            ``None`` on pandas 2 and ``NaN`` on pandas 3, which types those columns as ``str``
            rather than ``object``. Use ``pd.isna(value)`` rather than ``value is None``.

        Raises:
            ImportError: If pandas is not installed.
            ValueError: If an unknown column name is requested.

        Example:
            >>> data = await get_analyst_consensus("GULF")
            >>> df = data.to_dataframe()
            >>> df[["broker_name", "target_price", "research_url"]].head()
        """
        return _build_dataframe(
            list(self.consensuses),
            _BROKER_ACCESSORS,
            columns,
            _DEFAULT_BROKER_COLUMNS,
            self._frame_attrs(),
            "to_dataframe",
        )

    def stats_to_dataframe(self, columns: list[str] | None = None) -> "pd.DataFrame":
        """Render the aggregate rows (average, median, high, low) as a pandas DataFrame.

        Exactly four rows in website order, labelled by the ``statistic`` column. The broker
        identity columns (broker_name / analyst_name / recommend / dates / URLs) are excluded by
        default because settrade sends them as null on every aggregate row.

        Args:
            columns: Column names to include, in order. Defaults to statistic, target_price,
                target_price_change, target_price_percent_change and the eight current/next-year
                forecast columns. The broker-only columns stay selectable (they will be all-null).

        Returns:
            A DataFrame with one row per aggregate. ``df.attrs`` carries the same metadata as
            :meth:`to_dataframe`. Use ``df.set_index("statistic")`` for a label-indexed view.

            When ``has_coverage`` is False these rows are settrade's zero-fill, not estimates.

        Raises:
            ImportError: If pandas is not installed.
            ValueError: If an unknown column name is requested.

        Example:
            >>> data = await get_analyst_consensus("GULF")
            >>> stats_df = data.stats_to_dataframe()
            >>> stats_df.set_index("statistic")["target_price"]
        """
        return _build_dataframe(
            list(self.statistics),
            _STATISTIC_ACCESSORS,
            columns,
            _DEFAULT_STATISTIC_COLUMNS,
            self._frame_attrs(),
            "stats_to_dataframe",
        )


class ConsensusOverall(BaseModel):
    """One row of the buy/hold/sell summary shown above the analyst-consensus table."""

    symbol: str = Field(description="Stock symbol")
    last_price: float | None = Field(
        default=None, alias="lastPrice", description="Last traded price (THB)"
    )
    total_coverage: int | None = Field(
        default=None, alias="totalCoverage", description="Number of brokers covering the symbol"
    )
    buy: int | None = Field(default=None, description="Brokers recommending Buy")
    hold: int | None = Field(default=None, description="Brokers recommending Hold")
    sell: int | None = Field(default=None, description="Brokers recommending Sell")
    recommend_type: str | None = Field(
        default=None,
        alias="recommendType",
        description="Consensus recommendation, e.g. 'buy' (lower-case words here, unlike the "
        "single-letter codes on the table rows)",
    )
    median_target_price: float | None = Field(
        default=None, alias="medianTargetPrice", description="Median target price (THB)"
    )
    average_target_price: float | None = Field(
        default=None, alias="averageTargetPrice", description="Average target price (THB)"
    )
    bullish: float | None = Field(
        default=None, description="Share of covering brokers that are bullish (percent)"
    )
    bearish: float | None = Field(
        default=None, description="Share of covering brokers that are bearish (percent)"
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class ConsensusOverallResponse(BaseModel):
    """Response from the consensus summary endpoint (``marketTime`` + ``overall``).

    Holds one row when fetched for a symbol, every covered SET stock when fetched with no
    symbol, and zero rows when settrade does not know the symbol (it answers HTTP 200 with an
    empty list rather than an error).
    """

    market_time: datetime | None = Field(
        default=None,
        alias="marketTime",
        description="Market timestamp settrade computed the summary at (timezone-aware, +07:00)",
    )
    # Required, with no default. An envelope whose only field defaults to an empty list validates
    # ANY JSON object -- an error payload included -- into a successful-looking empty result, which
    # is the second half of the silent-loss rule in issue #135. Required is not non-empty: a genuine
    # `{...: []}` still validates; only a missing key fails.
    overall: list[ConsensusOverall] = Field(description="Summary rows, one per symbol")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def count(self) -> int:
        """Number of summary rows held."""
        return len(self.overall)

    def get(self, symbol: str) -> ConsensusOverall | None:
        """Look up one symbol's summary row, case-insensitively (None when absent)."""
        wanted = normalize_symbol(symbol)
        return next((row for row in self.overall if row.symbol.upper() == wanted), None)

    def to_dataframe(self, columns: list[str] | None = None) -> "pd.DataFrame":
        """Render the summary rows as a pandas DataFrame (one row per symbol).

        Args:
            columns: Column names to include, in order. Defaults to symbol, last_price,
                total_coverage, buy, hold, sell, recommend_type, median_target_price,
                average_target_price, bullish, bearish.

        Returns:
            A DataFrame with one row per symbol - the whole-market view when the response was
            fetched with no symbol. ``df.attrs["market_time"]`` carries the market timestamp.

        Raises:
            ImportError: If pandas is not installed.
            ValueError: If an unknown column name is requested.

        Example:
            >>> summary = await get_consensus_overall()  # whole market
            >>> df = summary.to_dataframe()
            >>> df.nlargest(10, "total_coverage")
        """
        return _build_dataframe(
            list(self.overall),
            _OVERALL_ACCESSORS,
            columns,
            _DEFAULT_OVERALL_COLUMNS,
            {"market_time": self.market_time},
            "to_dataframe",
        )


# Column name -> accessor. The first block of each dict is the default column set; everything
# after it is opt-in via ``columns=[...]``.
_BROKER_ACCESSORS: dict[str, Callable[[Any], Any]] = {
    "broker_name": lambda r: r.broker_name,
    "analyst_name": lambda r: r.analyst_name,
    "recommend": lambda r: r.recommend,
    "recommend_type": lambda r: r.recommend_type,
    "target_price": lambda r: r.target_price,
    "target_price_change": lambda r: r.target_price_change,
    "target_price_percent_change": lambda r: r.target_price_percent_change,
    "current_year_eps": lambda r: r.current_year_eps,
    "next_year_eps": lambda r: r.next_year_eps,
    "current_year_net_profit": lambda r: r.current_year_net_profit,
    "next_year_net_profit": lambda r: r.next_year_net_profit,
    "current_year_pe": lambda r: r.current_year_pe,
    "next_year_pe": lambda r: r.next_year_pe,
    "current_year_pbv": lambda r: r.current_year_pbv,
    "next_year_pbv": lambda r: r.next_year_pbv,
    "current_year_div": lambda r: r.current_year_div,
    "next_year_div": lambda r: r.next_year_div,
    "last_update_date": lambda r: r.last_update_date,
    "research_url": lambda r: r.research_url,
    # --- opt-in extras ---
    "broker_url": lambda r: r.broker_url,
    "last_research_url": lambda r: r.last_research_url,
    "full_research_url": lambda r: r.full_research_url,
    "has_research": lambda r: r.has_research,
    "recommend_group": lambda r: r.recommend_group,
    "id": lambda r: r.id,
    "symbol": lambda r: r.symbol,
    "last_research_id": lambda r: r.last_research_id,
    "full_research_id": lambda r: r.full_research_id,
}
_DEFAULT_BROKER_COLUMNS: tuple[str, ...] = (
    "broker_name",
    "analyst_name",
    "recommend",
    "recommend_type",
    "target_price",
    "target_price_change",
    "target_price_percent_change",
    "current_year_eps",
    "next_year_eps",
    "current_year_net_profit",
    "next_year_net_profit",
    "current_year_pe",
    "next_year_pe",
    "current_year_pbv",
    "next_year_pbv",
    "current_year_div",
    "next_year_div",
    "last_update_date",
    "research_url",
)

# The aggregate rows carry ONLY numbers - every identity/recommendation/date/URL field is null by
# construction - so those columns are excluded from the default set (still selectable, to prove
# it). ``statistic`` is the label the container stamps onto each aggregate row.
_STATISTIC_ACCESSORS: dict[str, Callable[[Any], Any]] = {
    "statistic": lambda r: r.statistic,
    **_BROKER_ACCESSORS,
}
_DEFAULT_STATISTIC_COLUMNS: tuple[str, ...] = (
    "statistic",
    "target_price",
    "target_price_change",
    "target_price_percent_change",
    "current_year_eps",
    "next_year_eps",
    "current_year_net_profit",
    "next_year_net_profit",
    "current_year_pe",
    "next_year_pe",
    "current_year_pbv",
    "next_year_pbv",
    "current_year_div",
    "next_year_div",
)

_OVERALL_ACCESSORS: dict[str, Callable[[Any], Any]] = {
    "symbol": lambda r: r.symbol,
    "last_price": lambda r: r.last_price,
    "total_coverage": lambda r: r.total_coverage,
    "buy": lambda r: r.buy,
    "hold": lambda r: r.hold,
    "sell": lambda r: r.sell,
    "recommend_type": lambda r: r.recommend_type,
    "median_target_price": lambda r: r.median_target_price,
    "average_target_price": lambda r: r.average_target_price,
    "bullish": lambda r: r.bullish,
    "bearish": lambda r: r.bearish,
}
_DEFAULT_OVERALL_COLUMNS: tuple[str, ...] = tuple(_OVERALL_ACCESSORS)


def _build_dataframe(
    rows: list[Any],
    accessors: dict[str, Callable[[Any], Any]],
    columns: list[str] | None,
    default_columns: tuple[str, ...],
    attrs: dict[str, Any],
    method: str,
) -> "pd.DataFrame":
    """Shared body for the DataFrame methods: lazy pandas import plus column validation."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatched sys.modules
        raise ImportError(
            f"pandas is required for {method}(). Install it with "
            "'pip install settfex[dataframe]' (or 'uv add pandas')."
        ) from exc

    cols = list(columns) if columns is not None else list(default_columns)
    unknown = [col for col in cols if col not in accessors]
    if unknown:
        raise ValueError(
            f"Unknown DataFrame column(s): {unknown}. Available columns: {sorted(accessors)}"
        )

    frame = pd.DataFrame([{col: accessors[col](row) for col in cols} for row in rows], columns=cols)
    # Year labels ride along as metadata so the column NAMES can stay stable year to year
    # (current_year_eps never becomes eps_2027 and breaks every downstream script each January).
    frame.attrs.update(attrs)
    return frame


class AnalystConsensusService:
    """
    Service for fetching IAA analyst-consensus research from settrade.com.

    Cookies come from ``SessionManager(warmup_site="settrade")``, which ``AsyncDataFetcher``
    selects automatically from the www.settrade.com URL. One warmup per process serves every
    symbol (~2-3s cold, ~100ms from the disk cache). ``use_session`` is deliberately left ON -
    unlike the TradingView and SEC services, this host REQUIRES cookies.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the analyst consensus service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)
        """
        self.config = config or FetcherConfig()
        self.base_url = SETTRADE_BASE_URL
        logger.info(f"AnalystConsensusService initialized with base_url={self.base_url}")

    async def _fetch_json(self, url: str, *, symbol: str, context: str) -> Any:
        """GET ``url`` with settrade headers and map its non-200 statuses to typed errors."""
        async with AsyncDataFetcher(config=self.config) as fetcher:
            response = await fetcher.fetch(url, headers=_build_settrade_headers(symbol))

        # AsyncDataFetcher.fetch() retries EXCEPTIONS only, never a bad status - check here.
        if response.status_code != 200:
            if response.status_code == 500:
                # Settrade answers an uncovered symbol with 500 rather than 404, and "uncovered"
                # includes valid SET symbols (ABICO), DRs (GOOG80) and warrants (JAS-W4) - so
                # this is NOT SymbolNotFoundError, whose suggester would produce the absurd
                # "'ABICO' not found - did you mean 'ABICO'?".
                error_msg = (
                    f"No analyst consensus for '{symbol}' (HTTP 500). Settrade answers a symbol "
                    f"it has no consensus record for with a 500 rather than a 404 - DRs, "
                    f"warrants and SET common stocks with no IAA coverage all return 500. A "
                    f"genuine server error is indistinguishable, so retry once before "
                    f"concluding the symbol is uncovered."
                )
                logger.error(error_msg)
                raise FetchError(error_msg, status_code=500, symbol=symbol)
            if response.status_code in (403, 452):
                error_msg = (
                    f"Blocked by settrade bot protection (HTTP {response.status_code}) for "
                    f"'{symbol}' - a warmed www.settrade.com session AND a www.settrade.com "
                    f"Referer are both required"
                )
            else:
                error_msg = (
                    f"Failed to fetch analyst consensus for {symbol}: HTTP {response.status_code}"
                )
            logger.error(error_msg)
            raise_for_status(response.status_code, error_msg, symbol=symbol, suggest=False)

        return decode_json(response.text, context=context)

    async def fetch_analyst_consensus(self, symbol: str) -> AnalystConsensus:
        """
        Fetch the analyst-consensus table for a stock symbol.

        There is deliberately no ``lang`` argument: the endpoint ignores ``?lang=``, the th and
        en payloads are byte-identical, and ``recommend`` is broker-supplied English free text.

        Args:
            symbol: Stock symbol (e.g., "GULF", "CPALL", "gulf" - normalized to uppercase)

        Returns:
            AnalystConsensus with the four aggregate rows, one row per covering broker
            (including each broker's research PDF URL) and the year labels

        Raises:
            InvalidSymbolError: If the symbol is empty.
            FetchError: If settrade has no consensus record for the symbol (reported as HTTP
                500), on bot-protection blocks (403), and on other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = AnalystConsensusService()
            >>> data = await service.fetch_analyst_consensus("GULF")
            >>> print(f"{data.count} brokers, avg target {data.average.target_price}")
            >>> for row in data.brokers[:3]:
            ...     print(row.broker_name, row.target_price, row.research_url)
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        url = f"{self.base_url}{SETTRADE_ANALYST_CONSENSUS_ENDPOINT.format(symbol=symbol)}"
        logger.info(f"Fetching analyst consensus for '{symbol}' from {url}")

        context = f"{symbol} (analyst-consensus)"
        data = await self._fetch_json(url, symbol=symbol, context=context)
        if not isinstance(data, dict):
            raise ResponseParseError(
                f"Expected a JSON object for {context}, got {type(data).__name__}"
            )

        # The payload carries no top-level symbol; inject the one we asked for.
        consensus = validate_or_raise(AnalystConsensus, {**data, "symbol": symbol}, context=context)

        if not consensus.has_coverage:
            logger.warning(
                f"No broker covers {symbol}: settrade returned an empty 'consensuses' list and "
                f"zero-filled average/median/high/low rows. Check has_coverage before using "
                f"those aggregates - the 0.0 values are placeholders, not estimates."
            )
        else:
            logger.info(
                f"Successfully fetched analyst consensus for {symbol}: {consensus.count} "
                f"brokers, years {consensus.current_year}/{consensus.next_year}, "
                f"avg target {consensus.average.target_price if consensus.average else None}"
            )
        return consensus

    async def fetch_analyst_consensus_raw(self, symbol: str) -> dict[str, Any]:
        """
        Fetch the raw analyst-consensus payload as a dictionary.

        Escape hatch for debugging or for fields not yet modelled. Unlike
        :meth:`fetch_analyst_consensus` this returns settrade's payload verbatim, with no
        injected ``symbol`` and no ``has_coverage`` flag.

        Args:
            symbol: Stock symbol (e.g., "GULF", "CPALL")

        Returns:
            Raw response dictionary with currentYear/nextYear/targetPriceYear, the four
            aggregate rows and the ``consensuses`` list

        Raises:
            InvalidSymbolError: If the symbol is empty.
            FetchError: On HTTP or transport failures (see fetch_analyst_consensus).
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = AnalystConsensusService()
            >>> raw = await service.fetch_analyst_consensus_raw("GULF")
            >>> print(raw["consensuses"][0]["brokerName"])
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        url = f"{self.base_url}{SETTRADE_ANALYST_CONSENSUS_ENDPOINT.format(symbol=symbol)}"
        logger.info(f"Fetching raw analyst consensus for '{symbol}' from {url}")

        context = f"{symbol} (analyst-consensus)"
        data = await self._fetch_json(url, symbol=symbol, context=context)
        if not isinstance(data, dict):
            raise ResponseParseError(
                f"Expected a JSON object for {context}, got {type(data).__name__}"
            )
        logger.debug(f"Raw response keys: {list(data.keys())}")
        return data

    async def fetch_overall(
        self, symbol: str | None = None, lang: Language = "en"
    ) -> ConsensusOverallResponse:
        """
        Fetch the buy/hold/sell consensus summary.

        Unlike the table endpoint, this one honours ``lang``. Pass ``symbol=None`` to get the
        summary for EVERY covered SET stock in one request - a market-wide consensus screener.

        Args:
            symbol: Stock symbol to summarize, or None for the whole market (default: None)
            lang: Language code - "en" or "th" (default: "en")

        Returns:
            ConsensusOverallResponse holding one row for a covered symbol, every covered SET
            stock when ``symbol`` is None, and ZERO rows when settrade does not know the symbol
            (it answers HTTP 200 with an empty list rather than an error - check ``count``)

        Raises:
            InvalidSymbolError: If a symbol is given but is blank.
            InvalidLanguageError: If the language code is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = AnalystConsensusService()
            >>> summary = await service.fetch_overall("GULF")
            >>> row = summary.get("GULF")
            >>> print(f"{row.buy} buy / {row.hold} hold / {row.sell} sell")
            >>>
            >>> market = await service.fetch_overall()  # every covered stock
            >>> print(f"{market.count} stocks with analyst coverage")
        """
        data = await self._fetch_overall_payload(symbol=symbol, lang=lang)
        label = symbol if symbol else "the whole market"
        response = validate_or_raise(
            ConsensusOverallResponse, data, context=f"{label} (consensus-overall)"
        )

        if symbol and response.count == 0:
            logger.warning(
                f"No consensus summary for '{normalize_symbol(symbol)}': settrade returned an "
                f"empty 'overall' list under HTTP 200 (unknown symbol, or a listed symbol with "
                f"no analyst coverage)"
            )
        else:
            logger.info(
                f"Successfully fetched consensus summary for {label}: {response.count} row(s)"
            )
        return response

    async def fetch_overall_raw(
        self, symbol: str | None = None, lang: Language = "en"
    ) -> dict[str, Any]:
        """
        Fetch the raw consensus-summary payload as a dictionary.

        Args:
            symbol: Stock symbol to summarize, or None for the whole market (default: None)
            lang: Language code - "en" or "th" (default: "en")

        Returns:
            Raw response dictionary with ``marketTime`` and an ``overall`` list

        Raises:
            InvalidSymbolError: If a symbol is given but is blank.
            InvalidLanguageError: If the language code is not recognized.
            FetchError: On HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = AnalystConsensusService()
            >>> raw = await service.fetch_overall_raw("GULF")
            >>> print(raw["overall"][0]["totalCoverage"])
        """
        data = await self._fetch_overall_payload(symbol=symbol, lang=lang)
        logger.debug(f"Raw response keys: {list(data.keys())}")
        return data

    async def _fetch_overall_payload(self, *, symbol: str | None, lang: Language) -> dict[str, Any]:
        """Shared request path for the two overall methods."""
        lang = normalize_language(lang)

        query = f"?lang={lang}"
        referer_symbol = "SET"
        if symbol is not None:
            symbol = normalize_symbol(symbol)
            if not symbol:
                error_msg = "Stock symbol cannot be empty"
                logger.error(error_msg)
                raise InvalidSymbolError(error_msg)
            query += f"&symbol={symbol}"
            referer_symbol = symbol

        url = f"{self.base_url}{SETTRADE_CONSENSUS_OVERALL_ENDPOINT}{query}"
        logger.info(f"Fetching consensus summary for '{referer_symbol}' from {url}")

        context = f"{referer_symbol} (consensus-overall)"
        data = await self._fetch_json(url, symbol=referer_symbol, context=context)
        if not isinstance(data, dict):
            raise ResponseParseError(
                f"Expected a JSON object for {context}, got {type(data).__name__}"
            )
        return data


# Convenience functions for quick access
async def get_analyst_consensus(
    symbol: str,
    config: FetcherConfig | None = None,
) -> AnalystConsensus:
    """
    Convenience function to fetch a stock's analyst-consensus table.

    Args:
        symbol: Stock symbol (e.g., "GULF", "CPALL", "gulf")
        config: Optional fetcher configuration

    Returns:
        AnalystConsensus with aggregate rows, per-broker rows and research PDF URLs

    Raises:
        InvalidSymbolError: If the symbol is empty.
        FetchError: If settrade has no consensus record for the symbol (HTTP 500) or on other
            HTTP/transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_analyst_consensus
        >>> data = await get_analyst_consensus("GULF")
        >>> stats_df = data.stats_to_dataframe()   # average / median / high / low
        >>> brokers_df = data.to_dataframe()       # one row per broker, with PDF links
    """
    service = AnalystConsensusService(config=config)
    return await service.fetch_analyst_consensus(symbol=symbol)


async def get_consensus_overall(
    symbol: str | None = None,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> ConsensusOverallResponse:
    """
    Convenience function to fetch the buy/hold/sell consensus summary.

    Args:
        symbol: Stock symbol, or None for every covered SET stock (default: None)
        lang: Language code - "en" or "th" (default: "en")
        config: Optional fetcher configuration

    Returns:
        ConsensusOverallResponse - one row per symbol; zero rows when settrade does not know
        the symbol (HTTP 200 with an empty list)

    Raises:
        InvalidSymbolError: If a symbol is given but is blank.
        InvalidLanguageError: If the language code is not recognized.
        FetchError: On HTTP or transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_consensus_overall
        >>> summary = await get_consensus_overall("GULF")
        >>> print(summary.get("GULF").total_coverage)
        >>>
        >>> market = await get_consensus_overall()  # whole-market screener
        >>> market.to_dataframe().nlargest(10, "total_coverage")
    """
    service = AnalystConsensusService(config=config)
    return await service.fetch_overall(symbol=symbol, lang=lang)


async def get_analyst_consensus_dataframes(
    symbol: str,
    config: FetcherConfig | None = None,
) -> tuple["pd.DataFrame", "pd.DataFrame"]:
    """
    Fetch a stock's analyst consensus straight into the two DataFrames.

    Args:
        symbol: Stock symbol (e.g., "GULF", "CPALL")
        config: Optional fetcher configuration

    Returns:
        ``(stats_df, brokers_df)`` - the four aggregate rows first (average/median/high/low),
        then one row per covering broker including ``research_url``

    Raises:
        ImportError: If pandas is not installed ("pip install settfex[dataframe]").
        InvalidSymbolError: If the symbol is empty.
        FetchError: If settrade has no consensus record for the symbol (HTTP 500) or on other
            HTTP/transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_analyst_consensus_dataframes
        >>> stats_df, brokers_df = await get_analyst_consensus_dataframes("GULF")
    """
    data = await get_analyst_consensus(symbol, config=config)
    return data.stats_to_dataframe(), data.to_dataframe()
