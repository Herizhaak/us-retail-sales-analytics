-- US retail sales analysis (DuckDB SQL) on the Census Monthly Retail Trade Survey.
-- Run with:  python -m retail.sql_report
-- Each query starts with a line "-- name: <name>" and its result is written to
-- reports/sql/<name>.csv.
--
-- Tables (created by the runner from data/):
--   sales(month DATE, naics_code, category, adjustment 'NSA'|'SA', sales_musd, status)
--     sales_musd is in millions of dollars, nominal. 44X72 = retail and food services
--     total, 44000 = retail total, 4541 is a subset of 454. The 14 codes 441..454 and
--     722 add up to 44X72.
--   ecommerce(quarter, total_retail_musd, ecommerce_musd, ecommerce_share_pct)
--     quarterly, seasonally adjusted, from 1999-Q4.

-- name: yearly_growth
-- Annual retail and food services sales (complete years only) with YoY growth
-- and the growth over the previous five years.
WITH yearly AS (
    SELECT year(month) AS year, sum(sales_musd) AS sales, count(*) AS months
    FROM sales
    WHERE naics_code = '44X72' AND adjustment = 'NSA'
    GROUP BY year
    HAVING count(*) = 12
)
SELECT
    year,
    round(sales / 1e6, 3)                                                AS sales_trillion_usd,
    round(100 * (sales / lag(sales) OVER (ORDER BY year) - 1), 1)        AS yoy_growth_pct,
    round(100 * (power(sales / lag(sales, 5) OVER (ORDER BY year), 0.2) - 1), 1)
                                                                         AS cagr_5y_pct
FROM yearly
ORDER BY year;

-- name: category_mix_shift
-- Share of total retail and food services sales by category, 1992 to 2025.
WITH yearly AS (
    SELECT year(month) AS year, naics_code, category, sum(sales_musd) AS sales
    FROM sales
    WHERE adjustment = 'NSA' AND naics_code NOT IN ('44X72', '44000', '4541')
      AND year(month) IN (1992, 2000, 2010, 2019, 2025)
    GROUP BY ALL
), shares AS (
    SELECT *, 100 * sales / sum(sales) OVER (PARTITION BY year) AS share
    FROM yearly
)
SELECT
    naics_code,
    category,
    round(max(share) FILTER (WHERE year = 1992), 1)    AS share_1992_pct,
    round(max(share) FILTER (WHERE year = 2000), 1)    AS share_2000_pct,
    round(max(share) FILTER (WHERE year = 2010), 1)    AS share_2010_pct,
    round(max(share) FILTER (WHERE year = 2019), 1)    AS share_2019_pct,
    round(max(share) FILTER (WHERE year = 2025), 1)    AS share_2025_pct,
    round(max(share) FILTER (WHERE year = 2025)
          - max(share) FILTER (WHERE year = 1992), 1)  AS change_1992_2025_pp
FROM shares
GROUP BY naics_code, category
ORDER BY change_1992_2025_pp DESC, naics_code;

-- name: ecommerce_share
-- E-commerce share of retail (Census quarterly e-commerce estimate, annual mean)
-- next to the nonstore retailers' share of retail sales (NAICS 454 / retail total).
WITH ecom AS (
    SELECT CAST(left(quarter, 4) AS INTEGER) AS year,
           avg(ecommerce_share_pct)          AS ecommerce_share_pct,
           count(*)                          AS quarters
    FROM ecommerce
    GROUP BY year
), nonstore AS (
    SELECT year(month) AS year,
           100 * sum(sales_musd) FILTER (WHERE naics_code = '454')
               / sum(sales_musd) FILTER (WHERE naics_code = '44000') AS nonstore_share_pct
    FROM sales
    WHERE adjustment = 'NSA'
    GROUP BY year
    HAVING count(DISTINCT month) = 12
)
SELECT
    n.year,
    round(e.ecommerce_share_pct, 1)                                          AS ecommerce_share_pct,
    round(n.nonstore_share_pct, 1)                                           AS nonstore_share_pct,
    round(n.nonstore_share_pct - lag(n.nonstore_share_pct) OVER (ORDER BY n.year), 2)
                                                                             AS nonstore_change_pp
FROM nonstore n
LEFT JOIN ecom e ON e.year = n.year AND e.quarters = 4
ORDER BY n.year;

-- name: seasonality_by_category
-- Seasonal index: each month's sales as % of that year's average month,
-- averaged over 2015-2019 and 2022-2025 (2020-2021 excluded as abnormal).
WITH monthly AS (
    SELECT naics_code, category, month, sales_musd,
           avg(sales_musd) OVER (PARTITION BY naics_code, year(month)) AS year_avg
    FROM sales
    WHERE adjustment = 'NSA'
      AND (year(month) BETWEEN 2015 AND 2019 OR year(month) BETWEEN 2022 AND 2025)
)
SELECT
    naics_code,
    category,
    month(month)                                AS month,
    round(avg(100 * sales_musd / year_avg), 1)  AS seasonal_index
FROM monthly
GROUP BY naics_code, category, month(month)
ORDER BY naics_code, month;

