"""Typed exceptions for settfex.

Input-validation errors subclass :class:`ValueError` so existing ``except ValueError`` handlers
keep working; fetch/HTTP errors subclass :class:`Exception` and are still caught by
``except Exception``. All are therefore backward-compatible with pre-0.9 callers.

Example:
    >>> from settfex import get_highlight_data
    >>> from settfex.exceptions import SymbolNotFoundError
    >>> try:
    ...     await get_highlight_data("CPALLL")
    ... except SymbolNotFoundError as exc:
    ...     print(exc.status_code, exc.symbol, exc.suggestion)
    404 CPALLL CPALL
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, NoReturn

__all__ = [
    "FetchError",
    "SymbolNotFoundError",
    "StaleDataError",
    "ParseError",
    "IncompleteListingError",
    "HTTPStatusError",
    "CompanyNotFoundError",
    "AmbiguousCompanyError",
    "InvalidSymbolError",
    "InvalidLanguageError",
    "InvalidDateError",
    "raise_for_status",
]

# A pluggable, network-free provider mapping a not-found symbol to a close match from an
# already-available (cached) symbol list, or None. Registered by the SET stock-list service so
# this module stays a dependency-free leaf. The provider MUST NOT perform network I/O.
_symbol_suggester: Callable[[str], str | None] | None = None


def register_symbol_suggester(suggester: Callable[[str], str | None] | None) -> None:
    """Register (or clear, with ``None``) the callable used to compute a "did you mean?" suggestion.

    The provider is consulted by :func:`raise_for_status` when raising a
    :class:`SymbolNotFoundError`. It **must not** perform network I/O — it should only consult
    already-available data (e.g. a previously-fetched, in-memory symbol list) and return ``None``
    otherwise, so that a 404 never triggers an extra fetch.
    """
    global _symbol_suggester
    _symbol_suggester = suggester


class FetchError(Exception):
    """A data fetch failed.

    ``status_code`` is the HTTP status for non-2xx responses, or ``None`` for transport-level
    failures (timeouts, connection errors, retries exhausted). ``symbol`` is the stock/index
    symbol involved, when known.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        symbol: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.symbol = symbol


class SymbolNotFoundError(FetchError):
    """A symbol or index was not found (HTTP 404).

    ``suggestion`` is a close match from the SET stock-symbol list when one is available — but only
    if that list was already fetched earlier this session (it is never fetched on demand); ``None``
    otherwise. When present, the suggestion is also appended to the error message.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        symbol: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.suggestion = suggestion
        if suggestion:
            message = f"{message} — did you mean '{suggestion}'?"
        super().__init__(message, status_code=status_code, symbol=symbol)


class StaleDataError(FetchError):
    """An API answered HTTP 200 with data for a **different date** than was requested.

    ThaiBMA's yield-curve endpoint rolls back silently: a weekend, a Thai public holiday, or any
    **future** date all return the most recent curve on or before the request, with HTTP 200 and
    no signal of the substitution. Raised only when the caller opts in with ``on_rollback="raise"``
    — the default is to warn and set :attr:`~settfex.services.thaibma.YieldCurve.is_rolled_back`.

    Subclasses :class:`FetchError` because the input was valid and the *response* was not, so
    existing ``except FetchError`` handlers keep working.
    """

    def __init__(
        self,
        message: str,
        *,
        requested_date: date | None = None,
        as_of: date | None = None,
        rollback_days: int | None = None,
    ) -> None:
        super().__init__(message)
        self.requested_date = requested_date
        self.as_of = as_of
        self.rollback_days = rollback_days


class ParseError(FetchError):
    """A response arrived intact but could not be mapped to the expected shape.

    Raised when a page carries data rows the parser recognises *structurally* but cannot classify
    at all — the signature of a site-side change (a renamed section, an unhandled language) rather
    than of an empty result. An empty page is **not** an error and still returns an empty list;
    this fires only when rows were present and every one of them was unrecognised, because that is
    the case a caller cannot otherwise distinguish from "this issuer filed nothing".

    Subclasses :class:`FetchError` because the request was valid and the *response* was not, so
    existing ``except FetchError`` handlers keep working.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        rows_parsed: int | None = None,
        unknown_sections: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.rows_parsed = rows_parsed
        self.unknown_sections = list(unknown_sections or [])


