# Failure cases and messiness-handling decisions

This is a log of the deliberately-messy conditions in the data and how the
project handles (or explicitly declines to handle) each one. The goal isn't
to pretend the data is clean — it's to make every judgment call visible
rather than silently baked into a query.

## 1. Duplicate customer records

`data/generate_contoso_messy.py` injects ~3% duplicate customers: the same
company re-entered under a different `CUST-ID`, with a name variant like
"Acme Corp" vs "Acme Corporation" vs "Acme".

**Handling**: `dbt/models/marts/customer_crosswalk.sql` normalizes each name
(trim, lowercase, strip a trailing corporate suffix) and groups by
`(normalized_name, segment)`. Within a group, the record with the earliest
`signup_date` is canonical.

**Known limitation**: this is a heuristic, not a guarantee. Two distinct real
companies that happen to share a normalized name and segment would
incorrectly merge. At this dataset's scale (206 raw → 200 canonical
customers) that risk is low, but it would need a harder identity signal
(tax ID, domain, etc.) in a real warehouse.

## 2. Gross vs. net revenue

`fact_sales` carries both `gross_revenue` and `net_revenue` (gross minus
returns) as genuinely different figures.

**Handling**: both are certified as separate metrics
(`sales_gross_revenue`, `sales_net_revenue`). `agent/orchestrator.py`
pre-checks with a keyword pass (`_keyword_match_metrics` +
`guardrails.check_ambiguity`): a bare "revenue" with no gross/net qualifier
and no breakdown/entity context is treated as genuinely ambiguous and
triggers a clarifying question rather than a guess. A "revenue" mention
inside a breakdown query (e.g. "revenue by customer for CUST-00042") defaults
to net revenue, since that's the standard actuals figure once the question
already has enough specificity that the gross/net choice is unlikely to
change the *shape* of the answer the user wants.

## 3. Mid-year region hierarchy change

`dim_region` has two hierarchy versions: a single `APAC` region through
2024-06-30, split into `APAC-North`/`APAC-South` from 2024-07-01 on. The
source data ties a region only to `fact_target`, not to individual sales.

**Handling**:
- `dim_customer` carries each customer's home region under the pre-split
  naming (`region_name_v1`).
- `fct_sales` resolves the *effective* region for each sale from the
  customer's home region and the sale's transaction date: unchanged for
  non-APAC regions, and for APAC customers post-split, deterministically
  assigned to North or South via a hash of the customer key (documented in
  `fct_sales.sql` — the source data doesn't carry an explicit sub-region, so
  this is a modeling decision, not a fact).
- The agent's system prompt tells the LLM about the split explicitly; a
  question about APAC performance over a range spanning or ambiguous
  relative to 2024-07-01 gets a clarifying question instead of a silently
  wrong aggregate.

**Known limitation**: the North/South assignment is stable per customer but
arbitrary — it does not reflect any real geographic fact, since none exists
in the source data.

## 4. Late-arriving facts

~2% of `fact_sales` rows have a `load_date` weeks after their transaction
`date_key` (a late-arriving batch).

**Handling**: all marts and metrics report by transaction date (`date_key`),
not load date — i.e., "final actuals," not "as of when we happened to load
it." `fct_sales.is_late_arriving` flags these rows so a future "as of
<load_date>" point-in-time view could be built without re-deriving the
distinction, but no metric currently exposes that view.

**Known limitation**: this means the agent cannot currently answer "what did
we think Q1 revenue was as of April 1st" — only "what was Q1 revenue,
period." That's a real capability gap, not an oversight; it wasn't in the
certified metric set for this phase.

## 5. Target row overlap across region hierarchy versions

`fact_target` (as generated) emits a target row for every
`(region_key, quarter)` pair, including region versions that weren't
in effect yet for that quarter — e.g. a post-split "APAC-North" target row
exists for Q1, before the split happened. Left unfiltered, this double-counts
target revenue for Q1/Q2 across the pre/post-split versions.

**Handling**: `dbt/models/marts/fct_target.sql` keeps only rows where the
region version's `valid_from`/`valid_to` window actually covers the target's
quarter.

## 6. The exploratory SQL escape hatch

