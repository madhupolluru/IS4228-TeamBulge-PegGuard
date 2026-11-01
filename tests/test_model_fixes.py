# Regression tests for the model fixes: exit timing with a closed window, back-test units, Tether's minimum
# fee, Monte Carlo liquidity draws, fractional coupon maturities, $m labels in charts and the risk memo.

from datetime import date

import pytest

from pegguard import analysis, charts
from pegguard.analysis import STANDARD_SCENARIOS, exit_timing, monte_carlo, position_outcome
from pegguard.backtest import events, run_event
from pegguard.engine import Scenario, redemption_capacity, run_stress
from pegguard.presets import load_preset, redemption_terms
from pegguard.pricing import coupon_bond_price, reprice_factor
from pegguard.report import build_memo
from pegguard.reserves import ReserveLine


# 1. exit timing

def test_exit_waits_for_closed_window_when_nobody_else_redeems():
    p = load_preset("narrow")
    r = run_stress(p.lines, p.supply_bn, Scenario(redemption_pct=0, closed_days=2))
    assert exit_timing(r.daily) == (3, 1.0)
    out = position_outcome(r.summary, r.daily, 10)
    assert out["exit_day"] == 3 and out["exit_on_time"]
    assert exit_timing(run_stress(p.lines, p.supply_bn, Scenario(redemption_pct=0)).daily) == (1, 1.0)


def test_exit_when_window_stays_closed_past_the_run():
    p = load_preset("usdt_2026q2")
    r = run_stress(p.lines, p.supply_bn, Scenario(redemption_pct=0, closed_days=5, horizon_days=5, rate_shock_bp=200))
    assert exit_timing(r.daily) == (6, 1.0)
    assert position_outcome(r.summary, r.daily, 30)["redeem_value"] > 0


def test_exit_counts_frozen_cash_released_on_the_first_open_day():
    lines = [ReserveLine("Cash at bank", "cash_bank", 10.0, bank="Bank A")]
    sc = Scenario(redemption_pct=0, closed_days=2, bank_freeze_pct=100, frozen_recovery_pct=100, frozen_release_day=3)
    assert exit_timing(run_stress(lines, 10.0, sc).daily) == (3, 1.0)


# 2. back-test units

def test_usdc_backtest_requests_the_observed_dollar_flows():
    ev = next(e for e in events() if e.key == "usdc_2023")
    assert run_event(ev).summary["requested"] == pytest.approx(0.98 + 1.54 + 0.19 + 1.39, abs=1e-3)


def test_busd_march_outflow_spread_over_23_business_days():
    ev = next(e for e in events() if e.key == "busd_2023")
    path = ev.scenario.daily_requests_pct
    assert len(path) == 5 + 6 + 23
    assert sum(path[11:]) / 100 * ev.supply_bn == pytest.approx(3.02, abs=1e-2)


# 3. Tether minimum fee

def test_redemption_fee_is_the_greater_of_bp_and_minimum():
    terms = redemption_terms("usdt_2026q2")
    assert terms["fee_min_m"] == pytest.approx(0.001)
    assert redemption_terms("narrow")["fee_min_m"] == 0.0
    lines = [ReserveLine("Cash", "cash_bank", 100)]
    r = run_stress(lines, 100, Scenario(redemption_pct=10, horizon_days=2))
    small = position_outcome(r.summary, r.daily, 0.5, terms)  # $500k: 0.1% is $500, so the $1,000 minimum applies
    assert small["redeem_fee"] == pytest.approx(0.001)
    assert small["redeem_value"] == pytest.approx(0.499)
    big = position_outcome(r.summary, r.daily, 30, terms)  # $30m: 0.1% is $30,000
    assert big["redeem_fee"] == pytest.approx(0.03)


# 4. Monte Carlo

