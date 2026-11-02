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


def select_exit(out: dict, days: int) -> dict:
    """Choose the higher net proceeds among full exits that meet the treasury's cash deadline.

    Requests are submitted on day 1 (T+0); day 2 is T+1. Closed redemption windows still count toward
    this cash deadline. Selling uses the model's immediate price, without exchange fees or slippage.
    """
    redeem_day = out["exit_day"]
    redeem_cash = float(out["position"] - out["redeem_fee"])
    redeem_ok = bool(out["can_redeem"] and redeem_day is not None and np.isfinite(redeem_day)
                     and 1 <= redeem_day <= max(days, 0) + 1 and np.isfinite(redeem_cash))
    sell_cash = out["sell_now_value"]
    sell_ok = sell_cash is not None and np.isfinite(sell_cash)
    if redeem_ok and (not sell_ok or redeem_cash >= sell_cash):
        route, cash, day = "redeem", redeem_cash, int(redeem_day)
        reason = ("Redemption meets your deadline and gives at least as much cash as selling immediately."
                  if sell_ok else "Redemption meets your deadline; no modelled sale price is available.")
    elif sell_ok:
        route, cash, day = "sell", float(sell_cash), 0
        if redeem_ok:
            reason = "Both routes meet your deadline; selling immediately gives more cash after redemption fees."
        elif not out["can_redeem"]:
            reason = "Direct redemption is unavailable under your account setting or the issuer's minimum."
        elif redeem_day is None or not np.isfinite(redeem_day):
            reason = "Full redemption is not reached in the simulation; an immediate sale has a modelled price."
        else:
            reason = "Full redemption misses your cash deadline; selling immediately meets it."
    else:
        route, cash, day = "unavailable", 0.0, None
        reason = "No full exit has modelled proceeds available within your cash deadline."
    return {"route": route, "cash_m": cash, "cash_day": day,
            "redeem_in_time": redeem_ok, "exit_reason": reason}


def cash_by(out: dict, days: int, closed: int = 0) -> float:
    # Keep the cash summary consistent with the route shown in the scorecard.
    return select_exit(out, days)["cash_m"]


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
            exit_choice = select_exit(o, liquidity_days)
            rows.append({
                "preset_id": h.preset_id, "scenario": sc.name, "amount_m": h.amount_m, "verdict": o["verdict"],
                "token_value": o["token_value"], "loss_m": o["hold_loss"],
                **exit_choice, "can_redeem": o["can_redeem"], "redeem_day": o["exit_day"],
                "redeem_cash_m": o["position"] - o["redeem_fee"] if o["can_redeem"] else float("nan"),
                "redeem_fee_m": o["redeem_fee"], "sell_cash_m": o["sell_now_value"],
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
