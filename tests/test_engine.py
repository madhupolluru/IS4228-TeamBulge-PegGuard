# Core engine behaviour: backing, liquidity, waterfall, rate shocks, frozen cash, deadlines, positions.

from dataclasses import replace

import pytest

from pegguard.analysis import (breaking_point_grid, exit_timing, position_outcome, regulatory_check, stress_sensitivity,
                               survival_frontier)
from pegguard.engine import Scenario, run_stress
from pegguard.presets import load_preset, preset_ids
from pegguard.reserves import ReserveLine


def cash_only(amount=100.0):
    return [ReserveLine("Cash", "cash_bank", amount)]


def test_fully_backed_cash_coin_survives_any_run():
    r = run_stress(cash_only(), 100, Scenario(redemption_pct=90, horizon_days=3))
    s = r.summary
    assert s["verdict"] == "HOLDS"
    assert s["paid"] == pytest.approx(90)
    assert s["final_coverage"] == pytest.approx(1.0)


def test_bank_freeze_with_zero_recovery_lowers_coverage():
    s = run_stress(cash_only(), 100, Scenario(redemption_pct=0, bank_freeze_pct=10, frozen_recovery_pct=0)).summary
    assert s["day0_coverage"] == pytest.approx(0.90)
    assert s["verdict"] == "BELOW PAR"


def test_paying_redeemers_at_par_hurts_those_who_stay():
    # Reserves 90 vs 100 tokens. Redeeming 50 at $1 leaves 40 of reserves for 50 tokens.
    lines = [ReserveLine("Cash", "cash_bank", 90.0)]
    s = run_stress(lines, 100, Scenario(redemption_pct=50, horizon_days=1)).summary
    assert s["final_coverage"] == pytest.approx(40 / 50)
    assert s["final_coverage"] < s["day0_coverage"]


def test_liquidity_limit_creates_unmet_redemptions():
    lines = [ReserveLine("Loans", "secured_loan", 100.0)]  # 2% sellable per day
    s = run_stress(lines, 100, Scenario(redemption_pct=50, horizon_days=2)).summary
    assert s["unmet"] > 0
    assert s["first_breach_day"] == 1
    assert s["verdict"] in ("LIQUIDITY STRAIN", "BREAKS")


def test_waterfall_uses_cash_before_bills():
    lines = [ReserveLine("Bills", "tbill", 50, 0.25, 4.0), ReserveLine("Cash", "cash_bank", 50)]
    r = run_stress(lines, 100, Scenario(redemption_pct=30, horizon_days=1, stress_multiplier=1))
    sold = dict(zip(r.by_holding["Holding"], r.by_holding["Sold ($bn)"]))
    assert sold["Cash"] == pytest.approx(30)
    assert sold["Bills"] == pytest.approx(0)


def test_rate_shock_hits_long_notes_not_cash():
    lines = [ReserveLine("Cash", "cash_bank", 50), ReserveLine("10y", "tnote", 50, 10, 2.0)]
    s = run_stress(lines, 100, Scenario(rate_shock_bp=300, redemption_pct=0)).summary
    assert s["rate_loss"] > 5  # 10y note loses >10% on +300bp
    assert s["day0_coverage"] < 0.95


def test_stable_nav_mmf_holds_buck_on_small_shock():
    lines = [ReserveLine("MMF", "mmf_gov", 100, 0.08, 4.0)]
    s = run_stress(lines, 100, Scenario(rate_shock_bp=200, redemption_pct=0)).summary
    assert s["rate_loss"] == pytest.approx(0)


def test_panic_feedback_increases_redemptions():
    lines = [ReserveLine("Cash", "cash_bank", 95.0)]
    calm = run_stress(lines, 100, Scenario(redemption_pct=20, horizon_days=5)).summary
    panic = run_stress(lines, 100, Scenario(redemption_pct=20, horizon_days=5, panic_sensitivity=0.3)).summary
    assert panic["requested"] > calm["requested"]


def test_all_presets_run_and_breaking_grid_is_monotonic():
    for pid in preset_ids():
        p = load_preset(pid)
        assert run_stress(p.lines, p.supply_bn, Scenario()).summary["verdict"]
    p = load_preset("svb_style")
    grid = breaking_point_grid(p.lines, p.supply_bn, Scenario(horizon_days=5),
                               rate_shocks_bp=[0, 100, 300], run_sizes_pct=[0, 20, 40])
    # coverage never improves when the rate shock grows, for the same run size
    for run, g in grid.groupby("run_pct"):
        cov = g.sort_values("rate_shock_bp")["min_coverage"].tolist()
        assert all(a >= b - 1e-12 for a, b in zip(cov, cov[1:]))
    assert len(survival_frontier(grid)) == 3


