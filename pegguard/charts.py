# Plotly figures used by the app. Kept separate so the engine has no UI dependencies.

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

INK = "#0B1324"
MUTED = "#5B6B82"
GRID = "#EDF1F6"
AXIS = "#D5DCE6"
ACCENT = "#0D7A6F"
TOKENS = "#2B3A55"
GOOD = "#13804A"
WARN = "#B7791F"
BAD = "#C53030"
PAR = "#94A3B8"

# short asset-class names so chart legends fit in narrow cards
SHORT_NAMES = {"cash_bank": "Bank cash", "repo": "Reverse repo", "mmf_gov": "Money market fund", "tbill": "T-bills",
               "tnote": "Treasury notes", "agency_mbs": "Agency MBS", "commercial_paper": "Commercial paper",
               "gold": "Gold", "crypto": "Bitcoin", "other": "Other investments", "secured_loan": "Secured loans"}

VERDICT_COLORS = {"HOLDS": GOOD, "LIQUIDITY STRAIN": WARN, "BELOW PAR": "#C05621", "BREAKS": BAD}

# chart text is 12px everywhere (ticks, legends, labels); titles are 14px
FONT = dict(family="Inter, -apple-system, Segoe UI, Roboto, sans-serif", color=INK, size=12)
TITLE_SIZE = 14


def _base(fig: go.Figure, height: int = 340, title: str | None = None, legend: bool = True) -> go.Figure:
    # legend=False for single-series charts: no empty legend row between the title and the plot
    fig.update_layout(
        template="simple_white",
        height=height,
        font=FONT,
        margin=dict(l=16, r=16, t=(86 if legend else 58) if title else 16, b=12),
        showlegend=legend,
        # title inset from the card's top-left corner (the card itself has no padding)
        title=dict(text=title, x=0, xanchor="left", y=1, yanchor="top", pad=dict(t=16, l=16),
                   font=dict(size=TITLE_SIZE, color=INK, weight=600)) if title else dict(text=""),
        # legend sits on its own row between the title and the plot, so long titles never collide with it
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, font=dict(size=FONT["size"], color=MUTED),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="white", bordercolor=AXIS, font=dict(family=FONT["family"], color=INK, size=FONT["size"])),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, ticks="", tickfont=dict(color=MUTED),
                     title_font=dict(color=MUTED, size=FONT["size"]))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, showline=False, ticks="", tickfont=dict(color=MUTED),
                     title_font=dict(color=MUTED, size=FONT["size"]), zeroline=False)
    return fig


def _money(bn: float) -> str:
    # $bn label that switches to $m below $1bn, so small values never read "$0.00bn"
    if abs(bn) < 5e-7:
        return "$0"
    return f"${bn * 1000:,.1f}m" if abs(bn) < 1 else f"${bn:,.2f}bn"


def _legend_line(fig: go.Figure, name: str, color: str, dash: str = "dash") -> None:
    # a reference line explained in the legend instead of a label drawn over the data
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=name, line=dict(color=color, dash=dash, width=2),
                             hoverinfo="skip"))


def composition_donut(comp: pd.DataFrame, center_text: str = "") -> go.Figure:
    fig = go.Figure(
        go.Pie(
            labels=comp["key"].map(SHORT_NAMES).fillna(comp["Asset class"]), values=comp["Amount ($bn)"],
            hole=0.62, sort=False,
            title=dict(text=center_text, position="middle center", font=dict(size=TITLE_SIZE, color=INK)),
            marker=dict(colors=comp["color"], line=dict(color="white", width=2)),
            textinfo="none",
            hovertemplate="<b>%{label}</b><br>$%{value:,.2f}bn<br>%{percent}<extra></extra>",
        )
    )
    # ring on the left half, legend on the right half, so neither is cut off in a narrow card
    fig.update_traces(domain=dict(x=[0, 0.5], y=[0, 1]))
    fig = _base(fig, height=240, legend=True)
    fig.update_layout(legend=dict(orientation="v", x=0.56, y=0.5, yanchor="middle", xanchor="left"),
                      margin=dict(l=12, r=12, t=12, b=12))
    return fig


