-- Staging model: light cleanup only, no business logic here.

select
    target_key,
    region_key,
    year,
    quarter,
    target_revenue
from {{ ref('fact_target') }}
