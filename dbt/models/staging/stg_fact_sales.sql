-- Staging model: light cleanup only, no business logic here.
-- Business logic (net revenue definition, etc.) belongs in marts/ + the semantic layer.

select
    sales_key,
    customer_key,
    product_key,
    cast(date_key as date) as date_key,
    cast(load_date as date) as load_date,
    quantity,
    gross_revenue,
    returns_amount,
    net_revenue
from {{ ref('raw_fact_sales') }}
