"""Unified SEC facade — one entry point for an issuer's disclosure documents.

>>> sec = SecCompany("CPALL")
>>> docs = await sec.list_documents(types="financial_statement", from_date="01/01/2025")
>>> files = await sec.download_all(docs, dest_dir="./out")
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from loguru import logger

from settfex.exceptions import CompanyNotFoundError
from settfex.services.sec.company import CompanyMatch, resolve_company
from settfex.services.sec.download import (
    DocumentDownloadService,
    DownloadedFile,
    DownloadResult,
)
from settfex.services.sec.financial_report import (
    DocumentCategory,
    FinancialReportService,
    SecDocument,
    SecDocumentList,
)
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import FetcherConfig


class SecCompany:
    """
    Facade over the SEC IDISC services for a single issuer (by symbol or name).

    The company is resolved lazily (and cached) on first use; the listing and download services
    are lazy-initialized. All methods are async.
    """

    def __init__(
        self,
        symbol_or_name: str,
        *,
        lang: Language = "en",
        config: FetcherConfig | None = None,
        allow_name_match: bool = False,
    ) -> None:
        self.query = symbol_or_name.strip()
        self.lang: Language = normalize_language(lang)
        self.config = config
        self.allow_name_match = allow_name_match
        """Accept a single unflagged candidate -- pass True when querying by company name.

        A ticker the SEC does not recognise returns a substring hit on some other company's name,
        which is indistinguishable from a correct name lookup without knowing which was intended.
        """
        self._company: CompanyMatch | None = None
        self._report_service: FinancialReportService | None = None
        self._download_service: DocumentDownloadService | None = None

    async def resolve(self) -> CompanyMatch:
        """Resolve (and cache) the issuer's SEC company record.

        Raises:
            CompanyNotFoundError: The search succeeded and matched no issuer — an **input** error.
                This was a ``SymbolNotFoundError`` until 0.24.0, which is a ``FetchError``
                subclass, so the identical condition was an input error through
                ``get_sec_documents`` and a transport error through here. ``except FetchError``
                caught one and not the other.
            AmbiguousCompanyError: Several issuers matched and the site flagged none as the match,
                or exactly one unflagged issuer matched and ``allow_name_match`` is False. Also an
                input error, and it carries the candidates.
        """
        if self._company is None:
            company = await resolve_company(
                self.query,
                self.lang,
                config=self.config,
                allow_name_match=self.allow_name_match,
            )
            if company is None:
                raise CompanyNotFoundError(f"No SEC issuer matched '{self.query}'")
            self._company = company
            logger.info(
                f"SecCompany '{self.query}' -> {company.company_name} ({company.unique_id})"
            )
        return self._company

    @property
    def report_service(self) -> FinancialReportService:
        """Lazily-constructed listing service."""
        if self._report_service is None:
            self._report_service = FinancialReportService(config=self.config)
        return self._report_service

    @property
    def download_service(self) -> DocumentDownloadService:
        """Lazily-constructed download service."""
        if self._download_service is None:
            self._download_service = DocumentDownloadService(config=self.config)
        return self._download_service

    async def list_documents(
        self,
        *,
        types: list[DocumentCategory | str] | DocumentCategory | str | None = None,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        follow_view_more: bool = True,
    ) -> SecDocumentList:
        """List this issuer's disclosure documents (all 5 categories by default)."""
        company = await self.resolve()
        return await self.report_service.fetch_documents(
            company.unique_id,
            company_name=company.company_name,
            types=types,
            from_date=from_date,
            to_date=to_date,
            lang=self.lang,
            follow_view_more=follow_view_more,
        )

    async def download(
        self,
        target: SecDocument | str,
        *,
        dest_dir: str | Path | None = None,
    ) -> DownloadedFile:
        """Download one document (optionally saving to ``dest_dir``)."""
        dl = await self.download_service.download(target)
        if dest_dir is not None:
            dl.save(dest_dir)
        return dl

    async def download_all(
        self,
        targets: Sequence[SecDocument | str],
        *,
        dest_dir: str | Path | None = None,
        max_concurrency: int = 3,
        continue_on_error: bool = True,
        keep_bytes: bool | None = None,
        progress: bool = False,
    ) -> DownloadResult:
        """Download many documents concurrently (deduped by URL; optionally saved to disk).

        The result is a list of the successes that also carries ``.failed`` — see
        :class:`~settfex.services.sec.download.DownloadResult`.
        """
        return await self.download_service.download_all(
            targets,
            dest_dir=dest_dir,
            max_concurrency=max_concurrency,
            continue_on_error=continue_on_error,
            keep_bytes=keep_bytes,
            progress=progress,
        )
