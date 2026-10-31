# Invariants the engine must satisfy for *any* portfolio and scenario, checked on random inputs (hypothesis).
# These are the rules a reviewer would check by hand: money is conserved, worse shocks never help, nothing is
# worth more than $1, and a coin backed only by cash never fails.

from dataclasses import replace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pegguard.engine import PAR_TOL, Scenario, run_stress
from pegguard.reserves import ReserveLine

SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])

MATURITY = {"cash_bank": (0.0, 0.0), "repo": (0.003, 0.02), "mmf_gov": (0.05, 0.15), "tbill": (0.02, 1.0),
            "tnote": (1.5, 30.0), "agency_mbs": (2.0, 10.0), "commercial_paper": (0.05, 0.75), "gold": (0.0, 0.0),
            "crypto": (0.0, 0.0), "other": (0.0, 0.0), "secured_loan": (0.0, 0.0)}


@st.composite
def reserve_line(draw):
    cls = draw(st.sampled_from(sorted(MATURITY)))
    lo, hi = MATURITY[cls]
    return ReserveLine(name=f"{cls}-{draw(st.integers(0, 999))}", asset_class=cls,
                       amount_bn=draw(st.floats(0.1, 50.0)), maturity_years=draw(st.floats(lo, hi)),
                       yield_pct=draw(st.floats(0.0, 6.0)) if hi > 0 else 0.0,
                       bank=draw(st.sampled_from(["", "Bank A", "Bank B"])) if cls == "cash_bank" else "")


@st.composite
def portfolio(draw):
    lines = draw(st.lists(reserve_line(), min_size=1, max_size=5))
    reserves = sum(l.amount_bn for l in lines)
    supply = reserves / draw(st.floats(0.9, 1.1))
    return lines, supply


@st.composite
def scenario(draw):
    return Scenario(
        rate_shock_bp=draw(st.floats(-100, 500)), curve_twist_bp=draw(st.floats(-100, 100)),
        redemption_pct=draw(st.floats(0, 90)), horizon_days=draw(st.integers(1, 10)),
        front_load=draw(st.floats(0, 0.8)), closed_days=draw(st.integers(0, 3)),
        bank_freeze_pct=draw(st.floats(0, 100)), failed_bank=draw(st.sampled_from(["", "Bank A"])),
        frozen_recovery_pct=draw(st.floats(0, 100)), frozen_release_day=draw(st.integers(0, 6)),
        max_wait_days=draw(st.integers(0, 5)), stress_multiplier=draw(st.floats(0.5, 4)),
        price_impact=draw(st.floats(0, 2)), spillover=draw(st.floats(0, 1)),
        crypto_shock_pct=draw(st.floats(-80, 0)), gold_shock_pct=draw(st.floats(-30, 0)),
        risk_shock_pct=draw(st.floats(-50, 0)), loan_loss_pct=draw(st.floats(0, 50)),
        panic_sensitivity=draw(st.floats(0, 0.5)), panic_signal=draw(st.sampled_from(["coverage", "market"])),
        panic_lag_days=draw(st.integers(0, 2)),
    )


@SETTINGS
@given(portfolio(), scenario())
def test_money_is_conserved(pf, sc):
    lines, supply = pf
    s = run_stress(lines, supply, sc).summary
    reserves0 = sum(l.amount_bn for l in lines)
    expected = (reserves0 - s["rate_loss"] - s["price_loss"] - s["frozen_writedown"] - s["fire_sale_loss"]
                - s["spillover_loss"] + s["interest_income"] - s["paid"])
    assert s["reserves_end"] == pytest.approx(expected, rel=1e-9, abs=1e-9)
    assert s["supply_end"] == pytest.approx(supply - s["paid"], rel=1e-9, abs=1e-9)


