# Analysis built on top of the stress engine: scenario library, breaking-point grid, regulation checks,
# risk profile, what a run means for one holder, and sensitivity tools (tornado, Monte Carlo).

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

import numpy as np
import pandas as pd

from .engine import PAR_TOL, Scenario, redemption_capacity, run_stress
from .pricing import modified_duration
from .reserves import ASSET_CLASSES, ReserveLine

# Scenario library

STANDARD_SCENARIOS: list[Scenario] = [
    Scenario(name="Calm: normal redemptions", rate_shock_bp=0, redemption_pct=5, horizon_days=5,
             front_load=0.2),
    Scenario(name="Rate shock: +300bp, 20% run", rate_shock_bp=300, redemption_pct=20, horizon_days=5,
             front_load=0.35, stress_multiplier=2),
    Scenario(name="Crypto crash: BTC -50%, 30% run", rate_shock_bp=0, redemption_pct=30, horizon_days=5,
             front_load=0.4, crypto_shock_pct=-50, gold_shock_pct=-10, risk_shock_pct=-30,
             loan_loss_pct=10, stress_multiplier=3),
    Scenario(name="Bank failure: 30% of bank cash frozen", bank_freeze_pct=30, frozen_recovery_pct=0,
             redemption_pct=25, horizon_days=5, front_load=0.5, stress_multiplier=2),
    Scenario(name="Full panic: 50% run in 5 days", rate_shock_bp=150, redemption_pct=50, horizon_days=5,
             front_load=0.5, stress_multiplier=3, crypto_shock_pct=-40, gold_shock_pct=-10,
             risk_shock_pct=-30, loan_loss_pct=15, panic_sensitivity=0.3),
]


def run_library(lines: list[ReserveLine], supply: float,
                scenarios: Iterable[Scenario] = STANDARD_SCENARIOS) -> pd.DataFrame:
    rows = []
    for sc in scenarios:
        r = run_stress(lines, supply, sc).summary
        rows.append(
            {
                "Scenario": sc.name,
                "Verdict": r["verdict"],
                "Lowest coverage": r["min_coverage"],
                "Value per token": r["min_token_value"],
                "Total loss ($bn)": r["total_loss"],
                "Unmet redemptions ($bn)": r["unmet"],
                "First late day": r["first_breach_day"],
            }
        )
    return pd.DataFrame(rows)


# Breaking-point map

def breaking_point_grid(
    lines: list[ReserveLine],
    supply: float,
    base: Scenario,
    rate_shocks_bp: Iterable[float] = range(0, 525, 25),
    run_sizes_pct: Iterable[float] = range(0, 105, 5),
) -> pd.DataFrame:
    # Run the engine for every (rate shock, run size) pair, keeping all other settings from `base`.
    rows = []
    for bp in rate_shocks_bp:
        for run in run_sizes_pct:
            sc = replace(base, rate_shock_bp=float(bp), redemption_pct=float(run))
            s = run_stress(lines, supply, sc).summary
            rows.append(
                {
                    "rate_shock_bp": float(bp),
                    "run_pct": float(run),
                    "min_coverage": s["min_coverage"],
                    "unmet": s["unmet"],
                    "late": s["first_breach_day"] is not None,
                    "below_par": s["min_coverage"] < PAR_TOL,
                    "verdict": s["verdict"],
                }
            )
    return pd.DataFrame(rows)


def survival_frontier(grid: pd.DataFrame) -> pd.DataFrame:
    # For each rate shock: the largest run that is still paid on time, and still at par.
    out = []
    for bp, g in grid.groupby("rate_shock_bp"):
        g = g.sort_values("run_pct")
        ok_liq = g.loc[~g["late"], "run_pct"]
        ok_par = g.loc[~g["below_par"], "run_pct"]
        ok_all = g.loc[~g["late"] & ~g["below_par"], "run_pct"]
        out.append(
            {
                "rate_shock_bp": bp,
                "max_run_paid_on_time": float(ok_liq.max()) if len(ok_liq) else 0.0,
                "max_run_at_par": float(ok_par.max()) if len(ok_par) else float("nan"),
                "max_run_holds": float(ok_all.max()) if len(ok_all) else float("nan"),
            }
        )
    return pd.DataFrame(out)


# Regulation (simplified, educational)

MICA_MIN_DEPOSITS = 0.30  # MiCA: at least 30% of reserves as bank deposits
MICA_MIN_DEPOSITS_SIGNIFICANT = 0.60  # 60% for a "significant" token
MICA_MAX_PER_BANK = 0.25  # EBA draft rule: deposits at any one large bank (simplified)

