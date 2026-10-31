# Unit tests for the engine mechanics: price impact, closed window, named bank failure,
# curve twist, interest, market price, panic signal, redemption access, regimes, sensitivity tools.

from dataclasses import replace

import pytest

from pegguard.analysis import (bank_concentration, monte_carlo, position_outcome, projected_run, regulatory_check,
                               tornado)
from pegguard.engine import Scenario, _Market, run_stress
from pegguard.report import build_memo
from pegguard.reserves import ReserveLine
from pegguard.treasury import Holding, safe_caps, stress_treasury, treasury_summary
from pegguard.validation import validate_portfolio, validate_scenario


def bills(amount=100.0):
    return [ReserveLine("Bills", "tbill", amount, 0.25, 4.0)]


# price impact

def test_market_cost_is_additive_and_capped():
    m = _Market(0.002, 0.01, 50.0)
    assert m.cost(0, 10) + m.cost(10, 15) == pytest.approx(m.cost(0, 25))
    huge = _Market(0.5, 5.0, 1.0)
    assert huge.cost(0, 100) <= 0.95 * 100 + 1e-9  # a sale always raises at least 5 cents per dollar
    assert huge.marginal(1e6) == pytest.approx(0.95)


def test_bigger_sales_pay_a_bigger_discount_per_dollar():
    small = run_stress(bills(), 100, Scenario(redemption_pct=5, horizon_days=1, spillover=0)).summary
    big = run_stress(bills(), 100, Scenario(redemption_pct=45, horizon_days=1, spillover=0)).summary
    assert big["fire_sale_loss"] / big["paid"] > small["fire_sale_loss"] / small["paid"]


def test_no_impact_reproduces_fixed_discounts():
    sc = Scenario(redemption_pct=20, horizon_days=1, price_impact=0, spillover=0, accrue_interest=False)
    r = run_stress(bills(), 100, sc)
    sold = r.by_holding["Sold ($bn)"].iloc[0]
    assert r.summary["fire_sale_loss"] == pytest.approx(sold * 0.0010)  # 10bp base discount
    assert r.summary["paid"] == pytest.approx(20)


def test_spillover_marks_down_unsold_holdings():
    sc = Scenario(redemption_pct=30, horizon_days=1, accrue_interest=False)
    on = run_stress(bills(), 100, sc).summary
    off = run_stress(bills(), 100, replace(sc, spillover=0)).summary
    assert on["spillover_loss"] > 0 and off["spillover_loss"] == 0
    assert on["min_coverage"] < off["min_coverage"]


# closed window

def test_closed_window_delays_payment_but_moves_the_deadline():
    cash = [ReserveLine("Cash", "cash_bank", 100)]
    sc = Scenario(redemption_pct=30, horizon_days=1, closed_days=2)
    r = run_stress(cash, 100, sc)
    assert r.daily.loc[r.daily["day"].isin([1, 2]), "paid"].sum() == 0
    assert r.daily.loc[r.daily["day"] == 3, "paid"].iloc[0] == pytest.approx(30)
    assert r.summary["first_breach_day"] is None  # deadlines start when the window reopens
    assert r.summary["verdict"] == "HOLDS"


def test_explicit_daily_path_overrides_run_size():
    cash = [ReserveLine("Cash", "cash_bank", 100)]
    r = run_stress(cash, 100, Scenario(daily_requests_pct=(10, 25, 5), redemption_pct=90))
    assert r.summary["requested"] == pytest.approx(40)
    assert r.daily.loc[r.daily["day"] == 2, "new_requests"].iloc[0] == pytest.approx(25)


# bank failure

def test_failed_bank_freezes_only_that_bank():
    lines = [ReserveLine("SVB", "cash_bank", 3.3, bank="Silicon Valley Bank"),
             ReserveLine("Other", "cash_bank", 6.0, bank="BNY Mellon"),
             ReserveLine("Fund", "mmf_gov", 30.7, 0.08, 4.5)]
    sc = Scenario(redemption_pct=0, bank_freeze_pct=100, failed_bank="Silicon Valley Bank", frozen_recovery_pct=0)
    s = run_stress(lines, 40, sc).summary
    assert s["frozen"] == pytest.approx(3.3)
    assert s["day0_coverage"] == pytest.approx(36.7 / 40)


