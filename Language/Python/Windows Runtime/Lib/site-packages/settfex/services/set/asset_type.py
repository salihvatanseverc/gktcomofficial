"""Asset-type classification for SET-listed instruments.

Maps the SET API's single-letter ``securityType`` codes (as served by ``/api/set/stock/list``
and the per-symbol profile endpoints) to a friendly :class:`AssetType` enum, so callers can
tell common stocks apart from ETFs, DRs, DWs, warrants, preferred shares, and unit trusts.

This module is a dependency-free leaf (stdlib only) so that both ``services/set/list.py`` and
the ``services/set/stock`` package can import it without any import-cycle risk.

Note:
    Bonds do not appear anywhere in these APIs (there is no ``/api/set/bond/list`` and no bond
    rows in the stock list — SET-listed debt is served elsewhere), so there is deliberately no
    ``BOND`` member.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["AssetType", "SECURITY_TYPE_TO_ASSET_TYPE"]


class AssetType(StrEnum):
    """Friendly classification of a SET-listed instrument.

    As a ``StrEnum``, ``str(AssetType.DEPOSITARY_RECEIPT)`` and f-strings render the bare
    value (``"dr"``), and members compare equal to their plain string values.
    """

    STOCK = "stock"  # S - Common Stocks
    STOCK_FOREIGN = "stock_foreign"  # F - Common Foreign Stocks (e.g. 2S-F)
    PREFERRED_STOCK = "preferred_stock"  # P - Preferred Stocks (e.g. BH-P)
    PREFERRED_STOCK_FOREIGN = "preferred_stock_foreign"  # Q - Preferred Foreign Stocks (BH-Q)
    WARRANT = "warrant"  # W - Warrants (e.g. A5-W4)
    DERIVATIVE_WARRANT = "dw"  # V - Derivative Warrants (e.g. AAV01C2609T)
    ETF = "etf"  # L - ETFs (e.g. 1DIV)
    UNIT_TRUST = "unit_trust"  # U - Unit Trusts (e.g. SCBSET)
    DEPOSITARY_RECEIPT = "dr"  # X - Depositary Receipts (e.g. GOOG80)
    UNKNOWN = "unknown"  # unrecognized or missing securityType code

    @classmethod
    def from_security_type(cls, code: str | None) -> AssetType:
        """Map a SET ``securityType`` code (e.g. ``"X"``) to an :class:`AssetType`.

        Case- and whitespace-insensitive. ``None``, empty, and unrecognized codes map to
        :attr:`UNKNOWN` — never raises, so a new SET code degrades gracefully instead of
        breaking every caller.

        Args:
            code: The raw ``securityType`` value from a SET API payload.

        Returns:
            The matching :class:`AssetType`, or :attr:`UNKNOWN`.

        Example:
            >>> AssetType.from_security_type("X")
            <AssetType.DEPOSITARY_RECEIPT: 'dr'>
        """
        if not code:
            return cls.UNKNOWN
        return SECURITY_TYPE_TO_ASSET_TYPE.get(code.strip().upper(), cls.UNKNOWN)


# securityType code -> AssetType. All nine codes observed live in /api/set/stock/list
# (2026-08-03: S=930, F=864, P=8, Q=8, U=2, V=1651, W=85, L=13, X=493 symbols). Classify by
# CODE, never by securityTypeName — the API's own display name for Q carries a typo
# ("Prefered Foreign Stocks").
SECURITY_TYPE_TO_ASSET_TYPE: dict[str, AssetType] = {
    "S": AssetType.STOCK,
    "F": AssetType.STOCK_FOREIGN,
    "P": AssetType.PREFERRED_STOCK,
    "Q": AssetType.PREFERRED_STOCK_FOREIGN,
    "W": AssetType.WARRANT,
    "V": AssetType.DERIVATIVE_WARRANT,
    "L": AssetType.ETF,
    "U": AssetType.UNIT_TRUST,
    "X": AssetType.DEPOSITARY_RECEIPT,
}