# per regime: asset-class flag, longest maturity allowed for bills and repo (years), reason when not allowed
RULES = {
    "GENIUS": ("genius", {"tbill": 93 / 365, "repo": 7 / 365}, "Not permitted"),
    "MAS": ("mas", {"tbill": 0.25, "repo": 0.25}, "Not very-low-risk"),
    "MiCA": ("mica", {}, "Not highly liquid / low risk"),
    "HK": ("hk", {"tbill": 1.0, "repo": 1.0}, "Not a permitted reserve asset"),
}

REGIMES = ("GENIUS", "MAS", "MiCA", "HK")
REGIME_TITLES = {
    "GENIUS": "US GENIUS Act (2025)",
    "MAS": "MAS single-currency stablecoin framework (2023)",
    "MiCA": "EU MiCA, e-money tokens (2024)",
    "HK": "Hong Kong Stablecoins Ordinance (2025)",
}
REGIME_RULES = {
    "GENIUS": "Cash, insured deposits, T-bills of 93 days or less, repo of 7 days or less on bills, government "
              "money market funds. 1:1 backing, monthly attestation.",
    "MAS": "Very-low-risk assets: cash, cash equivalents and short-dated (3 months or less) government debt. At "
           "least 100% backing, par redemption within 5 business days, monthly attestation.",
    "MiCA": "Deposits at EU credit institutions and highly liquid, low-risk instruments (government debt, repo, "
            "qualifying MMFs). At least 30% in bank deposits (60% if significant), spread across banks.",
    "HK": "Cash, deposits, government securities of 1 year or less, repo and MMFs investing in them. Full "
          "backing, segregated, redemption at par within one business day.",
}


def _eligible(line: ReserveLine, regime: str) -> tuple[bool, str]:
    flag, max_years, reason = RULES[regime]
    if not getattr(line.spec, flag):
        return False, reason
    limit = max_years.get(line.asset_class)
    if limit is not None and line.maturity_years > limit + 1e-9:
        return False, f"Maturity over {limit * 365:.0f} days"
    return True, "Eligible"


def bank_concentration(lines: list[ReserveLine]) -> pd.DataFrame:
    # Bank cash by named bank, as a share of all reserves. Unnamed bank cash is grouped as 'Unspecified'.
    total = sum(l.amount_bn for l in lines) or 1.0
    agg: dict[str, float] = {}
    for l in lines:
        if l.asset_class == "cash_bank":
            key = l.bank.strip() or "Unspecified"
            agg[key] = agg.get(key, 0.0) + l.amount_bn
    rows = [{"Bank": k, "Amount ($bn)": v, "Share of reserves": v / total} for k, v in agg.items()]
    return pd.DataFrame(rows, columns=["Bank", "Amount ($bn)", "Share of reserves"]).sort_values(
        "Amount ($bn)", ascending=False).reset_index(drop=True)


def regulatory_check(lines: list[ReserveLine], supply: float, significant: bool = False) -> dict:
    # Eligible reserves under each regime, plus regime-specific extra tests (MiCA deposit floor and bank cap).
    # Each regime returns: eligible_bn, eligible_share, eligible_backing, passes, detail (per holding) and
    # checks (a list of {"Check", "Passes", "Detail"} rows that all have to pass).
    total = sum(l.amount_bn for l in lines)
    deposits = sum(l.amount_bn for l in lines if l.asset_class == "cash_bank")
    conc = bank_concentration(lines)
    out = {}
    for regime in REGIMES:
        detail = []
        eligible_amt = 0.0
        for l in lines:
            ok, why = _eligible(l, regime)
            if ok:
                eligible_amt += l.amount_bn
            detail.append({"Holding": l.name, "Amount ($bn)": l.amount_bn, "Eligible": ok, "Reason": why})
        backing = eligible_amt / supply if supply else float("nan")
        checks = [{"Check": "Eligible assets cover every token", "Passes": bool(supply) and bool(backing >= 1.0 - 1e-9),
                   "Detail": f"{backing:.1%} of tokens" if supply else "no tokens"}]
        if regime == "MiCA":
            floor = MICA_MIN_DEPOSITS_SIGNIFICANT if significant else MICA_MIN_DEPOSITS
            share = deposits / total if total else 0.0
            checks.append({"Check": f"At least {floor:.0%} in bank deposits", "Passes": bool(share >= floor - 1e-9),
                           "Detail": f"{share:.1%} in deposits"})
            named = conc[conc["Bank"] != "Unspecified"]
            if len(named):
                top = named.iloc[0]
                checks.append({"Check": f"No more than {MICA_MAX_PER_BANK:.0%} at one bank",
                               "Passes": bool(top["Share of reserves"] <= MICA_MAX_PER_BANK + 1e-9),
                               "Detail": f"{top['Bank']}: {top['Share of reserves']:.1%}"})
            elif deposits > 0:
                checks.append({"Check": f"No more than {MICA_MAX_PER_BANK:.0%} at one bank", "Passes": None,
                               "Detail": "Banks not named: add them in the reserve editor to test this"})
        out[regime] = {
            "title": REGIME_TITLES[regime],
            "rule": REGIME_RULES[regime],
            "eligible_bn": eligible_amt,
            "eligible_share": eligible_amt / total if total else float("nan"),
            "eligible_backing": backing,
            "passes": all(c["Passes"] is not False for c in checks),
            "checks": checks,
            "detail": pd.DataFrame(detail),
        }
    return out