def test_bank_concentration_and_mica_checks():
    lines = [ReserveLine("A", "cash_bank", 30, bank="Bank A"), ReserveLine("B", "cash_bank", 5, bank="Bank B"),
             ReserveLine("Bills", "tbill", 65, 0.1, 4.0)]
    conc = bank_concentration(lines)
    assert conc.iloc[0]["Bank"] == "Bank A" and conc.iloc[0]["Share of reserves"] == pytest.approx(0.30)
    mica = regulatory_check(lines, 100)["MiCA"]
    checks = {c["Check"]: c["Passes"] for c in mica["checks"]}
    assert checks["At least 30% in bank deposits"] is True
    assert checks["No more than 25% at one bank"] is False
    assert mica["passes"] is False
    assert regulatory_check(lines, 100, significant=True)["MiCA"]["checks"][1]["Passes"] is False  # 35% < 60%


def test_hong_kong_allows_bills_up_to_one_year():
    lines = [ReserveLine("Bills 9m", "tbill", 100, 0.75, 4.0)]
    rc = regulatory_check(lines, 100)
    assert rc["HK"]["passes"] is True
    assert rc["MAS"]["passes"] is False and rc["GENIUS"]["passes"] is False


# rates and interest

def test_steepener_hits_long_notes_harder_than_bills():
    lines = [ReserveLine("Bills", "tbill", 50, 0.25, 4.0), ReserveLine("10y", "tnote", 50, 10, 4.0)]
    base = run_stress(lines, 100, Scenario(rate_shock_bp=100, redemption_pct=0)).by_holding
    steep = run_stress(lines, 100, Scenario(rate_shock_bp=100, curve_twist_bp=100, redemption_pct=0)).by_holding
    assert steep["Rate loss ($bn)"].iloc[0] == pytest.approx(base["Rate loss ($bn)"].iloc[0])  # 3m unchanged
    assert steep["Rate loss ($bn)"].iloc[1] > base["Rate loss ($bn)"].iloc[1]


def test_reserves_earn_interest_during_the_run():
    s = run_stress([ReserveLine("Repo", "repo", 100, 0.003, 3.65)], 100,
                   Scenario(redemption_pct=0, horizon_days=10)).summary
    assert s["interest_income"] == pytest.approx(100 * ((1 + 0.0365 / 365) ** 10 - 1))
    assert s["final_coverage"] > 1


# market price

def test_market_prices_frozen_cash_at_the_markets_guess():
    lines = [ReserveLine("SVB", "cash_bank", 3.3, bank="SVB"), ReserveLine("Fund", "mmf_gov", 36.7, 0.08, 4.5)]
    sc = Scenario(redemption_pct=0, bank_freeze_pct=100, failed_bank="SVB", frozen_recovery_pct=100,
                  closed_days=2, market_frozen_recovery_pct=0, market_required_return_pct=0)
    s = run_stress(lines, 40, sc).summary
    assert s["min_token_value"] == pytest.approx(1.0)  # the model's own view: fully recoverable
    assert s["min_market_price"] == pytest.approx(36.7 / 40)  # the market fears the worst
    feared = run_stress(lines, 40, replace(sc, fear_premium_bp=400)).summary
    assert feared["min_market_price"] == pytest.approx(36.7 / 40 - 0.04)


