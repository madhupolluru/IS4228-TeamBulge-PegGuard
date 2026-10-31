# Treasury planner: put every coin a treasury holds through the same crises at once, then report the loss,
# the cash raisable in time, a safe cap per coin and the diversification benefit.

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .analysis import STANDARD_SCENARIOS, position_outcome
from .engine import Scenario, run_stress
from .presets import load_preset, redemption_terms


@dataclass
class Holding:
    preset_id: str
    amount_m: float  # $m held


def _outcome(preset_id: str, sc: Scenario, amount_m: float, direct_access: bool = True) -> dict:
    p = load_preset(preset_id)
    r = run_stress(p.lines, p.supply_bn, sc)
    terms = redemption_terms(preset_id)
    terms["direct"] = bool(terms["direct"] and direct_access)
    out = position_outcome(r.summary, r.daily, amount_m, terms)
    return {"verdict": r.summary["verdict"], "token_value": r.summary["min_token_value"],
            "market_low": r.summary["min_market_price"], **out}


def cash_by(out: dict, days: int, closed: int = 0) -> float:
    # Cash ($m) the holder can count on within `days` business days of the crisis starting.
    # A direct customer redeems if the issuer pays them in time, net of the fee. Anyone else (or anyone whose
    # redemption is too slow) sells on the market at the model's price on day 0.
    redeem_ok = out["can_redeem"] and out["exit_day"] is not None and out["exit_day"] - 1 <= days
    if redeem_ok:
        return out["position"] - out["redeem_fee"]
    sell = out["sell_now_value"]
    return 0.0 if sell is None or not np.isfinite(sell) else float(sell)


def stress_treasury(holdings: list[Holding], scenarios: list[Scenario] | None = None,
                    liquidity_days: int = 2, direct_access: bool = True) -> pd.DataFrame:
    # One row per (holding, crisis): verdict, loss if held, cash available within `liquidity_days`.
    # With `direct_access` False the treasury has no issuer accounts, so every exit is a sale on the market.
    scenarios = scenarios or STANDARD_SCENARIOS
    rows = []
    for h in holdings:
        if h.amount_m <= 0:
            continue
        for sc in scenarios:
            o = _outcome(h.preset_id, sc, h.amount_m, direct_access)
            rows.append({
                "preset_id": h.preset_id, "scenario": sc.name, "amount_m": h.amount_m, "verdict": o["verdict"],
                "token_value": o["token_value"], "loss_m": o["hold_loss"],
                "cash_m": cash_by(o, liquidity_days), "route": o["best_route"], "can_redeem": o["can_redeem"],
            })
    return pd.DataFrame(rows)


def treasury_summary(detail: pd.DataFrame, total_m: float, loss_budget_bp: float) -> dict:
    # Totals across the treasury, the worst crisis, and the diversification benefit.
    if detail.empty:
        return {"worst_scenario": None, "worst_loss_m": 0.0, "worst_loss_bp": 0.0, "cash_m": total_m,
                "cash_share": 1.0, "sum_of_worsts_m": 0.0, "diversification_m": 0.0, "within_budget": True}
    # keep the crises in their natural order (calm first, full panic last)
    by_sc = detail.groupby("scenario", sort=False).agg(loss_m=("loss_m", "sum"), cash_m=("cash_m", "sum"))
    worst = by_sc["loss_m"].idxmax()
    sum_of_worsts = float(detail.groupby("preset_id")["loss_m"].max().sum())
    worst_loss = float(by_sc.loc[worst, "loss_m"])
    min_cash = float(by_sc["cash_m"].min())
    return {
        "worst_scenario": worst,
        "worst_loss_m": worst_loss,
        "worst_loss_bp": worst_loss / total_m * 1e4 if total_m else 0.0,
        "cash_m": min_cash,  # in the crisis that leaves the least cash
        "cash_share": min_cash / total_m if total_m else float("nan"),
        "sum_of_worsts_m": sum_of_worsts,
        "diversification_m": sum_of_worsts - worst_loss,
        "within_budget": worst_loss <= total_m * loss_budget_bp / 1e4 + 1e-9,
        "by_scenario": by_sc.reset_index(),
    }


def safe_caps(preset_ids: list[str], total_m: float, loss_budget_bp: float,
              scenarios: list[Scenario] | None = None) -> pd.DataFrame:
    # Largest holding of each coin whose worst-crisis loss stays within the treasury's loss budget.
    # Loss is linear in the position (the holding is small next to the run), so the cap is
    # budget / worst haircut. A coin that never loses value under these crises has no cap from this test.
    scenarios = scenarios or STANDARD_SCENARIOS
    budget = total_m * loss_budget_bp / 1e4
    rows = []
    for pid in preset_ids:
        worst_haircut, worst_sc = 0.0, None
        for sc in scenarios:
            o = _outcome(pid, sc, 1.0)
            if o["hold_loss"] > worst_haircut + 1e-12:
                worst_haircut, worst_sc = o["hold_loss"], sc.name
        cap = budget / worst_haircut if worst_haircut > 1e-9 else float("inf")
        rows.append({"preset_id": pid, "worst_haircut_bp": worst_haircut * 1e4, "worst_scenario": worst_sc,
                     "cap_m": cap, "cap_share": cap / total_m if total_m else float("nan")})
    return pd.DataFrame(rows)
