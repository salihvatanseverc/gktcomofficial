"""Stock-specific utilities and services for SET stock operations."""

from settfex.services.set.stock.analyst_consensus import (
    AnalystConsensus,
    AnalystConsensusRow,
    AnalystConsensusService,
    ConsensusOverall,
    ConsensusOverallResponse,
    ConsensusStatistic,
    get_analyst_consensus,
    get_analyst_consensus_dataframes,
    get_consensus_overall,
)
from settfex.services.set.stock.board_of_director import (
    BoardOfDirectorService,
    Director,
    get_board_of_directors,
)
from settfex.services.set.stock.chart_quotation import (
    ChartQuotation,
    ChartQuotationService,
    Intermission,
    Quotation,
    get_chart_quotation,
    get_latest_price,
)
from settfex.services.set.stock.corporate_action import (
    CorporateAction,
    CorporateActionService,
    get_corporate_actions,
)
from settfex.services.set.stock.dr_indicative_price import (
    DrIndicativePrice,
    DrIndicativePriceService,
    DrIndicativeQuotation,
    TradingViewQuote,
    get_dr_indicative_price,
)
from settfex.services.set.stock.financial import (
    Account,
    BalanceSheet,
    CashFlow,
    FinancialService,
    FinancialStatement,
    IncomeStatement,
    get_balance_sheet,
    get_cash_flow,
    get_income_statement,
)
from settfex.services.set.stock.highlight_data import (
    StockHighlightData,
    StockHighlightDataService,
    get_highlight_data,
)
from settfex.services.set.stock.info import (
    Inav,
    StockInfo,
    StockInfoService,
    get_stock_info,
    parse_signs,
)
from settfex.services.set.stock.latest_historical_trading import (
    LatestHistoricalTrading,
    LatestHistoricalTradingService,
    get_latest_historical_trading,
)
from settfex.services.set.stock.nvdr_holder import (
    NVDRHolderData,
    NVDRHolderService,
    get_nvdr_holder_data,
)
from settfex.services.set.stock.price_performance import (
    PricePerformanceData,
    PricePerformanceService,
    get_price_performance,
)
from settfex.services.set.stock.profile_company import (
    CompanyProfile,
    CompanyProfileService,
    get_company_profile,
)
from settfex.services.set.stock.profile_dr import (
    DrProfile,
    DrProfileService,
    IndicativePriceExpression,
    get_dr_profile,
    parse_indicative_price_expression,
)
from settfex.services.set.stock.profile_stock import (
    StockProfile,
    StockProfileService,
    get_profile,
)
from settfex.services.set.stock.shareholder import (
    ShareholderData,
    ShareholderService,
    get_shareholder_data,
)
from settfex.services.set.stock.stock import Stock
from settfex.services.set.stock.trading_stat import (
    TradingStat,
    TradingStatService,
    get_trading_stats,
)
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol

__all__ = [
    # Main Stock Class
    "Stock",
    # Analyst Consensus (IAA) Service
    "AnalystConsensusService",
    "AnalystConsensus",
    "AnalystConsensusRow",
    "ConsensusStatistic",
    "ConsensusOverall",
    "ConsensusOverallResponse",
    "get_analyst_consensus",
    "get_analyst_consensus_dataframes",
    "get_consensus_overall",
    # Latest Historical Trading Service
    "LatestHistoricalTradingService",
    "LatestHistoricalTrading",
    "get_latest_historical_trading",
    # Chart Quotation Service
    "ChartQuotationService",
    "ChartQuotation",
    "Intermission",
    "Quotation",
    "get_chart_quotation",
    "get_latest_price",
    # Utilities
    "Language",
    "normalize_symbol",
    "normalize_language",
    # Highlight Data Service
    "StockHighlightDataService",
    "StockHighlightData",
    "get_highlight_data",
    # Stock Info Service (live quote block + trading signs)
    "StockInfoService",
    "StockInfo",
    "Inav",
    "get_stock_info",
    "parse_signs",
    # Profile Service
    "StockProfileService",
    "StockProfile",
    "get_profile",
    # Company Profile Service
    "CompanyProfileService",
    "CompanyProfile",
    "get_company_profile",
    # DR Profile Service
    "DrProfileService",
    "DrProfile",
    "IndicativePriceExpression",
    "parse_indicative_price_expression",
    "get_dr_profile",
    # DR Indicative Price Service (TradingView)
    "DrIndicativePriceService",
    "DrIndicativePrice",
    "DrIndicativeQuotation",
    "TradingViewQuote",
    "get_dr_indicative_price",
    # Corporate Action Service
    "CorporateActionService",
    "CorporateAction",
    "get_corporate_actions",
    # Shareholder Service
    "ShareholderService",
    "ShareholderData",
    "get_shareholder_data",
    # NVDR Holder Service
    "NVDRHolderService",
    "NVDRHolderData",
    "get_nvdr_holder_data",
    # Board of Director Service
    "BoardOfDirectorService",
    "Director",
    "get_board_of_directors",
    # Trading Statistics Service
    "TradingStatService",
    "TradingStat",
    "get_trading_stats",
    # Price Performance Service
    "PricePerformanceService",
    "PricePerformanceData",
    "get_price_performance",
    # Financial Service
    "FinancialService",
    "FinancialStatement",
    "Account",
    "BalanceSheet",
    "IncomeStatement",
    "CashFlow",
    "get_balance_sheet",
    "get_income_statement",
    "get_cash_flow",
]
