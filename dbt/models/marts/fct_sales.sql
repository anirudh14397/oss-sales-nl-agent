-- Grain: one row per VALID sales transaction (sales_key). Anomalous and
-- duplicate rows are quarantined into fct_sales_quarantine instead of
-- landing here — see int_fact_sales_validated.sql and
-- docs/failure_cases.md §9.
--
-- Messiness handled here:
-- 1. Customer dedup: raw customer_key is resolved to its canonical customer
--    via int_customer_crosswalk, so duplicate customer records don't fragment
--    a customer's sales history.
-- 2. Region hierarchy change: the source data ties a *region* only to
--    fact_target, not to individual sales — a customer's home region
--    (region_name_v1, fixed at signup under the pre-split hierarchy) is
--    resolved to whichever region_key was actually in effect on the sale's
--    transaction date. For APAC customers after the 2024-07-01 split, the
--    sub-region (North/South) is assigned deterministically from the
--    customer's key, since the source data doesn't carry an explicit
--    sub-region — documented here as a modeling decision, not hidden.
-- 3. Load-date vs transaction-date: reported here by transaction date_key
--    (the period the sale actually happened in), not load_date (when it
--    landed in the warehouse). is_late_arriving flags rows where the two
--    differ, so "as of" reporting caveats can be surfaced downstream instead
--    of silently restating history.

with sales as (
    select * from {{ ref('int_fact_sales_validated') }}
    where is_valid
),

customers as (
    select * from {{ ref('dim_customer') }}
),

sales_with_region_name as (
    select
        s.*,
        case
            when s.date_key < date '2024-07-01' then c.region_name_v1
            when c.region_name_v1 = 'APAC' then
                case when hash(s.canonical_customer_key) % 2 = 0 then 'APAC-North' else 'APAC-South' end
            else c.region_name_v1
        end as effective_region_name
    from sales s
    join customers c on s.canonical_customer_key = c.customer_key
),

region as (
    select * from {{ ref('dim_region') }}
)

select
    s.sales_key,
    s.canonical_customer_key as customer_key,
    s.product_key,
    r.region_key,
    s.date_key,
    s.load_date,
    (s.load_date != s.date_key) as is_late_arriving,
    s.quantity,
    s.gross_revenue,
    s.returns_amount,
    s.net_revenue
from sales_with_region_name s
join region r
    on r.region_name = s.effective_region_name
    and s.date_key >= r.valid_from
    and (r.valid_to is null or s.date_key <= r.valid_to)
