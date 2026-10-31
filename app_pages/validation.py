# Validation: does the model agree with what actually happened in real runs?

import pandas as pd
import streamlit as st

from app_pages.common import esc, fmt_bn, fmt_token, page_header, section, steps_list, verdict_chip
from pegguard import charts
from pegguard.backtest import events, fear_premium_calibration, run_backtests, run_event

page_header("Evidence", "Validation",
            "Four real runs, each rebuilt from the last public reserve report before it and driven by the "
            "redemptions that actually happened. Does the model reach the same outcome?")


@st.cache_data(show_spinner="Replaying four historical runs...")
def results() -> pd.DataFrame:
    return run_backtests()


@st.cache_data(show_spinner=False)
def event_run(key: str):
    # daily table and summary for one event (cached, so the page does not re-run them on every click)
    ev = next(e for e in events() if e.key == key)
    r = run_event(ev)
    return r.daily, r.summary


bt = results().rename(columns={"Model verdict": "model_verdict"})
premium = fear_premium_calibration(bt)
k = st.columns(3)
k[0].metric("Matches history", f"{int(bt['Match'].sum())} of {len(bt)}",
            help="Matches = the model pays (or fails to pay) on time exactly as the issuer did")
k[1].metric("Average miss at the price low", f"{premium:,.0f}bp" if pd.notna(premium) else "n/a",
            help="How much higher the model's market price is than the observed low. This is panic the "
                 "fundamentals do not explain; set it as the fear premium to reproduce the lows.")
k[2].metric("Events with a known market low", f"{int(bt['observed_low'].notna().sum())}")

html = ['<table class="pg-table"><tr><th>Event</th><th>What happened</th><th>Model verdict</th>'
        '<th>Agrees?</th><th>Low price: real vs model</th></tr>']
for r in bt.itertuples():
    low = (f"{fmt_token(r.observed_low)} vs {fmt_token(r.model_low)}" if pd.notna(r.observed_low)
           else f"no market low · model {fmt_token(r.model_low)}")
    ok = '<span style="color:#13804A;font-weight:700">Yes</span>' if r.Match else \
        '<span style="color:#C53030;font-weight:700">No</span>'
    html.append(f'<tr><td class="name">{r.Event}<div class="val">{r.Dates}</div></td>'
                f'<td style="text-align:left;max-width:380px">{r.Observed}</td><td>{verdict_chip(r.model_verdict)}</td>'
                f'<td>{ok}</td><td>{low}</td></tr>')
html.append("</table>")
st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)
st.caption("'Agrees' compares what was actually observed: whether redemptions were paid on time. Reserves marked to "
           "market were never observed, so BELOW PAR with everyone paid still agrees with a coin that paid out.")

priced = bt[bt["observed_low"].notna()]
if len(priced):
    st.plotly_chart(charts.backtest_chart(priced), width="stretch", config={"displayModeBar": False})
    st.markdown(esc(
        f"**Reading it.** In both events with a market low, the model's price is about {premium / 100:.1f} cents too "
        "high. The model prices what a buyer would get from the reserves; at the trough of a panic, traders "
        "pay less than that. The miss is consistent across two very different coins, so it can be treated as a "
        f"calibrated **fear premium** of about {premium:,.0f}bp (a slider in the Stress Lab)."))

st.divider()
section("Each event in detail", "Inputs and sources")
for ev in events():
    daily, s = event_run(ev.key)
    with st.expander(f"{ev.name} · {ev.date}", icon=":material/history:"):
        c1, c2 = st.columns([1.2, 1])
        with c1:
            model = (f"{s['verdict']}: {s['headline']} Paid {fmt_bn(s['paid'])} of {fmt_bn(s['requested'])} "
                     f"requested; lowest reserves per token {fmt_token(s['min_token_value'])}; lowest market price "
                     f"{fmt_token(s['min_market_price'])}."
                     + (f" First late payment on day {s['first_breach_day']}." if s["first_breach_day"] else ""))
            steps_list([f"**What happened:** {ev.observed}", f"**Model:** {model}",
                        f"**Reserves:** {ev.reserves_source}", f"**Redemptions:** {ev.run_source}"]
                       + [f"**Note:** {n}" for n in ev.notes])
        with c2:
            st.plotly_chart(charts.coverage_path(daily), width="stretch", config={"displayModeBar": False},
                            key=f"bt_{ev.key}")
        st.dataframe(pd.DataFrame([{"Holding": l.name, "Asset class": l.spec.label, "Amount ($bn)": l.amount_bn,
                                    "Maturity (yrs)": l.maturity_years, "Yield (%)": l.yield_pct}
                                   for l in ev.lines]),
                     hide_index=True, width="stretch")

st.caption("Small sample: four events are what history offers for fiat-backed runs with public data. They test "
           "the mechanics (liquidity, closures, frozen cash, duration losses), not a forecast of any coin.")
