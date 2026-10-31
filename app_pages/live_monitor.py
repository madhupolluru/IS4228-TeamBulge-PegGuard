# Live Monitor (a Dashboard section): current USD stablecoin supply and how close each coin trades to $1.

from datetime import datetime

import pandas as pd
import streamlit as st

from app_pages.common import PEG_ALERT_BP, SUPPLY_ALERT_PCT, esc, request_preset, section
from pegguard import charts
from pegguard.analysis import projected_run
from pegguard.data_sources import load_price_history, load_stablecoins
from pegguard.presets import preset_info

NUMERIC = ["supply_usd", "prev_day_usd", "prev_week_usd", "prev_month_usd", "price", "peg_gap_bps", "chg_7d_pct",
           "chg_30d_pct"]
REFRESH = "5m"  # auto-refresh interval while live data is on


@st.cache_data(ttl=300, show_spinner="Fetching stablecoin data...")
def get_table(live: bool):
    df, meta = load_stablecoins(live=live)
    df = df.copy()
    for c in NUMERIC:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, {**meta, "fetched": datetime.now().strftime("%d %b %Y, %H:%M")}


@st.cache_data(ttl=300, show_spinner="Fetching price history...")
def get_history(live: bool):
    return load_price_history(days=90, live=live)


def market_table(live: bool) -> tuple[pd.DataFrame, dict]:
    # cleaned market table (numbers as floats) plus metadata, shared with the Dashboard tiles
    return get_table(live)


def alerts(df: pd.DataFrame) -> pd.DataFrame:
    # coins breaching the peg or supply thresholds, with a plain-English reason
    rows = []
    for _, r in df.iterrows():
        why = []
        if pd.notna(r.get("peg_gap_bps")) and abs(r["peg_gap_bps"]) > PEG_ALERT_BP:
            why.append(f"{r['peg_gap_bps']:+.0f}bp off peg")
        if pd.notna(r.get("chg_7d_pct")) and r["chg_7d_pct"] < SUPPLY_ALERT_PCT:
            why.append(f"supply {r['chg_7d_pct']:+.1f}% in 7 days")
        if why:
            rows.append({"symbol": r["symbol"], "reason": ", ".join(why)})
    return pd.DataFrame(rows, columns=["symbol", "reason"])


