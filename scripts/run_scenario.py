# Command-line stress test.
# Examples
# -
#     python scripts/run_scenario.py --preset usdt_2026q2 --rate-shock 300 --run 30
#     python scripts/run_scenario.py --preset svb_style --rate-shock 300 --run 20 --export results.csv
#     python scripts/run_scenario.py --preset usdt_2026q2 --scenario "Full panic"
#     python scripts/run_scenario.py --list
#     python scripts/run_scenario.py --preset usdc_2023_svb --bank-freeze 100 --failed-bank "Silicon Valley Bank" \
#         --closed 2 --release-day 3 --run 8
#     python scripts/run_scenario.py --backtest

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_pages.common import SC_DEFAULTS, SC_FIELDS, fmt_bn  # noqa: E402
from pegguard.analysis import STANDARD_SCENARIOS  # noqa: E402
from pegguard.engine import Scenario, run_stress  # noqa: E402
from pegguard.presets import load_preset, preset_ids, preset_names  # noqa: E402

# Slider key -> (flag, help). Defaults come from the app (SC_DEFAULTS), so the CLI and the Stress Lab agree.
FLAGS = {
    "sc_rate": ("--rate-shock", "parallel rate shock in bp"),
    "sc_run": ("--run", "%% of supply that asks to redeem"),
    "sc_days": ("--days", "business days the run lasts"),
    "sc_front": ("--front-load", "0 = spread evenly, higher = more on day 1"),
    "sc_freeze": ("--bank-freeze", "%% of bank cash frozen"),
    "sc_recovery": ("--recovery", "%% recovery on frozen cash"),
    "sc_release": ("--release-day", "day frozen cash becomes usable (0 = never)"),
    "sc_stress": ("--stress", "fire-sale stress multiplier"),
    "sc_gold": ("--gold-shock", "gold price change in %%"),
    "sc_crypto": ("--crypto-shock", "bitcoin / crypto price change in %%"),
    "sc_risk": ("--risk-shock", "equities / other investments change in %%"),
    "sc_loan": ("--loan-loss", "credit loss on secured loans in %%"),
    "sc_panic": ("--panic", "panic sensitivity (0.2 = +20%% per 1pp under par)"),
    "sc_wait": ("--wait", "redemption deadline in business days (0 = same day)"),
    "sc_twist": ("--twist", "extra rate shock at 10y in bp (steepener > 0)"),
    "sc_closed": ("--closed", "redemption window closed for the first N days"),
    "sc_failed_bank": ("--failed-bank", "freeze cash only at this named bank"),
    "sc_impact": ("--impact", "price-impact multiplier (0 = fixed discounts)"),
    "sc_spill": ("--spillover", "share of price impact marking down unsold assets"),
    "sc_signal": ("--panic-signal", "what holders react to: coverage (true reserves) or market (price)"),
    "sc_lag": ("--panic-lag", "days late holders see the panic signal"),
    "sc_mkt_return": ("--market-return", "yearly return a market buyer demands while waiting, in %%"),
    "sc_mkt_recovery": ("--market-recovery", "market's guess of recovery on frozen cash, in %%"),
    "sc_fear": ("--fear", "market fear premium in bp"),
}
CRISES = {sc.name.split(":")[0].lower(): sc for sc in STANDARD_SCENARIOS}  # "full panic" -> Scenario


def build_scenario(a: argparse.Namespace) -> Scenario:
    # App defaults, then the chosen crisis (all but the deadline, a policy, as in the app), then explicit flags.
    values = dict(SC_DEFAULTS)
    name = "CLI"
    if a.scenario:
        sc = CRISES[a.scenario.lower()]
        name = sc.name
        values.update({k: getattr(sc, f) for k, f in SC_FIELDS.items() if k != "sc_wait"})
    for k in FLAGS:
        v = getattr(a, k)
        if v is not None:
            values[k] = v
    return Scenario(name=name, **{f: type(SC_DEFAULTS[k])(values[k]) for k, f in SC_FIELDS.items()})


