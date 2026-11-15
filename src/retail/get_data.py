"""Download the Census Monthly Retail Trade Survey (MRTS) files and write tidy CSVs.

Sources (U.S. Census Bureau, public domain):
  - Monthly sales by kind of business, 1992-present, not adjusted and seasonally adjusted:
    https://www.census.gov/retail/mrts/www/mrtssales92-present.xlsx
  - Quarterly e-commerce sales and share of total retail (seasonally adjusted):
    https://www.census.gov/retail/mrts/www/data/excel/tsadjustedsales.xlsx

The Census time-series API (api.census.gov/data/timeseries/eits/mrts) now requires
an API key, so the script reads the official Excel releases instead. The raw files
are cached in data/raw/ (git-ignored); only the processed CSVs are committed.

Run from the repo root:  python -m retail.get_data
"""
import re
import urllib.request

import pandas as pd

from .data import DATA_DIR, ECOMMERCE_PATH, SALES_PATH

RAW_DIR = DATA_DIR / "raw"
SALES_URL = "https://www.census.gov/retail/mrts/www/mrtssales92-present.xlsx"
ECOM_URL = "https://www.census.gov/retail/mrts/www/data/excel/tsadjustedsales.xlsx"

# Series kept in the tidy extract: code -> short, stable name.
# The two aggregate rows have no NAICS code in the file; Census uses 44X72 and 44000.
CATEGORIES = {
    "44X72": "Retail and food services, total",
    "44000": "Retail, total",
    "441": "Motor vehicles and parts",
    "442": "Furniture and home furnishings",
    "443": "Electronics and appliances",
    "444": "Building materials and garden",
    "445": "Food and beverage stores",
    "446": "Health and personal care",
    "447": "Gasoline stations",
    "448": "Clothing and accessories",
    "451": "Sporting goods, hobby, music, books",
    "452": "General merchandise",
    "453": "Miscellaneous stores",
    "454": "Nonstore retailers",
    "4541": "Electronic shopping and mail order",
    "722": "Food services and drinking places",
}
AGGREGATE_LABELS = {
    "retail and food services sales, total": "44X72",
    "retail sales, total": "44000",
}
MONTH_RE = re.compile(r"^([A-Z][a-z]{2})\.?\s*(\d{4})\s*(\((?:p|r)\))?")


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (retail-analytics)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())
    return dest


def _code(naics, label):
    """Return the series code for a row, or None if the row is not one we keep."""
    naics = "" if pd.isna(naics) else str(naics).strip().removesuffix(".0")
    if naics in CATEGORIES:
        return naics
    if not naics and isinstance(label, str):
        return AGGREGATE_LABELS.get(" ".join(label.lower().split()))
    return None


def parse_year_sheet(sheet):
    """One yearly sheet -> long rows (month, code, adjustment, sales, status)."""
    header = sheet.iloc[4]
    months = {}
    for col, text in header.items():
        m = MONTH_RE.match(str(text))
        if m:
            month = pd.to_datetime(f"{m.group(1)} {m.group(2)}", format="%b %Y")
            months[col] = (month, "preliminary" if m.group(3) == "(p)" else "final")
    rows, adjustment = [], None
    for _, r in sheet.iloc[5:].iterrows():
        label = r.iloc[1]
        if isinstance(label, str) and label.strip().upper().startswith("NOT ADJUSTED"):
            adjustment = "NSA"
            continue
        if isinstance(label, str) and label.strip().upper().startswith("ADJUSTED"):
            adjustment = "SA"
            continue
        code = _code(r.iloc[0], label)
        if adjustment is None or code is None:
            continue
        for col, (month, status) in months.items():
            value = pd.to_numeric(r[col], errors="coerce")  # "(S)" suppressed -> NaN
            rows.append((month, code, adjustment, value, status))
    return rows


def tidy_sales(xlsx_path):
    sheets = pd.read_excel(xlsx_path, sheet_name=None, header=None)
    rows = [row for name, sheet in sheets.items() if name.isdigit()
            for row in parse_year_sheet(sheet)]
    df = pd.DataFrame(rows, columns=["month", "naics_code", "adjustment", "sales_musd", "status"])
    df = df.dropna(subset=["sales_musd"]).drop_duplicates(["month", "naics_code", "adjustment"])
    df.insert(2, "category", df["naics_code"].map(CATEGORIES))
    df["month"] = df["month"].dt.strftime("%Y-%m-%d")
    return df.sort_values(["adjustment", "naics_code", "month"]).reset_index(drop=True)


def tidy_ecommerce(xlsx_path):
    raw = pd.read_excel(xlsx_path, header=None)
    rows = []
    for _, r in raw.iterrows():
        m = re.match(r"(\d)(?:st|nd|rd|th) quarter (\d{4})", str(r.iloc[1]))
        if m and pd.notna(r.iloc[2]):
            rows.append({
                "quarter": f"{m.group(2)}-Q{m.group(1)}",
                "total_retail_musd": float(r.iloc[2]),
                "ecommerce_musd": float(r.iloc[3]),
                "ecommerce_share_pct": float(r.iloc[4]),
            })
    return pd.DataFrame(rows).sort_values("quarter").reset_index(drop=True)


def main():
    sales = tidy_sales(download(SALES_URL, RAW_DIR / "mrtssales92-present.xlsx"))
    ecom = tidy_ecommerce(download(ECOM_URL, RAW_DIR / "tsadjustedsales.xlsx"))
    sales.to_csv(SALES_PATH, index=False)
    ecom.to_csv(ECOMMERCE_PATH, index=False)
    print(f"{SALES_PATH.name}: {len(sales):,} rows, {sales['month'].min()} to "
          f"{sales['month'].max()}, {sales['naics_code'].nunique()} series")
    print(f"{ECOMMERCE_PATH.name}: {len(ecom)} quarters, "
          f"{ecom['quarter'].min()} to {ecom['quarter'].max()}")


if __name__ == "__main__":
    main()
