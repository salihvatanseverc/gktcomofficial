"""Thai SEC IDISC (market.sec.or.th) document services.

Fetch and download raw disclosure documents — financial-statement Excel packages, Form 56-1,
Form 56-2, Key Financial Ratio, and MD&A — for any SET/mai-listed issuer.
"""

from settfex.services.sec.company import (
    CompanyMatch,
    resolve_company,
    search_companies,
)
from settfex.services.sec.download import (
    DocumentDownloadService,
    DownloadedFile,
    DownloadResult,
    FailedDownload,
    download_sec_document,
    download_sec_documents,
)
from settfex.services.sec.financial_report import (
    CATEGORY_TO_REPORT_TYPE,
    CodeFailure,
    DocumentCategory,
    FinancialReportService,
    ListingAccounting,
    RowTally,
    SecDocument,
    SecDocumentList,
    category_for_section,
    get_sec_documents,
    row_to_document,
)
from settfex.services.sec.sec import SecCompany

__all__ = [
    "CATEGORY_TO_REPORT_TYPE",
    "CodeFailure",
    "CompanyMatch",
    "DocumentCategory",
    "DocumentDownloadService",
    "DownloadResult",
    "DownloadedFile",
    "FailedDownload",
    "FinancialReportService",
    "ListingAccounting",
    "RowTally",
    "SecCompany",
    "SecDocument",
    "SecDocumentList",
    "category_for_section",
    "download_sec_document",
    "download_sec_documents",
    "get_sec_documents",
    "resolve_company",
    "row_to_document",
    "search_companies",
]