def _body() -> None:
    live = bool(st.session_state.get("live_data", False))
    df, meta = market_table(live)
    mode_text = {"live": "Live", "cache": "Cached", "snapshot": "Bundled snapshot"}.get(meta["mode"], "")
    dot = "#13804A" if meta["mode"] == "live" else "#B7791F"
    refresh = f" · refreshes every {REFRESH}" if live else ""
    st.markdown(
        f'<div class="pg-status"><span class="dot" style="background:{dot}"></span><b>{mode_text}</b>'
        f'<span>Data as of {meta["as_of"]}</span><span>Loaded {meta["fetched"]}{refresh}</span></div>',
        unsafe_allow_html=True)
    if meta["mode"] == "snapshot":
        st.caption("Showing supply from the latest issuer reports. Turn on **Fetch live market data** in the "
                   "sidebar for current prices, peg alerts and supply changes.")
    else:
        st.caption(esc(meta["note"]))

    if df.empty:
        st.warning("No stablecoin data available.")
        return

    has_price = df["price"].notna().any()
    flagged = alerts(df)
    # alerts need prices; without them the caption above explains how to switch them on
    if has_price and flagged.empty:
        st.success(f"No coin is more than {PEG_ALERT_BP:.0f}bp off peg or losing more than "
                   f"{-SUPPLY_ALERT_PCT:.0f}% of supply in 7 days.", icon=":material/check_circle:")
    elif has_price:
        st.error("**Alerts:** " + " · ".join(f"**{r.symbol}** {r.reason}" for r in flagged.itertuples()),
                 icon=":material/warning:")

    early_warning(df)

    # headline numbers only when there is live data to fill them
    if has_price:
        total = df["supply_usd"].sum()
        k = st.columns(4)
        k[0].metric("USD stablecoins tracked", f"{len(df)}")
        k[1].metric("Total supply", f"${total / 1e9:,.1f}bn")
        k[2].metric("Share of top 2", f"{df.head(2)['supply_usd'].sum() / total:.0%}" if total else "n/a",
                    help="Concentration: how much of the market two issuers control")
        k[3].metric("Coins on alert", f"{len(flagged)}",
                    help=f"More than {PEG_ALERT_BP:.0f}bp off peg, or supply down more than "
                         f"{-SUPPLY_ALERT_PCT:.0f}% in 7 days")

    c1, c2 = st.columns([1, 1.25])
    with c1:
        st.plotly_chart(charts.supply_bars(df), width="stretch", config={"displayModeBar": False})
    with c2:
        show = df.head(20)[["symbol", "name", "mechanism", "supply_usd", "chg_7d_pct", "chg_30d_pct", "price",
                            "peg_gap_bps"]].copy()
        show["supply_usd"] = show["supply_usd"] / 1e9
        show["alert"] = show["symbol"].map(dict(zip(flagged["symbol"], flagged["reason"]))).fillna("")
        # Columns with no data at all (offline snapshot) are dropped rather than shown as empty.
        show = show[[c for c in show.columns if c in ("symbol", "name", "alert") or show[c].notna().any()]]
        cfg = {
            "symbol": st.column_config.TextColumn("Coin", width="small"),
            "name": st.column_config.TextColumn("Name"),
            "mechanism": st.column_config.TextColumn("Backing"),
            "supply_usd": st.column_config.NumberColumn("Supply ($bn)", format="%.2f"),
            "chg_7d_pct": st.column_config.NumberColumn("7d chg (%)", format="%+.1f"),
            "chg_30d_pct": st.column_config.NumberColumn("30d chg (%)", format="%+.1f"),
            "price": st.column_config.NumberColumn("Price", format="%.4f"),
            "peg_gap_bps": st.column_config.NumberColumn("Peg gap (bp)", format="%+.0f"),
            "alert": st.column_config.TextColumn("Alert"),
        }
        st.dataframe(show, width="stretch", hide_index=True, column_config=cfg,
                     height=min(420, 35 * (len(show) + 1) + 3))

    hist, hmeta = get_history(live)
    # no price history offline: the caption at the top already says how to switch live data on
    if not hist.empty:
        coins = sorted(hist["coin"].unique())
        pick = st.multiselect("Coins", coins, default=[c for c in ["USDT", "USDC", "DAI"] if c in coins] or coins[:3])
        st.plotly_chart(charts.peg_history(hist[hist["coin"].isin(pick)]), width="stretch",
                        config={"displayModeBar": False})
        st.caption(f"{hmeta['note']} · as of {hmeta['as_of']}")


def _stress_outflow(preset_id: str, run_pct: float) -> None:
    request_preset(preset_id)
    st.session_state.update(sc_run=float(min(100.0, max(5.0, round(run_pct / 5) * 5))), sc_days=5, sc_front=0.0,
                            sc_rate=0.0, sc_freeze=0.0, sc_crypto=0.0, sc_gold=0.0, sc_risk=0.0, sc_loan=0.0)
    st.switch_page("app_pages/stress_lab.py")


def early_warning(df: pd.DataFrame) -> None:
    # for each coin with a bundled reserve portfolio: last 7 days' supply change, carried forward as a run
    issuers = [i for i in preset_info() if i["group"] == "issuer" and i.get("symbol")]
    rows = []
    for info in issuers:
        m = df[df["symbol"] == info["symbol"]]
        if m.empty:
            continue
        chg = float(m["chg_7d_pct"].iloc[0]) if pd.notna(m["chg_7d_pct"].iloc[0]) else float("nan")
        rows.append((info, chg, projected_run(chg, days=5)))
    # no supply history (offline snapshot): nothing useful to show
    if not any(pd.notna(chg) for _, chg, _ in rows):
        return
    cols = st.columns(len(rows))
    for col, (info, chg, run) in zip(cols, rows):
        with col:
            trend = "n/a" if pd.isna(chg) else f"{chg:+.1f}% in 7 days"
            col.metric(f"{info['symbol']} supply", trend, delta=None if run == 0 else f"{run:.1f}% run if it continues",
                       delta_color="inverse",
                       help="Projected run: the last 7 days' net redemptions carried on for another 5 business "
                            "days. A growing supply means no run.")
            if run > 0 and st.button(f"Stress-test this outflow ({info['symbol']})", key=f"ew_{info['id']}",
                                     icon=":material/science:", width="stretch"):
                _stress_outflow(info["id"], run)
    st.caption("Early warning: real redemption flows (on-chain supply) turned into a run scenario you can test "
               "against the coin's reserves in one click.")


def render() -> None:
    section("Market now", "Live monitor")
    live = bool(st.session_state.get("live_data", False))
    # Only this section re-runs on the timer, so the rest of the page is not recomputed.
    st.fragment(_body, run_every=REFRESH if live else None)()
