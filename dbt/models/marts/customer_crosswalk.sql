-- Maps every raw customer_key to a canonical customer_key, collapsing the
-- near-duplicate customer records injected in data generation (same company
-- re-entered under a different CUST-ID, with a corporate-suffix name
-- variant like "Corp" / "Corporation" / "Inc" — see
-- data/generate_contoso_messy.py).
--
-- Matching strategy: normalize the name (trim, lowercase, strip a trailing
-- corporate suffix) and group by (normalized_name, segment). Within a group,
-- the record with the earliest signup_date is canonical — by construction in
-- this dataset, duplicates are always later re-registrations, never the
-- original (see gen_dim_customer). This is a heuristic, not a guarantee: two
-- distinct real companies with the same normalized name and segment would
-- incorrectly merge. Documented here rather than silently ignored.

with normalized as (
    select
        customer_key,
        segment,
        signup_date,
        lower(trim(regexp_replace(customer_name, '\s+(corp|corporation|inc|llc|ltd)\.?\s*$', '', 'i'))) as name_key
    from {{ ref('stg_dim_customer') }}
),

ranked as (
    select
        customer_key,
        name_key,
        segment,
        row_number() over (
            partition by name_key, segment
            order by signup_date asc, customer_key asc
        ) as rn
    from normalized
),

canonical as (
    select name_key, segment, customer_key as canonical_customer_key
    from ranked
    where rn = 1
)

select
    n.customer_key,
    c.canonical_customer_key
from normalized n
join canonical c using (name_key, segment)