def test_monte_carlo_does_not_draw_liquidity_for_always_available_classes(monkeypatch):
    lines = [ReserveLine("Cash", "cash_bank", 10), ReserveLine("Repo", "repo", 20, 0.003, 4.0),
             ReserveLine("Bills", "tbill", 70, 0.25, 4.0)]
    base = Scenario(redemption_pct=40, horizon_days=3, front_load=0.5)
    seen = []

    def spy(lines_, supply_, sc):
        seen.append(dict(sc.class_liquidity))
        return run_stress(lines_, supply_, sc)

    monkeypatch.setattr(analysis, "run_stress", spy)
    monte_carlo(lines, 100, base, n=5)
    monkeypatch.undo()
    assert len(seen) == 5 and all(set(d) == {"tbill"} for d in seen)
    a, b = monte_carlo(lines, 100, base, n=20)[1], monte_carlo(lines, 100, base, n=20)[1]
    assert a == b


# 5. fractional coupon maturities

def test_fractional_maturity_is_priced_between_coupon_dates():
    assert coupon_bond_price(0.07, 0.05, 10, freq=1) * 1000 == pytest.approx(1154.43, abs=0.01)
    assert coupon_bond_price(0.05, 0.05, 3) == pytest.approx(1.0)
    one, one_two, one_five = (coupon_bond_price(0.05, 0.07, t) for t in (1.0, 1.2, 1.5))
    assert one_five < one_two < one  # a discount bond falls in price the longer it has left
    assert coupon_bond_price(0.05, 0.07, 1.25) == pytest.approx(coupon_bond_price(0.05, 0.07, 1.25 - 1e-7), abs=1e-6)
    assert reprice_factor("coupon", 1.2, 0.04, 0.01) < reprice_factor("coupon", 1.01, 0.04, 0.01)


# 7. chart units

def test_small_coin_hover_shows_millions():
    p = load_preset("xsgd_style")
    r = run_stress(p.lines, p.supply_bn, Scenario(redemption_pct=20))
    fig = charts.reserves_vs_tokens(r.daily)
    assert fig.data[0].customdata[0][0].endswith("m") and "bn" not in fig.data[0].hovertemplate
    assert charts._money(40.0) == "$40.00bn" and charts._money(0.0099) == "$9.9m"


def test_released_frozen_cash_is_not_counted_as_lost():
    ev = next(e for e in events() if e.key == "usdc_2023")
    b = run_event(ev).by_holding
    fig = charts.holdings_used(b)
    svb = list(b["Holding"]).index("Cash at Silicon Valley Bank")
    assert sum(float(t.x[svb]) for t in fig.data) == pytest.approx(3.3, abs=1e-3)
    assert float(fig.data[2].x[svb]) == pytest.approx(0.0, abs=1e-9)  # fully recovered


# 9. redemption capacity keeps the caller's stress level

def test_redemption_capacity_uses_scenario_stress_without_changing_it():
    lines = [ReserveLine("Bills", "tbill", 100, 0.25, 4.0)]
    sc = Scenario(redemption_pct=0, stress_multiplier=3.0)
    assert redemption_capacity(lines, days=1, sc=sc) < redemption_capacity(lines, days=1)
    assert sc.stress_multiplier == 3.0
    assert redemption_capacity(lines, days=1, stress_multiplier=1.0, sc=sc) == pytest.approx(
        redemption_capacity(lines, days=1))


# 8. risk memo

def _memo(preset_id, **meta):
    p = load_preset(preset_id)
    sc = STANDARD_SCENARIOS[3]
    r = run_stress(p.lines, p.supply_bn, sc)
    pos = position_outcome(r.summary, r.daily, 1.0)
    return build_memo({"name": p.name, "as_of": p.as_of, "basis": p.basis, **meta}, sc, r.summary, pos,
                      today=date(2026, 11, 1))


def test_memo_uses_millions_for_small_coins_and_skips_fake_dates():
    m = _memo("xsgd_style")
    assert "$0.00bn" not in m["markdown"] and "$0.00bn" not in m["html"]
    assert "as of Hypothetical" not in m["markdown"]
    assert "as of 2026-06-30" in _memo("usdt_2026q2")["markdown"]


def test_html_memo_has_limits_section():
    m = _memo("usdt_2026q2")
    assert "## Limits" in m["markdown"] and "<h2>Limits</h2>" in m["html"]
    assert "point-in-time" in m["html"]
