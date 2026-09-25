"""settfex - Stock Exchange of Thailand (SET) and Thailand Futures Exchange (TFEX) Data Library.

A modern Python library for fetching real-time and historical data from Thai financial markets.

Designed for both humans and AI/LLM agents. Every service exposes three tiers:

- ``get_*()`` — flat, one-call convenience functions (e.g. ``get_highlight_data("CPALL")``);
  the intended entry point for LLM tool-calling.
- ``fetch_*()`` — return validated Pydantic models, giving structured, schema-checked output
  that lowers hallucination risk for agents.
- ``fetch_*_raw()`` — return the raw API ``dict`` as an escape hatch.

All I/O is async. Language arguments accept ``en``/``th`` (plus ``english``/``thai`` aliases);
symbols are auto-normalized (uppercased).

Completeness signals — check these; a partial failure does **not** raise:

- ``docs.accounting.has_losses`` — the one flag to branch on
- ``docs.accounting.failed_codes`` — a whole sub-search that never ran
- ``docs.accounting.degraded_sections`` — a section that fell back to truncated rows
- ``files.is_complete`` / ``files.failed`` — a partial download batch

Exceptions come in two families, and the split is what you act on:

- ``settfex.exceptions.FetchError`` — transport, HTTP or parse; a retry may help
- ``ValueError`` subclasses — ``InvalidSymbolError``, ``CompanyNotFoundError``,
  ``AmbiguousCompanyError``; the request succeeded and the input was wrong, so a retry never helps

Looking an SEC issuer up by company **name** needs ``allow_name_match=True``. Without it a lone
unflagged match raises ``AmbiguousCompanyError`` carrying the candidate, because the site did not
resolve the query as an identifier and the row may be an unrelated company.

If you persisted results from a version before 0.24.0, see the data completeness advisory in
``CHANGELOG.md`` — earlier versions could return incomplete or wrong data with no error.

Usage:
    >>> import asyncio
    >>> from settfex.services.set import SetIndex, Stock, get_stock_list
    >>>
    >>> async def main():
    ...     # Fetch stock list (index memberships included by default)
    ...     stock_list = await get_stock_list()
    ...     print(f"Total stocks: {stock_list.count}")
    ...
    ...     # Fetch stock data
    ...     stock = Stock("CPALL")
    ...     highlight = await stock.get_highlight_data()
    ...     print(f"Market Cap: {highlight.market_cap:,.0f}")
    ...
    ...     # Fetch market index data
    ...     index = SetIndex("SET50")
    ...     info = await index.get_info()
    ...     print(f"SET50: {info.last} ({info.percent_change:+.2f}%)")
    >>>
    >>> asyncio.run(main())
"""

__version__ = "0.24.0"
__author__ = "batt"
__license__ = "MIT"

# Public API exports - import commonly used classes/functions
from settfex.exceptions import (
    FetchError,
    InvalidDateError,
    InvalidLanguageError,
    InvalidSymbolError,
    StaleDataError,
    SymbolNotFoundError,
)

# SEC IDISC document services (market.sec.or.th) — disclosure document retrieval
from settfex.services.sec import (
    DocumentCategory,
    DownloadedFile,
    SecCompany,
    SecDocument,
    SecDocumentList,
    download_sec_document,
    download_sec_documents,
    get_sec_documents,
    resolve_company,
)
from settfex.services.set import (
    AnalystConsensus,
    AssetType,
    CompanyProfile,
    ConsensusOverallResponse,
    DrIndicativePrice,
    DrProfile,
    HolidayCalendar,
    IndexCompositionResponse,
    IndexInfo,
    IndexListResponse,
    Language,
    NewsSearchResponse,
    SetIndex,
    Stock,
    StockHighlightData,
    StockInfo,
    StockListResponse,
    StockProfile,
    get_analyst_consensus,
    get_analyst_consensus_dataframes,
    get_company_profile,
    get_consensus_overall,
    get_dr_indicative_price,
    get_dr_profile,
    get_highlight_data,
    get_holidays,
    get_index_composition,
    get_index_info,
    get_index_list,
    get_news,
    get_profile,
    get_stock_info,
    get_stock_list,
)

# ThaiBMA government bond yield curve services (www.thaibma.or.th) — Thai fixed income
from settfex.services.thaibma import (
    BondQuote,
    CurvePoint,
    ThaiBMA,
    YieldCurve,
    YieldCurveAvailability,
    YieldCurveHistory,
    get_bond_yield_history,
    get_government_yield_curve,
    get_yield_curve_availability,
    get_yield_curve_history,
)

# Utility exports
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.logging import setup_logger

__all__ = [
    # Version info
    "__version__",
    "__author__",
    "__license__",
    # SET Services - Most commonly used
    "Stock",
    "SetIndex",
    "get_stock_list",
    "get_stock_info",
    "get_highlight_data",
    "get_profile",
    "get_company_profile",
    "get_index_list",
    "get_index_info",
    "get_index_composition",
    "get_news",
    "get_holidays",
    "get_dr_profile",
    "get_dr_indicative_price",
    "get_analyst_consensus",
    "get_analyst_consensus_dataframes",
    "get_consensus_overall",
    # SEC IDISC document services (market.sec.or.th)
    "SecCompany",
    "get_sec_documents",
    "download_sec_document",
    "download_sec_documents",
    "resolve_company",
    "SecDocument",
    "SecDocumentList",
    "DocumentCategory",
    "DownloadedFile",
    # ThaiBMA government bond yield curve (www.thaibma.or.th)
    "ThaiBMA",
    "get_government_yield_curve",
    "get_yield_curve_history",
    "get_bond_yield_history",
    "get_yield_curve_availability",
    "YieldCurve",
    "YieldCurveHistory",
    "YieldCurveAvailability",
    "CurvePoint",
    "BondQuote",
    # Data Models
    "StockListResponse",
    "StockHighlightData",
    "StockInfo",
    "StockProfile",
    "CompanyProfile",
    "AssetType",
    "DrProfile",
    "DrIndicativePrice",
    "IndexListResponse",
    "IndexInfo",
    "IndexCompositionResponse",
    "NewsSearchResponse",
    "HolidayCalendar",
    "AnalystConsensus",
    "ConsensusOverallResponse",
    # Utilities
    "AsyncDataFetcher",
    "FetcherConfig",
    "setup_logger",
    # Exceptions
    "FetchError",
    "SymbolNotFoundError",
    "StaleDataError",
    "InvalidSymbolError",
    "InvalidLanguageError",
    "InvalidDateError",
    # Types
    "Language",
]
