# Risk-committee memo: one stress run written up as a document a treasury team can file.
# Produces Markdown (for email / wikis) and a self-contained HTML page (open it and print to PDF). No extra
# dependencies: the HTML is built directly.

from __future__ import annotations

import html
from datetime import date

import pandas as pd

from .engine import Scenario


def _bn(x: float) -> str:
    return "n/a" if x is None or pd.isna(x) else f"${x:,.2f}bn"


def _m(x: float) -> str:
    return "n/a" if x is None or pd.isna(x) else f"${x:,.2f}m"


def _tok(x: float) -> str:
    return "n/a" if x is None or pd.isna(x) else f"${x:.4f}"


def scenario_lines(sc: Scenario) -> list[tuple[str, str]]:
    run = (f"{sc.redemption_pct:.0f}% of tokens over {sc.horizon_days} business days" if not len(sc.daily_requests_pct)
           else "custom daily path: " + ", ".join(f"{x:g}%" for x in sc.daily_requests_pct))
    rows = [("Rate shock", f"{sc.rate_shock_bp:+.0f}bp parallel" + (f", {sc.curve_twist_bp:+.0f}bp twist at 10y"
                                                                     if sc.curve_twist_bp else "")),
            ("Redemption run", run),
            ("Redemption deadline", "same day" if sc.max_wait_days == 0 else f"T+{sc.max_wait_days}")]
    if sc.closed_days:
        rows.append(("Redemption window closed", f"first {sc.closed_days} day(s)"))
    if sc.bank_freeze_pct:
        where = f"at {sc.failed_bank}" if sc.failed_bank else "across all banks"
        rel = f"released day {sc.frozen_release_day}" if sc.frozen_release_day else "never released"
        rows.append(("Bank failure", f"{sc.bank_freeze_pct:.0f}% of bank cash frozen {where}, "
                                     f"{sc.frozen_recovery_pct:.0f}% recovery, {rel}"))
    shocks = [f"{lbl} {v:+.0f}%" for lbl, v in (("crypto", sc.crypto_shock_pct), ("gold", sc.gold_shock_pct),
                                                ("other investments", sc.risk_shock_pct)) if v]
    if sc.loan_loss_pct:
        shocks.append(f"loan loss {sc.loan_loss_pct:.0f}%")
    if shocks:
        rows.append(("Price shocks", ", ".join(shocks)))
    rows.append(("Market conditions", f"fire-sale stress x{sc.stress_multiplier:g}, price impact x{sc.price_impact:g}, "
                                      f"spillover {sc.spillover:.0%}"))
    if sc.panic_sensitivity:
        rows.append(("Panic feedback", f"{sc.panic_sensitivity:g} per 1pp below par, reacting to "
                                       f"{sc.panic_signal} with a {sc.panic_lag_days}-day lag"))
    return rows