def reserves_vs_tokens(daily: pd.DataFrame) -> go.Figure:
    short = bool((daily["reserves"] < daily["supply"] * 0.9995).any())
    fill = "rgba(197,48,48,0.10)" if short else "rgba(13,122,111,0.10)"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily["day"], y=daily["supply"], name="Tokens outstanding", mode="lines+markers",
                             line=dict(color=TOKENS, width=2, dash="dot"), marker=dict(size=6),
                             hovertemplate="Day %{x}<br>Tokens $%{y:,.2f}bn<extra></extra>"))
    fig.add_trace(go.Scatter(x=daily["day"], y=daily["reserves"], name="Reserves (marked to market)",
                             mode="lines+markers", line=dict(color=ACCENT, width=3), marker=dict(size=7),
                             fill="tonexty", fillcolor=fill,
                             hovertemplate="Day %{x}<br>Reserves $%{y:,.2f}bn<extra></extra>"))
    fig = _base(fig, title="Reserves vs tokens outstanding ($bn)")
    fig.update_xaxes(title="Day", dtick=1)
    return fig


def coverage_path(daily: pd.DataFrame) -> go.Figure:
    cov = daily["coverage"].astype(float)
    colors = [GOOD if c >= 0.9995 else BAD for c in cov.fillna(0)]
    fig = go.Figure()
    fig.add_hline(y=1.0, line=dict(color=PAR, dash="dash"))
    fig.add_trace(go.Scatter(x=daily["day"], y=cov, mode="lines+markers", name="Reserves per token",
                             line=dict(color=INK, width=2), marker=dict(size=8, color=colors),
                             hovertemplate="Day %{x}<br>Reserves per token $%{y:.4f}<extra></extra>"))
    vals = cov
    if "market_price" in daily:
        mkt = daily["market_price"].astype(float)
        fig.add_trace(go.Scatter(x=daily["day"], y=mkt, mode="lines", name="Market price (model)",
                                 line=dict(color=WARN, width=2, dash="dot"),
                                 hovertemplate="Day %{x}<br>Market price $%{y:.4f}<extra></extra>"))
        vals = pd.concat([cov, mkt])
    _legend_line(fig, "Par ($1.00)", PAR)
    title = "Reserves per token and market price"
    if "open" in daily and (~daily["open"]).any():
        closed = daily.loc[~daily["open"], "day"]
        fig.add_vrect(x0=float(closed.min()) - 0.5, x1=float(closed.max()) + 0.5, fillcolor="rgba(100,116,139,0.10)",
                      line_width=0)
        title += " (shaded: redemptions closed)"
    fig = _base(fig, title=title)
    lo = float(np.nanmin(vals)) if vals.notna().any() else 0.9
    hi = float(np.nanmax(vals)) if vals.notna().any() else 1.1
    fig.update_yaxes(range=[min(lo, 0.99) - 0.01, max(hi, 1.01) + 0.01], tickformat=".3f")
    fig.update_xaxes(title="Day", dtick=1)
    return fig


def redemptions_bars(daily: pd.DataFrame) -> go.Figure:
    d = daily[daily["day"] > 0]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d["day"], y=d["paid"], name="Paid at $1", marker_color=ACCENT,
                         hovertemplate="Day %{x}<br>Paid $%{y:,.2f}bn<extra></extra>"))
    fig.add_trace(go.Bar(x=d["day"], y=d["unmet"], name="Waiting (unmet)", marker_color=BAD,
                         hovertemplate="Day %{x}<br>Unmet $%{y:,.2f}bn<extra></extra>"))
    fig = _base(fig, title="Redemptions each day ($bn)")
    fig.update_layout(barmode="stack", bargap=0.35, legend_traceorder="normal")
    fig.update_xaxes(title="Day", dtick=1)
    return fig


def loss_breakdown(summary: dict) -> go.Figure:
    items = [
        ("Rate shock (bills/notes)", summary["rate_loss"]),
        ("Price & credit shocks", summary["price_loss"]),
        ("Frozen bank cash written off", summary["frozen_writedown"]),
        ("Fire-sale discounts", summary["fire_sale_loss"]),
        ("Price drop on unsold holdings", summary.get("spillover_loss", 0.0)),
    ]
    df = pd.DataFrame(items, columns=["Source", "Loss"])
    fig = go.Figure(go.Bar(x=df["Loss"], y=df["Source"], orientation="h",
                           marker_color=["#7A4FD1", "#C77D12", "#2B59C3", BAD, "#E07A5F"],
                           text=[_money(v) for v in df["Loss"]], textposition="outside",
                           cliponaxis=False, hovertemplate="%{y}: $%{x:,.3f}bn<extra></extra>"))
    fig = _base(fig, height=260, title="Where the losses came from", legend=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, max(df["Loss"].max() * 1.5, 0.01)])
    return fig


