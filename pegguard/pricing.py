# Bond pricing (Week 3 maths) used to mark reserves to market after a rate shock.
# Yields and shocks are decimals: 0.045 = 4.5%, 0.01 = +100bp.

from __future__ import annotations

MIN_YIELD = -0.99  # guard against (1 + y) <= 0


def zero_coupon_price(y: float, t_years: float, face: float = 1.0) -> float:
    # Price of a zero-coupon bond paying `face` in `t_years`.
    if t_years <= 0:
        return face
    y = max(y, MIN_YIELD)
    return face / (1.0 + y) ** t_years


def coupon_bond_price(
    coupon_rate: float, y: float, t_years: float, freq: int = 2, face: float = 1.0
) -> float:
    # Price of a plain-vanilla coupon bond (coupon quoted as APR, paid `freq` times a year).
    if t_years <= 0:
        return face
    n = max(1, round(t_years * freq))
    per = max(y, MIN_YIELD) / freq
    cpn = face * coupon_rate / freq
    if abs(per) < 1e-12:
        return cpn * n + face
    annuity = (1 - (1 + per) ** -n) / per
    return cpn * annuity + face / (1 + per) ** n


def reprice_factor(kind: str, maturity_years: float, y: float, shock: float) -> float:
    # value after an instant rate shock, as a fraction of value before it.
    # Bills (1 year or less) are zero-coupon; longer bonds are coupon bonds bought at par.
    if maturity_years <= 0 or shock == 0:
        return 1.0
    if kind == "zero" or maturity_years <= 1.0:
        return zero_coupon_price(y + shock, maturity_years) / zero_coupon_price(y, maturity_years)
    before = coupon_bond_price(y, y, maturity_years)
    after = coupon_bond_price(y, y + shock, maturity_years)
    return after / before


def modified_duration(kind: str, maturity_years: float, y: float, bump: float = 1e-4) -> float:
    # Numerical modified duration: % price change for a 1-unit change in yield.
    if maturity_years <= 0:
        return 0.0
    up = reprice_factor(kind, maturity_years, y, bump)
    down = reprice_factor(kind, maturity_years, y, -bump)
    return -(up - down) / (2 * bump)
