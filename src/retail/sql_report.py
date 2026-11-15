"""Run every query in sql/analysis.sql with DuckDB and save the results.

Run from the repo root:  python -m retail.sql_report
"""
import re

import duckdb

from .data import REPORTS, ROOT, load_ecommerce, load_sales

SQL_PATH = ROOT / "sql" / "analysis.sql"


def split_queries(text):
    """Return {name: sql} for blocks that start with '-- name: <name>'."""
    parts = re.split(r"^-- name:\s*(\w+)\s*$", text, flags=re.M)
    return {name: body.strip() for name, body in zip(parts[1::2], parts[2::2])}


def connect():
    """In-memory DuckDB with the `sales` and `ecommerce` tables."""
    con = duckdb.connect()
    sales = load_sales()
    sales["month"] = sales["month"].dt.date
    con.register("sales_df", sales)
    con.execute("CREATE TABLE sales AS SELECT * FROM sales_df")
    con.register("ecommerce_df", load_ecommerce())
    con.execute("CREATE TABLE ecommerce AS SELECT * FROM ecommerce_df")
    return con


def run(con=None, save=True):
    con = con or connect()
    out = REPORTS / "sql"
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, query in split_queries(SQL_PATH.read_text(encoding="utf-8")).items():
        df = con.execute(query).df()
        if save:
            df.to_csv(out / f"{name}.csv", index=False)
        results[name] = df
    return results


def main():
    for name, df in run().items():
        print(f"\n== {name} ({len(df)} rows)\n{df.head(20).to_string(index=False)}")


if __name__ == "__main__":
    main()