def holdings_used(by_holding: pd.DataFrame) -> go.Figure:
    d = by_holding.copy()
    fig = go.Figure()
    fig.add_trace(go.Bar(y=d["Holding"], x=d["End ($bn)"], name="Left", orientation="h", marker_color="#D5DCE6",
                         hovertemplate="%{y}<br>Left $%{x:,.2f}bn<extra></extra>"))
    fig.add_trace(go.Bar(y=d["Holding"], x=d["Sold ($bn)"], name="Sold to pay redemptions", orientation="h",
                         marker_color=ACCENT, hovertemplate="%{y}<br>Sold $%{x:,.2f}bn<extra></extra>"))
    lost = d["Rate loss ($bn)"] + d["Price/credit loss ($bn)"] + d["Frozen ($bn)"] + d.get("Spillover loss ($bn)", 0.0)
    fig.add_trace(go.Bar(y=d["Holding"], x=lost, name="Lost, frozen or marked down", orientation="h",
                         marker_color=BAD, hovertemplate="%{y}<br>Lost/frozen $%{x:,.2f}bn<extra></extra>"))
    fig = _base(fig, height=max(240, 40 * len(d) + 90), title="What each holding did ($bn)")
    # legend in the same left-to-right order as the bars; right margin so the last tick is not cut
    fig.update_layout(barmode="stack", legend_traceorder="normal", margin=dict(r=28))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


def breaking_heatmap(grid: pd.DataFrame, frontier: pd.DataFrame | None = None,
                     marker: tuple[float, float] | None = None) -> go.Figure:
    pivot = grid.pivot(index="run_pct", columns="rate_shock_bp", values="min_coverage")
    late = grid.pivot(index="run_pct", columns="rate_shock_bp", values="late")
    z = pivot.values.astype(float)
    zc = np.clip(z, 0.80, 1.05)
    # diverging scale centred on par: red below 1.0, green at/above
    lo, hi = 0.80, 1.05
    mid = (1.0 - lo) / (hi - lo)
    scale = [[0.0, "#7F1D1D"], [mid * 0.5, "#DC2626"], [mid * 0.98, "#FCA5A5"], [mid, "#F8FAFC"],
             [min(mid + 0.02, 1), "#BBF7D0"], [1.0, "#15803D"]]
    text = np.where(late.values, "late", "")
    fig = go.Figure(go.Heatmap(
        z=zc, x=pivot.columns, y=pivot.index, zmin=lo, zmax=hi, colorscale=scale,
        customdata=np.dstack([z, late.values]),
        text=text, texttemplate="%{text}", textfont=dict(size=9, color="rgba(255,255,255,0.85)"),
        # no colour-bar title: the chart title already says this is the lowest coverage
        colorbar=dict(tickformat=".2f", len=0.8, thickness=14),
        hovertemplate="Rate shock +%{x:.0f}bp<br>Run %{y:.0f}% of supply<br>Lowest coverage %{customdata[0]:.4f}"
                      "<br>Paid late: %{customdata[1]}<extra></extra>",
    ))
    if frontier is not None and len(frontier):
        fig.add_trace(go.Scatter(x=frontier["rate_shock_bp"], y=frontier["max_run_paid_on_time"], mode="lines",
                                 line=dict(color=INK, width=2, dash="dash", shape="hv"),
                                 name="Largest run paid on time",
                                 hovertemplate="+%{x:.0f}bp: paid on time up to %{y:.0f}% run<extra></extra>"))
        fig.add_trace(go.Scatter(x=frontier["rate_shock_bp"], y=frontier["max_run_holds"], mode="lines",
                                 line=dict(color=INK, width=3, shape="hv"), name="Largest run that still holds",
                                 hovertemplate="+%{x:.0f}bp: survives up to %{y:.0f}% run<extra></extra>"))
    if marker:
        fig.add_trace(go.Scatter(x=[marker[0]], y=[marker[1]], mode="markers", name="Your scenario",
                                 marker=dict(symbol="x", size=16, color=INK, line=dict(width=2, color="white"))))
    fig = _base(fig, height=520, title="Where the peg breaks: lowest coverage by rate shock and run size")
    fig.update_xaxes(title="Parallel rate shock (bp)")
    fig.update_yaxes(title="Run size (% of supply asking to redeem)", showgrid=False)
    return fig


