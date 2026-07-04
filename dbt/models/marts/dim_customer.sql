-- One row per canonical customer, after collapsing near-duplicates via
-- customer_crosswalk. merged_record_count > 1 flags customers where we
-- merged more than one raw record — surfaced for transparency, not hidden.

with merge_counts as (
    select canonical_customer_key, count(*) as merged_record_count
    from {{ ref('customer_crosswalk') }}
    group by canonical_customer_key
)

select
    s.customer_key,
    s.customer_id,
    s.customer_name,
    s.segment,
    s.region_name_v1,
    s.signup_date,
    m.merged_record_count
from {{ ref('stg_dim_customer') }} s
join merge_counts m on s.customer_key = m.canonical_customer_key
