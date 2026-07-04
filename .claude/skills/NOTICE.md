# Third-party skills in this directory

Three skills adapted unmodified from
[AltimateAI/data-engineering-skills](https://github.com/AltimateAI/data-engineering-skills)
(MIT License, Copyright (c) 2026 AltimateAI):

- `creating-dbt-models/` — build-then-verify workflow for new dbt models
  (discover conventions, `dbt build` not just compile, spot-check calculations
  against sample data by hand)
- `testing-dbt-models/` — adds schema.yml tests (unique/not_null/relationships/
  accepted_values). We had zero dbt-level tests before this — the Python eval
  suite in `eval/` checks agent *behavior*, not underlying data integrity.
- `documenting-dbt-models/` — schema.yml descriptions focused on grain,
  business rules, and caveats, not just column names.

## Why these three, not the whole repo

The source repo also has `refactoring-dbt-models`, `developing-incremental-models`,
`debugging-dbt-errors`, `migrating-sql-to-dbt`, three Snowflake-specific skills
(we use DuckDB), and `altimate-code` (a skill that delegates work to a separate
third-party CLI tool requiring its own npm install and LLM/warehouse auth —
deliberately excluded, not something to pull in silently).

Picked these three because they close a real, immediate gap: we're about to
expand the semantic layer (more dimensions, 3 years of data) with zero dbt
tests and inconsistent model documentation today. Revisit `refactoring-dbt-models`
if `fct_sales.sql` grows complex enough to warrant extracting intermediate
models, and `debugging-dbt-errors` if dbt build failures become a recurring
time sink.
