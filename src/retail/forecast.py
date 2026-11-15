"""Backtest monthly US retail sales forecasts, then forecast the next 12 months.

Series: retail and food services total (NAICS 44X72), not seasonally adjusted,
$ millions, January 1992 to the latest month in data/mrts_monthly.csv.

Backtest design (rolling origin, 12 months ahead, training always starts in 1992):
  "normal" origins, used to rank the models:
      Dec 2016, Dec 2017, Dec 2018  -> forecast 2017, 2018, 2019
      Dec 2022, Dec 2023, Dec 2024  -> forecast 2023, 2024, 2025
  stress-test origins, reported separately and not used for ranking:
      Dec 2019 -> forecast 2020 (the COVID shock: no model could see it coming)
      Dec 2021 -> forecast 2022 (reopening and 8-9% inflation)
Models that are trained through 2020 still see the COVID months; "SARIMA + COVID
dummies" adds one pulse dummy per month from March 2020 to June 2021 so those
months do not distort the seasonal and trend estimates.

The model-based winner (lowest mean MAPE on the normal origins) is refit on the
full history for the final 12-month forecast with an 80% prediction interval.

Run from the repo root:  python -m retail.forecast
"""
import json
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from statsmodels.tsa.holtwinters import ExponentialSmoothing  # noqa: E402
from statsmodels.tsa.statespace.sarimax import SARIMAX  # noqa: E402

from .data import FIGURES, REPORTS, load_sales, monthly_series  # noqa: E402

HORIZON = 12
NORMAL_ORIGINS = ["2016-12", "2017-12", "2018-12", "2022-12", "2023-12", "2024-12"]
STRESS_ORIGINS = ["2019-12", "2021-12"]
COVID_MONTHS = pd.date_range("2020-03-01", "2021-06-01", freq="MS")
INTERVAL = 0.80


def _future(train):
    return pd.date_range(train.index[-1] + pd.offsets.MonthBegin(), periods=HORIZON, freq="MS")


# Each model takes a training series and returns a 12-month forecast series.
def naive(train):
    """Last month, repeated."""
    return pd.Series(train.iloc[-1], index=_future(train))


def seasonal_naive(train):
    """Same month last year."""
    return pd.Series(train.iloc[-12:].to_numpy(), index=_future(train))


def seasonal_naive_drift(train):
    """Same month last year, scaled by the latest year-over-year growth."""
    growth = train.iloc[-12:].sum() / train.iloc[-24:-12].sum()
    return seasonal_naive(train) * growth


def covid_dummies(index):
    """One pulse dummy per COVID month that appears in the index (empty if none)."""
    cols = {m.strftime("covid_%Y_%m"): (index == m).astype(float)
            for m in COVID_MONTHS if m in index}
    return pd.DataFrame(cols, index=index)


def _sarima_fit(train, dummies):
    exog = covid_dummies(train.index) if dummies else None
    if exog is not None and exog.empty:
        exog = None
    model = SARIMAX(np.log(train), exog=exog, order=(0, 1, 1), seasonal_order=(0, 1, 1, 12))
    return model.fit(disp=False), exog


def _sarima_forecast(train, dummies, alpha=None):
    fit, exog = _sarima_fit(train, dummies)
    future_exog = None
    if exog is not None:
        future_exog = pd.DataFrame(0.0, index=_future(train), columns=exog.columns)
    fc = fit.get_forecast(HORIZON, exog=future_exog)
    point = np.exp(fc.predicted_mean)
    if alpha is None:
        return point
    ci = np.exp(fc.conf_int(alpha=alpha))
    return point, ci.iloc[:, 0], ci.iloc[:, 1]


def sarima(train):
    """Airline model, SARIMA(0,1,1)(0,1,1)12 on log sales."""
    return _sarima_forecast(train, dummies=False)


def sarima_covid(train):
    """Airline model on log sales with pulse dummies for Mar 2020 - Jun 2021."""
    return _sarima_forecast(train, dummies=True)


def _hw_fit(train, damped):
    return ExponentialSmoothing(train, trend="add", damped_trend=damped, seasonal="mul",
                                seasonal_periods=12).fit()


def holt_winters(train):
    return _hw_fit(train, damped=False).forecast(HORIZON)


def ets_damped(train):
    return _hw_fit(train, damped=True).forecast(HORIZON)


MODELS = {
    "Naive (last month)": naive,
    "Seasonal naive (same month last year)": seasonal_naive,
    "Seasonal naive + last YoY growth": seasonal_naive_drift,
    "SARIMA(0,1,1)(0,1,1)12, log": sarima,
    "SARIMA + COVID dummies": sarima_covid,
    "Holt-Winters (add. trend, mult. season)": holt_winters,
    "ETS damped trend, mult. season": ets_damped,
}
MODEL_BASED = [name for name in MODELS if name.startswith(("SARIMA", "Holt", "ETS"))]


def errors(actual, forecast):
    actual, forecast = np.asarray(actual, float), np.asarray(forecast, float)
    return {
        "mae_busd": float(np.mean(np.abs(actual - forecast)) / 1000),
        "mape_pct": float(100 * np.mean(np.abs(actual - forecast) / actual)),
        "annual_total_error_pct": float(100 * (forecast.sum() / actual.sum() - 1)),
    }


