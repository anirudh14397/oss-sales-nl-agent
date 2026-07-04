-- Passthrough of the region SCD2 (two hierarchy versions: pre/post the
-- 2024-07-01 APAC split). Carried as-is; fct_sales and fct_target resolve
-- which version applies to a given date/quarter.
select * from {{ ref('stg_dim_region') }}
