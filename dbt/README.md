# dbt semantic layer

Star schema in `models/staging/` (light cleanup only) and `models/marts/`
(business logic — customer dedup, region hierarchy resolution, target
overlap fix; see [../docs/failure_cases.md](../docs/failure_cases.md) for the
reasoning behind each). Metrics are defined in
`models/marts/_semantic_models.yml` using dbt's `semantic_models` and
`metrics` config, queried via dbt MetricFlow (`mf query`).

## Commands

```bash
cd dbt
DBT_PROFILES_DIR=. dbt seed --full-refresh   # load data/seed/raw_*.csv
DBT_PROFILES_DIR=. dbt run                   # build staging + marts
DBT_PROFILES_DIR=. dbt test                  # schema tests: unique/not_null/relationships/accepted_values
DBT_PROFILES_DIR=. dbt parse                 # validate semantic model / metric config
DBT_PROFILES_DIR=. mf list metrics           # list certified metrics + dimensions
DBT_PROFILES_DIR=. DBT_TARGET=agent mf query --metrics sales_net_revenue
```

`profiles.yml` defines two targets: `dev` (read-write, for `dbt run`/`dbt
seed`) and `agent` (read-only — the one `agent/metrics_client.py` uses at
query time).

Seed CSVs are named `raw_*` (`raw_dim_customer.csv`, etc.) so the seed node
never collides with the mart model of the (near-)same name — see
`docs/failure_cases.md` §8. Schema documentation and tests live in
`models/staging/schema.yml` and `models/marts/schema.yml`.

## Certified metrics

- `sales_gross_revenue` — total gross revenue before returns
- `sales_net_revenue` — gross revenue minus returns
- `sales_returns_rate` — returns as a fraction of gross revenue
- `sales_target_attainment` — net revenue as a fraction of target, by region + quarter

`sales_returns_amount` and `sales_target_revenue` also exist in the semantic
layer as base metrics backing the two ratio metrics above — they're not
part of the certified set the agent is allowed to call directly (see
`agent/metrics_client.py::CERTIFIED_METRICS`).
