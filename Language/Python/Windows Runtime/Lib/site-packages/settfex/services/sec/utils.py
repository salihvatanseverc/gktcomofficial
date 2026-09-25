"""Utilities for the SEC IDISC services: headers, ASP.NET token scraping, an HTML result-table
parser (stdlib only), and small value coercers.

The SEC site is server-rendered ASP.NET HTML with no JSON list endpoint, so listing means
parsing HTML tables. Parsing uses the standard library ``html.parser`` (no new dependency).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from settfex.services.sec.constants import (
    SEC_ASPNET_TOKEN_FIELDS,
    SEC_BASE_URL,
    SEC_REFERER,
)

# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def build_sec_headers(referer: str = SEC_REFERER, *, origin: bool = False) -> dict[str, str]:
    """
    Browser-like headers for market.sec.or.th requests (Incapsula-friendly, mirrors SET).

    ``Content-Type`` is intentionally omitted — ``curl_cffi`` sets it automatically for JSON
    vs form bodies. Pass ``origin=True`` for POSTs that want an ``Origin`` header.
    """
    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
        "Sec-Ch-Ua": '"Chromium";v="120", "Not=A?Brand";v="24", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if origin:
        headers["Origin"] = SEC_BASE_URL
    return headers


# ---------------------------------------------------------------------------
# ASP.NET postback token scraping
# ---------------------------------------------------------------------------


def extract_aspnet_tokens(html: str) -> dict[str, str]:
    """
    Scrape the hidden ASP.NET postback tokens (__VIEWSTATE etc.) from a GET page.

    These MUST be echoed back on the search POST — omitting them does not error but silently
    returns a wrong (broader) result set. Returns a dict with every field in
    ``SEC_ASPNET_TOKEN_FIELDS`` (value ``""`` if a field is unexpectedly absent).
    """
    tokens: dict[str, str] = {}
    for field in SEC_ASPNET_TOKEN_FIELDS:
        match = re.search(rf'id="{re.escape(field)}"[^>]*value="([^"]*)"', html)
        tokens[field] = unescape(match.group(1)) if match else ""
    return tokens


# ---------------------------------------------------------------------------
# HTML result-table parsing
# ---------------------------------------------------------------------------


class ReportRow(dict[str, Any]):
    """A parsed result-table row: ``section`` (card heading), ``headers``, ``cells``, ``href``."""


class _ReportTableParser(HTMLParser):
    """Extract (section-heading, column-headers, row-cells, first-href) tuples from result HTML.

    The result panel is a sequence of ``<div class="card-heading">…</div>`` + ``<table>`` pairs;
    each table has a ``<th>`` header row followed by ``<td>`` data rows. We track the current
    heading and, per table, the header list; each data row is emitted with its section + the
    first anchor href found in the row (the download link).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[ReportRow] = []
        self._section = ""
        self._heading_depth = 0  # >0 while inside a card-heading div (handles nested divs)
        self._heading_buf: list[str] = []
        self._in_table = False
        self._headers: list[str] = []
        self._in_th = False
        self._in_td = False
        self._cell_buf: list[str] = []
        self._row: list[str] = []
        self._row_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        adict = {k: (v or "") for k, v in attrs}
        cls = adict.get("class", "")
        if tag == "div":
            if self._heading_depth > 0:
                self._heading_depth += 1
            elif "card-heading" in cls:
                self._heading_depth = 1
                self._heading_buf = []
        elif tag == "table":
            self._in_table = True
            self._headers = []
        elif tag == "tr":
            self._row = []
            self._row_href = None
        elif tag == "th" and self._in_table:
            self._in_th = True
            self._cell_buf = []
        elif tag == "td" and self._in_table:
            self._in_td = True
            self._cell_buf = []
        elif tag == "a" and self._in_td and self._row_href is None:
            href = adict.get("href", "")
            if href:
                self._row_href = href

    def handle_data(self, data: str) -> None:
        if self._heading_depth > 0:
            self._heading_buf.append(data)
        elif self._in_th or self._in_td:
            self._cell_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._heading_depth > 0:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                self._section = re.sub(r"\s+", " ", "".join(self._heading_buf)).strip()
        elif tag == "th" and self._in_th:
            self._headers.append(re.sub(r"\s+", " ", "".join(self._cell_buf)).strip())
            self._in_th = False
        elif tag == "td" and self._in_td:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell_buf)).strip())
            self._in_td = False
        elif tag == "tr":
            # Emit a data row only (header rows have <th>, so self._row stays empty for them).
            if self._row and self._headers:
                self.rows.append(
                    ReportRow(
                        section=self._section,
                        headers=list(self._headers),
                        cells=list(self._row),
                        href=self._row_href,
                    )
                )
            self._row = []
            self._row_href = None
        elif tag == "table":
            self._in_table = False
            self._headers = []


