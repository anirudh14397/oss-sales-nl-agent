-- Staging model: light cleanup only, no business logic here.

select
    product_key,
    product_id,
    trim(product_name) as product_name,
    category,
    unit_cost
from {{ ref('raw_dim_product') }}