# Risk profile

def risk_profile(lines: list[ReserveLine], supply: float) -> dict:
    total = sum(l.amount_bn for l in lines) or 1.0
    dur = 0.0
    wam = 0.0
    for l in lines:
        kind = l.spec.rate_kind
        if kind != "none":
            k = "zero" if kind in ("zero", "mmf") else "coupon"
            dur += l.amount_bn / total * modified_duration(k, l.maturity_years, l.yield_pct / 100)
            wam += l.amount_bn / total * l.maturity_years
    # cash raisable within N days, each computed once
    cash = {d: redemption_capacity(lines, days=d) for d in (1, 2, 3, 5, 10, 20)}
    ladder = pd.DataFrame([{"Days": d, "Cash raisable ($bn)": c, "Share of supply": c / supply if supply else np.nan}
                           for d, c in cash.items()])
    cap1, cap5 = cash[1], cash[5]
    conc = bank_concentration(lines)
    named = conc[conc["Bank"] != "Unspecified"]
    return {
        "largest_bank": named.iloc[0]["Bank"] if len(named) else None,
        "largest_bank_share": float(named.iloc[0]["Share of reserves"]) if len(named) else float("nan"),
        "duration": dur,
        "wam_days": wam * 365,
        "dv01_bn": dur * total / 10_000,  # $bn lost per +1bp
        "loss_per_100bp_pct": dur,  # % of reserves lost per +100bp (approx)
        "day1_capacity_share": cap1 / supply if supply else np.nan,
        "day5_capacity_share": cap5 / supply if supply else np.nan,
        "ladder": ladder,
    }


# Position view (for a holder of the token)

def exit_timing(daily: pd.DataFrame) -> tuple[int | None, float]:
    # When a holder who asks to redeem on day 1 is paid in full.
    # The queue is first in, first out, so day-1 requests are paid before anything filed later. Returns
    # (first day fully paid or None, share of the day-1 request paid by the end of the horizon). The holder's own
    # request is assumed small next to the run, so it does not change the run itself.
    run = daily[daily["day"] > 0]
    if run.empty:
        return None, float("nan")
    first = float(run["new_requests"].iloc[0])
    if first <= 1e-12:
        # Nobody else is redeeming: paid on day 1 if anything can be sold that day.
        return (1, 1.0) if float(daily["liquid_next_day"].iloc[0]) > 0 else (None, 0.0)
    cum_paid = run["paid"].cumsum().to_numpy()
    done = np.nonzero(cum_paid >= first * (1 - 1e-9))[0]
    day_full = int(run["day"].iloc[done[0]]) if len(done) else None
    return day_full, min(1.0, float(cum_paid[-1]) / first)