-- name: december_concentration
-- How dependent is each category on December (and the Nov-Dec holiday season)?
WITH yearly AS (
    SELECT naics_code, category, year(month) AS year,
           sum(sales_musd) FILTER (WHERE month(month) = 12)        AS december,
           sum(sales_musd) FILTER (WHERE month(month) IN (11, 12)) AS nov_dec,
           sum(sales_musd)                                         AS total
    FROM sales
    WHERE adjustment = 'NSA' AND year(month) BETWEEN 2022 AND 2025
    GROUP BY ALL
)
SELECT
    rank() OVER (ORDER BY avg(december / total) DESC)  AS rank,
    naics_code,
    category,
    round(100 * avg(december / total), 1)              AS december_share_pct,
    round(100 * avg(nov_dec / total), 1)               AS nov_dec_share_pct,
    round(100 * avg(december / total) / (100 / 12.0), 2)
                                                       AS december_vs_average_month
FROM yearly
GROUP BY naics_code, category
ORDER BY rank, naics_code;

-- name: covid_shock_recovery
-- Seasonally adjusted sales against February 2020: the lowest month of
-- March-December 2020, the first month back at the pre-COVID level, and where each category stands now.
WITH base AS (
    SELECT naics_code, sales_musd AS feb_2020
    FROM sales
    WHERE adjustment = 'SA' AND month = DATE '2020-02-01'
), indexed AS (
    SELECT s.naics_code, s.category, s.month, 100 * s.sales_musd / b.feb_2020 AS idx
    FROM sales s JOIN base b USING (naics_code)
    WHERE s.adjustment = 'SA' AND s.month >= DATE '2020-02-01'
), trough AS (
    SELECT naics_code, arg_min(month, idx) AS trough_month, min(idx) AS trough_idx
    FROM indexed
    WHERE month BETWEEN DATE '2020-03-01' AND DATE '2020-12-01'
    GROUP BY naics_code
)
SELECT
    i.naics_code,
    i.category,
    strftime(t.trough_month, '%Y-%m')                                    AS low_month_2020,
    round(t.trough_idx - 100, 1)                                         AS low_vs_feb2020_pct,
    -- NULL when sales never fell below February 2020 (the category grew through COVID)
    CASE WHEN t.trough_idx < 100 THEN strftime(
        min(i.month) FILTER (WHERE i.month > t.trough_month AND i.idx >= 100), '%Y-%m')
    END                                                                  AS recovered_month,
    round(max(i.idx) FILTER (WHERE i.month = DATE '2021-12-01') - 100, 1) AS dec2021_vs_feb2020_pct,
    round(arg_max(i.idx, i.month) - 100, 1)                              AS latest_vs_feb2020_pct
FROM indexed i JOIN trough t USING (naics_code)
GROUP BY i.naics_code, i.category, t.trough_month, t.trough_idx
ORDER BY low_vs_feb2020_pct, naics_code;

-- name: category_growth_ranking
-- Nominal growth by category, 2019 (pre-COVID) to 2025, ranked.
WITH yearly AS (
    SELECT naics_code, category, year(month) AS year, sum(sales_musd) AS sales
    FROM sales
    WHERE adjustment = 'NSA' AND year(month) IN (2019, 2025)
    GROUP BY ALL
), pivoted AS (
    SELECT naics_code, category,
           max(sales) FILTER (WHERE year = 2019) AS sales_2019,
           max(sales) FILTER (WHERE year = 2025) AS sales_2025
    FROM yearly
    GROUP BY naics_code, category
)
SELECT
    dense_rank() OVER (ORDER BY sales_2025 / sales_2019 DESC)        AS rank,
    naics_code,
    category,
    round(sales_2019 / 1e3, 1)                                       AS sales_2019_busd,
    round(sales_2025 / 1e3, 1)                                       AS sales_2025_busd,
    round(100 * (sales_2025 / sales_2019 - 1), 1)                    AS growth_pct,
    round(100 * (power(sales_2025 / sales_2019, 1 / 6.0) - 1), 1)    AS cagr_pct
FROM pivoted
ORDER BY rank, naics_code;

-- name: rolling_12m_growth
-- Trailing 12-month sales and their YoY growth: a smooth, seasonality-free
-- view of the cycle (dips in 2001, 2008-09 and 2020 stand out).
WITH total AS (
    SELECT month, sales_musd
    FROM sales
    WHERE naics_code = '44X72' AND adjustment = 'NSA'
), rolling AS (
    SELECT month,
           sum(sales_musd) OVER (ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS ttm,
           count(*) OVER (ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)        AS n
    FROM total
)
SELECT
    strftime(month, '%Y-%m')                                        AS month,
    round(ttm / 1e3, 1)                                             AS ttm_sales_busd,
    round(100 * (ttm / lag(ttm, 12) OVER (ORDER BY month) - 1), 1)  AS ttm_yoy_pct
FROM rolling
WHERE n = 12
ORDER BY month;

-- name: adjustment_check
-- Data check: seasonally adjusted and unadjusted annual totals should be close,
-- because seasonal adjustment only moves sales between months.
SELECT
    year(month)                                                        AS year,
    round(sum(sales_musd) FILTER (WHERE adjustment = 'NSA') / 1e3, 1)  AS nsa_busd,
    round(sum(sales_musd) FILTER (WHERE adjustment = 'SA') / 1e3, 1)   AS sa_busd,
    round(100 * (sum(sales_musd) FILTER (WHERE adjustment = 'SA')
               / sum(sales_musd) FILTER (WHERE adjustment = 'NSA') - 1), 2) AS sa_minus_nsa_pct
FROM sales
WHERE naics_code = '44X72'
GROUP BY year
HAVING count(*) = 24
ORDER BY year;