The certified-metrics-only design (rules 1-2 in CLAUDE.md) exists specifically
to avoid the standard "chat with your data" failure mode: an LLM writes a
plausible-looking SQL join, gets it subtly wrong (e.g. forgets the customer
dedup or region-hierarchy logic in `marts/`), and states a wrong number with
full confidence. That's a real, deliberate constraint, not an oversight — and
it means some legitimate sales-data questions (ad-hoc counts, distributions,
lookups) get refused even though the data to answer them exists.

**Decision**: rather than either (a) refusing all of those, or (b) letting
the agent write SQL freely, `agent/sql_explorer.py` adds a second, clearly
lower-trust path:
- It's a last resort — the LLM is instructed to prefer a certified metric
  whenever one applies, and only fall through to this when none does.
- It's restricted to the `marts` schema (dim_customer, dim_product,
  dim_region, dim_date, fct_sales, fct_target) — never the raw `main_raw`
  seed tables, which still carry the unresolved messiness (duplicate
  customers, unlinked regions) that `marts/` exists to clean up. An LLM
  writing ad-hoc SQL is the *least* likely place to get that messiness
  handling right.
- Every query is statically validated with `sqlglot` before execution: must
  parse as exactly one `SELECT` statement, must reference only the allowed
  tables, and is keyword-blocked against `ATTACH`/`COPY`/`PRAGMA`/file-reading
  functions (`read_csv`, `read_parquet`, etc.) that could exfiltrate data or
  read the filesystem even under a read-only DB connection.
- Every result comes back as `AgentResponse(type="exploratory")`, with the
  generated SQL attached (`sql_used`) — the Streamlit UI renders it as a
  visibly different (orange warning) block, always shows the SQL for review,
  and never lets it read as a certified number.

**Known limitation**: this reintroduces some of the exact risk certified
metrics avoid — a technically-valid query can still join or aggregate in a
way that's semantically wrong even though it executes cleanly (e.g.
double-counting across the region hierarchy versions, the same failure mode
fixed in `fct_target.sql` §5 above, but there's no guardrail that catches
"valid SQL, wrong answer" the way there is for certified metrics). That's the
tradeoff of the hybrid design: broader coverage, explicitly at the cost of
the stronger correctness guarantee certified metrics give — and that tradeoff
is made visible to the user every time, not hidden.

## 7. Multi-turn context — a real wrong-answer bug, found and fixed

Early versions of `agent/orchestrator.py::answer()` took only the current
message, with no conversation history. This is a chat UI, so that broke the
most basic follow-up pattern: the agent asks "gross or net?", the user
replies with just "net", and the agent — with zero context — answered a
different question entirely (total net revenue, ~$50.1M) instead of the one
actually being asked (net revenue for the original "last quarter" scope,
~$13.3M). Confirmed with a direct before/after test, not assumed.

This is the exact "wrong number stated with full confidence" failure mode the
whole certified-metrics design exists to prevent — just relocated from
single-turn to multi-turn. A correctness bug, not a nice-to-have.

**Handling**: `answer()` now takes an optional `history` parameter (prior
turns, oldest first) threaded into the LLM's message list, plus a system
prompt note telling the model to resolve short follow-ups against the
original request in that history rather than treat them as new, context-free
questions. `app/streamlit_app.py` passes the full prior conversation on every
turn. `eval/run_eval.py` is unaffected — each eval question is a standalone
turn by design, so it calls `answer(question)` with no history, which still
works (the parameter is optional).

A related bug found in the same pass: a genuinely non-ambiguous compound
question like "compare gross and net revenue for Q2" was incorrectly forced
into a clarifying question, because the keyword ambiguity pre-check treated
"both gross and net mentioned" the same as "neither specified." Fixed by only
triggering that pre-check when neither qualifier is present; `call_metric`
can now also be invoked more than once per turn so a genuine comparison
question gets both figures back in one answer instead of one at a time.

## 8. Seed/model name collision — masked by a workaround, not actually fixed, until it blocked schema.yml

From early in the build, dbt emitted a recurring warning: `dim_customer`,
`dim_product`, `dim_region`, and `dim_date` existed as both a seed (the raw
CSV) and a mart model (the cleaned/deduplicated version) — same name, two
different resources. `dbt_project.yml`'s `+schema: raw` config for seeds
worked around the *physical* relation collision (they land in different
DuckDB schemas), but the *node name* collision remained latent.

