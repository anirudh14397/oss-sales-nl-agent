# dbt semantic layer

Three-layer project structure:
- `models/staging/` — light cleanup only (trim, cast), no business logic
- `models/intermediate/` — multi-step transformations not meant for direct
  consumption (currently just `int_customer_crosswalk`, the dedup mapping)
- `models/marts/` — consumption-ready star schema (customer dedup, region
  hierarchy resolution, target overlap fix baked in; see
  [../docs/failure_cases.md](../docs/failure_cases.md) for the reasoning
  behind each), plus the semantic layer (`_semantic_models.yml`) and
  `exposures.yml` declaring the agent/Streamlit app as a consumer.

Metrics are defined using dbt's `semantic_models` and `metrics` config,
queried via dbt MetricFlow (`mf query`).

## Commands

```bash
cd dbt
DBT_PROFILES_DIR=. dbt deps                  # install dbt_utils (packages.yml)
DBT_PROFILES_DIR=. dbt seed --full-refresh   # load data/seed/raw_*.csv
DBT_PROFILES_DIR=. dbt run                   # build staging + intermediate + marts
DBT_PROFILES_DIR=. dbt test                  # schema tests: unique/not_null/relationships/accepted_values/compound-grain
DBT_PROFILES_DIR=. dbt docs generate         # build lineage docs (includes exposures.yml)
DBT_PROFILES_DIR=. dbt parse                 # validate semantic model / metric config
DBT_PROFILES_DIR=. mf list metrics           # list certified metrics + dimensions
DBT_PROFILES_DIR=. DBT_TARGET=agent mf query --metrics sales_net_revenue
```

`profiles.yml` defines two targets: `dev` (read-write, for `dbt run`/`dbt
seed`) and `agent` (read-only — the one `agent/metrics_client.py` uses at
query time).

Seed CSVs are named `raw_*` (`raw_dim_customer.csv`, etc.) so the seed node
never collides with the mart model of the (near-)same name — see
`docs/failure_cases.md` §8. Schema documentation and tests live alongside
each layer (`models/staging/schema.yml`, `models/intermediate/schema.yml`,
`models/marts/schema.yml`). Tests include both surrogate-key checks
(`unique`/`not_null`) and business-grain checks
(`dbt_utils.unique_combination_of_columns` on `fct_target` and the
`dim_region` SCD2) — the latter catch a regression a surrogate-key test
alone would miss, e.g. the region-hierarchy double-counting bug fixed in
`fct_target.sql`.

## Certified metrics

- `sales_gross_revenue` — total gross revenue before returns
- `sales_net_revenue` — gross revenue minus returns
- `sales_returns_rate` — returns as a fraction of gross revenue
- `sales_target_attainment` — net revenue as a fraction of target, by region + quarter

`sales_returns_amount` and `sales_target_revenue` also exist in the semantic
layer as base metrics backing the two ratio metrics above — they're not
part of the certified set the agent is allowed to call directly (see
`agent/metrics_client.py::CERTIFIED_METRICS`).