def position_outcome(summary: dict, daily: pd.DataFrame, position: float, access: dict | None = None) -> dict:
    # What a stress run means for one holder. Three ways out:
    # hold (marked at the worst value per token), redeem with the issuer on day 1 (if they have an account and
    # are above the minimum, less the fee), or sell on the market at the model's price.
    access = {"direct": True, "fee_bp": 0.0, "min_m": 0.0, **(access or {})}
    worst = summary["min_token_value"]
    haircut = 0.0 if not np.isfinite(worst) else max(0.0, 1.0 - worst)
    day_full, share = exit_timing(daily)
    wait = summary.get("max_wait_days", 0)
    closed = summary.get("closed_days", 0)
    can_redeem = bool(access["direct"]) and position >= float(access["min_m"]) - 1e-12
    fee = max(float(access["fee_bp"]), 0.0) / 10_000
    prices = daily["market_price"] if "market_price" in daily else pd.Series([1.0])
    now_price = float(prices.iloc[0]) if prices.notna().any() else float("nan")
    low_price = float(prices.min()) if prices.notna().any() else float("nan")
    redeem_value = position * share * (1 - fee) if can_redeem and np.isfinite(share) else float("nan")
    sell_now = position * now_price if np.isfinite(now_price) else float("nan")
    if can_redeem and np.isfinite(redeem_value) and (not np.isfinite(sell_now) or redeem_value >= sell_now):
        best = "redeem"
    else:
        best = "sell"
    return {
        "position": position,
        "hold_value": position * (1 - haircut),
        "hold_loss": position * haircut,
        "haircut_bp": haircut * 10_000,
        "exit_day": day_full,
        "exit_share_paid": share,
        "exit_on_time": day_full is not None and day_full - 1 - closed <= wait,
        "last_day": int(daily["day"].max()),
        "can_redeem": can_redeem,
        "redeem_fee": position * fee if can_redeem else 0.0,
        "redeem_value": redeem_value,
        "market_price_now": now_price,
        "market_price_low": low_price,
        "sell_now_value": sell_now,
        "sell_low_value": position * low_price if np.isfinite(low_price) else float("nan"),
        "best_route": best,
    }


# Sensitivity

STRESS_LEVELS = (1.0, 2.0, 3.0, 5.0)


def stress_sensitivity(lines: list[ReserveLine], supply: float, base: Scenario,
                       levels: Iterable[float] = STRESS_LEVELS) -> pd.DataFrame:
    # The same scenario re-run at several fire-sale stress multipliers (the user's own level is always included).
    levels = sorted(set(float(x) for x in levels) | {float(base.stress_multiplier)})
    rows = []
    for m in levels:
        s = run_stress(lines, supply, replace(base, stress_multiplier=m)).summary
        rows.append({"stress": m, "current": m == float(base.stress_multiplier), "verdict": s["verdict"],
                     "min_token_value": s["min_token_value"], "fire_sale_loss": s["fire_sale_loss"],
                     "late_amount": s["late_amount"], "unmet": s["unmet"],
                     "min_market_price": s["min_market_price"]})
    return pd.DataFrame(rows)


# Which assumption matters most (tornado) and how often does it break (Monte Carlo)

def _tornado_params(lines: list[ReserveLine], base: Scenario) -> list[tuple[str, Scenario, Scenario]]:
    # (label, low case, high case) for every assumption worth flexing in this scenario.
    out = [
        ("Fire-sale stress multiplier", replace(base, stress_multiplier=base.stress_multiplier * 0.5),
         replace(base, stress_multiplier=base.stress_multiplier * 1.5)),
        ("Price impact of large sales", replace(base, price_impact=0.0),
         replace(base, price_impact=max(base.price_impact, 0.5) * 2)),
        ("Mark-to-market spillover", replace(base, spillover=0.0), replace(base, spillover=1.0)),
        ("Run size", replace(base, redemption_pct=base.redemption_pct * 0.5),
         replace(base, redemption_pct=min(100.0, base.redemption_pct * 1.5))),
        ("How front-loaded the run is", replace(base, front_load=base.front_load * 0.5),
         replace(base, front_load=min(0.9, base.front_load * 1.5 + 0.05))),
    ]
    if base.rate_shock_bp:
        out.append(("Rate shock", replace(base, rate_shock_bp=base.rate_shock_bp * 0.5),
                    replace(base, rate_shock_bp=base.rate_shock_bp * 1.5)))
    else:
        out.append(("Rate shock", replace(base, rate_shock_bp=-100.0), replace(base, rate_shock_bp=100.0)))
    if base.bank_freeze_pct > 0:
        out.append(("Recovery on frozen cash", replace(base, frozen_recovery_pct=0.0),
                    replace(base, frozen_recovery_pct=100.0)))
    out.append(("Panic feedback", replace(base, panic_sensitivity=0.0),
                replace(base, panic_sensitivity=max(0.3, base.panic_sensitivity * 2))))
    for key in sorted({l.asset_class for l in lines}, key=lambda k: ASSET_CLASSES[k].rank):
        spec = ASSET_CLASSES[key]
        if spec.daily_liquidity >= 1.0:
            continue  # cash and overnight repo are always fully available
        out.append((f"Liquidity: {spec.label}", replace(base, class_liquidity={**base.class_liquidity, key: 0.5}),
                    replace(base, class_liquidity={**base.class_liquidity, key: 1.5})))
    return out


