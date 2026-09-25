"""SEC company resolution — map a symbol/name to the IDISC uniqueIDReference.

The search page identifies an issuer by a 10-digit ``uniqueIDReference`` (e.g. CPALL =
``0000003875``), obtained from a small JSON autocomplete API. This is a clean JSON POST and
reuses the existing ``AsyncDataFetcher.fetch_json`` (stateless — no SessionManager).
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import AmbiguousCompanyError
from settfex.services.sec.constants import (
    SEC_BASE_URL,
    SEC_COMPANY_SEARCH_ENDPOINT,
    SEC_REFERER,
)
from settfex.services.sec.utils import build_sec_headers
from settfex.services.set.stock.utils import Language, normalize_language
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError


class CompanyMatch(BaseModel):
    """One issuer match from the SEC company autocomplete."""

    company_name: str = Field(alias="Text", description="Full issuer name")
    unique_id: str = Field(alias="Value", description="10-digit SEC uniqueIDReference")
    is_primary: bool = Field(
        default=False,
        alias="Flag",
        description="True when the query was an identifier the SEC resolved (ticker or uniqueID)",
    )
    """True when the query was an **identifier** the site resolved, not "the best match".

    Live-probed 2026-09-20, and the distinction is load-bearing for :func:`resolve_company`:

    ====================================  ==========================  =======  ======
    query                                 kind                        matches  Flag
    ====================================  ==========================  =======  ======
    ``CPALL`` / ``cpall``                 ticker (case-insensitive)   1        True
    ``0000003875``                        the uniqueIDReference        1        True
    ``CP ALL``                            partial name                 1        False
    ``CP ALL PUBLIC COMPANY LIMITED``     **exact full legal name**    1        False
    ``CHINA`` (a SET ETF, no SEC issuer)  unknown identifier           60       False
    ====================================  ==========================  =======  ======

    So a **name** query never flags a row, not even the exact legal name — the flag says the site
    recognised the *query*, and the search itself is a plain substring match over company names
    returned in alphabetical order. That is why an unflagged multi-candidate result has no
    non-arbitrary winner, and why :func:`resolve_company` raises instead of taking the first row.

    No probed query ever returned more than one flagged row, which is what makes "no primary" a
    safe trigger rather than a tie-break.
    """

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


def _stateless_config(config: FetcherConfig | None) -> FetcherConfig:
    """Force use_session=False (the SEC host is stateless), preserving other config."""
    base = config or FetcherConfig()
    return base.model_copy(update={"use_session": False})


async def search_companies(
    query: str,
    lang: Language = "en",
    *,
    config: FetcherConfig | None = None,
) -> list[CompanyMatch]:
    """
    Search issuers by name or symbol; returns all matches (primary match flagged).

    Args:
        query: Symbol or (partial) company name, e.g. "CPALL" or "CP ALL".
        lang: Response language ('en' or 'th').
        config: Optional fetcher configuration (use_session is forced off).

    Returns:
        List of CompanyMatch (may be empty). Primary/exact matches have is_primary=True.
    """
    lang = normalize_language(lang)
    url = f"{SEC_BASE_URL}{SEC_COMPANY_SEARCH_ENDPOINT}"
    body = {"lang": lang, "content": query.strip()}
    headers = build_sec_headers(referer=SEC_REFERER, origin=True)

    logger.info(f"Resolving SEC company for query={query!r} (lang={lang})")
    async with AsyncDataFetcher(config=_stateless_config(config)) as fetcher:
        data: Any = await fetcher.fetch_json(url, headers=headers, method="POST", json_body=body)

    if not isinstance(data, list):
        # Returning [] here made a malformed response indistinguishable from "no such company",
        # which is the whole of D10 in three lines.
        error_msg = (
            f"Expected a list response from the SEC company search, got {type(data).__name__}"
        )
        logger.error(error_msg)
        raise ResponseParseError(error_msg)
    matches = [CompanyMatch.model_validate(item) for item in data]
    logger.info(f"Found {len(matches)} company match(es) for {query!r}")
    return matches


async def resolve_company(
    query: str,
    lang: Language = "en",
    *,
    config: FetcherConfig | None = None,
    allow_name_match: bool = False,
) -> CompanyMatch | None:
    """
    Resolve a symbol/name to a single best CompanyMatch.

    ``Flag`` (:attr:`CompanyMatch.is_primary`) means *the site resolved your query as an
    **identifier*** — a ticker or the uniqueIDReference — and **not** "this is the best match". A
    name never flags, not even the exact full legal name. In every live probe a query had **0 or
    1** flagged rows, never more, which is what makes "no flag" a safe trigger rather than a
    tie-break.

    =========  =========  ====================================  ==========================
    matches    flagged    ``allow_name_match=False`` (default)  ``allow_name_match=True``
    =========  =========  ====================================  ==========================
    0          --         ``None``                              ``None``
    1          yes        return it                             return it
    **1**      **no**     **raise** with that one candidate     return it
    >1         one        return the flagged one                same
    >1         none       raise                                 **raise** -- never rescued
    =========  =========  ====================================  ==========================

    The lone-unflagged row is the case ``allow_name_match`` exists for, and it is genuinely
    two different situations the library cannot tell apart:

    * you searched by **name** and got one hit -- the normal, correct outcome; pass the flag
    * you searched by **ticker** and the site did not recognise it, so what came back is a
      substring match on some *other* company's name. That is the ``UBOT`` → ``KUBOTA`` failure
      with a single candidate instead of thirteen, and it is indistinguishable from success
      without knowing which you meant.

    Args:
        query: Symbol or (partial) company name.
        lang: Response language ('en' or 'th').
        config: Optional fetcher configuration (use_session is forced off).
        allow_name_match: Accept a single **unflagged** candidate. Pass ``True`` when you are
            deliberately searching by company name. It never affects the multi-candidate case.

    Returns:
        The resolved :class:`CompanyMatch`, or ``None`` when the site knows no such issuer.

    Raises:
        AmbiguousCompanyError: Several candidates and none flagged primary; or a single unflagged
            candidate without ``allow_name_match=True``. Carries ``.candidates`` either way, so the
            caller can inspect and choose without issuing a second request.
        FetchError: On a transport failure or a non-listing response.
    """
    matches = await search_companies(query, lang, config=config)
    if not matches:
        return None
    for match in matches:
        if match.is_primary:
            return match
    if len(matches) == 1:
        if allow_name_match:
            logger.info(
                f"Resolved {query!r} to the single unflagged candidate "
                f"{matches[0].company_name!r} (allow_name_match=True)"
            )
            return matches[0]
        # Until 0.24.0 this returned the candidate unconditionally, on the reasoning that one
        # candidate leaves nothing to be ambiguous *between*. True of the candidates; false of
        # the question. The site did not recognise the query as an identifier, so the single row
        # is a substring hit on a company NAME -- which is correct when the caller typed a name,
        # and an unrelated company when they typed a ticker the SEC does not know.
        only = matches[0]
        error_msg = (
            f"{query!r} matched exactly one SEC issuer, {only.company_name!r} ({only.unique_id}), "
            f"but the site did not flag it as the match -- meaning it did not resolve {query!r} as "
            f"an identifier, and this row is a substring match on the company NAME. If you are "
            f"searching by name, that is the answer you want: pass allow_name_match=True. If "
            f"{query!r} was meant to be a symbol, the SEC does not know it (ETFs, warrants and DWs "
            f"are not issuers) and this company is unrelated. The candidate is on "
            f"`.candidates` either way."
        )
        logger.error(error_msg)
        raise AmbiguousCompanyError(error_msg, candidates=matches)

    # Until 0.24.0 this returned `matches[0]`. The autocomplete does substring matching on the
    # company NAME and returns its candidates alphabetically, not by relevance, so the first row
    # is an arbitrary company -- and when the query is not an SEC-registered issuer at all (a SET
    # ETF, a warrant, a DW), *every* candidate is unrelated. Live-probed 2026-09-20: `CHINA` (an
    # ETF) resolved to "ASEAN CHINA INVESTMENT FUND L.P." out of 60 candidates, and the Thai query
    # `ปตท` resolved to the PTT employees' provident fund while the real issuer sat fifth in the
    # same list.
    #
    # Raising rather than guessing is the whole point: every downstream listing and download would
    # otherwise attach that company's filings to the requested name. This release is about
    # incompleteness that never reaches the return value, and a confident wrong answer is the
    # sharpest form of it.
    names = ", ".join(f"{m.company_name} ({m.unique_id})" for m in matches[:5])
    error_msg = (
        f"{len(matches)} SEC issuers matched {query!r} and the site flagged none of them as the "
        f"match, so there is no non-arbitrary way to choose one. First {min(5, len(matches))}: "
        f"{names}{', …' if len(matches) > 5 else ''}. Call search_companies({query!r}) to see "
        f"every candidate and pass the one you want, or query an exact symbol. Note a SET symbol "
        f"that is not an SEC-registered issuer (an ETF, warrant or DW) will never match one."
    )
    logger.error(error_msg)
    raise AmbiguousCompanyError(error_msg, candidates=matches)
