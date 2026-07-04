-- Staging model: light cleanup only, no business logic here.

select
    cast(date_key as date) as date_key,
    year,
    quarter,
    month,
    month_name,
    day,
    fiscal_year
from {{ ref('dim_date') }}
