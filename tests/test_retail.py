import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retail.data import TOTAL, load_ecommerce, load_sales, monthly_series  # noqa: E402
from retail.forecast import (  # noqa: E402
    COVID_MONTHS,
    covid_dummies,
    errors,
    sarima,
    seasonal_naive,
    seasonal_naive_drift,
)
from retail.get_data import CATEGORIES, parse_year_sheet  # noqa: E402
from retail.sql_report import SQL_PATH, run, split_queries  # noqa: E402

COMPONENTS = ["441", "442", "443", "444", "445", "446", "447", "448", "451", "452", "453",
              "454", "722"]


@pytest.fixture(scope="module")
def sales():
    return load_sales()


@pytest.fixture(scope="module")
def series(sales):
    return monthly_series(sales)


@pytest.fixture(scope="module")
def sql():
    return run(save=False)


# --- extract -----------------------------------------------------------------

def test_extract_schema(sales):
    assert list(sales.columns) == ["month", "naics_code", "category", "adjustment",
                                   "sales_musd", "status"]
    assert set(sales["adjustment"]) == {"NSA", "SA"}
    assert set(sales["naics_code"]) == set(CATEGORIES)
    assert set(sales["status"]) <= {"final", "preliminary"}
    assert not sales.duplicated(["month", "naics_code", "adjustment"]).any()
    assert (sales["sales_musd"] > 0).all()
    assert (sales["month"].dt.day == 1).all()
    # every series covers every month from January 1992
    counts = sales.groupby(["naics_code", "adjustment"])["month"].agg(["min", "max", "size"])
    assert (counts["min"] == pd.Timestamp("1992-01-01")).all()
    assert counts["size"].nunique() == 1


def test_only_latest_month_is_preliminary(sales):
    prelim = sales.loc[sales["status"] == "preliminary", "month"].unique()
    assert len(prelim) <= 1
    if len(prelim):
        assert prelim[0] == sales["month"].max()


def test_categories_add_up_to_total(sales):
    nsa = sales[sales["adjustment"] == "NSA"].pivot(index="month", columns="naics_code",
                                                    values="sales_musd")
    parts = nsa[COMPONENTS].sum(axis=1)
    # published components are independently rounded/estimated: allow 0.5%
    assert np.allclose(parts, nsa[TOTAL], rtol=5e-3)
    assert (nsa["4541"] <= nsa["454"]).all()


def test_ecommerce_extract():
    ecom = load_ecommerce()
    assert ecom["quarter"].is_unique and ecom["quarter"].is_monotonic_increasing
    assert ecom["ecommerce_share_pct"].between(0, 100).all()
    implied = 100 * ecom["ecommerce_musd"] / ecom["total_retail_musd"]
    assert np.allclose(implied, ecom["ecommerce_share_pct"], atol=0.11)


def test_parse_year_sheet_handles_sections_and_suppressed_cells():
    rows = [
        ["title", None, None, None],
        [None] * 4, [None] * 4,
        ["NAICS  Code", "Kind of Business", None, None],
        [None, None, "Jan. 2026", "Feb. 2026(p)"],
        [None, "NOT ADJUSTED", None, None],
        [None, "Retail and food services sales, total", 100, 110],
        [441, "Motor vehicle and parts dealers", 20, "(S)"],
        ["4422", "Home furnishings stores", 5, 6],
        [None, "ADJUSTED(2)", None, None],
        [None, "Retail and food services sales, total", 105, 106],
    ]
    out = pd.DataFrame(parse_year_sheet(pd.DataFrame(rows)),
                       columns=["month", "code", "adj", "value", "status"])
    assert set(out["code"]) == {"44X72", "441"}  # 4422 is not a kept series
    assert out.loc[(out["code"] == "44X72") & (out["adj"] == "SA"), "value"].tolist() == [105, 106]
    assert out.loc[out["code"] == "441", "value"].isna().tolist() == [False, True]
    assert out.loc[out["month"] == pd.Timestamp("2026-02-01"), "status"].eq("preliminary").all()


# --- SQL -----------------------------------------------------------------------

def test_sql_file_parses_into_named_queries():
    names = split_queries(SQL_PATH.read_text(encoding="utf-8"))
    assert 7 <= len(names) <= 10
    assert {"yearly_growth", "category_mix_shift", "covid_shock_recovery"} <= set(names)


def test_sql_queries_run_and_return_rows(sql):
    assert all(len(df) > 0 for df in sql.values())


def test_sql_yearly_totals_reconcile_with_pandas(sql, series):
    yearly = sql["yearly_growth"].set_index("year")["sales_trillion_usd"]
    by_year = series.groupby(series.index.year)
    complete = by_year.size()[lambda n: n == 12].index
    expected = by_year.sum()[complete] / 1e6
    assert list(yearly.index) == list(complete)
    assert np.allclose(yearly.to_numpy(), expected.to_numpy(), atol=5e-4)


def test_sql_category_shares_sum_to_100(sql):
    mix = sql["category_mix_shift"]
    for col in ["share_1992_pct", "share_2025_pct"]:
        assert abs(mix[col].sum() - 100) < 1


def test_seasonal_adjustment_preserves_annual_totals(sql):
    assert sql["adjustment_check"]["sa_minus_nsa_pct"].abs().max() < 1.0


# --- forecast ------------------------------------------------------------------

def test_monthly_series_is_complete(series):
    assert series.index.freqstr == "MS"
    assert series.index.min() == pd.Timestamp("1992-01-01")
    assert series.notna().all()


def test_seasonal_naive_repeats_last_year(series):
    train = series[:"2023-12-01"]
    fc = seasonal_naive(train)
    assert fc.index[0] == pd.Timestamp("2024-01-01") and len(fc) == 12
    assert np.allclose(fc.to_numpy(), series["2023"].to_numpy())


def test_seasonal_naive_drift_scales_by_last_yoy(series):
    train = series[:"2023-12-01"]
    growth = series["2023"].sum() / series["2022"].sum()
    assert np.allclose(seasonal_naive_drift(train), series["2023"].to_numpy() * growth)


def test_covid_dummies_only_for_months_in_training():
    pre = pd.date_range("2015-01-01", "2019-12-01", freq="MS")
    assert covid_dummies(pre).empty
    full = pd.date_range("2015-01-01", "2024-12-01", freq="MS")
    d = covid_dummies(full)
    assert d.shape[1] == len(COVID_MONTHS) and (d.sum() == 1).all()


def test_sarima_forecast_is_positive_and_seasonal(series):
    fc = sarima(series[:"2024-12-01"])
    assert len(fc) == 12 and (fc > 0).all()
    assert fc.idxmax().month == 12  # December peak


def test_error_metrics():
    e = errors([100, 200], [110, 180])
    assert np.isclose(e["mae_busd"], 15 / 1000)
    assert np.isclose(e["mape_pct"], 10)
    assert np.isclose(e["annual_total_error_pct"], -100 / 30)
