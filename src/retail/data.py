"""Paths and loaders for the tidy Census MRTS extract."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SALES_PATH = DATA_DIR / "mrts_monthly.csv"
ECOMMERCE_PATH = DATA_DIR / "ecommerce_quarterly.csv"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"

TOTAL = "44X72"  # retail and food services, total


def load_sales(path=SALES_PATH):
    """Long table: month, naics_code, category, adjustment (NSA/SA), sales_musd, status."""
    df = pd.read_csv(path, dtype={"naics_code": str}, parse_dates=["month"])
    return df


def load_ecommerce(path=ECOMMERCE_PATH):
    return pd.read_csv(path)


def monthly_series(sales, code=TOTAL, adjustment="NSA"):
    """One series as a month-start indexed Series ($ millions) with no gaps."""
    s = (sales[(sales["naics_code"] == code) & (sales["adjustment"] == adjustment)]
         .set_index("month")["sales_musd"].sort_index())
    s = s.asfreq("MS")
    s.name = code
    return s
