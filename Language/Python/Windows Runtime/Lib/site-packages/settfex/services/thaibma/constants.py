"""Constants for the ThaiBMA (www.thaibma.or.th) government bond yield curve services.

ThaiBMA — the Thai Bond Market Association — is the official publisher of the Thai government
bond yield curve. Unlike ``www.set.or.th`` and ``market.sec.or.th``, this host is a plain,
**stateless JSON API** behind an ASP.NET Web API: live-probed 2026-08-10 it answers with no
cookies, no ``User-Agent``, no referer and no bot wall, in ~0.2 s per request, and served 20
concurrent requests in 0.34 s without throttling.

Both ``/yieldcurve/...`` and ``/api/yieldcurve/...`` resolve to the same handlers. This package
uses the bare ``/yieldcurve/...`` form — it is what the site's own front-end calls, so it is the
form least likely to be retired.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

# Base URL for every ThaiBMA endpoint in this package.
THAIBMA_BASE_URL = "https://www.thaibma.or.th"

# Point-in-time government yield curve. ``{curve_date}`` must be a zero-padded YYYY-MM-DD; the
# date segment is optional and omitting it returns the latest available curve.
# Returns {"Curve": [{Asof, X, Y}], "Stat": [{Asof, Symbol, ...}]}.
THAIBMA_GOV_CURVE_ENDPOINT = "/yieldcurve/gov"

# Constant-maturity yield history for a whole calendar year, one row per business day:
# [{"asof": ..., "1M": ..., "3M": ..., "1Y": ..., ... "50Y": ...}].
# The tenor columns grow over time (14 in 1999, 54 in 2026) — see history.py.
THAIBMA_TENOR_HISTORY_ENDPOINT = "/yieldcurve/getintpttm"

# Per-bond yield history for a whole calendar year, one row per business day:
# [{"asof": ..., "T-BILL1M": ..., "LB776A": ...}]. Bond-symbol columns differ every year.
# This is a SUPERSET of the daily ``Stat`` panel — it also carries inflation-linked (ILB) and
# amortizing (LBA) issues that are quoted but excluded from curve fitting.
THAIBMA_BOND_HISTORY_ENDPOINT = "/yieldcurve/getbyyear"

# Data-availability window: a 2-element array ["<first>", "<last>"] of ISO datetimes.
THAIBMA_AVAIL_ENDPOINT = "/yieldcurve/avail"

# Calendar years with curve data: a bare array of ints, [1999, ..., <current year>].
THAIBMA_AVAIL_YEAR_ENDPOINT = "/yieldcurve/availyear"

# The wire date format. The API is strict about zero-padding: "2026-8-10" returns an HTML 404,
# so every date is re-emitted through this format rather than interpolated verbatim.
THAIBMA_DATE_FORMAT = "%Y-%m-%d"

# First date the government curve exists for, reported by /yieldcurve/avail and confirmed by
# bisection (1999-09-14 -> JSON ``null``, 1999-09-15 -> data). Requests for earlier dates return
# HTTP 200 with a body of literal ``null`` rather than a 404.
THAIBMA_FIRST_CURVE_DATE = date(1999, 9, 15)

# Client-side typo guard on the ``year`` argument, deliberately wider than the served range —
# the API reports an unserved year with an empty list, so a narrow client check would be the
# only thing rejecting a year ThaiBMA later starts publishing.
THAIBMA_MIN_YEAR = 1999
THAIBMA_MAX_YEAR = 2100

# The classification flags in the ``Stat`` payload were never backfilled: IsBenchmark is all-false
# before 2013 and IsSynthetic all-false before 2014 (probed 2026-08-10 — 2012-01-04: 0/0,
# 2013-01-04: 8/0, 2014-01-06: 5/17). Filtering history on these flags silently yields nothing
# for the first ~14 years, so the models expose them but never imply they mean anything earlier.
THAIBMA_FIRST_BENCHMARK_FLAG_YEAR = 2013
THAIBMA_FIRST_SYNTHETIC_FLAG_YEAR = 2014

# Thailand observes no DST, so the API's implicit +07:00 is equivalent to Asia/Bangkok. Kept as a
# ZoneInfo (not a fixed offset) so "today in Bangkok" comes from the real zone.
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

# The standard sub-year tenor labels and their exact position on the curve's ``X`` grid. Verified
# 2026-08-10: every ``getintpttm`` column equals the point-in-time ``Curve.Y`` at the same grid
# position to 6 decimals, so the mapping is exact rather than a day-count approximation.
THAIBMA_SUBYEAR_TENORS: dict[str, float] = {
    "1M": 28 / 365,  # 0.076712...
    "3M": 91 / 365,  # 0.249315...
    "6M": 182 / 365,  # 0.498630...
}