It stayed harmless until schema.yml documentation was added for the mart
models: dbt's `models:` section resolved `dim_customer` to the seed instead
of the mart model, warning that the seed should be documented under `seeds:`
instead. Adding real tests forced the actual fix rather than another
workaround: seed files (and the generator that writes them) were renamed
with a `raw_` prefix (`raw_dim_customer.csv`, etc.), and every staging
model's `ref()` updated to match. Node names are now unambiguous — one
resource per name, full stop.

**Lesson**: a warning that doesn't block anything is still a real problem;
it just hasn't found the thing it blocks yet.

## 9. Five years of data, row-level anomalies, and the quarantine pattern

The dataset originally spanned one calendar year (2024). Extending it to
five years (2020-2024) and injecting realistic row-level data-quality
anomalies (not just the structural messiness above) surfaced one real bug
and required one new architectural pattern.

**Bug found**: `gen_dim_region`'s pre-split region rows had `valid_from`
hardcoded to `"2024-01-01"`. With data now starting in 2020, every sale
before 2024 would have silently vanished from `fct_sales` — the join
`s.date_key >= r.valid_from` would fail for all of them, and an inner join
just drops non-matches, no error, no warning. Fixed by parametrizing
`valid_from` to the actual dataset start date instead of a hardcoded
literal. The same class of bug as §8: a hardcoded assumption that happened
to be true for the data that existed at the time, silently wrong once the
data changed.

**Anomalies injected** (`ANOMALY_RATE` = 3% of `fact_sales` rows, plus a
separate 0.5% duplicate-key rate): null customer_key/product_key, orphaned
customer_key/product_key (references a record that doesn't exist), negative
quantity, negative gross_revenue, net_revenue exceeding gross_revenue, and
outright duplicate sales_key rows (simulating an ETL replay bug). These are
deliberately **not** cleaned in the generator or in staging — staging stays
pass-through by convention (see `models/staging/schema.yml`'s
`stg_fact_sales` entry, which had its `not_null`/`unique`/`relationships`
tests removed for exactly this reason: those tests were correct when the
data was clean, and became false failures once real anomalies were
introduced at that layer on purpose).

**Handling — the quarantine pattern**: `dbt/models/intermediate/int_fact_sales_validated.sql`
classifies every raw sales row against the business rules above and computes
`is_valid` + a human-readable `validation_errors` reason. This is NOT the
same as a `dbt test` (which only gates the build pass/fail after the fact)
and NOT the same as an inner join (which drops bad rows invisibly). It's a
third thing: bad rows are routed to `marts/fct_sales_quarantine` — visible,
queryable, with a reason attached — while `marts/fct_sales` only ever
contains rows that passed every check. A singular test
(`tests/assert_fact_sales_rows_fully_accounted_for.sql`) proves the
reconciliation property directly: every raw row lands in exactly one of the
two tables, never both, never neither.

`fct_sales_quarantine` is also reachable through the agent's exploratory SQL
path (`agent/sql_explorer.py`), so questions like "how much of our sales
data failed validation and why" are answerable — still labeled unverified,
same as any other exploratory answer, but a real capability this project
didn't have before.

**Known limitation**: the duplicate-sales_key resolution
(`row_number() over (partition by sales_key order by load_date)`) keeps
exactly one row per duplicated key and quarantines the rest, picking
whichever has the earliest load_date. That's a reasonable default (earliest
load = likely the original), not a verified-correct one — there's no
source-of-truth signal in this synthetic data to confirm which of the
duplicates is "real."

## Eval results

`eval/questions.yaml` + `eval/run_eval.py` exercise the live agent (real Groq
call, real DuckDB warehouse) against 9 leadership questions covering each of
the above. As of the pre-§9 data (single year, no anomalies), 9/9 passing,
re-verified across multiple runs to rule out LLM non-determinism.

After extending to 5 years + anomalies (§9), only 3/9 were re-verified live
before hitting Groq's free-tier daily token limit (100,000/100,000 used) —
the 3 that ran passed, including the ones exercising `DATASET_ANCHOR`'s
updated date range ("last quarter", "this year"). The dbt-level
verification (69/69 schema tests, the row-reconciliation singular test, and
manual spot-checks of `fct_sales`/`fct_sales_quarantine` contents) is
complete and doesn't depend on Groq quota; the full live eval re-run is
pending the next quota window.

One operational note surfaced during testing: Groq's Llama 3.3 70B
occasionally emits a malformed function-call payload (`tool_use_failed`) —
a model-level quirk, not a logic bug. `agent/orchestrator.py::_llm_pick_action`
retries up to 3 times before giving up.
