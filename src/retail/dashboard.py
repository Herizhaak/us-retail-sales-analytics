"""Build the interactive HTML dashboard and a static overview PNG for the README.

Inputs: data/*.csv, the SQL results (re-run here) and the forecast outputs in
reports/ (run `python -m retail.forecast` first).
Outputs: reports/dashboard.html (self-contained, Plotly) and
reports/figures/overview.png (matplotlib).

Run from the repo root:  python -m retail.dashboard
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

from .data import FIGURES, REPORTS, TOTAL, load_ecommerce, load_sales, monthly_series  # noqa: E402
from .sql_report import run  # noqa: E402

# Categorical slots in fixed order, chart ink and surfaces.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
COVID_SERIES = [("44X72", "Total retail and food services", INK),
                ("722", "Food services and drinking places", ORANGE),
                ("448", "Clothing and accessories", AQUA),
                ("454", "Nonstore retailers", BLUE)]


def covid_index(sales):
    """Seasonally adjusted sales indexed to February 2020 = 100, from 2019."""
    sa = sales[(sales["adjustment"] == "SA") & (sales["month"] >= "2019-01-01")]
    wide = sa.pivot(index="month", columns="naics_code", values="sales_musd")
    return 100 * wide / wide.loc["2020-02-01"]


def _layout(fig, title, ytitle, height=380):
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15, color=INK)),
        template="simple_white", height=height, margin=dict(l=60, r=20, t=50, b=40),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, hovermode="x unified",
        legend=dict(orientation="h", y=-0.15, font=dict(color=INK_2)),
        font=dict(family="Segoe UI, Helvetica, Arial, sans-serif", color=INK_2),
    )
    fig.update_yaxes(title=ytitle, gridcolor=GRID, showgrid=True)
    return fig


def build_figures(sales, ecom, sql, forecast, backtest):
    figs = []
    nsa, sa = monthly_series(sales), monthly_series(sales, adjustment="SA")

    f = go.Figure()
    f.add_scatter(x=nsa.index, y=nsa / 1000, name="Not seasonally adjusted",
                  line=dict(color=INK_2, width=1))
    f.add_scatter(x=sa.index, y=sa / 1000, name="Seasonally adjusted",
                  line=dict(color=BLUE, width=2))
    fc_x = pd.to_datetime(forecast.index)
    f.add_scatter(x=fc_x, y=forecast["upper_80_musd"] / 1000, line=dict(width=0),
                  showlegend=False, hoverinfo="skip")
    f.add_scatter(x=fc_x, y=forecast["lower_80_musd"] / 1000, fill="tonexty", line=dict(width=0),
                  fillcolor="rgba(235,104,52,0.2)", name="80% interval")
    f.add_scatter(x=fc_x, y=forecast["forecast_musd"] / 1000, name="Forecast (NSA)",
                  line=dict(color=ORANGE, width=2))
    figs.append(_layout(f, "Monthly retail and food services sales, 1992 to forecast",
                        "$ billions per month"))

    share = sql["ecommerce_share"].dropna(subset=["nonstore_share_pct"])
    q = ecom.assign(date=pd.PeriodIndex(ecom["quarter"].str.replace("-", ""),
                                        freq="Q").to_timestamp())
    f = go.Figure()
    f.add_scatter(x=q["date"], y=q["ecommerce_share_pct"], name="E-commerce share (quarterly, SA)",
                  line=dict(color=BLUE, width=2))
    f.add_scatter(x=pd.to_datetime(share["year"].astype(str) + "-07-01"),
                  y=share["nonstore_share_pct"], name="Nonstore retailers (annual)",
                  mode="lines+markers", line=dict(color=ORANGE, width=2), marker=dict(size=8))
    figs.append(_layout(f, "Online keeps taking share: % of retail sales", "% of retail sales"))

    mix = sql["category_mix_shift"].sort_values("share_2025_pct")
    f = go.Figure()
    f.add_bar(y=mix["category"], x=mix["share_1992_pct"], name="1992", orientation="h",
              marker_color=INK_2)
    f.add_bar(y=mix["category"], x=mix["share_2025_pct"], name="2025", orientation="h",
              marker_color=BLUE)
    _layout(f, "Category mix: share of retail and food services sales", None, height=480)
    f.update_layout(barmode="group", hovermode="y unified", margin=dict(l=230))
    f.update_xaxes(title="% of total", gridcolor=GRID, showgrid=True)
    figs.append(f)

    idx = covid_index(sales)
    f = go.Figure()
    for code, name, color in COVID_SERIES:
        f.add_scatter(x=idx.index, y=idx[code], name=name, line=dict(color=color, width=2))
    f.add_hline(y=100, line=dict(color=INK_2, width=1, dash="dot"))
    figs.append(_layout(f, "COVID shock and recovery (seasonally adjusted, Feb 2020 = 100)",
                        "Index"))

    seas = sql["seasonality_by_category"]
    seas = seas[~seas["naics_code"].isin(["44000", "4541"])]
    grid = seas.pivot(index="category", columns="month", values="seasonal_index")
    grid = grid.loc[grid[12].sort_values().index]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    f = go.Figure(go.Heatmap(
        z=grid.to_numpy(), x=months, y=grid.index, zmid=100,
        colorscale=[[0, "#1c5cab"], [0.5, "#f0efec"], [1, "#e34948"]],
        colorbar=dict(title="Index"), xgap=2, ygap=2,
        hovertemplate="%{y}<br>%{x}: %{z:.1f}<extra></extra>"))
    _layout(f, "Seasonal index by category (average month = 100; 2015-19 and 2022-25)", None,
            height=480)
    f.update_layout(margin=dict(l=230), hovermode="closest")
    figs.append(f)

    bt = backtest.sort_values("mape_pct", ascending=False)
    f = go.Figure(go.Bar(x=bt["mape_pct"], y=bt["model"], orientation="h", marker_color=BLUE,
                         text=bt["mape_pct"].map("{:.2f}%".format), textposition="outside",
                         hovertemplate="%{y}: %{x:.2f}%<extra></extra>"))
    _layout(f, "Backtest: mean MAPE, six 12-month-ahead origins", None)
    f.update_layout(margin=dict(l=270), hovermode="closest")
    f.update_xaxes(title="Mean absolute % error", gridcolor=GRID, showgrid=True)
    figs.append(f)
    return figs


def kpis(sql, ecom, summary):
    yearly = sql["yearly_growth"].iloc[-1]
    nonstore = sql["ecommerce_share"].dropna(subset=["nonstore_share_pct"]).iloc[-1]
    last_q = ecom.iloc[-1]
    return [
        (f"${yearly['sales_trillion_usd']:.2f}T", f"retail and food services sales in "
         f"{int(yearly['year'])} ({yearly['yoy_growth_pct']:+.1f}% YoY)"),
        (f"{last_q['ecommerce_share_pct']:.1f}%",
         f"e-commerce share of retail, {last_q['quarter']}"),
        (f"{nonstore['nonstore_share_pct']:.1f}%",
         f"nonstore share of retail in {int(nonstore['year'])}"),
        (f"${summary['forecast_total_busd'] / 1000:.2f}T",
         f"forecast {summary['forecast_period']} "
         f"({summary['forecast_growth_vs_last_12m_pct']:+.1f}% on last 12 months)"),
        (f"{summary['mape_pct_by_model'][summary['best_model']]:.1f}%",
         f"backtest MAPE, {summary['best_model']}"),
    ]


def write_html(figs, tiles, path):
    parts = [fig.to_html(full_html=False, include_plotlyjs=(i == 0), div_id=f"chart-{i + 1}",
                         config={"displaylogo": False, "responsive": True})
             for i, fig in enumerate(figs)]
    tiles_html = "".join(f'<div class="tile"><div class="v">{v}</div><div class="l">{lbl}</div>'
                         "</div>" for v, lbl in tiles)
    cards = "".join(f'<div class="card">{p}</div>' for p in parts)
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>US Retail Sales Dashboard (Census MRTS)</title>
<style>
body {{ margin: 0; background: #f4f3ef; color: {INK};
       font-family: "Segoe UI", Helvetica, Arial, sans-serif; }}
header {{ padding: 24px 32px 8px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
header p {{ color: {INK_2}; margin: 0; font-size: 13px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
         gap: 12px; padding: 16px 32px; }}
.tile {{ background: {SURFACE}; border-radius: 8px; padding: 14px 16px; }}
.tile .v {{ font-size: 26px; font-weight: 600; }}
.tile .l {{ font-size: 12px; color: {INK_2}; margin-top: 4px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(560px, 1fr));
        gap: 16px; padding: 0 32px 32px; }}
.card {{ background: {SURFACE}; border-radius: 8px; padding: 8px; }}
footer {{ color: {INK_2}; font-size: 12px; padding: 0 32px 24px; }}
</style></head><body>
<header><h1>US Retail Sales: SQL Analytics and a Backtested Forecast</h1>
<p>U.S. Census Bureau, Monthly Retail Trade Survey, January 1992 to the latest month.
Nominal $, not adjusted for inflation.</p></header>
<section class="tiles">{tiles_html}</section>
<section class="grid">{cards}</section>
<footer>Source: U.S. Census Bureau, Monthly Retail Trade Survey and Quarterly E-commerce Report
(public domain). Generated by <code>python -m retail.dashboard</code>.</footer>
</body></html>
"""
    path.write_text(html, encoding="utf-8")