def backtest(series, origins):
    rows, paths = [], []
    for origin in origins:
        origin_ts = pd.Timestamp(origin)
        train = series[:origin_ts]
        actual = series[origin_ts + pd.offsets.MonthBegin():][:HORIZON]
        if len(actual) < HORIZON:
            raise ValueError(f"origin {origin} has only {len(actual)} months to score")
        for name, fn in MODELS.items():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fc = fn(train)
            rows.append({"origin": origin, "model": name, **errors(actual, fc)})
            paths.append(pd.DataFrame({"origin": origin, "model": name, "month": fc.index,
                                       "forecast_musd": fc.to_numpy(),
                                       "actual_musd": actual.to_numpy()}))
    return pd.DataFrame(rows), pd.concat(paths, ignore_index=True)


def summarise(table):
    return (table.groupby("model")[["mae_busd", "mape_pct", "annual_total_error_pct"]]
            .mean().sort_values("mape_pct"))


def final_forecast(series, model):
    """Refit `model` on all data; return point forecast and an 80% interval ($ millions)."""
    alpha = 1 - INTERVAL
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if model.startswith("SARIMA"):
            point, lower, upper = _sarima_forecast(series, dummies="COVID" in model, alpha=alpha)
        else:  # Holt-Winters / ETS: simulate future paths with multiplicative errors
            fit = _hw_fit(series, damped=model.startswith("ETS"))
            point = fit.forecast(HORIZON)
            sims = fit.simulate(HORIZON, repetitions=2000, error="mul", random_state=42)
            lower = sims.quantile(alpha / 2, axis=1)
            upper = sims.quantile(1 - alpha / 2, axis=1)
    return pd.DataFrame({"forecast_musd": point, "lower_80_musd": lower,
                         "upper_80_musd": upper}).round(0)


def main():
    FIGURES.mkdir(parents=True, exist_ok=True)
    series = monthly_series(load_sales())
    normal, normal_paths = backtest(series, NORMAL_ORIGINS)
    stress, stress_paths = backtest(series, STRESS_ORIGINS)
    summary = summarise(normal)
    stress_summary = stress.pivot(index="model", columns="origin", values="mape_pct")

    pd.concat([normal.assign(kind="normal"), stress.assign(kind="stress")]).round(2) \
        .to_csv(REPORTS / "backtest_by_origin.csv", index=False)
    summary.round(2).to_csv(REPORTS / "backtest_summary.csv")
    stress_summary.round(2).to_csv(REPORTS / "backtest_stress_mape.csv")
    pd.concat([normal_paths, stress_paths]).round(0) \
        .to_csv(REPORTS / "backtest_paths.csv", index=False)
    print(normal.round(2).to_string(index=False))
    print("\nMean over the six normal origins:\n", summary.round(2).to_string())
    print("\nStress-test MAPE (%):\n", stress_summary.round(2).to_string())

    best = summary.loc[MODEL_BASED].index[0]
    fc = final_forecast(series, best)
    out = fc.copy()
    out.index = out.index.strftime("%Y-%m")
    out.index.name = "month"
    out.to_csv(REPORTS / "forecast_12m.csv")

    last_12 = series.iloc[-HORIZON:].sum()
    seasonal_mape = summary.loc["Seasonal naive (same month last year)", "mape_pct"]
    result = {
        "series": "Retail and food services total (44X72), not seasonally adjusted, $ millions",
        "data_through": series.index[-1].strftime("%Y-%m"),
        "best_model": best,
        "mape_pct_by_model": summary["mape_pct"].round(2).to_dict(),
        "best_beats_seasonal_naive_mape_by_pct": round(
            float(100 * (1 - summary.loc[best, "mape_pct"] / seasonal_mape)), 1),
        "forecast_period": f"{out.index[0]} to {out.index[-1]}",
        "forecast_total_busd": round(float(fc["forecast_musd"].sum()) / 1000, 1),
        "forecast_growth_vs_last_12m_pct": round(
            float(100 * (fc["forecast_musd"].sum() / last_12 - 1)), 1),
        "last_12m_total_busd": round(float(last_12) / 1000, 1),
        "interval": "80% prediction interval for each month (not for the 12-month total)",
    }
    (REPORTS / "forecast_summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    plot(series, normal_paths, stress_paths, fc, best)


def plot(series, normal_paths, stress_paths, fc, best):
    fig, ax = plt.subplots(figsize=(10, 4.8))
    recent = series["2016":]
    ax.plot(recent.index, recent / 1000, color="#0b0b0b", lw=1.6, label="Actual")
    paths = pd.concat([normal_paths, stress_paths])
    for i, origin in enumerate(sorted(paths["origin"].unique())):
        p = paths[(paths["origin"] == origin) & (paths["model"] == best)]
        ax.plot(p["month"], p["forecast_musd"] / 1000, color="#eb6834", lw=1.4, ls="--",
                label=f"{best}: backtests (12 months ahead)" if i == 0 else None)
    ax.plot(fc.index, fc["forecast_musd"] / 1000, color="#2a78d6", lw=2,
            label="Forecast, next 12 months")
    ax.fill_between(fc.index, fc["lower_80_musd"] / 1000, fc["upper_80_musd"] / 1000,
                    color="#2a78d6", alpha=0.2, label="80% prediction interval")
    ax.axvspan(pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"), color="#c3c2b7",
               alpha=0.25, lw=0, label="COVID months (Mar 2020 - Jun 2021)")
    ax.set_ylabel("Monthly sales, $ billions (not seasonally adjusted)")
    ax.set_title("US retail and food services sales: backtests and 12-month forecast")
    ax.grid(axis="y", color="#e6e5e0", lw=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, loc="upper left", frameon=False)
    fig.tight_layout()
    fig.savefig(FIGURES / "forecast.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
