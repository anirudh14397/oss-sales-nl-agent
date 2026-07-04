-- Staging model: light cleanup only, no business logic here.
-- The mid-year hierarchy change (APAC -> APAC-North/APAC-South) is preserved
-- as-is here; marts/dim_region.sql decides how sales get mapped to a region
-- version as of their transaction date.

select
    region_key,
    region_name,
    cast(valid_from as date) as valid_from,
    cast(valid_to as date) as valid_to
from {{ ref('raw_dim_region') }}
