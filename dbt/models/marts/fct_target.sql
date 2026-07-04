-- Grain: one row per (region_key, quarter) target.
--
-- Messiness handled here: the source data emits a target row for every
-- region_key/quarter combination regardless of whether that region version
-- was actually in effect during the quarter (e.g. a post-split "APAC-North"
-- row exists for Q1, before the split happened). Left as-is, that
-- double-counts target_revenue for Q1/Q2 across the pre/post-split region
-- versions. Keep only rows where the region version's validity window
-- actually covers the quarter.

with target as (
    select * from {{ ref('stg_fact_target') }}
),

region as (
    select * from {{ ref('dim_region') }}
),

target_with_region as (
    select
        t.target_key,
        t.region_key,
        r.region_name,
        t.year,
        t.quarter,
        t.target_revenue,
        r.valid_from,
        r.valid_to,
        make_date(t.year, (t.quarter - 1) * 3 + 1, 1) as quarter_start_date
    from target t
    join region r on t.region_key = r.region_key
)

select
    target_key,
    region_key,
    region_name,
    year,
    quarter,
    quarter_start_date,
    target_revenue
from target_with_region
where quarter_start_date >= valid_from
  and (valid_to is null or quarter_start_date <= valid_to)