class IncompleteListingError(ParseError):
    """A listing page carried real rows and every one of them was lost before it became a record.

    The sibling of :class:`ParseError`. That one fires when rows cannot be *classified*; this one
    fires when they classify fine and then vanish for another reason — on the SEC IDISC listing,
    a data row whose download link is missing. Both describe the same hazard: an empty result that
    is indistinguishable from an issuer who filed nothing.

    Deliberately narrow. A **partial** loss does not raise — it is reported on the returned model
    and logged, because a partial result is still usable and the loss is no longer silent. This
    fires only when the loss is total, which is the case the caller cannot otherwise detect.

    ``lost_rows`` is how many rows were dropped that way; ``reported`` is what the page said each
    section holds. Subclasses :class:`ParseError`, so ``except ParseError`` and
    ``except FetchError`` handlers keep working.

    ``unknown_sections`` is inherited from :class:`ParseError` and, until 0.23.0, this subclass had
    no way to accept it — so it was **always** ``[]`` here, a field that looked answered and was
    not. A page can lose every row to a missing download link *and* carry a heading nobody
    recognises, and that combination is worth seeing.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        rows_parsed: int | None = None,
        unknown_sections: list[str] | None = None,
        lost_rows: int | None = None,
        reported: dict[str, int] | None = None,
    ) -> None:
        super().__init__(
            message, url=url, rows_parsed=rows_parsed, unknown_sections=unknown_sections
        )
        self.lost_rows = lost_rows
        self.reported = dict(reported or {})


class HTTPStatusError(FetchError):
    """A request returned a non-2xx status, with the context needed to act on it.

    :class:`FetchError` already carries ``status_code``, so this adds no new *capability* — what it
    adds is a place for the two facts a caller otherwise has to scrape out of the message string:
    which URL, and which report code. An archive recording "the R562 search failed" should not have
    to parse prose to do it, which is the same failure mode this family of fixes keeps closing.

    Deliberately **not** raised via ``raise_for_status``: that helper maps 404 to
    :class:`SymbolNotFoundError` and consults the symbol suggester, which is wrong on an endpoint
    where the identifier was already resolved — a 404 there means the route or the host is wrong,
    not that an issuer does not exist. (The same reasoning gives the DR-profile and
    analyst-consensus endpoints their own handling; see CLAUDE.md's Known Gotchas.)

    Subclasses :class:`FetchError`, so ``except FetchError`` keeps working.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        report_code: str | None = None,
        symbol: str | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, symbol=symbol)
        self.url = url
        self.report_code = report_code


class CompanyNotFoundError(ValueError):
    """No SEC issuer matched the query.

    An **input** error, not a :class:`FetchError`: the request succeeded and the site answered
    honestly that it knows no such issuer. Deliberately separate from a fetch failure, because
    conflating the two is what made this worth fixing — ``get_sec_documents`` used to return an
    empty list for *both*, so "this issuer does not exist" and "the lookup broke" were the same
    answer, and a backfill recorded the window as covered either way (settfex D10, issue #131).

    Subclasses :class:`ValueError`, like the other input errors (``InvalidSymbolError``,
    ``InvalidDateError``, ``InvalidLanguageError``), so it is caught by handlers for bad input and
    **not** by ``except FetchError``.
    """


class AmbiguousCompanyError(ValueError):
    """Several SEC issuers matched the query and the site flagged none of them as *the* match.

    An **input** error, and a sibling of :class:`CompanyNotFoundError` for the same reason: the
    request succeeded, and what failed was the caller's ability to name one issuer unambiguously.
    It is a :class:`ValueError`, **not** a :class:`FetchError` — retrying will return the same
    candidates forever.

    Until 0.24.0 ``resolve_company`` answered this case by returning ``matches[0]``. That is a
    **silent guess**, and the candidate list is ordered alphabetically rather than by relevance,
    so the guess is not even a good one. Live-probed 2026-09-20:

    * ``CHINA`` (a SET **ETF**, so no SEC-registered issuer exists) returned 60 name-substring
      candidates, none flagged, and resolved to ``ASEAN CHINA INVESTMENT FUND L.P.``
    * ``UBOT`` (also an ETF) returned 13 and resolved to ``KUBOTA AYUTTHAYA (HUAHENGLEE) COMPANY
      LIMITED``
    * the Thai query ``ปตท`` (PTT) returned 17 and resolved to
      ``กองทุนสำรองเลี้ยงชีพพนักงานบริษัท ปตท.`` (the PTT employees' provident fund) — while the
      real issuer, ``บริษัท ปตท. จำกัด (มหาชน)``, sat fifth in the very same list

    Every downstream call then attaches that company's filings to the requested name, which is the
    worst version of this release's theme: not missing data, but **confidently wrong data**.

    Attributes:
        candidates: The matches the site returned, so a caller can choose one instead of guessing.
    """

    def __init__(self, message: str, *, candidates: Sequence[Any] = ()) -> None:
        super().__init__(message)
        self.candidates = list(candidates)


class InvalidSymbolError(ValueError):
    """A symbol was empty or invalid."""


class InvalidLanguageError(ValueError):
    """A language string was not recognized (not ``en``/``th`` or an accepted alias)."""


class InvalidDateError(ValueError):
    """A date string was not in the format the target API accepts (dd/MM/yyyy for SET news)."""


def raise_for_status(
    status_code: int,
    message: str,
    *,
    symbol: str | None = None,
    suggest: bool = True,
) -> NoReturn:
    """Raise :class:`SymbolNotFoundError` for HTTP 404, otherwise :class:`FetchError`.

    Args:
        status_code: The non-2xx HTTP status code from the response.
        message: The error message to attach.
        symbol: The stock/index symbol involved, when known.
        suggest: Whether to attempt a network-free "did you mean?" suggestion for a 404 (default
            True). Pass ``False`` for non-stock lookups (e.g. index symbols) that should not be
            matched against the stock-symbol list.

    Raises:
        SymbolNotFoundError: If ``status_code`` is 404.
        FetchError: For any other non-2xx status.
    """
    if status_code == 404:
        suggestion = (
            _symbol_suggester(symbol)
            if (suggest and symbol and _symbol_suggester is not None)
            else None
        )
        raise SymbolNotFoundError(
            message, status_code=status_code, symbol=symbol, suggestion=suggestion
        )
    raise FetchError(message, status_code=status_code, symbol=symbol)