def overview_png(sales, ecom, sql, path):
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.5))
    nsa, sa = monthly_series(sales), monthly_series(sales, adjustment="SA")
    ax = axes[0, 0]
    ax.plot(nsa.index, nsa / 1000, color=INK_2, lw=0.8, label="Not seasonally adjusted")
    ax.plot(sa.index, sa / 1000, color=BLUE, lw=1.8, label="Seasonally adjusted")
    ax.set_title("Monthly retail and food services sales ($bn)")

    ax = axes[0, 1]
    q = pd.PeriodIndex(ecom["quarter"].str.replace("-", ""), freq="Q").to_timestamp()
    share = sql["ecommerce_share"].dropna(subset=["nonstore_share_pct"])
    ax.plot(q, ecom["ecommerce_share_pct"], color=BLUE, lw=1.8, label="E-commerce (quarterly)")
    ax.plot(pd.to_datetime(share["year"].astype(str) + "-07-01"), share["nonstore_share_pct"],
            color=ORANGE, lw=1.8, marker="o", ms=3, label="Nonstore retailers (annual)")
    ax.set_title("Share of retail sales (%)")

    ax = axes[1, 0]
    idx = covid_index(sales)
    for code, name, color in COVID_SERIES:
        ax.plot(idx.index, idx[code], color=color, lw=1.6, label=name)
    ax.axhline(100, color=INK_2, lw=0.8, ls=":")
    ax.set_title("COVID shock and recovery (SA, Feb 2020 = 100)")

    ax = axes[1, 1]
    mix = sql["category_mix_shift"].sort_values("change_1992_2025_pp")
    colors = [BLUE if v > 0 else ORANGE for v in mix["change_1992_2025_pp"]]
    ax.barh(mix["category"], mix["change_1992_2025_pp"], color=colors, height=0.7)
    ax.axvline(0, color=INK_2, lw=0.8)
    ax.tick_params(axis="y", labelsize=8)
    ax.set_title("Change in share of total sales, 1992 to 2025 (pp)")

    for ax in axes.flat:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="x" if ax is axes[1, 1] else "y", color=GRID, lw=0.8)
        if ax is not axes[1, 1]:
            ax.legend(fontsize=8, frameon=False, loc="upper left")
    fig.suptitle("US retail sales, Census Monthly Retail Trade Survey, 1992-2026", fontsize=13)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    FIGURES.mkdir(parents=True, exist_ok=True)
    sales, ecom = load_sales(), load_ecommerce()
    sql = run(save=False)
    forecast = pd.read_csv(REPORTS / "forecast_12m.csv", index_col="month")
    backtest = pd.read_csv(REPORTS / "backtest_summary.csv")
    summary = json.loads((REPORTS / "forecast_summary.json").read_text())
    figs = build_figures(sales, ecom, sql, forecast, backtest)
    write_html(figs, kpis(sql, ecom, summary), REPORTS / "dashboard.html")
    overview_png(sales, ecom, sql, FIGURES / "overview.png")
    print(f"wrote {REPORTS / 'dashboard.html'} and {FIGURES / 'overview.png'} "
          f"(series {TOTAL}, data through {sales['month'].max():%Y-%m})")


if __name__ == "__main__":
    main()
