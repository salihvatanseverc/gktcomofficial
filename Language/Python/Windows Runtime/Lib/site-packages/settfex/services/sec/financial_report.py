"""SEC financial-report document models and the HTML-row → model mapper.

The listing service (added on top of this module) replays the SEC search and turns each parsed
result-table row into a :class:`SecDocument`. Models + mapping live here; the service that does
the HTTP orchestration is appended below (see ``FinancialReportService``).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from html import unescape
from typing import Literal, overload
from urllib.parse import urljoin, urlparse

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, computed_field

from settfex.exceptions import (
    CompanyNotFoundError,
    FetchError,
    HTTPStatusError,
    IncompleteListingError,
    InvalidDateError,
    ParseError,
)
from settfex.services.sec.company import resolve_company
from settfex.services.sec.constants import (
    SEC_BASE_URL,
    SEC_FINANCIAL_REPORT_ENDPOINT,
    SEC_FORM_DATE_FORMAT,
    SEC_FORM_FIELD_COMPANY,
    SEC_FORM_FIELD_COMPANY_TEXT,
    SEC_FORM_FIELD_COMPANY_VALUE,
    SEC_FORM_FIELD_DATE_FROM,
    SEC_FORM_FIELD_DATE_TO,
    SEC_FORM_FIELD_REPORT_TYPE,
    SEC_FORM_FIELD_SEARCH,
    SEC_REFERER,
)
from settfex.services.sec.utils import (
    ReportRow,
    build_sec_headers,
    classify_download_href,
    extract_aspnet_tokens,
    parse_dmy_date,
    parse_report_tables,
    parse_year,
    split_section_count,
)
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig


class DocumentCategory(StrEnum):
    """The five disclosure-document categories exposed by the SEC IDISC search.

    A :class:`enum.StrEnum`, so ``str(cat)`` and ``f"{cat}"`` render the bare value
    (``"financial_statement"``) rather than ``"DocumentCategory.FINANCIAL_STATEMENT"``.
    Equality with the plain string, ``.value`` and JSON serialization are unchanged.
    """

    FINANCIAL_STATEMENT = "financial_statement"
    FORM_56_1 = "form_56_1"
    FORM_56_2 = "form_56_2"
    KEY_FINANCIAL_RATIO = "key_financial_ratio"
    MDA = "mda"


# Requested category -> the search ddlReportType code that returns it. A single "FS" search
# returns the financial-statement, KFR and MD&A sections together, so those three share it.
CATEGORY_TO_REPORT_TYPE: dict[DocumentCategory, str] = {
    DocumentCategory.FINANCIAL_STATEMENT: "FS",
    DocumentCategory.KEY_FINANCIAL_RATIO: "FS",
    DocumentCategory.MDA: "FS",
    DocumentCategory.FORM_56_1: "R561",
    DocumentCategory.FORM_56_2: "R562",
}


class SecDocument(BaseModel):
    """A single downloadable disclosure document parsed from a SEC result row."""

    company_name: str = Field(description="Issuer name (row 'Name' cell, or resolved fallback)")
    unique_id: str = Field(description="SEC uniqueIDReference the search was run for")
    category: DocumentCategory = Field(description="Document category (from the section heading)")
    section: str = Field(description="Raw section heading (record-count suffix stripped)")
    title: str | None = Field(
        default=None, description="Document heading/title (MD&A rows carry this instead of Name)"
    )
    year: int | None = Field(default=None, description="Reporting year, if the row has one")
    period: str | None = Field(default=None, description="Period, e.g. 'Q1', 'Q3', 'Year'")
    statement_type: str | None = Field(
        default=None, description="'Company' or 'Consolidated' (financial statements only)"
    )
    status: str | None = Field(
        default=None, description="'Reviewed' or 'Audited' (financial statements only)"
    )
    business_type: str | None = Field(
        default=None, description="Business type (Key Financial Ratio rows only)"
    )
    as_of: date | None = Field(
        default=None, description="Row date column ('As Of' for statements, 'Date' for MD&A)"
    )
    receive_date: date | None = Field(default=None, description="'Receive Date', if present")
    file_url: str = Field(description="Absolute download URL for this document")
    file_id: str | None = Field(
        default=None, description="FILEID path (or 'ipos:<id>' for IPOS-hosted files)"
    )
    file_kind: str | None = Field(default=None, description="File extension: 'zip'/'pdf'/…")

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


def _coerce_category(value: DocumentCategory | str) -> DocumentCategory:
    """Coerce a DocumentCategory or its string value into a DocumentCategory."""
    return value if isinstance(value, DocumentCategory) else DocumentCategory(value)


#: How many unlinked hrefs a single category keeps as a sample. Small on purpose: this is a lead
#: for a bug report, not an archive of the page.
_UNLINKED_HREF_CAP = 20


def _sample_unlinked(hrefs: Sequence[str], cap: int = _UNLINKED_HREF_CAP) -> list[str]:
    """Sample dropped-row hrefs for DIVERSITY first, then fill to ``cap`` in order.

    One href per distinct ``(host, path)`` comes first, and only then are the remaining slots
    filled. The reason is the whole point of keeping them at all: every unknown download shape so
    far -- ``fsdl``, ``viewdoc``, ``capfin`` -- was found by a human looking at a dropped row's
    URL. A naive "first 20" would let twenty instances of one already-known shape crowd out the
    single instance of a new one, which is exactly the row worth seeing.
    """
    first_of_kind: dict[tuple[str, str], str] = {}
    for href in hrefs:
        parsed = urlparse(href)
        first_of_kind.setdefault((parsed.hostname or "", parsed.path), href)
    sample = list(first_of_kind.values())[:cap]
    if len(sample) < cap:
        chosen = set(sample)
        for href in hrefs:
            if len(sample) >= cap:
                break
            if href not in chosen:
                sample.append(href)
                chosen.add(href)
    return sample


class RowTally(BaseModel):
    """What became of one section's rows: one document, or one of three reasons it is not.

    ``rows == documents + placeholders + navigation + no_link`` — every row lands in exactly one
    bucket, which is the point: before this, all three "not a document" outcomes shared a single
    counter and only one of them is a defect.
    """

    rows: int = Field(default=0, description="Data rows seen for this category")
    documents: int = Field(default=0, description="Rows that became a SecDocument")
    placeholders: int = Field(
        default=0, description="'Data not found' / 'ไม่พบข้อมูล' rows — the site saying it has none"
    )
    navigation: int = Field(
        default=0, description="'Display all results' ViewMore rows — a link to a page, not a file"
    )
    no_link: int = Field(
        default=0, description="A data row that should have carried a download link and did not"
    )
    unlinked_hrefs: list[str] = Field(
        default_factory=list,
        description="Sample of the hrefs behind `no_link`, diversity-first and capped; `no_link` "
        "remains the true total",
    )

    def plus(self, other: RowTally) -> RowTally:
        """Return the element-wise sum of two tallies (used when merging search codes)."""
        return RowTally(
            rows=self.rows + other.rows,
            documents=self.documents + other.documents,
            placeholders=self.placeholders + other.placeholders,
            navigation=self.navigation + other.navigation,
            no_link=self.no_link + other.no_link,
            # Re-sampled rather than concatenated, so a merge cannot exceed the cap or lose the
            # diversity property the sample exists for.
            unlinked_hrefs=_sample_unlinked([*self.unlinked_hrefs, *other.unlinked_hrefs]),
        )


class CodeFailure(BaseModel):
    """One report-code search that failed, and why.

    Deliberately the same shape as
    :class:`~settfex.services.sec.download.FailedDownload`: one pattern for "what did not make it",
    whether the thing that did not make it was a file or a whole search.
    """

    code: str = Field(description="The ddlReportType code, e.g. 'R562'")
    categories: list[str] = Field(
        default_factory=list, description="The requested categories this code would have returned"
    )
    error: str = Field(description="The exception message")
    error_type: str = Field(description="The exception class name, e.g. 'HTTPStatusError'")
    status_code: int | None = Field(default=None, description="HTTP status, when the cause was one")
    url: str | None = Field(default=None, description="The URL involved, when known")


class DegradedSection(BaseModel):
    """One section that fell back to its truncated inline rows, and why.

    The third member of the family :class:`CodeFailure` and
    :class:`~settfex.services.sec.download.FailedDownload` belong to — "what did not make it",
    whether that was a file, a whole search, or one section's completeness.

    A "display all results" page holds the COMPLETE list for its section and *replaces* the inline
    rows, so a broken one is a **partial** loss: the truncated rows are still a real answer, and
    keeping them is what ``follow_view_more=False`` would have given. Until 0.24.0 that fallback
    was announced only in a log line, and ``has_losses`` stayed ``False`` — so the one flag callers
    branch on said the listing was clean while a section was knowingly short.
    """

    category: str = Field(description="The category whose section fell back, e.g. 'mda'")
    url: str = Field(description="The ViewMore page that could not be used")
    reason: str = Field(description="Why it was unusable, in one line")
    status_code: int | None = Field(default=None, description="HTTP status, when there was one")
    error_type: str | None = Field(
        default=None, description="Exception class name, when the page could not be fetched at all"
    )


class ListingAccounting(BaseModel):
    """Where every row and column of a listing went — the parser's own account of itself.

    This is the second of two independent cross-checks, and the two answer different questions.
    :meth:`SecDocumentList.completeness` compares what we hold against **the number the site
    printed**, so a shortfall there is usually legitimate "view more" truncation. This one is
    internal: it says what the parser did with each row it actually received, and needs no
    record-count marker at all. ``no_link``, ``unknown_sections`` and ``unmapped_headers`` are
    defects; ``skipped``, ``placeholders`` and ``navigation`` are rows dropped on purpose.

    The totals are **computed fields**, so they survive ``model_dump()`` into JSON or Parquet
    rather than being recomputed by every consumer.
    """

    rows: int = Field(default=0, description="Data rows this listing was built from")
    skipped: int = Field(
        default=0, description="Rows in revision-tracking sections — dropped by design"
    )
    unknown_rows: int = Field(
        default=0, description="Rows in sections that could not be classified"
    )
    by_category: dict[str, RowTally] = Field(
        default_factory=dict, description="Per-category row tally, keyed by category value"
    )
    unknown_sections: list[str] = Field(
        default_factory=list, description="Section headings that could not be classified"
    )
    unmapped_headers: list[str] = Field(
        default_factory=list,
        description="Column headers in a read section that map to no field and are not ignored",
    )
    unverifiable_sections: list[str] = Field(
        default_factory=list,
        description="Read sections whose heading has no record-count marker — not checkable",
    )
    failed_codes: list[CodeFailure] = Field(
        default_factory=list,
        description="Report-code searches that failed while their siblings succeeded",
    )
    degraded_sections: list[DegradedSection] = Field(
        default_factory=list,
        description="Sections that kept their truncated inline rows because ViewMore failed",
    )

    model_config = ConfigDict(populate_by_name=True)

    def _sum(self, attribute: str) -> int:
        return sum(getattr(tally, attribute) for tally in self.by_category.values())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def documents(self) -> int:
        """Rows that became a document."""
        return self._sum("documents")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def placeholders(self) -> int:
        """Rows that were the site's own "nothing here" placeholder."""
        return self._sum("placeholders")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def navigation(self) -> int:
        """Rows that were a "display all results" link rather than a filing."""
        return self._sum("navigation")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def no_link(self) -> int:
        """Rows lost: a real data row whose download link was missing."""
        return self._sum("no_link")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_losses(self) -> bool:
        """True if anything was lost or not understood — the one flag worth branching on.

        Includes a failed report code since 0.23.0: a search that never ran is the largest loss of
        all, and this flag is what callers branch on. Includes a degraded section since 0.24.0, for
        the same reason — a section that knowingly fell back to its truncated rows is short, and
        the flag said "clean" while a WARNING in the log said otherwise.
        """
        return bool(
            self.no_link
            or self.unknown_sections
            or self.unmapped_headers
            or self.failed_codes
            or self.degraded_sections
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_balanced(self) -> bool:
        """``rows == skipped + unknown_rows + Σ by_category[*].rows`` — the accounting identity."""
        return self.rows == self.skipped + self.unknown_rows + self._sum("rows")

    def absorb(self, other: ListingAccounting) -> None:
        """Add another page's accounting into this one (one search code per page)."""
        self.rows += other.rows
        self.skipped += other.skipped
        self.unknown_rows += other.unknown_rows
        for key, tally in other.by_category.items():
            current = self.by_category.get(key)
            # Copy rather than alias: the merged accounting outlives the one it absorbed.
            self.by_category[key] = tally.model_copy() if current is None else current.plus(tally)
        self._merge_names(other)

    def supersede(self, category: str, other: ListingAccounting) -> None:
        """Replace one category's tally with the ViewMore page's, which holds the complete list.

        The inline rows for that category are not *also* returned — they are replaced — so adding
        both would double-count them and break :attr:`is_balanced`.
        """
        previous = self.by_category.pop(category, None)
        if previous is not None:
            self.rows -= previous.rows
        replacement = other.by_category.get(category)
        if replacement is not None:
            self.by_category[category] = replacement.model_copy()
            self.rows += replacement.rows
        self._merge_names(other)

    def _merge_names(self, other: ListingAccounting) -> None:
        """Union the diagnostic name lists, preserving first-seen order."""
        for attribute in ("unknown_sections", "unmapped_headers", "unverifiable_sections"):
            merged: list[str] = getattr(self, attribute)
            for name in getattr(other, attribute):
                if name not in merged:
                    merged.append(name)
        seen = {(f.code, f.error) for f in self.failed_codes}
        for failure in other.failed_codes:
            if (failure.code, failure.error) not in seen:
                self.failed_codes.append(failure)
                seen.add((failure.code, failure.error))
        degraded_seen = {(d.category, d.url) for d in self.degraded_sections}
        for section in other.degraded_sections:
            if (section.category, section.url) not in degraded_seen:
                self.degraded_sections.append(section)
                degraded_seen.add((section.category, section.url))


class SecDocumentList(BaseModel):
    """The documents a listing produced, **and the parser's account of everything it received**.

    Until 0.24.0 this was a ``list[SecDocument]`` subclass carrying ``accounting`` and
    ``reported_counts`` as instance attributes. That made the completeness signal a **side
    channel**: 6 of 8 ordinary list operations — slicing, ``sorted()``, ``list()``, ``+``, a
    comprehension — build a *new* list, and a new list has no instance attributes, so the evidence
    that a listing was incomplete vanished with no exception and no warning (issue #134). It got
    sharper in 0.23.0, when ``accounting.failed_codes`` began recording *which report code failed*:
    ``sorted(docs, …)`` discarded that too.

    A Pydantic model makes the signal a **field**, so it survives ``model_dump()``, JSON, and every
    round trip a pipeline or an agent puts the result through. That is the point — function-calling
    results are JSON, so a signal that reaches the return value could still be lost on the way out.

    The list-like surface is kept, so ordinary use is unchanged::

        docs = await get_sec_documents("CPALL")
        len(docs), bool(docs), docs[0], [d.year for d in docs]   # all exactly as before
        if docs.accounting.has_losses:                           # a field now, not an attribute
            ...

    .. warning::
       **``isinstance(docs, list)`` is now ``False``.** Unlike the operations this redesign fixes,
       that one fails *silently* — it takes the other branch rather than raising. Code that
       dispatches on the type instead of the interface wants ``docs.documents``. ``dict(docs)``
       raises now too, but loudly; use ``docs.model_dump()``.

    .. note::
       **A slice carries the accounting of the whole call, not of the slice.** ``docs[:5]`` is a
       ``SecDocumentList`` whose ``accounting`` still describes every row the *listing* received,
       including rows the slice does not contain. That is deliberate, and it is the rule
       :meth:`filter` already followed: narrowing a result must never hide a loss from exactly the
       caller who narrowed it. ``reported_counts`` is carried across for the same reason.

       ``sorted(docs)`` and ``list(docs)`` still return plain lists, because iteration is kept —
       removing it would break every consumer for no gain. Sort ``docs.documents``, and read the
       accounting off the object the call returned.
    """

    documents: list[SecDocument] = Field(
        default_factory=list, description="The filings this listing yielded, in page order"
    )
    reported_counts: dict[str, int] = Field(
        default_factory=dict,
        description="How many records the site said each section holds, keyed by category value",
    )
    accounting: ListingAccounting = Field(
        default_factory=ListingAccounting,
        description="What the parser did with every row and column it received",
    )

    model_config = ConfigDict(populate_by_name=True)

    # -- the list-like surface -------------------------------------------------------------
    #
    # Iteration, length, truthiness and integer indexing are kept so that every existing caller
    # keeps working. `__len__` is what restores `if docs:` — a BaseModel is otherwise always
    # truthy, which would have quietly inverted the meaning of an empty listing.

    def __iter__(self) -> Iterator[SecDocument]:  # type: ignore[override]
        return iter(self.documents)

    def __len__(self) -> int:
        return len(self.documents)

    def __contains__(self, item: object) -> bool:
        return item in self.documents

    @overload
    def __getitem__(self, index: int) -> SecDocument: ...

    @overload
    def __getitem__(self, index: slice) -> SecDocumentList: ...

    def __getitem__(self, index: int | slice) -> SecDocument | SecDocumentList:
        """Integer indexing yields a document; slicing yields a **model**, never a bare list.

        The overloads are not decoration. Without them the annotation collapses to the union, and
        **every** consumer call is a type error under mypy strict -- ``docs[0].year`` reports
        "Item SecDocumentList of SecDocument | SecDocumentList has no attribute year", and
        ``docs[:5].accounting`` reports the mirror image. settfex ships ``py.typed``, so its hints
        are a shipped interface, not an internal convenience; the library itself never indexes
        these containers, which is exactly why an internally-green ``mypy settfex/`` said nothing.
        """
        if isinstance(index, slice):
            return SecDocumentList(
                documents=self.documents[index],
                reported_counts=dict(self.reported_counts),
                accounting=self.accounting,
            )
        return self.documents[index]

    # -- the helpers -----------------------------------------------------------------------

    def completeness(self) -> dict[str, tuple[int, int]]:
        """Per category: ``(documents here, records the site said that section holds)``.

        A shortfall is **not** automatically an error. A long section is truncated behind a "view
        more" link, so with ``follow_view_more=False`` the site's number is legitimately larger
        than what was returned. It is the cross-check that makes a silent parse failure visible:
        a section reporting 27 records that yields 0 documents is the shape of a bug, and without
        this the caller has to re-derive the number from the raw HTML to notice.
        """
        held: dict[str, int] = {}
        for document in self.documents:
            held[document.category.value] = held.get(document.category.value, 0) + 1
        keys = {*held, *self.reported_counts}
        return {
            k: (held.get(k, 0), self.reported_counts[k]) for k in keys if k in self.reported_counts
        }

    def categories(self) -> list[DocumentCategory]:
        """Distinct categories present, in ``DocumentCategory`` enum order."""
        present = {d.category for d in self.documents}
        return [c for c in DocumentCategory if c in present]

    def available_years(self, category: DocumentCategory | str | None = None) -> list[int]:
        """
        Sorted-descending unique reporting years, optionally restricted to one category.

        Documents without a year (``year is None``) are ignored.
        """
        cat = _coerce_category(category) if category is not None else None
        years = {
            d.year
            for d in self.documents
            if d.year is not None and (cat is None or d.category == cat)
        }
        return sorted(years, reverse=True)

    def years_by_category(self) -> dict[str, list[int]]:
        """
        Available years for each category present, keyed by the category **string value**
        (e.g. ``"form_56_1"``) for clean printing; iterated in enum order.

        Example:
            >>> docs.years_by_category()
            {'financial_statement': [2026, 2025, 2024], 'form_56_1': [2025, 2024, 2023]}
        """
        return {c.value: self.available_years(c) for c in self.categories()}

    def filter(
        self,
        *,
        category: DocumentCategory | str | None = None,
        year: int | None = None,
    ) -> SecDocumentList:
        """Return a new ``SecDocumentList`` matching the given category and/or year (AND)."""
        cat = _coerce_category(category) if category is not None else None
        # Carry the reported counts across, narrowed to the category kept. A `year` filter does
        # not narrow them: they describe what the SITE said each section holds, which a
        # client-side year filter cannot change. Without this the cross-check would evaporate
        # exactly when someone narrows a result.
        counts = (
            dict(self.reported_counts)
            if cat is None
            else {k: v for k, v in self.reported_counts.items() if k == cat.value}
        )
        return SecDocumentList(
            documents=[
                d
                for d in self.documents
                if (cat is None or d.category == cat) and (year is None or d.year == year)
            ],
            reported_counts=counts,
            # Carried whole, for the same reason: it describes the PARSE that produced these
            # documents, which no client-side filter can change. Narrowing it would hide a loss
            # from exactly the caller who narrowed the result.
            accounting=self.accounting,
        )

    def summary(self) -> str:
        """A ready-to-``print()`` block of the available years per category.

        A failed report code is appended, because a summary that lists only what arrived reads as
        success when a whole search is missing — the same reason the listing's log line stopped
        being derived from ``len()``.
        """
        by_cat = self.years_by_category()
        lines = []
        if by_cat:
            width = max(len(k) for k in by_cat)
            lines = [
                f"{cat:<{width}} : {', '.join(str(y) for y in years) or '-'}"
                for cat, years in by_cat.items()
            ]
        elif not self.accounting.failed_codes and not self.accounting.degraded_sections:
            return "(no documents)"
        for failure in self.accounting.failed_codes:
            lines.append(
                f"FAILED {failure.code} ({', '.join(failure.categories) or 'no category'}): "
                f"{failure.error_type}: {failure.error}"
            )
        for degraded in self.accounting.degraded_sections:
            lines.append(f"DEGRADED {degraded.category}: {degraded.reason}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        """Show the losses, so a REPL or a log line cannot make a partial result look whole."""
        parts = [f"{len(self)} document(s)"]
        if self.accounting.failed_codes:
            parts.append(
                f"FAILED code(s): {', '.join(f.code for f in self.accounting.failed_codes)}"
            )
        if self.accounting.degraded_sections:
            parts.append(
                f"DEGRADED: {', '.join(d.category for d in self.accounting.degraded_sections)}"
            )
        if self.accounting.no_link:
            parts.append(f"{self.accounting.no_link} row(s) lost")
        short = {k for k, (held, said) in self.completeness().items() if held < said}
        if short:
            parts.append(f"short in {sorted(short)}")
        return f"<SecDocumentList {' | '.join(parts)}>"


def _clean_section(heading: str) -> str:
    """Strip the record-count suffix (either language) from a section heading."""
    return split_section_count(heading)[0]


# Why a section was not mapped. "skipped" is deliberate; "unknown" is a bug or a site change and
# is escalated by the caller -- the distinction is the whole point, because before it existed
# both outcomes were an indistinguishable `None`.
_SectionDisposition = Literal["mapped", "skipped", "unknown"]

# Revision-tracking sections, in both languages. `แก้ไข` ("amend/revise") covers both Thai forms:
# `งบการเงินที่อยู่ระหว่างการแก้ไข` and `งบการเงินที่สำนักงานแจ้งให้แก้ไข`.
_SKIP_TOKENS = ("revis", "amend", "order", "แก้ไข")


def _section_disposition(heading: str) -> tuple[DocumentCategory | None, _SectionDisposition]:
    """
    Classify a result-section heading into (category, disposition), in English or Thai.

    Tolerant of the site's "Finanacial" misspelling. The record-count suffix is stripped first,
    in either language, so it can never affect the match.
    """
    lower = _clean_section(heading).lower()

    # ASCII form numbers first: the Thai headings embed them verbatim ("แบบ 56-1 One Report").
    if "56-1" in lower:
        return DocumentCategory.FORM_56_1, "mapped"
    if "56-2" in lower:
        return DocumentCategory.FORM_56_2, "mapped"

    # ORDER IS LOAD-BEARING. The Thai heading for statements *being revised*
    # (`งบการเงินที่อยู่ระหว่างการแก้ไข`) CONTAINS the heading for financial statements
    # (`งบการเงิน`) as a prefix, so probing for the latter first files every amended-statement
    # section under FINANCIAL_STATEMENT. This is the same reason the English chain has always
    # tested revis/amend/order before finan+statement.
    if any(token in lower for token in _SKIP_TOKENS):
        return None, "skipped"  # status-tracking sections, not downloadable disclosures

    if "key financial ratio" in lower or "อัตราส่วน" in lower:
        return DocumentCategory.KEY_FINANCIAL_RATIO, "mapped"
    if "discussion and analysis" in lower or "md&a" in lower or "คำอธิบายและวิเคราะห์" in lower:
        return DocumentCategory.MDA, "mapped"
    if ("finan" in lower and "statement" in lower) or "งบการเงิน" in lower:
        return DocumentCategory.FINANCIAL_STATEMENT, "mapped"
    return None, "unknown"


def category_for_section(heading: str) -> DocumentCategory | None:
    """
    Classify a result-section heading into a DocumentCategory (or None to skip).

    Skips the revision-tracking sections ("… need to be revised", "… ordered to amend", and their
    Thai equivalents). Use :func:`_section_disposition` when you need to tell a deliberate skip
    apart from an unrecognised heading.
    """
    return _section_disposition(heading)[0]


# Result column header (lower-cased) -> SecDocument field. Sections differ: financial
# statements use Name/Year/Status/Type/Period/As Of; MD&A uses Date/Time/Heading/Link; Key
# Financial Ratio REORDERS the columns it shares with financial statements (Year moves from
# position 2 to 5, and Business Type stands where Status does). Matching on the header NAME
# rather than the column index is what makes that reordering a non-event.
#
# The Thai half is an exact MIRROR of the English half: the same fields, no more and no fewer,
# so the same page in either language produces the same document. Terms observed on the English
# pages that map to nothing (Details, Link, Description, Time, Company Name, Order Date,
# Reviewed Financial Statement) are deliberately absent from BOTH halves.
#
# That omission is what resolves `รายละเอียด`, which is one Thai word for three English headers
# -- Details, Link and Description -- and appears TWICE IN ONE HEADER ROW on the ordered-to-amend
# table (Thai `['ชื่อ', 'รายละเอียด', 'รายละเอียด']` against English
# `['Name', 'Description', 'Details']`). A flat dict cannot distinguish those three, but it does
# not have to: all three are unmapped in English, so "no entry" is the right answer in all three
# places. Do not add it.
#
# `Receive Date` was the one gap left by the #123 work: the corpus behind it was FS searches only,
# so the Thai spelling was never observed and a guessed header was refused. It was OBSERVED on
# 2026-09-21 -- `วันที่ได้รับข้อมูล`, in the same column position as the English header on PTT 56-1,
# PTT 56-2 and ADVANC 56-1 -- and is mapped below. Era handling already turns `12/03/2569` into
# `2026-03-12`, which is exactly what the English page yields for the same filing (issue #127 P4).
_HEADER_FIELD_MAP: dict[str, str] = {
    # English
    "name": "company_name",
    "heading": "title",
    "year": "year",
    "status": "status",
    "type": "statement_type",
    "period": "period",
    "as of": "as_of",
    "date": "as_of",
    "receive date": "receive_date",
    "business type": "business_type",
    # Thai -- every pair VERIFIED 5/5 issuer-pairs by column position in the captured corpus
    "ชื่อ": "company_name",
    "หัวข้อข่าว": "title",
    "ประจำปี": "year",
    "ประเภทงบ": "status",
    "ชนิดงบ": "statement_type",
    "งวด": "period",
    "สิ้นสุดวันที่": "as_of",
    "วันที่": "as_of",
    "วันที่ได้รับข้อมูล": "receive_date",
    "ประเภทธุรกิจ": "business_type",
}

# Headers that appear in a MAPPED section, carry no value this model records, and are therefore
# ignored on purpose. Anything outside this set and `_HEADER_FIELD_MAP` is reported (never raised):
# an unmodelled column is "the site has more than we model", not lost data, so it must not be able
# to fail a listing -- but it must not be invisible either, because that is how P4 survived.
#
# Deliberately ABSENT, and not an oversight: `Description`, `Company Name`, `Order Date`,
# `Reviewed Financial Statement`, `ชื่อบริษัท`, `วันที่สั่งการ/ขอผ่อนผัน` and
# `งบการเงินที่ต้องแก้ไข/ขอผ่อนผัน`. Every one of them occurs ONLY in the revision-tracking sections,
# whose rows are skipped before a single cell is read, so they never reach this check. Adding them
# would make the ignore-list look like it covers more than it does.
_IGNORED_HEADERS: frozenset[str] = frozenset(
    {
        "details",
        "รายละเอียด",  # one Thai word for Details/Link/Description -- see the note above
        "link",
        "time",
        "เวลา",
    }
)

# A results row that states the section is empty, in either language. It is not a dropped document:
# the section reports 0 records and this row is the site saying so.
_EMPTY_ROW_MARKERS = ("data not found", "ไม่พบข้อมูล")


def row_to_document(
    row: ReportRow, unique_id: str, *, company_name: str | None = None
) -> SecDocument | None:
    """
    Map one parsed :class:`ReportRow` to a :class:`SecDocument`, or None if it isn't a real
    downloadable row (unknown/skipped section, "Data not found", or no download link).

    Args:
        row: A parsed result-table row.
        unique_id: The uniqueIDReference the search was run for.
        company_name: Fallback issuer name for rows without a 'Name' cell (e.g. MD&A) — the
            listing service passes the resolved company name here.
    """
    category = category_for_section(row["section"])
    if category is None:
        return None

    href = row.get("href")
    file_url, file_id, file_kind = classify_download_href(href)
    if not file_url:
        return None  # e.g. a "Data not found" placeholder row

    headers = [h.lower() for h in row["headers"]]
    cells = row["cells"]
    values: dict[str, str] = {}
    for header, cell in zip(headers, cells, strict=False):
        field = _HEADER_FIELD_MAP.get(header)
        if field:
            values[field] = cell

    resolved_name = values.get("company_name", "").strip() or (company_name or "").strip()

    return SecDocument(
        company_name=resolved_name,
        unique_id=unique_id,
        category=category,
        section=_clean_section(row["section"]),
        title=values.get("title") or None,
        year=parse_year(values.get("year")),
        period=values.get("period") or None,
        statement_type=values.get("statement_type") or None,
        status=values.get("status") or None,
        business_type=values.get("business_type") or None,
        as_of=parse_dmy_date(values.get("as_of")),
        receive_date=parse_dmy_date(values.get("receive_date")),
        file_url=file_url,
        file_id=file_id,
        file_kind=file_kind,
    )


# The three ways a row can produce no document. Named exactly as the RowTally fields they
# increment, so the counter can be picked by name instead of a three-branch if.
_DropReason = Literal["placeholders", "navigation", "no_link"]

# The path fragment of a "display all results" link. A row whose only anchor points there is
# navigation, not a filing -- it is how the site truncates a long section, and counting it as a
# lost document would make every truncated section look like a defect.
_VIEWMORE_PATH_MARKER = "/viewmore/"


def _drop_reason(row: ReportRow, *, reported: int | None) -> _DropReason:
    """Why a row produced no document: 'placeholders', 'navigation' or 'no_link' (a real loss).

    Before this existed the three shared one counter, so the only one that is a defect was
    indistinguishable from the two that are the site working normally.
    """
    href = str(row.get("href") or "").lower()
    if _VIEWMORE_PATH_MARKER in href:
        return "navigation"
    text = " ".join(str(cell) for cell in row["cells"]).strip().lower()
    if not text or reported == 0 or any(marker in text for marker in _EMPTY_ROW_MARKERS):
        return "placeholders"
    return "no_link"


# A listing response is an ASP.NET result panel: a sequence of `card-heading` divs, each with a
# `<table>`. An error page has neither. Measured over every page this repo holds -- 8 committed
# fixtures, 3 listing test constants and a real 104 KB ViewMore capture -- every genuine listing
# body carries at least one of each, INCLUDING the one whose section is genuinely empty (MOTHER's
# Key Financial Ratio, which reports 0 records). The two error-shaped bodies in the evidence, a
# real HTTP 505 page and a capital.sec.or.th indirection page, carry neither.
#
# NOTE it deliberately does NOT key on `ctl00_CPH_pnlControl` alone: the real ViewMore capture has
# that id and the trimmed `FS_VIEWMORE_HTML` test constant does not, so a panel-id rule would call
# a legitimate page an error.
_LISTING_MARKERS = ("card-heading", "<table")


def _require_ok(status_code: int, url: str, code: str) -> None:
    """Raise ``FetchError`` unless the listing response is 2xx (issue #131).

    The listing path used to read ``response.text`` without ever looking at the status, so an
    error page parsed to zero rows and was returned as an empty list -- indistinguishable from an
    issuer with no filings, and worse than a parse bug because it is intermittent.

    Raised as :class:`HTTPStatusError`, **not** through ``raise_for_status``: that helper maps
    404 to ``SymbolNotFoundError`` and would consult the symbol suggester, which is wrong here.
    The unique_id was already resolved, so a 404 on this endpoint means the route or the host is
    wrong, not that an issuer does not exist -- the same reasoning that gives the DR-profile and
    analyst-consensus endpoints their own handling (see CLAUDE.md's Known Gotchas).
    """
    if 200 <= status_code < 300:
        return
    message = (
        f"SEC listing request failed: HTTP {status_code} for report code {code!r} "
        f"at {url}. The response was not parsed — an error page yields zero rows, which is "
        f"indistinguishable from an issuer that filed nothing."
    )
    logger.error(message)
    raise HTTPStatusError(message, status_code=status_code, url=url, report_code=code)


def _require_listing_page(html: str, url: str, code: str) -> None:
    """Raise ``ParseError`` when a 2xx body is not a listing page at all (issue #131).

    The status check above catches the evidenced case (a real HTTP 505). This is the companion for
    an error or interstitial page served with HTTP 200, which no status check can see. It is
    deliberately the weakest rule that separates the two populations, so that a genuinely empty
    listing -- the thing that must never raise -- cannot trip it. See :data:`_LISTING_MARKERS`.
    """
    lowered = html.lower()
    if any(marker in lowered for marker in _LISTING_MARKERS):
        return
    message = (
        f"SEC listing response for report code {code!r} at {url} carries no result table and no "
        f"section heading, so it is not a listing page (HTTP 200 error or interstitial page?). "
        f"Raised rather than returned as an empty list, which would be indistinguishable from an "
        f"issuer that filed nothing."
    )
    logger.error(message)
    raise ParseError(message, url=url, rows_parsed=0)


def _view_more_degradation(
    status_code: int, html: str, url: str, code: str, category: DocumentCategory
) -> DegradedSection | None:
    """Is this "display all results" page fit to replace its section's inline rows?

    Returns a :class:`DegradedSection` (with a WARNING) instead of raising, so a broken ViewMore
    page costs the caller the *completeness* of one section rather than the whole listing; ``None``
    means the page is usable. See the call site for why the severity is set this way.

    Returning the record rather than a bare ``False`` is 0.24.0's change: the log line was the only
    place the fallback was stated, so ``has_losses`` reported a clean parse for a listing that had
    knowingly kept truncated rows.
    """
    if not 200 <= status_code < 300:
        reason = (
            f"the 'display all results' page answered HTTP {status_code}; kept the truncated "
            f"inline rows for this section"
        )
        logger.warning(
            f"The 'display all results' page for report code {code!r} at {url} answered HTTP "
            f"{status_code}. Keeping the truncated inline rows for that section instead of "
            f"replacing them; `completeness()` will show the shortfall."
        )
        return DegradedSection(
            category=category.value, url=url, reason=reason, status_code=status_code
        )
    lowered = html.lower()
    if not any(marker in lowered for marker in _LISTING_MARKERS):
        reason = (
            "the 'display all results' page was not a listing page (no result table, no section "
            "heading); kept the truncated inline rows for this section"
        )
        logger.warning(
            f"The 'display all results' page for report code {code!r} at {url} is not a listing "
            f"page (no result table, no section heading). Keeping the truncated inline rows for "
            f"that section; `completeness()` will show the shortfall."
        )
        return DegradedSection(
            category=category.value, url=url, reason=reason, status_code=status_code
        )
    return None


# ViewMore slug -> the category whose complete list that page holds.
@dataclass
class _MapResult:
    """What one results page yielded, and why the rest of it did not.

    The legacy scalars (``rows`` / ``skipped`` / ``no_href`` / ``unknown_sections``) are views onto
    :attr:`accounting`, so there is one set of numbers rather than two that can drift apart.
    """

    documents: list[SecDocument] = field(default_factory=list)
    accounting: ListingAccounting = field(default_factory=ListingAccounting)
    reported_counts: dict[str, int] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        """Data rows parsed from the page."""
        return self.accounting.rows

    @property
    def skipped(self) -> int:
        """Rows in revision-tracking sections — dropped on purpose."""
        return self.accounting.skipped

    @property
    def no_href(self) -> int:
        """Rows that produced no document for want of a usable download link.

        The sum of the three reasons, kept as one number for continuity. Branch on
        ``accounting.no_link`` instead: this one is non-zero on perfectly healthy pages.
        """
        acc = self.accounting
        return acc.placeholders + acc.navigation + acc.no_link

    @property
    def unknown_sections(self) -> list[str]:
        """Section headings that could not be classified."""
        return self.accounting.unknown_sections


def _map_rows(
    rows: Sequence[ReportRow],
    unique_id: str,
    *,
    company_name: str | None,
    source: str,
    wanted: set[DocumentCategory] | None = None,
) -> _MapResult:
    """Map parsed rows to documents, account for every drop, and escalate a total loss.

    The accounting exists because a row used to vanish three different ways behind one ``None``: a
    section we skip deliberately, a placeholder row with no download link, and a heading we do
    not recognise at all. Only the third is a defect, and it was indistinguishable from the other
    two -- and from an issuer with no filings. ``no_href`` is now split again, because the "no
    download link" bucket was itself three things (see :func:`_drop_reason`).

    ``wanted`` scopes the escalation to the categories the caller asked for; ``None`` means all. A
    single "FS" search returns three sections, so without it a loss in a section that was filtered
    out on request would raise for a caller who never wanted it.
    """
    if not rows:
        # Reachable only for a body that looked like a listing and yielded no data row at all.
        # Within this package that cannot be a real listing: every captured page — including the
        # ones whose sections are empty — carries at least the revision-tracking placeholder row.
        # Returning [] here is the exact silent loss of issue #131, one layer below the transport.
        message = (
            f"No result rows were parsed from {source}. A SEC listing always carries at least one "
            f"row, including the placeholder an empty section serves, so this body is not a "
            f"listing — raised rather than returned as an empty list."
        )
        logger.error(message)
        raise ParseError(message, url=source, rows_parsed=0)

    result = _MapResult()
    accounting = result.accounting
    accounting.rows = len(rows)
    unknown: dict[str, None] = {}  # ordered set
    unmapped: dict[str, None] = {}
    unverifiable: dict[str, None] = {}
    unlinked: dict[str, list[str]] = {}
    for row in rows:
        heading = str(row["section"])
        category, disposition = _section_disposition(heading)
        text, count = split_section_count(heading)
        if count is not None and category is not None:
            result.reported_counts[category.value] = count
        if disposition == "skipped":
            accounting.skipped += 1
            continue
        if disposition == "unknown":
            unknown.setdefault(text, None)
            accounting.unknown_rows += 1
            continue
        if category is None:  # unreachable: 'mapped' always carries a category
            accounting.unknown_rows += 1  # ...but still account for the row if it ever happens
            continue
        tally = accounting.by_category.setdefault(category.value, RowTally())
        tally.rows += 1
        # Column coverage, for READ sections only. A header inside a revision-tracking section is
        # never consulted, so reporting it would be noise -- see the note on _IGNORED_HEADERS.
        for header in row["headers"]:
            name = str(header).strip()
            key = name.lower()
            if key and key not in _HEADER_FIELD_MAP and key not in _IGNORED_HEADERS:
                unmapped.setdefault(name, None)
        if count is None:
            unverifiable.setdefault(text, None)
        document = row_to_document(row, unique_id, company_name=company_name)
        if document is None:
            reason = _drop_reason(row, reported=count)
            setattr(tally, reason, getattr(tally, reason) + 1)
            if reason == "no_link":
                # Kept whole here and sampled once at the end -- the diversity rule needs to see
                # every candidate before it can choose.
                unlinked.setdefault(category.value, []).append(str(row.get("href") or ""))
            continue
        tally.documents += 1
        result.documents.append(document)

    for category_value, hrefs in unlinked.items():
        accounting.by_category[category_value].unlinked_hrefs = _sample_unlinked(hrefs)
    accounting.unknown_sections = list(unknown)
    accounting.unmapped_headers = list(unmapped)
    accounting.unverifiable_sections = list(unverifiable)
    logger.debug(
        f"Parsed {accounting.rows} row(s) from {source}: {len(result.documents)} mapped, "
        f"{accounting.skipped} skipped (revision-tracking), {accounting.placeholders} placeholder, "
        f"{accounting.navigation} navigation, {accounting.no_link} without a download link, "
        f"{len(accounting.unknown_sections)} unrecognised section(s)"
    )

    if accounting.unknown_sections:
        listed = ", ".join(repr(h) for h in accounting.unknown_sections)
        if not result.documents:
            message = (
                f"{accounting.rows} row(s) parsed from {source} but NONE could be classified; "
                f"unrecognised section heading(s): {listed}. The page structure or its language "
                f"may have changed. This is raised rather than returned as an empty list because "
                f"an empty list is indistinguishable from an issuer with no filings."
            )
            logger.error(message)
            raise ParseError(
                message,
                url=source,
                rows_parsed=accounting.rows,
                unknown_sections=accounting.unknown_sections,
            )
        logger.warning(
            f"{len(accounting.unknown_sections)} unrecognised section heading(s) in {source} were "
            f"dropped: {listed}. {len(result.documents)} document(s) from the recognised sections "
            f"were kept."
        )

    _escalate_losses(accounting, result.reported_counts, source=source, wanted=wanted)
    return result


def _escalate_losses(
    accounting: ListingAccounting,
    reported_counts: dict[str, int],
    *,
    source: str,
    wanted: set[DocumentCategory] | None,
) -> None:
    """Raise on a total loss, warn on a partial one, and name every column we did not understand.

    The severity ladder is the one the unrecognised-heading check already uses, for the same
    reason: an empty result is the case a caller cannot diagnose, a partial result is one they can
    act on once the loss is named. Raising on a single lost row would turn one bad row in a long
    listing into no listing at all.
    """
    in_scope = (
        accounting.by_category
        if wanted is None
        else {k: v for k, v in accounting.by_category.items() if k in {c.value for c in wanted}}
    )
    lost = sum(tally.no_link for tally in in_scope.values())
    kept = sum(tally.documents for tally in in_scope.values())
    if lost:
        sections = ", ".join(
            f"{name} ({tally.no_link} of {tally.rows})"
            for name, tally in in_scope.items()
            if tally.no_link
        )
        if not kept:
            message = (
                f"{accounting.rows} row(s) parsed from {source} and every usable one was dropped "
                f"for want of a download link: {sections}. The site reports "
                f"{reported_counts or 'no'} record(s) for these sections, so this is a loss, not "
                f"an empty result — raised rather than returned as an empty list because the two "
                f"are otherwise indistinguishable."
            )
            logger.error(message)
            raise IncompleteListingError(
                message,
                url=source,
                rows_parsed=accounting.rows,
                # Inherited from ParseError and never passed until 0.23.0, so it was always [] on
                # this subclass -- a field that looked answered and was not.
                unknown_sections=accounting.unknown_sections,
                lost_rows=lost,
                reported=reported_counts,
            )
        logger.warning(
            f"{lost} row(s) in {source} carried no download link and were dropped: {sections}. "
            f"{kept} document(s) were kept — see `accounting.no_link` on the returned list."
        )

    if accounting.unmapped_headers:
        logger.warning(
            f"{len(accounting.unmapped_headers)} column header(s) in {source} map to no field and "
            f"are not on the ignore-list: "
            f"{', '.join(repr(h) for h in accounting.unmapped_headers)}. Any value in those "
            f"columns is dropped; the site may have added a column."
        )
    if accounting.unverifiable_sections:
        logger.warning(
            f"{len(accounting.unverifiable_sections)} section(s) in {source} carry no record-count "
            f"marker, so completeness cannot be checked for them: "
            f"{', '.join(repr(h) for h in accounting.unverifiable_sections)}."
        )


# Every slug the site serves a "display all results" page under. The `fs-` prefix is the site's,
# not a category hint: `fs-r561` and `fs-r562` belong to the 56-1 / 56-2 searches, not to the FS
# one. Live-probed 2026-09-20 across CPALL/PTT/SCB in both languages.
#
# The last two were MISSING until 0.22.2, so a 56-1 or 56-2 section over the ~10-row inline cap
# was truncated even with `follow_view_more=True`. 0.22.0's notes predicted exactly this and said
# the shortfall WARNING would surface it; it did, from a downstream consumer reading
# `completeness()`. Scale, with a 2000-2026 window: CPALL Thai 56-1 served 11 of 23, PTT Thai 56-1
# 11 of 25, PTT 56-2 10 of 15 in BOTH languages -- so this was never a Thai-only gap.
_CATEGORY_FOR_VIEWMORE_SLUG: dict[str, DocumentCategory] = {
    "fs-norm": DocumentCategory.FINANCIAL_STATEMENT,
    "fs-kf": DocumentCategory.KEY_FINANCIAL_RATIO,
    "fs-mda": DocumentCategory.MDA,
    "fs-r561": DocumentCategory.FORM_56_1,
    "fs-r562": DocumentCategory.FORM_56_2,
}

_VIEWMORE_HREF = re.compile(r'href="([^"]*?/ViewMore/([a-z0-9-]+)[^"]*)"', re.IGNORECASE)


def _normalize_categories(
    types: list[DocumentCategory | str] | DocumentCategory | str | None,
) -> list[DocumentCategory]:
    """Coerce the ``types`` argument into a de-duplicated list of DocumentCategory (all if None)."""
    if types is None:
        return list(DocumentCategory)
    if isinstance(types, (DocumentCategory, str)):
        types = [types]
    out: list[DocumentCategory] = []
    for t in types:
        cat = t if isinstance(t, DocumentCategory) else DocumentCategory(t)
        if cat not in out:
            out.append(cat)
    return out


def _format_sec_date(value: date | str | None, param_name: str) -> str:
    """Format a from/to date into the form's dd/MM/yyyy wire value ('' when None)."""
    if value is None:
        return ""
    if isinstance(value, date):  # datetime subclasses date
        return value.strftime(SEC_FORM_DATE_FORMAT)
    text = value.strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, SEC_FORM_DATE_FORMAT)
    except ValueError as exc:
        error_msg = (
            f"Invalid {param_name} '{value}': expected dd/mm/yyyy (e.g. '31/12/2025') or a "
            f"datetime.date/datetime object."
        )
        logger.error(error_msg)
        raise InvalidDateError(error_msg) from exc
    return text


def _code_failure(
    code: str,
    error: BaseException,
    categories: list[DocumentCategory],
    wanted: set[DocumentCategory],
) -> CodeFailure:
    """Describe one failed report code, in data rather than in prose."""
    return CodeFailure(
        code=code,
        categories=sorted(
            c.value for c in categories if c in wanted and CATEGORY_TO_REPORT_TYPE[c] == code
        ),
        error=str(error),
        error_type=type(error).__name__,
        status_code=getattr(error, "status_code", None),
        url=getattr(error, "url", None),
    )


def _log_listing_summary(
    listing: SecDocumentList, *, unique_id: str, follow_view_more: bool
) -> None:
    """Emit the caller-facing summary, derived from the accounting rather than from ``len()``.

    This is where :meth:`SecDocumentList.completeness` finally gets an internal caller. The
    cross-check existed from 0.21.0 and nothing in the package ran it, so the one line an operator
    actually sees said "Listed 0 SEC document(s)" in the same words, at the same level, as a
    complete success.

    Two different shortfalls are deliberately given two different levels. With
    ``follow_view_more=False`` a section truncated behind "display all results" is *expected* to
    fall short of the site's number -- warning about what the caller asked for trains people to
    ignore warnings. With ``follow_view_more=True`` the ViewMore page holds the complete list, so a
    residual shortfall has nothing left to explain it and is worth a warning.
    """
    accounting = listing.accounting
    total_reported = sum(listing.reported_counts.values())
    short = {k: v for k, v in listing.completeness().items() if v[0] < v[1]}
    head = f"Listed {len(listing)} SEC document(s) for uid={unique_id}"

    if accounting.has_losses:
        logger.warning(
            f"{head}, but the parse was not clean: {accounting.no_link} row(s) lost to a missing "
            f"download link, {len(accounting.unknown_sections)} unrecognised section(s), "
            f"{len(accounting.unmapped_headers)} unmapped column(s), "
            f"{len(accounting.failed_codes)} failed report code(s), "
            f"{len(accounting.degraded_sections)} degraded section(s). The site reports "
            f"{total_reported} record(s). Inspect `.accounting` on the returned listing."
        )
        return
    if short and follow_view_more:
        logger.warning(
            f"{head} of {total_reported} the site reports; short in {sorted(short)} even after "
            f"following the 'display all results' pages, which should have returned each section "
            f"in full. Compare `.completeness()` on the returned list."
        )
        return
    if short:
        logger.info(
            f"{head} of {total_reported} the site reports; {sorted(short)} truncated because "
            f"follow_view_more=False. Pass follow_view_more=True for the complete sections."
        )
        return
    logger.info(f"{head} (the site reports {total_reported})")


class FinancialReportService:
    """
    List downloadable SEC disclosure documents for an issuer.

    Replays the ASP.NET WebForms search (GET fresh VIEWSTATE tokens → form POST), parses the
    result tables into :class:`SecDocument` models, and (by default) follows the "display all
    results" ViewMore pages so large sections are returned in full. Stateless host — no
    SessionManager (``use_session`` is forced off).
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        base = config or FetcherConfig()
        self.config = base.model_copy(update={"use_session": False})
        logger.info("FinancialReportService initialized (host=market.sec.or.th)")

    async def fetch_documents(
        self,
        unique_id: str,
        *,
        company_name: str | None = None,
        types: list[DocumentCategory | str] | DocumentCategory | str | None = None,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        lang: Language = "en",
        follow_view_more: bool = True,
    ) -> SecDocumentList:
        """
        List documents for a resolved ``unique_id`` (10-digit SEC uniqueIDReference).

        Args:
            unique_id: The issuer's SEC uniqueIDReference (see ``resolve_company``).
            company_name: Issuer name used as a fallback for rows lacking a Name cell (MD&A).
            types: One or more :class:`DocumentCategory` (or their string values); None = all 5.
            from_date / to_date: Window bounds — ``date``/``datetime`` or dd/mm/yyyy string.
                Pass a **wide** window to see the full year history — without dates the SEC form
                returns only a recent window.
            lang: 'en' or 'th'.
            follow_view_more: Follow ViewMore pages so truncated sections are returned in full.

        Returns:
            A :class:`SecDocumentList` (a ``list[SecDocument]`` with ``years_by_category()`` /
            ``available_years()`` / ``filter()`` / ``summary()`` helpers).
        """
        lang = normalize_language(lang)
        categories = _normalize_categories(types)
        date_from = _format_sec_date(from_date, "from_date")
        date_to = _format_sec_date(to_date, "to_date")

        # Minimal set of ddlReportType codes covering the requested categories.
        codes = list(dict.fromkeys(CATEGORY_TO_REPORT_TYPE[c] for c in categories))
        wanted = set(categories)

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # return_exceptions=True: one code failing must not discard the documents its siblings
            # already parsed. `gather` without it propagates the first exception before the line
            # that collects them is ever reached, which turned a partial, well-accounted failure
            # into a total, unrecoverable one (issue #132).
            outcomes = await asyncio.gather(
                *(
                    self._search_code(
                        fetcher,
                        code,
                        unique_id,
                        company_name,
                        date_from,
                        date_to,
                        lang,
                        follow_view_more,
                        wanted,
                    )
                    for code in codes
                ),
                return_exceptions=True,
            )

        docs: list[SecDocument] = []
        reported: dict[str, int] = {}
        accounting = ListingAccounting()
        failures: list[tuple[str, BaseException]] = []
        for code, outcome in zip(codes, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                # Only a FetchError is a "failed code". Anything else is a bug in settfex, and
                # laundering it into an accounting entry would bury it.
                if not isinstance(outcome, FetchError):
                    raise outcome
                failures.append((code, outcome))
                accounting.failed_codes.append(_code_failure(code, outcome, categories, wanted))
                # A log line cannot be sliced away, and `accounting` can -- see the
                # SecDocumentList note in its docstring. So the failure is stated here too.
                logger.warning(
                    f"SEC report code {code!r} failed and was skipped: "
                    f"{type(outcome).__name__}: {outcome}. Its sibling code(s) kept their "
                    f"documents; see `accounting.failed_codes` on the returned list."
                )
                continue
            group, counts, part = outcome
            docs.extend(group)
            reported.update(counts)
            accounting.absorb(part)

        if failures and len(failures) == len(codes):
            # Every code failed, so there is nothing partial to return -- raise the first cause,
            # with its own type and traceback intact. Deliberately NOT an ExceptionGroup:
            # `except FetchError` does not catch one, so it would silently break every existing
            # handler.
            #
            # "First" means first in REPORT-CODE order, not first to fail in time: `gather`
            # returns results positionally, so this is deterministic and two identical runs raise
            # the same cause.
            #
            # The other causes would otherwise be lost, which would break the rule the rest of
            # this work is built on -- the caller can always tell which code failed and why. They
            # ride along as PEP 678 notes: visible in the traceback and in `__notes__`, with no
            # new type, no changed signature, and `except FetchError` still catching it.
            first_code, first_error = failures[0]
            for code, error in failures[1:]:
                first_error.add_note(
                    f"SEC report code {code!r} also failed: {type(error).__name__}: {error}"
                )
            logger.error(
                f"Every requested SEC report code failed for uid={unique_id}: "
                + "; ".join(f"{c}={type(e).__name__}" for c, e in failures)
            )
            raise first_error
        listing = SecDocumentList(documents=docs, reported_counts=reported, accounting=accounting)
        _log_listing_summary(listing, unique_id=unique_id, follow_view_more=follow_view_more)
        return listing

    async def fetch_documents_raw(
        self,
        unique_id: str,
        *,
        code: str,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        lang: Language = "en",
    ) -> list[dict[str, object]]:
        """
        Escape hatch: return the raw parsed result rows (section/headers/cells/href dicts) for a
        single ddlReportType ``code`` ('FS'/'R561'/'R562'/'KFR'), without model mapping.
        """
        lang = normalize_language(lang)
        date_from = _format_sec_date(from_date, "from_date")
        date_to = _format_sec_date(to_date, "to_date")
        async with AsyncDataFetcher(config=self.config) as fetcher:
            html, _ = await self._run_search(
                fetcher, code, unique_id, None, date_from, date_to, lang
            )
        return [dict(r) for r in parse_report_tables(html)]

    async def _run_search(
        self,
        fetcher: AsyncDataFetcher,
        code: str,
        unique_id: str,
        company_name: str | None,
        date_from: str,
        date_to: str,
        lang: Language,
    ) -> tuple[str, str]:
        """GET fresh tokens then POST the search form; return (result HTML, the URL used)."""
        report_url = (
            f"{SEC_BASE_URL}{SEC_FINANCIAL_REPORT_ENDPOINT.format(lang=lang, report_type=code)}"
        )
        get_resp = await fetcher.fetch(report_url, headers=build_sec_headers(referer=SEC_REFERER))
        # This leg was already guarded by accident: an error page carries no __VIEWSTATE, so the
        # check below fired. It reported the wrong cause, though — "the page structure may have
        # changed" for what was really an HTTP 500 — so the status is now read first.
        _require_ok(get_resp.status_code, report_url, code)
        tokens = extract_aspnet_tokens(get_resp.text)
        if not tokens.get("__VIEWSTATE"):
            raise FetchError(
                "SEC search page returned no __VIEWSTATE token — the page structure may have "
                "changed, or the request was blocked."
            )
        form = {
            **tokens,
            SEC_FORM_FIELD_REPORT_TYPE: code,
            SEC_FORM_FIELD_COMPANY: company_name or "",
            SEC_FORM_FIELD_COMPANY_TEXT: company_name or "",
            SEC_FORM_FIELD_COMPANY_VALUE: unique_id,
            SEC_FORM_FIELD_DATE_FROM: date_from,
            SEC_FORM_FIELD_DATE_TO: date_to,
            SEC_FORM_FIELD_SEARCH: "Search",
        }
        post_resp = await fetcher.fetch(
            report_url,
            headers=build_sec_headers(referer=report_url, origin=True),
            method="POST",
            data=form,
        )
        _require_ok(post_resp.status_code, report_url, code)
        return post_resp.text, report_url

    async def _search_code(
        self,
        fetcher: AsyncDataFetcher,
        code: str,
        unique_id: str,
        company_name: str | None,
        date_from: str,
        date_to: str,
        lang: Language,
        follow_view_more: bool,
        wanted: set[DocumentCategory],
    ) -> tuple[list[SecDocument], dict[str, int], ListingAccounting]:
        """Run one search code, map rows, and (optionally) complete sections via ViewMore."""
        html, url = await self._run_search(
            fetcher, code, unique_id, company_name, date_from, date_to, lang
        )
        wanted_values = {c.value for c in wanted}
        _require_listing_page(html, url, code)
        mapped = _map_rows(
            parse_report_tables(html),
            unique_id,
            company_name=company_name,
            source=url,
            wanted=wanted,
        )
        accounting = mapped.accounting
        # Only for the categories the caller asked for. A single "FS" search returns the
        # financial-statement, Key Financial Ratio and MD&A sections together, so without this
        # `completeness()` would report 0-of-2 for sections that were filtered out on request --
        # a shortfall that is pure noise, in the one place meant to make a real shortfall visible.
        reported = {k: v for k, v in mapped.reported_counts.items() if k in wanted_values}
        inline = [d for d in mapped.documents if d.category in wanted]
        if not follow_view_more:
            return inline, reported, accounting

        # Follow each ViewMore link whose category is wanted; its page holds the COMPLETE list
        # for that section, so it replaces the truncated inline rows for that category.
        replacements: dict[DocumentCategory, list[SecDocument]] = {}
        seen_slugs: set[str] = set()
        vm_targets: list[tuple[DocumentCategory, str]] = []
        for href, slug in _VIEWMORE_HREF.findall(html):
            slug = slug.lower()
            cat = _CATEGORY_FOR_VIEWMORE_SLUG.get(slug)
            if cat is None or cat not in wanted or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            vm_targets.append((cat, urljoin(SEC_BASE_URL, unescape(href))))

        if vm_targets:
            # Same reasoning as the per-code gather: one "display all results" page failing at the
            # transport level must cost its own section's completeness, not the whole call. The
            # response-level cases (non-2xx, not-a-listing) already degrade below.
            pages = await asyncio.gather(
                *(
                    fetcher.fetch(url, headers=build_sec_headers(referer=SEC_REFERER))
                    for _, url in vm_targets
                ),
                return_exceptions=True,
            )
            for (cat, url), page in zip(vm_targets, pages, strict=True):
                if isinstance(page, BaseException):
                    if not isinstance(page, FetchError):
                        raise page
                    logger.warning(
                        f"The 'display all results' page for report code {code!r} at {url} could "
                        f"not be fetched ({type(page).__name__}: {page}). Keeping the truncated "
                        f"inline rows for that section; `completeness()` will show the shortfall."
                    )
                    accounting.degraded_sections.append(
                        DegradedSection(
                            category=cat.value,
                            url=url,
                            reason=(
                                f"the 'display all results' page could not be fetched "
                                f"({type(page).__name__}: {page}); kept the truncated inline rows "
                                f"for this section"
                            ),
                            status_code=getattr(page, "status_code", None),
                            error_type=type(page).__name__,
                        )
                    )
                    continue
                # A ViewMore page holds the COMPLETE list for its section and REPLACES the inline
                # rows, so an unusable one here does not merely add nothing -- on 0.22.0 it
                # silently deleted the whole section, the inline rows included.
                #
                # It is DEGRADED rather than raised, which is the same severity ladder the rest of
                # this module uses: a failed ViewMore is a PARTIAL loss, because the truncated
                # inline rows are still a real answer -- exactly the one `follow_view_more=False`
                # would have given. Skipping the replacement keeps them, and the shortfall then
                # shows up on its own through `completeness()` and the WARNING in
                # `_log_listing_summary`, which already treats "short even after following the
                # view-more pages" as the case worth warning about.
                #
                # This is not hypothetical: on 2026-09-20 the live Thai `fs-kf` page answered
                # HTTP 500 on every attempt while the other five slug/language pairs answered 200.
                degraded = _view_more_degradation(page.status_code, page.text, url, code, cat)
                if degraded is not None:
                    accounting.degraded_sections.append(degraded)
                    continue
                vm = _map_rows(
                    parse_report_tables(page.text),
                    unique_id,
                    company_name=company_name,
                    source=url,
                    wanted=wanted,
                )
                reported.update({k: v for k, v in vm.reported_counts.items() if k in wanted_values})
                replacements[cat] = [d for d in vm.documents if d.category == cat]
                # The ViewMore page holds the COMPLETE list for this section, so it replaces
                # the truncated inline rows -- in the accounting too, or they double-count.
                accounting.supersede(cat.value, vm.accounting)

        result = [d for d in inline if d.category not in replacements]
        for docs in replacements.values():
            result.extend(docs)
        return result, reported, accounting


async def get_sec_documents(
    query: str,
    *,
    types: list[DocumentCategory | str] | DocumentCategory | str | None = None,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    lang: Language = "en",
    follow_view_more: bool = True,
    config: FetcherConfig | None = None,
    allow_name_match: bool = False,
) -> SecDocumentList:
    """
    Convenience: resolve a symbol/name and list its SEC disclosure documents (all 5 categories
    by default). This is the flat, one-call entry point (LLM tool-calling friendly).

    Args:
        query: Symbol or company name (e.g. "CPALL").
        types: One or more :class:`DocumentCategory` (or string values); None = all.
        from_date / to_date: Window bounds — ``date``/``datetime`` or dd/mm/yyyy string. Pass a
            **wide** window to see the full year history (default returns only a recent window).
        lang: 'en' or 'th'.
        follow_view_more: Follow ViewMore pages for complete large sections.
        config: Optional fetcher configuration.

    Returns:
        A :class:`SecDocumentList` — a model with ``documents`` plus ``accounting`` /
        ``reported_counts``, and ``years_by_category()`` / ``available_years()`` / ``filter()`` /
        ``summary()`` helpers. It iterates and indexes like the list it used to be.

    Raises:
        CompanyNotFoundError: The search succeeded and matched no issuer (an input error).
        AmbiguousCompanyError: Several issuers matched and none was flagged as the match, or a
            single unflagged one matched without ``allow_name_match=True``. It carries the
            candidates, so the caller can choose without a second request.
        FetchError: On a transport failure, or when every requested report code failed.
    """
    company = await resolve_company(query, lang, config=config, allow_name_match=allow_name_match)
    if company is None:
        # Raised, not returned as []. A lookup that FAILED now raises from the fetch layer, so
        # reaching here means the search succeeded and matched nothing -- a fact about the input.
        # Returning an empty list conflated the two and let a backfill record the window as
        # covered either way (D10).
        error_msg = f"No SEC issuer matched {query!r}"
        logger.error(error_msg)
        raise CompanyNotFoundError(error_msg)
    service = FinancialReportService(config=config)
    return await service.fetch_documents(
        company.unique_id,
        company_name=company.company_name,
        types=types,
        from_date=from_date,
        to_date=to_date,
        lang=lang,
        follow_view_more=follow_view_more,
    )