def parse_report_tables(html: str) -> list[ReportRow]:
    """Parse a SEC result page/panel into a flat list of :class:`ReportRow` (one per data row)."""
    parser = _ReportTableParser()
    parser.feed(html)
    return parser.rows


# ---------------------------------------------------------------------------
# Value coercers
# ---------------------------------------------------------------------------

# Thai digits ๐-๙ (U+0E50..U+0E59). `\d` in a str pattern is Unicode-aware, so re.search finds
# them -- but `datetime.strptime` is NOT, so a Thai-digit date silently parsed to None. Normalize
# before any parse rather than relying on that asymmetry.
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# Buddhist era: B.E. = C.E. + 543. The Thai pages state years as 2568/2569.
_BUDDHIST_ERA_OFFSET = 543

# Keyed on the VALUE's magnitude, never on the requested language -- the English page serves Thai
# values too (`งบรวม`, "3 เดือน"), so `lang` is not a reliable signal for what era a cell is in.
# 2400 B.E. is 1857 C.E., and a Gregorian 2400 is centuries beyond any filing, so the two ranges
# cannot collide in this data.
_BUDDHIST_ERA_MIN = 2400


def normalize_thai_digits(value: str) -> str:
    """Return ``value`` with Thai numerals ๐-๙ replaced by their ASCII equivalents."""
    return value.translate(_THAI_DIGITS)


def to_christian_year(year: int) -> int:
    """Convert a Buddhist-era year to C.E.; return a year that is already C.E. unchanged.

    The test is the value's magnitude (``>= 2400``), not the page language, because the SEC's
    English pages carry Thai-language cells as well.
    """
    return year - _BUDDHIST_ERA_OFFSET if year >= _BUDDHIST_ERA_MIN else year


# The record-count marker a section heading carries, one form per language. NOTE the English
# form has no space before the closing parenthesis on the live page -- "( 27 record(s) found)" --
# so a pattern that requires one matches nothing at all.
_SECTION_COUNT_MARKERS = (
    re.compile(r"\s*\(\s*([\d,]+)\s*record\(s\)\s*found\s*\)\s*$", re.IGNORECASE),
    re.compile(r"\s*\(\s*จำนวนรายการที่พบ\s*([\d,]+)\s*รายการ\s*\)\s*$"),
)


def split_section_count(heading: str) -> tuple[str, int | None]:
    """Split a section heading into its text and the record count it states about itself.

    ``"Finanacial Statements ( 27 record(s) found)"`` and
    ``"งบการเงิน (จำนวนรายการที่พบ 27 รายการ)"`` both give ``("…", 27)``; a heading with no
    marker gives ``(heading, None)``.

    The count is the total **available** for that section, which is not the same as the number of
    rows in this response: a long section is truncated behind a "view more" link. So it is a
    completeness signal, not a row-count assertion.
    """
    text = heading.strip()
    for marker in _SECTION_COUNT_MARKERS:
        match = marker.search(text)
        if match:
            return marker.sub("", text).strip(), int(match.group(1).replace(",", ""))
    return text, None


def parse_dmy_date(value: str | None) -> date | None:
    """Parse a dd/MM/yyyy result-cell date; return None for blank/unparseable input.

    Handles Thai numerals and Buddhist-era years: ``"๓๐/๐๖/๒๕๖๙"`` and ``"30/06/2569"`` both give
    ``date(2026, 6, 30)``. Without the era conversion a B.E. date does not fail -- ``strptime``
    accepts year 2569 -- it silently yields a date 543 years in the future.
    """
    if not value:
        return None
    text = normalize_thai_digits(value.strip())
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None
    year = to_christian_year(parsed.year)
    return parsed if year == parsed.year else parsed.replace(year=year)


def parse_year(value: str | None) -> int | None:
    """Parse a reporting-year cell, converting Buddhist-era and Thai numerals to a C.E. year.

    Separate from :func:`parse_int` on purpose: an era rule hidden inside something named
    "parse_int" would be a trap for the next caller. This one says what it does.
    """
    parsed = parse_int(normalize_thai_digits(value) if value else value)
    return None if parsed is None else to_christian_year(parsed)