def test_panic_can_react_to_the_market_price_with_a_lag():
    lines = [ReserveLine("Cash", "cash_bank", 96)]
    base = Scenario(redemption_pct=20, horizon_days=5, panic_sensitivity=0.3)
    now = run_stress(lines, 100, base).daily
    lagged = run_stress(lines, 100, replace(base, panic_lag_days=2)).daily
    # with a lag, holders on day 2 still see day 0's coverage, so the panic has not yet built up
    assert lagged.loc[2, "panic_multiplier"] <= now.loc[2, "panic_multiplier"] + 1e-12
    mkt = run_stress(lines, 100, replace(base, panic_signal="market")).summary
    assert mkt["requested"] > run_stress(lines, 100, replace(base, panic_sensitivity=0)).summary["requested"]


# position

def test_small_holder_below_minimum_must_sell():
    lines = [ReserveLine("Cash", "cash_bank", 100)]
    r = run_stress(lines, 100, Scenario(redemption_pct=10, horizon_days=2))
    out = position_outcome(r.summary, r.daily, 0.05, {"direct": True, "fee_bp": 10, "min_m": 0.1})
    assert out["can_redeem"] is False and out["best_route"] == "sell"
    big = position_outcome(r.summary, r.daily, 50, {"direct": True, "fee_bp": 10, "min_m": 0.1})
    assert big["can_redeem"] and big["redeem_fee"] == pytest.approx(0.05)
    assert big["redeem_value"] == pytest.approx(49.95)


# sensitivity tools

def test_tornado_ranks_assumptions_and_monte_carlo_is_reproducible():
    lines = [ReserveLine("Cash", "cash_bank", 10), ReserveLine("Bills", "tbill", 50, 0.25, 4.0),
             ReserveLine("Loans", "secured_loan", 42)]
    sc = Scenario(redemption_pct=40, horizon_days=5, stress_multiplier=2)
    t = tornado(lines, 100, sc)
    assert list(t["swing"]) == sorted(t["swing"], reverse=True)
    assert any(t["Assumption"].str.startswith("Liquidity:"))
    df1, st1 = monte_carlo(lines, 100, sc, n=40, seed=1)
    df2, st2 = monte_carlo(lines, 100, sc, n=40, seed=1)
    assert st1 == st2 and len(df1) == 40
    assert 0 <= st1["p_holds"] <= 1 and st1["value_p5"] <= st1["value_median"]


def test_projected_run_from_live_supply_drop():
    assert projected_run(-5.0, days=5) == pytest.approx(5.0)
    assert projected_run(2.0) == 0.0
    assert projected_run(float("nan")) == 0.0
    assert projected_run(-300, days=5) == 100.0


# treasury

def test_treasury_diversification_is_never_negative():
    d = stress_treasury([Holding("usdc_2026_09", 60), Holding("usdt_2026q2", 40)])
    s = treasury_summary(d, 100, 50)
    assert s["diversification_m"] >= -1e-9
    assert s["worst_loss_m"] <= s["sum_of_worsts_m"] + 1e-9
    caps = safe_caps(["usdc_2026_09", "usdt_2026q2"], 100, 50)
    assert (caps["cap_m"] > 0).all()


# validation, memo

def test_validation_catches_unit_mistakes():
    lines = [ReserveLine("Bills", "tbill", 100, 2.0, 450.0)]
    issues = validate_portfolio(lines, 100)
    msgs = " ".join(m for _, m in issues)
    assert "450.0%" in msgs and "bill maturing after 1 year" in msgs
    assert ("error", "Tokens outstanding must be above zero.") in validate_portfolio(lines, 0)
    assert validate_scenario(Scenario(bank_freeze_pct=50, failed_bank="Nowhere"),
                             [ReserveLine("Cash", "cash_bank", 1, bank="Bank A")])[0][0] == "warning"


def test_memo_has_the_verdict_and_escapes_html():
    lines = [ReserveLine("Cash", "cash_bank", 100)]
    r = run_stress(lines, 100, Scenario(redemption_pct=10))
    out = position_outcome(r.summary, r.daily, 30)
    memo = build_memo({"name": "<script>x</script>", "basis": "hypothetical"}, r.scenario, r.summary, out)
    assert "HOLDS" in memo["markdown"] and "<script>x" not in memo["html"]
