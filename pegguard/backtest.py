# Back-tests: replay four real runs and check the model gets the same outcome.
# Each event rebuilds the reserves from the last public report before the run and feeds in the observed
# redemptions. Amounts are in $bn. Sources are kept next to each number.
#
# USDT May 2022 and BUSD Feb 2023 paid everyone, USDC on the SVB weekend paid at par once banks reopened
# (but traded near $0.88), and SVB itself could not pay. "Match" compares what was actually observed:
# whether redemptions were paid on time.

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from .engine import Scenario, run_stress
from .presets import load_preset
from .reserves import ReserveLine


@dataclass
class Event:
    key: str
    name: str
    date: str
    lines: list[ReserveLine]
    supply_bn: float
    scenario: Scenario
    observed: str  # what happened, in words
    expect: str  # "holds" or "fails"
    observed_low: float | None  # lowest market price seen, if there is one
    reserves_source: str
    run_source: str
    notes: list[str] = field(default_factory=list)


def _pct(amounts_bn: list[float], base_bn: float) -> tuple:
    return tuple(round(a / base_bn * 100, 3) for a in amounts_bn)


def _usdt_2022() -> Event:
    supply = 82.188  # tokens issued, 31 Mar 2022
    lines = [
        ReserveLine("US T-bills (< 120 days)", "tbill", 39.199, 0.2, 0.6),
        ReserveLine("Commercial paper & CDs (avg 44 days)", "commercial_paper", 20.097, 0.12, 1.0),
        ReserveLine("Money market funds", "mmf_gov", 6.798, 0.08, 0.3),
        ReserveLine("Cash & bank deposits", "cash_bank", 4.100),
        ReserveLine("Reverse repo", "repo", 0.105, 0.003, 0.3),
        ReserveLine("Non-US T-bills (< 180 days)", "tbill", 0.286, 0.25, 0.5),
        ReserveLine("Corporate bonds, funds & precious metals", "other", 3.730),
        ReserveLine("Secured loans", "secured_loan", 3.150),
        ReserveLine("Other investments (incl. digital tokens)", "other", 4.960),
    ]
    # Supply (DefiLlama): 82.69 on 11 May, 79.49 on 12 May, 75.78 on 15 May, 72.56 on 31 May 2022.
    # Spread over business days: 3.2 on day 1, 3.7 over the next 3, 3.2 over the remaining 11.
    path = _pct([3.2, 1.25, 1.25, 1.2] + [3.22 / 11] * 11, supply)
    sc = Scenario(name="USDT May 2022", daily_requests_pct=path, max_wait_days=5, stress_multiplier=2.0,
                  crypto_shock_pct=-25, risk_shock_pct=-15, rate_shock_bp=0, market_required_return_pct=20)
    return Event(
        "usdt_2022", "USDT, May 2022 (Terra collapse)", "12-31 May 2022", lines, supply, sc,
        observed="All redemptions paid (about $7bn in 48 hours, $10bn by month-end). Traded as low as $0.95 on "
                 "12 May on some venues; back near $1 within days.",
        expect="holds", observed_low=0.95,
        reserves_source="Tether consolidated reserves report as of 31 Mar 2022 (MHA Cayman assurance opinion, "
                        "authorised 18 May 2022). Bill and CP maturities from the report; yields are assumptions.",
        run_source="DefiLlama USDT circulating supply, 11-31 May 2022; Tether: $7bn redeemed in 48 hours.",
        notes=["Crypto and other-investment shocks (-25% / -15%) are our estimate of the May 2022 sell-off.",
               "Commercial paper was the worry at the time; the model sells it only after bills, MMFs and cash."],
    )


def _usdc_2023() -> Event:
    p = load_preset("usdc_2023_svb")
    # Supply (DefiLlama): 43.18 (10 Mar), 42.20 (11), 40.66 (12), 40.47 (13), 39.08 (14 Mar 2023).
    path = _pct([0.98, 1.54, 0.19, 1.39], 43.18)
    sc = Scenario(name="USDC March 2023", daily_requests_pct=path, closed_days=2, bank_freeze_pct=100,
                  failed_bank="Silicon Valley Bank", frozen_recovery_pct=100, frozen_release_day=3,
                  max_wait_days=0, market_frozen_recovery_pct=0, market_required_return_pct=20)
    return Event(
        "usdc_2023", "USDC, March 2023 (SVB weekend)", "10-14 Mar 2023", p.lines, p.supply_bn, sc,
        observed="$3.3bn stuck at SVB over the weekend with redemptions closed. Traded as low as about $0.88 on "
                 "Saturday 11 Mar. Banks reopened Monday 13 Mar, deposits were guaranteed, and Circle redeemed "
                 "$3.8bn at par by 15 Mar.",
        expect="holds", observed_low=0.8774,
        reserves_source="Circle disclosure (10-11 Mar 2023): $3.3bn of about $40bn reserves at SVB. The split of the "
                        "rest is our reconstruction (see the preset).",
        run_source="DefiLlama USDC circulating supply 10-14 Mar 2023; Circle blog, 15 Mar 2023; Cointelegraph "
                   "(low $0.8774).",
        notes=["Day 0 is Friday evening; days 1-2 are the weekend (redemptions closed); the frozen cash becomes "
               "usable on Monday (day 3), as it did after the FDIC guarantee.",
               "The market is assumed to price the SVB cash at 0% recovery until Monday: the worst case traders "
               "were weighing that weekend."],
    )


