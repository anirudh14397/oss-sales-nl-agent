-- Staging model: light cleanup only, no business logic here.
-- Dedup matching (near-duplicate customers) belongs in marts/, not here.

select
    customer_key,
    customer_id,
    trim(customer_name) as customer_name,
    segment,
    region_name_v1,
    cast(signup_date as date) as signup_date
from {{ ref('dim_customer') }}
