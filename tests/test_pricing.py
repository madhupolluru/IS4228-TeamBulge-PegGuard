# Bond pricing checked against hand calculations.

import pytest

from pegguard.pricing import coupon_bond_price, modified_duration, reprice_factor, zero_coupon_price


def test_zero_coupon_matches_formula():
    # Week 3: P = FV / (1 + y)^n
    assert zero_coupon_price(0.035, 1, face=100_000) == pytest.approx(96_618.36, abs=0.01)


def test_par_bond_prices_at_face():
    assert coupon_bond_price(0.05, 0.05, 10, freq=1, face=1000) == pytest.approx(1000)


def test_premium_bond_hand_calculation():
    # 10y, 7% annual coupon, YTM 5%: 70 x 7.7217 + 1000 / 1.05^10 = $1,154.43
    assert coupon_bond_price(0.07, 0.05, 10, freq=1, face=1000) == pytest.approx(1154.43, abs=0.01)


def test_rate_rise_lowers_price_and_longer_bonds_fall_more():
    short = reprice_factor("coupon", 2, 0.02, 0.03)
    long = reprice_factor("coupon", 10, 0.02, 0.03)
    assert long < short < 1


def test_no_shock_no_change():
    assert reprice_factor("coupon", 10, 0.04, 0.0) == 1.0
    assert reprice_factor("zero", 0.25, 0.04, 0.0) == 1.0


def test_duration_approximates_price_change():
    d = modified_duration("coupon", 10, 0.04)
    approx = -d * 0.001
    actual = reprice_factor("coupon", 10, 0.04, 0.001) - 1
    assert actual == pytest.approx(approx, rel=0.02)
    assert 7 < d < 9  # 10y par bond at 4%: modified duration ~8.2