def _svb_2023() -> Event:
    # 10-K, 31 Dec 2022 ($bn). Securities at fair value: AFS 26.069 (already marked), HTM 91.321 at cost,
    # 76.1 at fair value (unrealised loss about 15.2). Liabilities that rank ahead of or alongside depositors
    # (short-term secured borrowings 13.565, other liabilities 3.041) are added to what must be paid.
    lines = [
        ReserveLine("Cash and equivalents", "cash_bank", 13.803),
        ReserveLine("Available-for-sale securities (fair value)", "tnote", 26.069, 3.6, 4.5),
        ReserveLine("Held-to-maturity, mostly agency MBS (fair value)", "agency_mbs", 76.1, 8.0, 4.5),
        ReserveLine("Loans (net)", "secured_loan", 73.614),
        ReserveLine("Other assets", "other", 6.986),
    ]
    supply = 173.109 + 13.565 + 3.041  # deposits + senior claims
    path = _pct([42.0, 100.0], supply)
    sc = Scenario(name="SVB March 2023", daily_requests_pct=path, max_wait_days=0, stress_multiplier=2.0,
                  market_required_return_pct=20)
    return Event(
        "svb_2023", "Silicon Valley Bank, March 2023", "9-10 Mar 2023", lines, supply, sc,
        observed="$42bn withdrawn on 9 Mar and more than $100bn requested for 10 Mar. Closed by regulators on the "
                 "morning of 10 Mar before it could pay.",
        expect="fails", observed_low=None,
        reserves_source="SVB Financial Group 10-K, balance sheet at 31 Dec 2022 (SEC EDGAR); HTM fair value from the "
                        "10-K notes; HTM duration 6.2 years (Federal Reserve review, 28 Apr 2023).",
        run_source="Federal Reserve review of SVB (28 Apr 2023): over $40bn outflow on 9 Mar, over $100bn expected "
                   "on 10 Mar.",
        notes=["A bank is not a stablecoin: we treat deposits plus secured borrowings and other liabilities as the "
               "'tokens' that must be paid, and ignore Fed and FHLB emergency borrowing.",
               "Securities start at fair value, so the 2022 rate rise is already in the numbers (no extra shock)."],
    )


def _busd_2023() -> Event:
    supply = 16.17  # 13 Feb 2023
    # Paxos did not keep its early-2023 reports online; we apply the 30 Jun 2022 split (cash 4.2%, bills under
    # 90 days 60.9%, overnight repo 35.6%, rescaled to 100%) to the 13 Feb 2023 supply.
    shares = {"cash": 0.738, "bills": 10.6, "repo": 6.2}
    total = sum(shares.values())
    lines = [
        ReserveLine("Cash", "cash_bank", supply * shares["cash"] / total),
        ReserveLine("US T-bills (< 90 days)", "tbill", supply * shares["bills"] / total, 0.12, 4.5),
        ReserveLine("Overnight Treasury reverse repo", "repo", supply * shares["repo"] / total, 0.003, 4.5),
    ]
    # Supply (DefiLlama): 16.17 (13 Feb), 13.26 (20 Feb), 10.65 (28 Feb), 7.63 (31 Mar 2023).
    path = _pct([2.91 / 5] * 5 + [2.61 / 6] * 6 + [3.02 / 22] * 22, supply)
    sc = Scenario(name="BUSD Feb 2023", daily_requests_pct=path, max_wait_days=1, stress_multiplier=1.0,
                  market_required_return_pct=20)
    return Event(
        "busd_2023", "BUSD, February 2023 (minting stopped)", "13 Feb - 31 Mar 2023", lines, supply, sc,
        observed="Supply fell from $16.2bn to $7.6bn in seven weeks. Every redemption was paid; no depeg was "
                 "reported.",
        expect="holds", observed_low=None,
        reserves_source="Paxos BUSD attestation as of 30 Jun 2022 (via Forkast): the closest breakdown still "
                        "available; Paxos's early-2023 reports are no longer published.",
        run_source="DefiLlama BUSD circulating supply, 13 Feb - 31 Mar 2023; Fortune, 13 Feb 2023.",
        notes=["Reserve split is from mid-2022 rescaled to the Feb 2023 supply, so this is an approximation."],
    )


EVENTS = (_usdt_2022, _usdc_2023, _svb_2023, _busd_2023)


def events() -> list[Event]:
    return [f() for f in EVENTS]


def run_event(ev: Event):
    return run_stress(ev.lines, ev.supply_bn, ev.scenario)


def _matches(ev: Event, s: dict) -> bool:
    if ev.expect == "fails":
        return s["first_breach_day"] is not None and s["first_breach_day"] <= len(ev.scenario.daily_requests_pct)
    return s["first_breach_day"] is None and s["unmet"] <= 1e-9


def run_backtests() -> pd.DataFrame:
    # One row per event: what happened, what the model says, and whether they agree.
    rows = []
    for ev in events():
        s = run_event(ev).summary
        worst = run_stress(ev.lines, ev.supply_bn, replace(ev.scenario, frozen_recovery_pct=0)).summary \
            if ev.scenario.bank_freeze_pct > 0 else s
        rows.append({
            "key": ev.key, "Event": ev.name, "Dates": ev.date, "Observed": ev.observed,
            "Expected": "Paid in full" if ev.expect == "holds" else "Fails to pay",
            "Model verdict": s["verdict"], "Match": _matches(ev, s),
            "first_breach_day": s["first_breach_day"], "min_token_value": s["min_token_value"],
            "worst_case_value": worst["min_token_value"],
            "observed_low": ev.observed_low, "model_low": s["min_market_price"],
            "gap": (s["min_market_price"] - ev.observed_low) if ev.observed_low is not None else None,
        })
    return pd.DataFrame(rows)


def fear_premium_calibration(bt: pd.DataFrame) -> float:
    # Average gap (in bp) between the model's market low and the observed low, across events that have one.
    gaps = bt["gap"].dropna()
    return float(gaps.mean() * 1e4) if len(gaps) else float("nan")
