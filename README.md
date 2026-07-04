# OSS Sales NL Agent

<!-- Replace YOUR_USERNAME once the repo is pushed to GitHub -->
[![eval](https://github.com/YOUR_USERNAME/oss-sales-nl-agent/actions/workflows/eval.yml/badge.svg)](https://github.com/YOUR_USERNAME/oss-sales-nl-agent/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)

A natural-language query agent for sales data, built for a non-technical
leadership audience. Fully open source.

## Why this project

Most "chat with your data" demos let an LLM write freeform SQL against raw
tables. That's fine for a toy demo — it's dangerous for a leadership-facing
tool, because a wrong join produces a wrong number that gets stated with full
confidence.

This project takes the opposite approach: the agent can **only** call
pre-certified metrics from a semantic layer. If a question doesn't map
cleanly to a defined metric, the agent asks for clarification instead of
guessing.

## Architecture

```
Sales data warehouse (star schema, deliberately messy)
        │
Semantic layer (dbt + MetricFlow — certified metrics, preferred path)
        │
Agent orchestration (multi-turn — sees prior chat history, not just the latest message)
  ├─ keyword-based ambiguity pre-check (agent/guardrails.py)
  ├─ LLM tool-calling: call_metric (repeatable, for comparisons) / explore_with_sql /
  │  ask_clarifying_question / refuse
  ├─ guardrails (row limits, dimension validation, read-only DB access)
  ├─ exploratory SQL fallback (agent/sql_explorer.py) — read-only, marts-only,
  │  sqlglot-validated single SELECT, always labeled unverified — see
  │  docs/failure_cases.md §6 for why this exists and what it costs
  └─ answer synthesis (narrative + chart, built deterministically in Python)
        │
Streamlit chat UI
```

Conversation history is passed into every turn specifically so short follow-ups
work correctly — e.g. the agent asks "gross or net?" and you just reply "net";
it resolves that against the original question rather than answering "net" as
an isolated, context-free fragment (see docs/failure_cases.md §7).

Two trust tiers, always visibly distinguished:
- **Certified** (`call_metric`) — every number traces to a reviewed metric
  definition. This is the default and preferred path.
- **Exploratory** (`explore_with_sql`) — a last resort for questions no
  certified metric covers. The agent writes a single read-only SQL query
  against the curated marts (never raw tables), but the answer is always
  flagged unverified, with the SQL shown for review — never presented with
  certified confidence.

## Data

Synthetic Contoso-style retail sales data, generated with deliberate
imperfections to stress-test the semantic layer and guardrails:
- Duplicate customer records under slightly different IDs
- A `revenue` vs `net_revenue` distinction with genuinely different logic
- A late-arriving fact batch (records that "show up" a period late)
- A region hierarchy that changes mid-year (APAC splits into
  APAC-North/APAC-South on 2024-07-01)

See `data/generate_contoso_messy.py` and [docs/failure_cases.md](docs/failure_cases.md)
for how each of these is (and isn't) handled downstream.

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/oss-sales-nl-agent.git
cd oss-sales-nl-agent
conda create -n sales_agent python=3.11 && conda activate sales_agent
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEY

# 1. Generate the synthetic warehouse (already committed under data/seed/,
#    only needed if you want to regenerate it)
python data/generate_contoso_messy.py --scale small

# 2. Build the dbt star schema + semantic layer
cd dbt
DBT_PROFILES_DIR=. dbt seed --full-refresh
DBT_PROFILES_DIR=. dbt run
cd ..

# 3. Run the eval suite (exercises the full agent against real questions)
python eval/run_eval.py

# 4. Launch the chat UI
streamlit run app/streamlit_app.py
```

Notes:
- `dbt/profiles.yml` defines two targets: `dev` (read-write, used by `dbt
  run`/`dbt seed`) and `agent` (read-only, used by `agent/metrics_client.py`
  at query time via `DBT_TARGET=agent`). The agent can never write to the
  warehouse, at the connection level, not just by convention.
- Querying metrics requires `mf` (installed via `dbt-metricflow[duckdb]` in
  requirements.txt) to be on PATH.

## Certified metrics

Defined in `dbt/models/marts/_semantic_models.yml`, mirrored in
`agent/metrics_client.py::CERTIFIED_METRICS`:

| Metric | What it is |
|---|---|
| `sales_gross_revenue` | Total gross revenue before returns |
| `sales_net_revenue` | Gross revenue minus returns — the default "actuals" figure |
| `sales_returns_rate` | Returns as a fraction of gross revenue |
| `sales_target_attainment` | Net revenue as a fraction of target, by region + quarter |

The agent may only call these four. Everything else — headcount, marketing
spend, forecasts — gets a refusal, not a guess.

## Development

```bash
pip install -r requirements-dev.txt
ruff check .              # lint (config: ruff.toml)
python eval/run_eval.py   # regression suite — treat failures like test failures
```

CI (`.github/workflows/eval.yml`) runs both on every PR, against a real Groq
call and a real DuckDB build — not mocks. It needs a `GROQ_API_KEY` repo
secret to pass.

## Roadmap

- [x] Repo scaffolding
- [x] Synthetic messy Contoso data generator
- [x] dbt star schema + MetricFlow semantic layer
- [x] Agent orchestration + guardrails (multi-turn, certified + exploratory tiers)
- [x] Streamlit UI
- [x] Eval harness (9 leadership questions, 9/9 passing) + CI
- [x] Failure case writeup (docs/failure_cases.md)

## License

MIT — see LICENSE.
