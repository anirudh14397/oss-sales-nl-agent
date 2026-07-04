-- Sales rows that failed one or more business-logic checks in
-- int_fact_sales_validated.sql — never included in fct_sales, never
-- silently dropped either. Grain: one row per invalid raw sales_key.
-- See docs/failure_cases.md §9 for why this exists and how it's used.

select
    sales_key,
    customer_key,
    product_key,
    date_key,
    load_date,
    quantity,
    gross_revenue,
    returns_amount,
    net_revenue,
    validation_errors
from {{ ref('int_fact_sales_validated') }}
where not is_valid
