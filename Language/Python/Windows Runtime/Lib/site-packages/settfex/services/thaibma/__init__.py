"""ThaiBMA (www.thaibma.or.th) Thai government bond yield curve services.

Fetch the official Thai government bond yield curve — the fitted par curve and the bond quotes
behind it — for any date back to **1999-09-15**, plus whole years of daily history at one request
per year.

>>> from settfex.services.thaibma import ThaiBMA, get_government_yield_curve
>>>
>>> curve = await get_government_yield_curve()          # latest published curve
>>> curve.as_of, curve.yield_at("10Y"), curve.slope_bps("2Y", "10Y")
>>>
>>> tbma = ThaiBMA()
>>> history = await tbma.get_history("2020-01-01")      # 7 requests, not ~1,600
>>> history.to_dataframe()["10Y"].tail()

Two behaviours are worth knowing before you store anything:

- The curve endpoint **never 404s on a date** — a weekend, a Thai public holiday or any future
  date silently returns the most recent earlier curve. :attr:`YieldCurve.is_rolled_back` and
  :attr:`YieldCurve.rollback_days` always say whether that happened, and ``on_rollback="raise"``
  turns it into a :class:`~settfex.exceptions.StaleDataError`.
- ``yield_percent`` is in **percent** while ``change_bps`` is in **basis points**. The field names
  carry the units on purpose.
"""

from settfex.services.thaibma.availability import (
    YieldCurveAvailability,
    YieldCurveAvailabilityService,
    get_yield_curve_availability,
)
from settfex.services.thaibma.constants import (
    THAIBMA_BASE_URL,
    THAIBMA_FIRST_CURVE_DATE,
)
from settfex.services.thaibma.history import (
    HistoryKind,
    HistoryRow,
    YieldCurveHistory,
    YieldCurveHistoryService,
    get_bond_yield_history,
    get_yield_curve_history,
)
from settfex.services.thaibma.thaibma import ThaiBMA
from settfex.services.thaibma.utils import parse_tenor, tenor_label
from settfex.services.thaibma.yield_curve import (
    BondQuote,
    CurvePoint,
    RollbackPolicy,
    YieldCurve,
    YieldCurveService,
    get_government_yield_curve,
)

__all__ = [
    "THAIBMA_BASE_URL",
    "THAIBMA_FIRST_CURVE_DATE",
    "BondQuote",
    "CurvePoint",
    "HistoryKind",
    "HistoryRow",
    "RollbackPolicy",
    "ThaiBMA",
    "YieldCurve",
    "YieldCurveAvailability",
    "YieldCurveAvailabilityService",
    "YieldCurveHistory",
    "YieldCurveHistoryService",
    "YieldCurveService",
    "get_bond_yield_history",
    "get_government_yield_curve",
    "get_yield_curve_availability",
    "get_yield_curve_history",
    "parse_tenor",
    "tenor_label",
]