def liquidity_ladder(ladder: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(x=ladder["Days"].astype(str) + "d", y=ladder["Share of supply"],
                           marker_color=[GOOD if v >= 1 else ACCENT for v in ladder["Share of supply"]],
                           # labels inside the bar top, so they never sit on the 100% line
                           text=[f"{v:.0%}" for v in ladder["Share of supply"]], textposition="inside",
                           insidetextanchor="end", textfont=dict(color="white"),
                           hovertemplate="Within %{x}: %{y:.1%} of supply<extra></extra>"))
    fig.add_hline(y=1.0, line=dict(color=INK, dash="dash", width=1.5), layer="above")
    fig = _base(fig, height=300, title="Cash raisable within N days (dashed: every token)",
                legend=False)
    fig.update_yaxes(tickformat=".0%", range=[0, max(1.15, float(ladder["Share of supply"].max()) * 1.15)])
    return fig


def peg_history(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_hrect(y0=0.995, y1=1.005, fillcolor="rgba(19,128,74,0.07)", line_width=0)
    palette = [ACCENT, "#2B59C3", "#C77D12", "#7A4FD1", "#C2416B", "#5B6B82"]
    for i, (coin, g) in enumerate(df.groupby("coin")):
        fig.add_trace(go.Scatter(x=g["time"], y=g["price"], name=coin, mode="lines",
                                 line=dict(width=2, color=palette[i % len(palette)]),
                                 hovertemplate=f"{coin} %{{x|%d %b %Y}}: $%{{y:.4f}}<extra></extra>"))
    fig = _base(fig, height=360, title="Daily price vs the $1 peg (shaded: within 50bp)")
    fig.update_yaxes(tickformat=".3f")
    return fig


def supply_bars(df: pd.DataFrame, n: int = 10) -> go.Figure:
    d = df.head(n)
    fig = go.Figure(go.Bar(x=d["supply_usd"] / 1e9, y=d["symbol"], orientation="h", marker_color=ACCENT,
                           text=[f"${v / 1e9:,.1f}bn" for v in d["supply_usd"]], textposition="outside",
                           cliponaxis=False, hovertemplate="%{y}: $%{x:,.2f}bn<extra></extra>"))
    fig = _base(fig, height=max(220, 34 * len(d) + 60), title="Largest USD stablecoins by supply", legend=False)
    fig.update_layout(bargap=0.45)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, float(d["supply_usd"].max() / 1e9) * 1.3])
    return fig


def preset_frontier_bars(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(x=df["Largest run survived (%)"], y=df["Portfolio"], orientation="h",
                           marker_color=[GOOD if v >= 50 else (WARN if v >= 20 else BAD)
                                         for v in df["Largest run survived (%)"]],
                           text=[f"{v:.0f}%" for v in df["Largest run survived (%)"]], textposition="outside",
                           cliponaxis=False, hovertemplate="%{y}: %{x:.0f}% run<extra></extra>"))
    fig = _base(fig, height=max(220, 44 * len(df) + 60), title="Largest run survived at this rate shock",
                legend=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(range=[0, 130], showgrid=False, showticklabels=False)
    return fig


def tornado_chart(t: pd.DataFrame, base: float, n: int = 10) -> go.Figure:
    # Each assumption's low and high case as a bar around the base result (worst value per token).
    d = t.head(n).iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=d["Assumption"], x=d["low"] - base, base=base, orientation="h", name="Low case",
                         marker_color="#94A3B8", customdata=d[["low", "verdict_low"]],
                         hovertemplate="%{y}<br>Low case: $%{customdata[0]:.4f} (%{customdata[1]})<extra></extra>"))
    fig.add_trace(go.Bar(y=d["Assumption"], x=d["high"] - base, base=base, orientation="h", name="High case",
                         marker_color=ACCENT, customdata=d[["high", "verdict_high"]],
                         hovertemplate="%{y}<br>High case: $%{customdata[0]:.4f} (%{customdata[1]})<extra></extra>"))
    fig.add_vline(x=base, line=dict(color=INK, width=1.5))
    fig = _base(fig, height=max(280, 34 * len(d) + 110), title="What moves the worst value per token")
    fig.update_layout(barmode="overlay")
    fig.update_yaxes(showgrid=False)
    # zoom onto the bars rather than starting the axis at zero
    lo = float(min(d["low"].min(), d["high"].min(), base))
    hi = float(max(d["low"].max(), d["high"].max(), base))
    pad = max((hi - lo) * 0.1, 0.002)
    # the base value goes in the axis title, so the legend stays on one row in a narrow column
    fig.update_xaxes(tickformat=".3f", showgrid=True, gridcolor=GRID,
                     title=f"Worst value per token ($) · line = yours, ${base:.4f}",
                     range=[lo - pad, hi + pad])
    return fig


