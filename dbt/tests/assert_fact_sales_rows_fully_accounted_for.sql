-- The quarantine pattern's core promise: every raw sales row ends up in
-- exactly one place — fct_sales (valid) or fct_sales_quarantine (invalid).
-- No row silently vanishes via an inner join, and none get double-counted.
-- A dbt singular test passes when it returns zero rows; this returns a row
-- (failing) only if the counts don't reconcile.

with raw_count as (
    select count(*) as n from {{ ref('raw_fact_sales') }}
),

valid_count as (
    select count(*) as n from {{ ref('fct_sales') }}
),

quarantine_count as (
    select count(*) as n from {{ ref('fct_sales_quarantine') }}
)

select
    raw_count.n as raw_rows,
    valid_count.n as valid_rows,
    quarantine_count.n as quarantine_rows
from raw_count, valid_count, quarantine_count
where raw_count.n != valid_count.n + quarantine_count.n
