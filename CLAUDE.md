# Project: OSS Sales NL Agent (for Enterprise Leadership Team)

## What this is
A natural-language query agent over a synthetic sales data warehouse, built for
a portfolio. The audience is leadership (non-technical) — the agent must never
guess or silently return a wrong number. Correctness and graceful refusal matter
more than coverage.

## Stack (all open source)
- Data: synthetic Contoso-style star schema, deliberately messy (see data/generate_contoso_messy.py)
- Warehouse: DuckDB (local, zero-setup) — swap for Postgres later if needed
- Semantic layer: dbt + dbt MetricFlow — metrics are the agent's preferred/certified
  query path; a boxed-in exploratory SQL fallback exists for uncovered questions (see rule 6)
- Agent: Python, function-calling loop (no heavy framework unless needed)
- LLM: pluggable — Groq/Together-hosted Llama 3.3 70B or Qwen2.5 72B by default
- UI: Streamlit chat app
- Eval: eval/questions.yaml + eval/run_eval.py, run via GitHub Actions on every PR

## Hard design rules (do not violate these when generating code)
1. The agent NEVER writes freeform SQL against raw/seed tables (the
   `main_raw` schema). Certified questions are answered only by calling
   pre-defined metrics from the dbt semantic layer (agent/metrics_client.py).
   A separate, lower-trust "exploratory" path (agent/sql_explorer.py) exists
   for questions with no certified metric — see rule 6.
2. Every certified answer must be traceable to a specific metric + filter set
   — no numbers should be synthesized by the LLM itself.
3. If a question is ambiguous (e.g. "revenue" without specifying gross/net) or
   maps to no defined metric AND isn't answerable via the exploratory path,
   the agent asks a clarifying question instead of guessing. This is a
   FEATURE, not a fallback — test for it explicitly.
4. All DB access from the agent uses a read-only connection. No exceptions —
   including the exploratory path.
5. Every metric call (and every exploratory query) has a row/result-size cap
   to prevent runaway queries.
6. Exploratory SQL (agent/sql_explorer.py) is a deliberate, boxed-in exception
   to rule 1, not a replacement for it — the LLM should always prefer a
   certified metric when one exists. It is restricted to the documented
   marts tables (never `main_raw`), limited to a single statically-validated
   SELECT statement (no DDL/DML, no ATTACH/COPY/PRAGMA/file-reading
   functions), and its results always come back as AgentResponse(type=
   "exploratory") — shown to the user as unverified, never with the same
   confidence as a certified answer, and never silently upgraded to "answer".
   See docs/failure_cases.md for the reasoning.

## Folder structure
- `data/` — data generation (synthetic Contoso + injected messiness)
- `dbt/` — star schema models + semantic layer metric definitions
- `agent/` — orchestration loop, metrics client, guardrails, prompts
- `eval/` — question set + eval runner (this is a regression suite, treat it as one)
- `app/` — Streamlit UI
- `docs/` — architecture notes, failure case writeups

## Current build phase
Phase 1: data generation (DONE — data/generate_contoso_messy.py, adds customer
  home-region needed for region-hierarchy handling downstream)
Phase 2: dbt star schema + semantic layer metrics (DONE)
Phase 3: agent orchestration + guardrails (DONE — Groq/Llama 3.3 70B tool-calling)
Phase 4: Streamlit UI (DONE)
Phase 5: eval harness + CI (DONE — 7/7 passing, see .github/workflows/eval.yml)
Phase 6: README polish + failure case docs (DONE — docs/failure_cases.md)

## Conventions
- Python 3.11, type hints everywhere, `ruff` for linting
- Metric names: snake_case, prefixed by domain (e.g. `sales_net_revenue`, `sales_target_attainment`)
- Never commit `.env` — use `.env.example` as the template
- Commit in small, story-telling steps (see docs/commit_style.md) — this is a
  portfolio repo, commit history is part of the deliverable