def parse_int(value: str | None) -> int | None:
    """Parse an integer from a result cell (e.g. a year); None if not an integer."""
    if not value:
        return None
    match = re.search(r"-?\d+", value.replace(",", ""))
    return int(match.group(0)) if match else None


# The fifth download shape lives on a different host and is an indirection, not a file. Kept
# module-private on purpose: the other four shapes have public `SEC_*_ENDPOINT` constants in
# `constants.py`, but adding a fifth public symbol would put this fix outside a patch release.
# Promoting it is a recorded 0.23.0 consistency item.
_CAPITAL_HOST = "capital.sec.or.th"
_CAPITAL_ZIP_PATH = "get_zip_all_public_page.php"


def classify_download_href(href: str | None) -> tuple[str, str | None, str | None]:
    """
    Resolve a row href to (absolute_url, file_id, file_kind).

    - IDISC downloads: ``/public/idisc/Download?FILEID=<path>`` → file_id=<path>, kind from ext.
    - IPOS downloads:  ``/ipos/Common/IPOSGetFile.aspx?id=<id>`` → file_id="ipos:<id>", kind=None.
    - FS-package downloads: ``/public/idisc/Views/FinancialStatementDownload?query=<blob>`` →
      file_id="fsdl:<blob>", kind=None. The blob is opaque, so nothing can be derived from it.
    - Scanned originals: ``/public/idisc/views/viewdoc?…&TransId=<id>&FileSeq=<n>`` →
      file_id="viewdoc:<id>-<n>", kind=None (it answers a TIFF, but the URL does not say so).
    - Old-format filings on a DIFFERENT host: ``capital.sec.or.th/…/get_zip_all_public_page.php``
      → file_id="capfin:<comp_id>-<year>-<period>-<lang>", kind=None. This one is an
      **indirection**: the URL answers HTML whose whole body is a JavaScript redirect to a
      server-minted zip, so the download path resolves it (see
      :meth:`~settfex.services.sec.download.DocumentDownloadService.download`). The returned
      ``file_url`` is the STABLE indirection URL, never the minted target — that target is
      per-request and must not be cached.
    Relative hrefs are resolved against the SEC base URL.

    Anything else returns ``("", None, None)`` and the row produces no document — which is the
    drop path issue #127 is about, so a shape missing here is silent data loss, not a cosmetic
    gap. **Expect this list to grow**, and do not try to generalise it into "any link is a
    download": that would swallow the "display all results" ViewMore link and make every
    truncated section look like a filing. The mechanism that keeps the list honest is the loss
    accounting, not this function — shapes three and four were both found by
    ``ListingAccounting.no_link`` firing on a live CPALL listing, after a 39-page captured corpus
    had shown neither.
    """
    if not href:
        return "", None, None
    absolute = urljoin(SEC_BASE_URL + "/public/idisc/en/FinancialReport/ALL", unescape(href))
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    if "FILEID" in query:
        file_id = query["FILEID"][0]
        ext = file_id.rsplit(".", 1)[-1].lower() if "." in file_id else None
        kind = ext if ext in {"zip", "pdf", "xlsx", "doc", "docx"} else None
        return absolute, file_id, kind
    if parsed.path.lower().endswith("iposgetfile.aspx") and "id" in query:
        return absolute, f"ipos:{query['id'][0]}", None
    if parsed.path.lower().endswith("financialstatementdownload") and "query" in query:
        return absolute, f"fsdl:{query['query'][0]}", None
    if parsed.path.lower().endswith("viewdoc") and "TransId" in query:
        return absolute, f"viewdoc:{query['TransId'][0]}-{query.get('FileSeq', ['1'])[0]}", None
    if parsed.path.lower().endswith(_CAPITAL_ZIP_PATH) and "comp_id" in query:
        # `comp_id` is a DIFFERENT id space from the IDISC `set_id` in the same query — they are
        # not interchangeable, so the file_id is built from comp_id and never from set_id. `lang`
        # is load-bearing: the Thai request mints a different archive for the same filing.
        parts = (
            query["comp_id"][0],
            query.get("year", ["?"])[0],
            query.get("period", ["?"])[0],
            query.get("lang", ["?"])[0],
        )
        return absolute, "capfin:" + "-".join(parts), None
    # Not a recognized download link (e.g. a "display all results" ViewMore link) — not a doc.
    return "", None, None
