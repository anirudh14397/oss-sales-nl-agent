-- Classifies every raw sales row against business-logic rules and computes
-- is_valid + a human-readable validation_errors reason string. This is the
-- quarantine pattern: bad rows aren't silently dropped (an inner join to a
-- dimension would do that invisibly) and they don't fail the whole build
-- (a dbt test would do that) — they're routed to marts/fct_sales_quarantine
-- for review, while marts/fct_sales only ever sees is_valid rows. See
-- docs/failure_cases.md §9.
--
-- Grain: one row per raw sales_key, including anomalous and duplicate rows
-- (unlike fct_sales, which only has one row per valid sales transaction).

with sales as (
    select * from {{ ref('stg_fact_sales') }}
),

crosswalk as (
    select * from {{ ref('int_customer_crosswalk') }}
),

products as (
    select * from {{ ref('stg_dim_product') }}
),

dates as (
    select * from {{ ref('stg_dim_date') }}
),

checked as (
    select
        s.sales_key,
        s.customer_key,
        s.product_key,
        s.date_key,
        s.load_date,
        s.quantity,
        s.gross_revenue,
        s.returns_amount,
        s.net_revenue,
        x.canonical_customer_key,

        (s.sales_key is not null) as check_sales_key_present,
        (s.customer_key is not null) as check_customer_key_present,
        (s.customer_key is not null and x.customer_key is not null) as check_customer_exists,
        (s.product_key is not null) as check_product_key_present,
        (s.product_key is not null and p.product_key is not null) as check_product_exists,
        (s.date_key is not null and d.date_key is not null) as check_date_valid,
        (s.quantity is not null and s.quantity > 0) as check_quantity_positive,
        (s.gross_revenue is not null and s.gross_revenue >= 0) as check_gross_revenue_non_negative,
        (s.net_revenue is not null and s.net_revenue >= 0) as check_net_revenue_non_negative,
        (s.net_revenue is not null and s.gross_revenue is not null and s.net_revenue <= s.gross_revenue) as check_net_not_exceeding_gross,
        (row_number() over (partition by s.sales_key order by s.load_date) = 1) as check_sales_key_unique

    from sales s
    left join crosswalk x on s.customer_key = x.customer_key
    left join products p on s.product_key = p.product_key
    left join dates d on s.date_key = d.date_key
),

flagged as (
    select
        *,
        (
            check_sales_key_present
            and check_customer_key_present and check_customer_exists
            and check_product_key_present and check_product_exists
            and check_date_valid
            and check_quantity_positive
            and check_gross_revenue_non_negative
            and check_net_revenue_non_negative
            and check_net_not_exceeding_gross
            and check_sales_key_unique
        ) as is_valid
    from checked
)

select
    sales_key,
    customer_key,
    canonical_customer_key,
    product_key,
    date_key,
    load_date,
    quantity,
    gross_revenue,
    returns_amount,
    net_revenue,
    is_valid,
    case when not is_valid then
        concat_ws('; ',
            case when not check_sales_key_present then 'missing sales_key' end,
            case when not check_sales_key_unique then 'duplicate sales_key' end,
            case when not check_customer_key_present then 'missing customer_key' end,
            case when check_customer_key_present and not check_customer_exists then 'customer_key not found in dim_customer' end,
            case when not check_product_key_present then 'missing product_key' end,
            case when check_product_key_present and not check_product_exists then 'product_key not found in dim_product' end,
            case when not check_date_valid then 'date_key not found in dim_date' end,
            case when not check_quantity_positive then 'quantity must be > 0' end,
            case when not check_gross_revenue_non_negative then 'gross_revenue must be >= 0' end,
            case when not check_net_revenue_non_negative then 'net_revenue must be >= 0' end,
            case when not check_net_not_exceeding_gross then 'net_revenue exceeds gross_revenue' end
        )
    end as validation_errors
from flagged