def main() -> None:
    ap = argparse.ArgumentParser(description="PegGuard: stress-test a stablecoin's reserves")
    ap.add_argument("--list", action="store_true", help="list available presets")
    ap.add_argument("--preset", default="usdc_2026_09", choices=preset_ids(), metavar="PRESET",
                    help="reserve portfolio (see --list)")
    ap.add_argument("--scenario", type=str, default=None, metavar="NAME",
                    help="start from a standard crisis: " + ", ".join(f'"{sc.name.split(":")[0]}"'
                                                                      for sc in STANDARD_SCENARIOS))
    for k, (flag, text) in FLAGS.items():
        default = SC_DEFAULTS[k]
        extra = {"choices": ["coverage", "market"]} if k == "sc_signal" else {}
        ap.add_argument(flag, dest=k, type=type(default), default=None, metavar=flag[2:].upper().replace("-", "_"),
                        help=f"{text} (app default: {default!r})", **extra)
    ap.add_argument("--backtest", action="store_true", help="replay the four historical runs and compare")
    ap.add_argument("--export", type=str, default=None, help="write the daily table to this CSV")
    a = ap.parse_args()
    if a.scenario and a.scenario.lower() not in CRISES:
        ap.error(f"unknown --scenario {a.scenario!r}; choose from "
                 + ", ".join(sc.name.split(":")[0] for sc in STANDARD_SCENARIOS))

    if a.list:
        for k, v in preset_names().items():
            print(f"{k:16s} {v}")
        return
    if a.backtest:
        from pegguard.backtest import fear_premium_calibration, run_backtests
        bt = run_backtests().rename(columns={"Model verdict": "verdict"})
        for r in bt.itertuples():
            low = f"observed low {r.observed_low:.4f} vs model {r.model_low:.4f}" if not math.isnan(r.observed_low) else \
                f"model low {r.model_low:.4f}"
            print(f"{'MATCH' if r.Match else 'MISS ':5s}  {r.Event:42s} {r.verdict:17s} {low}")
        print(f"\nAverage miss at the price low: {fear_premium_calibration(bt):.0f}bp")
        return

    p = load_preset(a.preset)
    sc = build_scenario(a)
    r = run_stress(p.lines, p.supply_bn, sc)
    s = r.summary
    print(f"\n{p.name}  |  supply ${p.supply_bn:,.2f}bn  |  reserves ${p.reserves_bn:,.2f}bn")
    deadline = "same day" if sc.max_wait_days == 0 else f"T+{sc.max_wait_days}"
    print(f"Scenario: {sc.name}  |  {sc.rate_shock_bp:+.0f}bp, {sc.redemption_pct:.0f}% run over {sc.horizon_days} "
          f"days, bank freeze {sc.bank_freeze_pct:.0f}%, {deadline} deadline\n")
    print(f"VERDICT: {s['verdict']}  -  {s['headline']}")
    print(f"  Lowest coverage      {s['min_coverage']:.4f}")
    print(f"  Value per token      ${s['min_token_value']:.4f}")
    print(f"  Lowest market price  ${s['min_market_price']:.4f}")
    print(f"  Losses               ${s['total_loss']:,.3f}bn (rate {s['rate_loss']:.3f}, price {s['price_loss']:.3f}, "
          f"frozen {s['frozen_writedown']:.3f}, fire-sale {s['fire_sale_loss']:.3f}, spillover {s['spillover_loss']:.3f})")
    print(f"  Paid / requested     ${s['paid']:,.2f}bn / ${s['requested']:,.2f}bn, unmet ${s['unmet']:,.2f}bn")
    print(f"  Paid late            {fmt_bn(s['late_amount'])}"
          + (f" (from day {s['first_breach_day']})" if s["first_breach_day"] is not None else ""))
    print("\nDaily path:")
    cols = ["day", "open", "requested", "paid", "unmet", "supply", "reserves", "coverage", "market_price"]
    print(r.daily[cols].round(4).to_string(index=False))
    if a.export:
        r.daily.to_csv(a.export, index=False)
        print(f"\nSaved daily table to {a.export}")


if __name__ == "__main__":
    main()