def tornado(lines: list[ReserveLine], supply: float, base: Scenario) -> pd.DataFrame:
    # Push each assumption to a low and a high value, one at a time, and record the worst value per token.
    # Rows are sorted by swing (high minus low), so the top row is the assumption the answer depends on most.
    # `flips` is True when moving that one assumption changes the verdict.
    b = run_stress(lines, supply, base).summary
    rows = []
    for label, lo, hi in _tornado_params(lines, base):
        sl, sh = run_stress(lines, supply, lo).summary, run_stress(lines, supply, hi).summary
        rows.append({
            "Assumption": label,
            "base": b["min_token_value"], "low": sl["min_token_value"], "high": sh["min_token_value"],
            "late_low": sl["late_amount"], "late_high": sh["late_amount"],
            "verdict_low": sl["verdict"], "verdict_high": sh["verdict"],
            "swing": abs(sh["min_token_value"] - sl["min_token_value"]) + 1e-6 * abs(sh["late_amount"] - sl["late_amount"]),
            "flips": len({sl["verdict"], sh["verdict"], b["verdict"]}) > 1,
        })
    return pd.DataFrame(rows).sort_values("swing", ascending=False).reset_index(drop=True)


def monte_carlo(lines: list[ReserveLine], supply: float, base: Scenario, n: int = 300,
                seed: int = 4228) -> tuple[pd.DataFrame, dict]:
    # Re-run the scenario `n` times with the judgement-call assumptions drawn at random around their defaults.
    # The shocks the user chose stay fixed; what varies is what nobody knows precisely: how liquid each asset
    # really is, how big fire-sale discounts and price impact are, how large the run gets and (if a bank failed)
    # how much is recovered. The result is how often this scenario breaks across plausible assumption sets.
    rng = np.random.default_rng(seed)
    keys = sorted({l.asset_class for l in lines})
    rows = []
    for _ in range(int(n)):
        sc = replace(
            base,
            stress_multiplier=base.stress_multiplier * float(rng.lognormal(0.0, 0.35)),
            price_impact=float(rng.uniform(0.5, 1.5)) * max(base.price_impact, 0.0),
            spillover=float(rng.uniform(0.0, 1.0)),
            redemption_pct=min(100.0, base.redemption_pct * float(rng.lognormal(0.0, 0.25))),
            class_liquidity={k: float(np.clip(rng.lognormal(0.0, 0.35), 0.2, 3.0)) for k in keys},
            frozen_recovery_pct=(float(np.clip(base.frozen_recovery_pct + rng.uniform(-30, 30), 0, 100))
                                 if base.bank_freeze_pct > 0 else base.frozen_recovery_pct),
        )
        s = run_stress(lines, supply, sc).summary
        rows.append({"verdict": s["verdict"], "min_token_value": s["min_token_value"],
                     "late_amount": s["late_amount"], "min_market_price": s["min_market_price"],
                     "stress": sc.stress_multiplier, "run_pct": sc.redemption_pct})
    df = pd.DataFrame(rows)
    stats = {
        "n": len(df),
        "p_holds": float((df["verdict"] == "HOLDS").mean()),
        "p_below_par": float(df["verdict"].isin(["BELOW PAR", "BREAKS"]).mean()),
        "p_late": float(df["verdict"].isin(["LIQUIDITY STRAIN", "BREAKS"]).mean()),
        "p_breaks": float((df["verdict"] == "BREAKS").mean()),
        "value_p5": float(df["min_token_value"].quantile(0.05)),
        "value_median": float(df["min_token_value"].median()),
        "price_p5": float(df["min_market_price"].quantile(0.05)),
    }
    return df, stats


# Live early warning: turn observed outflows into a run scenario

def projected_run(chg_7d_pct: float, days: int = 5) -> float:
    # Run size (% of supply) if the last 7 days' supply drop carried on at the same pace for `days` business
    # days. A rising supply gives 0.
    if chg_7d_pct is None or not np.isfinite(chg_7d_pct) or chg_7d_pct >= 0:
        return 0.0
    per_business_day = -chg_7d_pct / 5
    return float(min(100.0, per_business_day * max(int(days), 1)))