@SETTINGS
@given(portfolio(), scenario())
def test_outputs_stay_in_range(pf, sc):
    lines, supply = pf
    r = run_stress(lines, supply, sc)
    s = r.summary
    assert 0.0 <= s["min_token_value"] <= 1.0
    assert 0.0 <= s["min_market_price"] <= 1.0
    assert s["paid"] <= s["requested"] + 1e-9
    assert s["unmet"] >= -1e-9 and s["late_amount"] >= -1e-9
    assert s["fire_sale_loss"] >= -1e-12 and s["spillover_loss"] >= -1e-12
    assert (r.daily["paid"] >= -1e-12).all()
    # nothing is paid while the redemption window is closed
    assert r.daily.loc[~r.daily["open"], "paid"].abs().sum() == 0.0
    assert s["verdict"] in ("HOLDS", "LIQUIDITY STRAIN", "BELOW PAR", "BREAKS")


@SETTINGS
@given(portfolio(), scenario(), st.floats(0, 300))
def test_bigger_rate_shock_never_helps(pf, sc, extra):
    lines, supply = pf
    # Interest accrual is switched off: a higher yield also earns more over the run, which can offset a tiny
    # repricing loss on very short bills. Here we test the valuation effect only.
    sc = replace(sc, accrue_interest=False, panic_sensitivity=0.0)
    lo = run_stress(lines, supply, sc).summary
    hi = run_stress(lines, supply, replace(sc, rate_shock_bp=sc.rate_shock_bp + extra)).summary
    assert hi["day0_coverage"] <= lo["day0_coverage"] + 1e-12
    assert hi["min_coverage"] <= lo["min_coverage"] + 1e-9


@SETTINGS
@given(portfolio(), scenario(), st.floats(1.0, 3.0))
def test_harsher_fire_sales_never_help(pf, sc, factor):
    lines, supply = pf
    sc = replace(sc, panic_sensitivity=0.0)
    lo = run_stress(lines, supply, sc).summary
    hi = run_stress(lines, supply, replace(sc, stress_multiplier=sc.stress_multiplier * factor)).summary
    assert hi["min_token_value"] <= lo["min_token_value"] + 1e-9
    assert hi["late_amount"] >= lo["late_amount"] - 1e-9


@SETTINGS
@given(portfolio(), scenario(), st.floats(0, 40))
def test_bigger_run_never_reduces_late_payments(pf, sc, extra):
    lines, supply = pf
    sc = replace(sc, panic_sensitivity=0.0, redemption_pct=min(sc.redemption_pct, 60))
    lo = run_stress(lines, supply, sc).summary
    hi = run_stress(lines, supply, replace(sc, redemption_pct=sc.redemption_pct + extra)).summary
    assert hi["late_amount"] >= lo["late_amount"] - 1e-9
    assert hi["unmet"] >= lo["unmet"] - 1e-9


@SETTINGS
@given(st.floats(1, 100), st.floats(0, 100), st.integers(1, 10), st.floats(0, 0.9), st.floats(0.5, 5))
def test_cash_only_coin_always_holds(amount, run, days, front, stress):
    lines = [ReserveLine("Cash", "cash_bank", amount)]
    s = run_stress(lines, amount, Scenario(redemption_pct=run, horizon_days=days, front_load=front,
                                           stress_multiplier=stress, rate_shock_bp=300)).summary
    assert s["verdict"] == "HOLDS"
    assert s["min_coverage"] >= PAR_TOL
    assert s["min_market_price"] == pytest.approx(1.0)


@SETTINGS
@given(st.sampled_from(["tbill", "tnote", "crypto", "secured_loan", "commercial_paper"]), st.floats(5, 80),
       st.floats(10, 90), st.floats(0.5, 4))
def test_splitting_a_holding_changes_nothing(cls, amount, run, stress):
    # Two identical half-size lines of the same asset share one market, so price impact must add up exactly.
    lo, hi = MATURITY[cls]
    mat = (lo + hi) / 2
    one = [ReserveLine("A", cls, amount, mat, 4.0)]
    two = [ReserveLine("A1", cls, amount / 2, mat, 4.0), ReserveLine("A2", cls, amount / 2, mat, 4.0)]
    sc = Scenario(redemption_pct=run, horizon_days=3, stress_multiplier=stress)
    a, b = run_stress(one, amount, sc).summary, run_stress(two, amount, sc).summary
    for k in ("paid", "fire_sale_loss", "spillover_loss", "min_coverage", "late_amount"):
        assert a[k] == pytest.approx(b[k], rel=1e-7, abs=1e-9)