def test_regulatory_check_flags_long_bills_and_crypto():
    lines = [ReserveLine("Bills 6m", "tbill", 50, 0.5, 4.0), ReserveLine("BTC", "crypto", 10),
             ReserveLine("Bills 1m", "tbill", 50, 0.08, 4.0)]
    rc = regulatory_check(lines, 100)
    assert rc["GENIUS"]["eligible_bn"] == pytest.approx(50)
    assert rc["GENIUS"]["passes"] is False
    assert rc["MAS"]["eligible_bn"] == pytest.approx(50)


def test_position_cash_coin_exits_day_one_without_loss():
    r = run_stress(cash_only(), 100, Scenario(redemption_pct=90, horizon_days=3))
    out = position_outcome(r.summary, r.daily, 30.0)
    assert out["exit_day"] == 1
    assert out["exit_share_paid"] == pytest.approx(1.0)
    assert out["hold_loss"] == pytest.approx(0.0)


def test_position_illiquid_coin_cannot_exit_on_time():
    # Only 2% of loans can be sold per day, so a 50% run on day 1 is never fully paid within 5 days.
    lines = [ReserveLine("Loans", "secured_loan", 100)]
    r = run_stress(lines, 100, Scenario(redemption_pct=50, horizon_days=5, front_load=0.9))
    day, share = exit_timing(r.daily)
    assert day is None
    assert 0 < share < 1


def test_position_loss_matches_worst_token_value():
    lines = [ReserveLine("10y note", "tnote", 100, maturity_years=10, yield_pct=2.0)]
    r = run_stress(lines, 100, Scenario(rate_shock_bp=300, redemption_pct=0, horizon_days=1))
    out = position_outcome(r.summary, r.daily, 1_000.0)
    assert out["hold_loss"] == pytest.approx(1_000.0 * (1 - r.summary["min_token_value"]))
    assert out["haircut_bp"] > 1_000  # a 300bp shock on a 10y note costs well over 10%


def test_frozen_cash_released_later_pays_the_queue():
    # 60 of 100 bank cash frozen but fully recoverable. Stuck for the whole run it causes late payments;
    # released on day 2 it pays everyone, so the coin holds.
    sc = Scenario(redemption_pct=80, horizon_days=3, front_load=0.0, bank_freeze_pct=60, frozen_recovery_pct=100)
    stuck = run_stress(cash_only(), 100, sc).summary
    freed = run_stress(cash_only(), 100, replace(sc, frozen_release_day=2)).summary
    assert stuck["verdict"] == "LIQUIDITY STRAIN"
    assert freed["verdict"] == "HOLDS"
    assert freed["frozen_released_day"] == 2
    assert freed["paid"] == pytest.approx(80)


def test_released_cash_with_partial_recovery_is_still_a_loss():
    sc = Scenario(redemption_pct=0, bank_freeze_pct=50, frozen_recovery_pct=60, frozen_release_day=1, horizon_days=2)
    s = run_stress(cash_only(), 100, sc).summary
    assert s["frozen_writedown"] == pytest.approx(20)
    assert s["final_coverage"] == pytest.approx(0.80)


def test_deadline_decides_what_counts_as_late():
    # 10 cash + 90 loans (1.8 sellable a day). A 13% run on day 1 is paid by day 2: late same-day, on time at T+1.
    lines = [ReserveLine("Cash", "cash_bank", 10.0), ReserveLine("Loans", "secured_loan", 90.0)]
    sc = Scenario(redemption_pct=13, horizon_days=1, front_load=0.0)
    same_day = run_stress(lines, 95, sc).summary
    next_day = run_stress(lines, 95, replace(sc, max_wait_days=1)).summary
    assert same_day["first_breach_day"] == 1
    assert same_day["late_amount"] > 0
    assert next_day["first_breach_day"] is None
    assert next_day["unmet"] == pytest.approx(0)  # the run is extended past the horizon to clear the queue
    assert next_day["days_simulated"] == 2


def test_stress_sensitivity_includes_current_level_and_worsens():
    p = load_preset("svb_style")
    df = stress_sensitivity(p.lines, p.supply_bn, Scenario(rate_shock_bp=200, redemption_pct=30, stress_multiplier=2.5))
    assert 2.5 in df["stress"].tolist() and df["current"].sum() == 1
    vals = df.sort_values("stress")["min_token_value"].tolist()
    assert all(a >= b - 1e-12 for a, b in zip(vals, vals[1:]))