def build_memo(meta: dict, sc: Scenario, summary: dict, position: dict, sensitivity: pd.DataFrame | None = None,
               tornado: pd.DataFrame | None = None, mc_stats: dict | None = None,
               today: date | None = None) -> dict:
    # Return {"markdown": str, "html": str} for the memo.
    today = today or date.today()
    s = summary
    title = f"Stablecoin stress test: {meta.get('name', 'Portfolio')}"
    facts = [
        ("Verdict", f"{s['verdict']}: {s['headline']}"),
        ("Lowest reserves per token", _tok(s["min_token_value"])),
        ("Lowest modelled market price", _tok(s.get("min_market_price"))),
        ("Total losses", _bn(s["total_loss"])),
        ("Redemptions requested / paid", f"{_bn(s['requested'])} / {_bn(s['paid'])}"),
        ("Paid late", _bn(s["late_amount"]) + (f" (from day {s['first_breach_day']})" if s["first_breach_day"] else "")),
        ("Unmet at end", _bn(s["unmet"])),
    ]
    pos = [
        ("Position", _m(position["position"])),
        ("Loss if held to the low point", f"{_m(position['hold_loss'])} ({position['haircut_bp']:,.0f}bp)"),
        ("Redeem with the issuer", ("not available (no direct account or below the minimum)"
                                    if not position["can_redeem"] else
                                    (f"paid in full on day {position['exit_day']}" if position["exit_day"]
                                     else f"only {position['exit_share_paid']:.0%} paid by the end")
                                    + (f", fee {_m(position['redeem_fee'])}" if position["redeem_fee"] else ""))),
        ("Sell on the market now", _m(position["sell_now_value"])),
        ("Preferred route", "redeem with the issuer" if position["best_route"] == "redeem" else "sell on the market"),
    ]
    basis = meta.get("basis", "hypothetical")
    data = [("Reserve data", f"{basis.title()}, as of {meta.get('as_of', 'n/a')}"
                             + (" (edited by the user)" if meta.get("edited") else "")),
            ("Source", meta.get("source", ""))]

    md = [f"# {title}", "", f"*Prepared {today.isoformat()} with PegGuard. Educational model, not investment advice.*",
          "", "## Result", ""]
    md += [f"- **{k}:** {v}" for k, v in facts]
    md += ["", "## What it means for our position", ""] + [f"- **{k}:** {v}" for k, v in pos]
    md += ["", "## Scenario", ""] + [f"- **{k}:** {v}" for k, v in scenario_lines(sc)]
    if sensitivity is not None and len(sensitivity):
        md += ["", "## Sensitivity to fire-sale stress", "", "| Stress | Verdict | Worst value / token | Paid late |",
               "|---|---|---|---|"]
        md += [f"| x{r.stress:g} | {r.verdict} | {_tok(r.min_token_value)} | {_bn(r.late_amount)} |"
               for r in sensitivity.itertuples()]
    if tornado is not None and len(tornado):
        md += ["", "## Assumptions that matter most", "", "| Assumption | Low case | High case | Changes verdict? |",
               "|---|---|---|---|"]
        md += [f"| {r.Assumption} | {_tok(r.low)} | {_tok(r.high)} | {'yes' if r.flips else 'no'} |"
               for r in tornado.head(5).itertuples()]
    if mc_stats:
        md += ["", "## Across plausible assumptions (Monte Carlo)", "",
               f"- {mc_stats['n']} runs with liquidity, fire-sale costs, run size and recovery drawn at random.",
               f"- Holds in {mc_stats['p_holds']:.0%} of runs; below par in {mc_stats['p_below_par']:.0%}; "
               f"some redemptions late in {mc_stats['p_late']:.0%}.",
               f"- 5th-percentile worst value per token: {_tok(mc_stats['value_p5'])}."]
    md += ["", "## Data", ""] + [f"- **{k}:** {v}" for k, v in data]
    md += ["", "## Limits", "",
           "- Reserve data are point-in-time; maturities and yields not disclosed by the issuer are assumptions.",
           "- Liquidity and fire-sale parameters are documented judgement calls (see the Methodology page).",
           "- The market price is a simple model of what a buyer would pay; real prices can overshoot in a panic."]
    markdown = "\n".join(md) + "\n"

    def table(rows):
        return "<table>" + "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
                                   for k, v in rows) + "</table>"

    parts = [f"<h1>{html.escape(title)}</h1>",
             f"<p class='sub'>Prepared {today.isoformat()} with PegGuard. Educational model, not investment advice.</p>",
             "<h2>Result</h2>", table(facts), "<h2>What it means for our position</h2>", table(pos),
             "<h2>Scenario</h2>", table(scenario_lines(sc))]
    if sensitivity is not None and len(sensitivity):
        parts.append("<h2>Sensitivity to fire-sale stress</h2>" + table(
            [(f"x{r.stress:g}", f"{r.verdict} · {_tok(r.min_token_value)} per token · late {_bn(r.late_amount)}")
             for r in sensitivity.itertuples()]))
    if tornado is not None and len(tornado):
        parts.append("<h2>Assumptions that matter most</h2>" + table(
            [(r.Assumption, f"{_tok(r.low)} to {_tok(r.high)}" + (" · changes the verdict" if r.flips else ""))
             for r in tornado.head(5).itertuples()]))
    if mc_stats:
        parts.append("<h2>Across plausible assumptions</h2>" + table([
            ("Runs", f"{mc_stats['n']}"), ("Holds", f"{mc_stats['p_holds']:.0%}"),
            ("Below par", f"{mc_stats['p_below_par']:.0%}"), ("Some redemptions late", f"{mc_stats['p_late']:.0%}"),
            ("5th-percentile value per token", _tok(mc_stats["value_p5"]))]))
    parts += ["<h2>Data</h2>", table(data)]
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>" + html.escape(title) + "</title><style>"
            "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#0B1324;max-width:780px;margin:40px auto;"
            "padding:0 16px;line-height:1.5}h1{font-size:1.6rem;margin-bottom:4px}h2{font-size:1.1rem;margin-top:28px;"
            "border-bottom:1px solid #E3E8EF;padding-bottom:4px}.sub{color:#5B6B82;margin-top:0}"
            "table{border-collapse:collapse;width:100%;font-size:.92rem}th{text-align:left;width:38%;color:#5B6B82;"
            "font-weight:600;padding:6px 10px 6px 0;vertical-align:top}td{padding:6px 0;border-bottom:1px solid #F0F3F7}"
            "@media print{body{margin:0}}</style></head><body>" + "".join(parts) + "</body></html>")
    return {"markdown": markdown, "html": page}
