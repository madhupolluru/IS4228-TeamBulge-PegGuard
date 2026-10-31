# Command-line stress test.
# Examples
# -
#     python scripts/run_scenario.py --preset usdt_2026q2 --rate-shock 300 --run 30
#     python scripts/run_scenario.py --preset svb_style --rate-shock 300 --run 20 --export results.csv
#     python scripts/run_scenario.py --list
#     python scripts/run_scenario.py --preset usdc_2023_svb --bank-freeze 100 --failed-bank "Silicon Valley Bank"         --closed 2 --release-day 3 --run 8
#     python scripts/run_scenario.py --backtest

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pegguard.engine import Scenario, run_stress  # noqa: E402
from pegguard.presets import load_preset, preset_names  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="PegGuard: stress-test a stablecoin's reserves")
    ap.add_argument("--list", action="store_true", help="list available presets")
    ap.add_argument("--preset", default="usdc_2026_09")
    ap.add_argument("--rate-shock", type=float, default=0, help="parallel rate shock in bp")
    ap.add_argument("--run", type=float, default=20, help="%% of supply that asks to redeem")
    ap.add_argument("--days", type=int, default=5, help="business days the run lasts")
    ap.add_argument("--front-load", type=float, default=0.35)
    ap.add_argument("--bank-freeze", type=float, default=0, help="%% of bank cash frozen")
    ap.add_argument("--recovery", type=float, default=100, help="%% recovery on frozen cash")
    ap.add_argument("--stress", type=float, default=1, help="fire-sale stress multiplier")
    ap.add_argument("--crypto-shock", type=float, default=0)
    ap.add_argument("--gold-shock", type=float, default=0)
    ap.add_argument("--panic", type=float, default=0, help="panic sensitivity (0.2 = +20%% per 1pp under par)")
    ap.add_argument("--twist", type=float, default=0, help="extra rate shock at 10y in bp (steepener > 0)")
    ap.add_argument("--wait", type=int, default=0, help="redemption deadline in business days (0 = same day)")
    ap.add_argument("--closed", type=int, default=0, help="redemption window closed for the first N days")
    ap.add_argument("--failed-bank", type=str, default="", help="freeze cash only at this named bank")
    ap.add_argument("--release-day", type=int, default=0, help="day frozen cash becomes usable (0 = never)")
    ap.add_argument("--impact", type=float, default=1.0, help="price-impact multiplier (0 = fixed discounts)")
    ap.add_argument("--spillover", type=float, default=0.5, help="share of price impact marking down unsold assets")
    ap.add_argument("--fear", type=float, default=0, help="market fear premium in bp")
    ap.add_argument("--backtest", action="store_true", help="replay the four historical runs and compare")
    ap.add_argument("--export", type=str, default=None, help="write the daily table to this CSV")
    a = ap.parse_args()

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
    sc = Scenario(name="CLI", rate_shock_bp=a.rate_shock, redemption_pct=a.run, horizon_days=a.days,
                  front_load=a.front_load, bank_freeze_pct=a.bank_freeze, frozen_recovery_pct=a.recovery,
                  stress_multiplier=a.stress, crypto_shock_pct=a.crypto_shock, gold_shock_pct=a.gold_shock,
                  panic_sensitivity=a.panic, curve_twist_bp=a.twist, max_wait_days=a.wait, closed_days=a.closed,
                  failed_bank=a.failed_bank, frozen_release_day=a.release_day, price_impact=a.impact,
                  spillover=a.spillover, fear_premium_bp=a.fear)
    r = run_stress(p.lines, p.supply_bn, sc)
    s = r.summary
    print(f"\n{p.name}  |  supply ${p.supply_bn:,.2f}bn  |  reserves ${p.reserves_bn:,.2f}bn")
    print(f"Scenario: +{a.rate_shock:.0f}bp, {a.run:.0f}% run over {a.days} days, bank freeze {a.bank_freeze:.0f}%\n")
    print(f"VERDICT: {s['verdict']}  -  {s['headline']}")
    print(f"  Lowest coverage      {s['min_coverage']:.4f}")
    print(f"  Value per token      ${s['min_token_value']:.4f}")
    print(f"  Lowest market price  ${s['min_market_price']:.4f}")
    print(f"  Losses               ${s['total_loss']:,.3f}bn (rate {s['rate_loss']:.3f}, price {s['price_loss']:.3f}, "
          f"frozen {s['frozen_writedown']:.3f}, fire-sale {s['fire_sale_loss']:.3f}, spillover {s['spillover_loss']:.3f})")
    print(f"  Paid / requested     ${s['paid']:,.2f}bn / ${s['requested']:,.2f}bn, unmet ${s['unmet']:,.2f}bn")
    print("\nDaily path:")
    cols = ["day", "open", "requested", "paid", "unmet", "supply", "reserves", "coverage", "market_price"]
    print(r.daily[cols].round(4).to_string(index=False))
    if a.export:
        r.daily.to_csv(a.export, index=False)
        print(f"\nSaved daily table to {a.export}")


if __name__ == "__main__":
    main()