def mc_histogram(df: pd.DataFrame) -> go.Figure:
    colors = {"HOLDS": GOOD, "LIQUIDITY STRAIN": WARN, "BELOW PAR": "#C05621", "BREAKS": BAD}
    fig = go.Figure()
    for v in ("HOLDS", "LIQUIDITY STRAIN", "BELOW PAR", "BREAKS"):
        g = df[df["verdict"] == v]
        if len(g):
            fig.add_trace(go.Histogram(x=g["min_token_value"], name=v, marker_color=colors[v], nbinsx=40,
                                       hovertemplate=f"{v}<br>$%{{x:.3f}}: %{{y}} runs<extra></extra>"))
    fig = _base(fig, height=320, title="Worst value per token across plausible assumptions")
    fig.update_layout(barmode="stack", bargap=0.05)
    fig.update_xaxes(title="Worst value per token ($)", tickformat=".3f")
    fig.update_yaxes(title="Runs")
    return fig


def treasury_losses(by_sc: pd.DataFrame, budget_m: float) -> go.Figure:
    fig = go.Figure(go.Bar(x=by_sc["loss_m"], y=by_sc["scenario"], orientation="h",
                           marker_color=[BAD if v > budget_m + 1e-9 else ACCENT for v in by_sc["loss_m"]],
                           # $m with 2 decimals, like the rest of the Treasury Planner page
                           text=["$0" if v < 0.005 else f"${v:,.2f}m" for v in by_sc["loss_m"]], textposition="outside",
                           cliponaxis=False,
                           hovertemplate="%{y}: $%{x:,.2f}m<extra></extra>", showlegend=False))
    fig.add_vline(x=budget_m, line=dict(color=INK, dash="dash"))
    _legend_line(fig, f"Loss budget (${budget_m:,.2f}m)", INK)
    fig = _base(fig, height=max(240, 46 * len(by_sc) + 80), title="Loss on the whole treasury in each crisis ($m)")
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(range=[0, max(float(by_sc["loss_m"].max()), budget_m) * 1.45 + 1e-6], showgrid=False,
                     showticklabels=False)
    return fig


def backtest_chart(df: pd.DataFrame) -> go.Figure:
    # Model vs observed lowest market price for each historical event.
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["Event"], y=df["observed_low"], name="Observed low price", marker_color=TOKENS,
                         text=[f"${v:.3f}" for v in df["observed_low"]], textposition="inside",
                         insidetextanchor="end", textfont=dict(color="white"),
                         hovertemplate="%{x}<br>Observed low $%{y:.4f}<extra></extra>"))
    fig.add_trace(go.Bar(x=df["Event"], y=df["model_low"], name="Model market price", marker_color=ACCENT,
                         text=[f"${v:.3f}" for v in df["model_low"]], textposition="inside",
                         insidetextanchor="end", textfont=dict(color="white"),
                         hovertemplate="%{x}<br>Model $%{y:.4f}<extra></extra>"))
    fig.add_hline(y=1.0, line=dict(color=INK, dash="dash", width=1.5), layer="above")
    _legend_line(fig, "Par ($1.00)", INK)
    fig = _base(fig, height=360, title="Lowest price: what happened vs what the model says")
    fig.update_yaxes(range=[max(0.0, float(min(df["observed_low"].min(), df["model_low"].min())) - 0.1), 1.06],
                     tickformat=".2f", tickprefix="$")
    fig.update_layout(barmode="group", bargap=0.3)
    return fig
