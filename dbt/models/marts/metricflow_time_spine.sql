-- Required by dbt's semantic layer for time-based aggregation (quarter
-- grouping, period comparisons, etc.). One row per day covering the range of
-- transaction dates in the warehouse.
select cast(date_key as date) as date_day
from {{ ref('dim_date') }}
