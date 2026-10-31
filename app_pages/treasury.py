# Treasury Planner: how should a treasury split its stablecoin float, and how much of each coin is safe?

import math

import pandas as pd
import streamlit as st

from app_pages.common import esc, fmt_m, page_header, section, steps_list, verdict_chip
from pegguard import charts
from pegguard.analysis import STANDARD_SCENARIOS
from pegguard.presets import preset_info, redemption_terms
from pegguard.treasury import Holding, safe_caps, stress_treasury, treasury_summary

page_header("Decide", "Treasury Planner",
            "Enter the coins you hold and your limits. Every coin goes through the same five crises at once, and "
            "you see the loss on the whole treasury and a safe amount for each coin.")

info = pd.DataFrame(preset_info())
names = dict(zip(info["id"], info["name"]))
# short labels: the symbol for issuers, the name without its bracketed note for benchmarks
short = {r.id: (r.symbol if isinstance(r.symbol, str) and r.symbol else r.name.split(" (")[0])
         for r in info.itertuples()}
default_mix = {"usdc_2026_09": 60.0, "usdt_2026q2": 30.0, "xsgd_style": 10.0}

if "treasury_mix" not in st.session_state:
    st.session_state.treasury_mix = pd.DataFrame(
        [{"Coin": short[k], "Holding ($m)": v} for k, v in default_mix.items() if k in short])

left, right = st.columns([1.3, 1])
with left:
    section("What you hold", "Step 1")
    mix = st.data_editor(
        st.session_state.treasury_mix, num_rows="dynamic", hide_index=True, width="stretch", key="treasury_editor",
        column_config={"Coin": st.column_config.SelectboxColumn(options=list(short.values()), required=True),
                       "Holding ($m)": st.column_config.NumberColumn(min_value=0.0, step=5.0, format="%.1f")})
with right:
    section("Your limits", "Step 2")
    budget_bp = st.slider("Loss limit (bp of the treasury)", 5, 500, 50, step=5,
                          help="Your risk budget. 50bp on a $100m treasury = $0.5m.")
    need_days = st.select_slider("Cash needed within", options=[0, 1, 2, 3, 5], value=2,
                                 format_func=lambda d: "Same day" if d == 0 else f"T+{d}",
                                 help="How quickly you must be able to turn the stablecoins back into cash.")
    direct = st.toggle("Direct redemption accounts", value=True,
                       help="Off = every exit is a sale on the market at the model's price.")

ids = {v: k for k, v in short.items()}
holdings = [Holding(ids[r["Coin"]], float(r["Holding ($m)"] or 0)) for _, r in pd.DataFrame(mix).iterrows()
            if r.get("Coin") in ids and (r.get("Holding ($m)") or 0) > 0]
if not holdings:
    st.info("Add at least one coin with a holding above zero.")
    st.stop()

# merge duplicate rows of the same coin
agg: dict[str, float] = {}
for h in holdings:
    agg[h.preset_id] = agg.get(h.preset_id, 0.0) + h.amount_m
holdings = [Holding(k, v) for k, v in agg.items()]
total = sum(h.amount_m for h in holdings)
budget_m = total * budget_bp / 1e4


@st.cache_data(show_spinner="Stress-testing the treasury...")
def run(hs: tuple, days: int, direct: bool) -> pd.DataFrame:
    return stress_treasury([Holding(p, a) for p, a in hs], STANDARD_SCENARIOS, liquidity_days=days,
                           direct_access=direct)


@st.cache_data(show_spinner=False)
def caps(pids: tuple, total: float, bp: float) -> pd.DataFrame:
    return safe_caps(list(pids), total, bp)


detail = run(tuple((h.preset_id, h.amount_m) for h in holdings), need_days, direct)
summ = treasury_summary(detail, total, budget_bp)

st.divider()
section("Result", "Step 3")
k = st.columns(4)
k[0].metric("Treasury", fmt_m(total, 1))
k[1].metric("Worst-crisis loss", fmt_m(summ["worst_loss_m"]), delta=f"{summ['worst_loss_bp']:,.0f}bp",
            delta_color="inverse", help=f"In: {summ['worst_scenario']}")
k[2].metric("Cash within the deadline", f"{summ['cash_share']:.0%}",
            help="In the crisis that leaves the least cash: redeemed in time, or sold on the market")
k[3].metric("Diversification benefit", fmt_m(summ["diversification_m"]),
            help="Sum of each coin's own worst-crisis loss, minus the worst loss of the mix. Positive when the coins "
                 "are hurt by different crises.")
if summ["within_budget"]:
    st.success(esc(f"Within budget: the worst crisis costs {fmt_m(summ['worst_loss_m'])}, under your "
                   f"{fmt_m(budget_m)} limit."), icon=":material/verified:")
else:
    st.error(esc(f"Over budget: {summ['worst_scenario']} costs {fmt_m(summ['worst_loss_m'])} against a "
                 f"{fmt_m(budget_m)} limit. See the safe caps below."), icon=":material/warning:")

c1, c2 = st.columns([1.1, 1])
with c1:
    st.plotly_chart(charts.treasury_losses(summ["by_scenario"], budget_m), width="stretch",
                    config={"displayModeBar": False})
with c2:
    section("Safe cap for each coin")
    cp = caps(tuple(agg), total, float(budget_bp))
    rows = []
    for r in cp.itertuples():
        held = agg[r.preset_id]
        cap_txt = "no limit from these crises" if math.isinf(r.cap_m) else fmt_m(r.cap_m)
        over = (not math.isinf(r.cap_m)) and held > r.cap_m + 1e-9
        rows.append({"Coin": short.get(r.preset_id, names[r.preset_id]), "Held": fmt_m(held), "Safe cap": cap_txt,
                     "Status": "Over cap" if over else "OK"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Coin": st.column_config.TextColumn(width="medium")})
    st.caption("Cap = your loss budget / the coin's worst haircut across the five crises: the most you could hold "
               "of that coin alone and still stay within budget.")

with st.expander("Coin by crisis detail", icon=":material/table:"):
    d = detail.copy()
    d["Coin"] = d["preset_id"].map(short)
    html = ['<table class="pg-table"><tr><th>Coin</th><th>Crisis</th><th>Verdict</th><th>Loss if held</th>'
            '<th>Cash in time</th><th>Exit route</th></tr>']
    for r in d.itertuples():
        html.append(f'<tr><td class="name">{r.Coin}</td><td>{r.scenario}</td><td>{verdict_chip(r.verdict)}</td>'
                    f'<td>{fmt_m(r.loss_m)}</td><td>{fmt_m(r.cash_m)}</td>'
                    f'<td>{"Redeem" if r.route == "redeem" else "Sell"}</td></tr>')
    html.append("</table>")
    st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)

section("Redemption terms used", "Access")
steps_list([f"**{names[pid]}:** " + redemption_terms(pid)["note"] for pid in agg])
st.caption("All coins face the same crisis at the same time (a common shock), which is the conservative view. "
           "Holdings are assumed small next to each coin's run, so they do not change it.")
