"""
Agent orchestration loop.

Contract (see CLAUDE.md hard design rules):
- answer(question: str, history: list[dict] | None = None) -> AgentResponse
  `history` is prior turns as [{"role": "user"|"assistant", "content": str}, ...],
  oldest first, NOT including the current `question`. Optional and
  order-independent of the original single-arg contract — every existing
  caller (eval/run_eval.py) that only passes `question` still works, since
  each eval question is a standalone turn.
- Certified questions are only ever answered via agent.metrics_client, never
  raw SQL against base tables
- Calls agent.guardrails.check_ambiguity() before generating any metric call
- Returns a "clarify" response for ambiguous questions rather than guessing
- A separate, lower-trust "exploratory" path (agent.sql_explorer) exists for
  questions with no certified metric — see EXPLORE_NOTE below and
  docs/failure_cases.md for why it's boxed in the way it is. It is never the
  first choice, and its answers are never phrased with certified confidence.

Pipeline:
1. Keyword-match the question against certified metrics (_keyword_match_metrics).
   This is deliberately dumb — it exists only to catch the clearest case of
   ambiguity (bare "revenue", no gross/net qualifier, no breakdown, AND no
   explicit request for both gross and net together) before an LLM call is
   even attempted. guardrails.check_ambiguity() runs on its output.
2. A scope check for forecasting/future-looking questions, which are out of
   scope regardless of metric — refused before any metric call is generated.
3. If neither of the above short-circuits, an LLM (Groq, function-calling)
   sees the full conversation history plus the current question, and picks
   one or more actions: call_metric (possibly more than once, e.g. to
   compare gross vs net in one turn), explore_with_sql,
   ask_clarifying_question, or refuse. History is what lets a bare follow-up
   like "net" correctly resolve against an earlier "...last quarter?" instead
   of being answered as an isolated, context-free fragment. The LLM is told
   about the certified metrics, their dimensions, the marts schema (for
   explore_with_sql), and the data's known messiness (gross/net split, the
   mid-year APAC hierarchy change) so it can decide when to clarify instead
   of guess.
4. Each call_metric action is validated (agent.guardrails.validate_metric_call),
   executed (agent.metrics_client.call_metric), and row-capped
   (agent.guardrails.enforce_row_limit) before any answer is synthesized;
   multiple call_metric actions in one turn are combined into one response.
   An explore_with_sql action is statically validated and executed by
   agent.sql_explorer (read-only, marts-only, single SELECT) and always
   comes back labeled "exploratory", with the SQL shown, not stated as fact.
5. Narrative text and an optional chart are built deterministically from the
   metric result in Python — the LLM never states a number that didn't come
   straight out of a metric call (or, for exploratory answers, straight out
   of the query result it wrote and that was executed under guardrails).
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import plotly.express as px
from dotenv import load_dotenv
from groq import BadRequestError, Groq

from agent import metrics_client, sql_explorer
from agent.guardrails import GuardrailViolation, check_ambiguity, enforce_row_limit, validate_metric_call
from agent.metrics_client import CERTIFIED_METRICS, MetricQueryError, MetricResult
from agent.sql_explorer import SqlGuardrailViolation

load_dotenv()

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# This dataset only ever covers calendar year 2024 — giving the LLM a fixed
# anchor lets it resolve relative time phrases ("last quarter", "this year")
# deterministically instead of guessing against a real-world "today".
DATASET_ANCHOR = (
    "This warehouse contains sales data for calendar year 2024 only "
    "(2024-01-01 through 2024-12-31). Resolve relative time phrases against "
    "that fixed window: 'last quarter' = Q4 2024 (metric_time__quarter = "
    "'2024-10-01'), 'this year' = all of 2024, 'Q2' = '2024-04-01', etc., "
    "unless the user names a different period."
)

REGION_HIERARCHY_NOTE = (
    "The region hierarchy changed mid-year: before 2024-07-01, APAC was a "
    "single region named 'APAC'. From 2024-07-01 on, it split into "
    "'APAC-North' and 'APAC-South'. If a question asks about APAC (or a time "
    "range spanning or ambiguous relative to 2024-07-01) without saying which "
    "side of the split it means, call ask_clarifying_question and explain the "
    "hierarchy change rather than guessing which grouping to use."
)

REVENUE_DEFAULT_NOTE = (
    "If the user says plain 'revenue' with no 'gross'/'net' qualifier, but the "
    "question is a breakdown or filtered to a specific entity (e.g. 'revenue "
    "by customer for CUST-00042'), default to sales_net_revenue — it's the "
    "standard actuals figure. Only bare top-line 'revenue' questions with no "
    "further context are genuinely ambiguous."
)

OUT_OF_SCOPE_NOTE = (
    "This warehouse only has sales revenue, returns, and target data. If "
    "asked about anything else (headcount, marketing spend, forecasting or "
    "predicting the future, etc.), call refuse with a short reason."
)

EXPLORE_NOTE = (
    "If a question is genuinely a sales-data question but doesn't fit any "
    "certified metric above (e.g. counts, distributions, or breakdowns the "
    "metrics don't cover), you may call explore_with_sql as a last resort: "
    "write ONE read-only SELECT query against the tables below. Always "
    "prefer call_metric when a certified metric answers the question — "
    "explore_with_sql results are shown to the user as unverified/exploratory, "
    "never with the same confidence as a certified metric.\n\n"
    "Marts schema (the only tables you may query):\n{schema}"
)

CONVERSATION_NOTE = (
    "The conversation may include your own earlier clarifying questions and "
    "the user's short follow-up replies (e.g. a single word like 'net' or "
    "'APAC-North'). Resolve such follow-ups against the original question "
    "in the history — a one-word reply to your own clarifying question means "
    "the full original request with that one detail filled in, not a new, "
    "context-free question."
)

FUTURE_KEYWORDS = [
    "forecast", "predict", "projection", "will be", "will our",
    "next year", "next quarter", "expected to", "going to be",
]

# label + display format per certified metric, used for deterministic narrative synthesis.
METRIC_DISPLAY = {
    "sales_gross_revenue": ("Gross Revenue", "currency"),
    "sales_net_revenue": ("Net Revenue", "currency"),
    "sales_returns_rate": ("Returns Rate", "percent"),
    "sales_target_attainment": ("Target Attainment", "percent"),
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_metric",
            "description": (
                "Query one certified metric from the sales semantic layer. "
                "You may call this more than once in the same turn if the question "
                "asks to compare metrics (e.g. \"compare gross and net revenue for Q2\" "
                "→ call it once for sales_gross_revenue and once for sales_net_revenue)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": list(CERTIFIED_METRICS)},
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Dimensions to break the result out by, e.g. region__region_name, product__category.",
                    },
                    "filters": {
                        "type": "object",
                        "description": "Equality filters as {dimension: value}, e.g. {\"region__region_name\": \"APAC-North\", \"metric_time__quarter\": \"2024-04-01\"}.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore_with_sql",
            "description": (
                "Last resort: answer a sales-data question with no matching certified "
                "metric by writing a single read-only SELECT against the marts schema. "
                "Never use this if a certified metric already covers the question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single read-only SELECT statement against the marts tables."},
                    "reasoning": {"type": "string", "description": "One sentence on why no certified metric covers this."},
                },
                "required": ["sql", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarifying_question",
            "description": "Ask the user a clarifying question instead of guessing, when the question is ambiguous or underspecified.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refuse",
            "description": "Refuse to answer because the question is out of scope for this warehouse.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


@dataclass
class AgentResponse:
    type: str  # "answer" | "clarify" | "refuse" | "exploratory"
    text: str
    metric_used: str | None = None
    chart: Any | None = None  # plotly.graph_objects.Figure | None
    sql_used: str | None = None  # set only for type == "exploratory"


def _keyword_match_metrics(question: str) -> list[str]:
    """Cheap candidate retrieval — just enough to catch the clear-cut
    ambiguous case (bare "revenue", no gross/net qualifier, no breakdown/
    specific-entity context) before an LLM call is generated."""
    q = question.lower()
    matched: set[str] = set()

    has_gross = "gross" in q
    has_net = "net" in q
    is_breakdown_or_specific = bool(re.search(r"\bby\b", q) or re.search(r"cust-\d+|prod-\d+", q))

    if has_gross and "revenue" in q:
        matched.add("sales_gross_revenue")
    if has_net and "revenue" in q:
        matched.add("sales_net_revenue")
    if not has_gross and not has_net and ("revenue" in q or "sales" in q):
        if is_breakdown_or_specific:
            matched.add("sales_net_revenue")
        else:
            matched.update({"sales_gross_revenue", "sales_net_revenue"})

    if "return" in q:
        matched.add("sales_returns_rate")
    if "target" in q or "attainment" in q or "quota" in q or "tracking" in q:
        matched.add("sales_target_attainment")

    return sorted(matched)


def _is_future_looking(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in FUTURE_KEYWORDS)


def _build_system_prompt() -> str:
    metric_lines = []
    for name, meta in CERTIFIED_METRICS.items():
        dims = ", ".join(meta["dimensions"])
        metric_lines.append(f"- {name}: {meta['description']} Dimensions: {dims}.")

    explore_note = EXPLORE_NOTE.format(schema=sql_explorer.describe_schema())

    return (
        "You are a sales metrics assistant for a non-technical leadership "
        "audience. Prefer answering using the certified metrics below, "
        "by calling the call_metric tool — never invent a number.\n\n"
        "Certified metrics:\n" + "\n".join(metric_lines) + "\n\n"
        f"{DATASET_ANCHOR}\n\n{REGION_HIERARCHY_NOTE}\n\n{REVENUE_DEFAULT_NOTE}\n\n{OUT_OF_SCOPE_NOTE}\n\n{explore_note}\n\n{CONVERSATION_NOTE}\n\n"
        "group_by and filter keys must be exactly one of the dimension names "
        "listed for the chosen metric. Always respond by calling exactly one "
        "of: call_metric, explore_with_sql, ask_clarifying_question, refuse."
    )


def _llm_pick_actions(question: str, history: list[dict] | None = None, max_attempts: int = 3) -> list[dict]:
    # Groq/Llama tool-calling occasionally emits malformed function-call
    # syntax (a known model-level quirk, not a logic error) — retry a couple
    # of times before giving up, since the same prompt usually succeeds on a
    # later attempt.
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    messages = [{"role": "system", "content": _build_system_prompt()}]
    messages += history or []
    messages.append({"role": "user", "content": question})

    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="required",
            )
        except BadRequestError as e:
            last_error = e
            continue

        tool_calls = resp.choices[0].message.tool_calls
        if not tool_calls:
            last_error = RuntimeError("model returned no tool call")
            continue

        actions = []
        parse_failed = False
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                parse_failed = True
                break
            actions.append({"tool": call.function.name, "args": args})

        if parse_failed:
            last_error = RuntimeError("model returned malformed tool call arguments")
            continue
        return actions

    raise MetricQueryError(f"LLM failed to produce a usable tool call after {max_attempts} attempts: {last_error}")


def _build_where_clause(filters: dict) -> str | None:
    if not filters:
        return None
    clauses = []
    for key, value in filters.items():
        if key == "metric_time" or key.startswith("metric_time__"):
            granularity = key.split("__", 1)[1] if "__" in key else "day"
            clauses.append(f"{{{{ TimeDimension('metric_time', '{granularity}') }}}} = '{value}'")
        else:
            clauses.append(f"{{{{ Dimension('{key}') }}}} = '{value}'")
    return " AND ".join(clauses)


def _format_value(metric: str, raw: str) -> str:
    val = float(raw)
    _, kind = METRIC_DISPLAY[metric]
    return f"${val:,.2f}" if kind == "currency" else f"{val:.1%}"


def _synthesize(metric: str, result: MetricResult, group_by: list[str]) -> tuple[str, Any | None]:
    label, _ = METRIC_DISPLAY[metric]
    value_col = metric

    if not result.rows:
        return f"No data was found for {label} with those filters.", None

    if not group_by or len(result.rows) == 1:
        row = result.rows[0]
        text = f"{label}: {_format_value(metric, row[value_col])}"
        return text, None

    dim_col = [c for c in result.columns if c != value_col][0]
    rows_sorted = sorted(result.rows, key=lambda r: float(r[value_col]), reverse=True)
    top = rows_sorted[0]
    lines = [f"{label} by {dim_col}, highest first — {top[dim_col]} leads at {_format_value(metric, top[value_col])}:"]
    for r in rows_sorted:
        lines.append(f"  {r[dim_col]}: {_format_value(metric, r[value_col])}")
    text = "\n".join(lines)

    chart = px.bar(
        x=[r[dim_col] for r in rows_sorted],
        y=[float(r[value_col]) for r in rows_sorted],
        labels={"x": dim_col, "y": label},
        title=f"{label} by {dim_col}",
    )
    return text, chart


def _synthesize_sql_result(result: sql_explorer.SqlResult) -> tuple[str, Any | None]:
    prefix = "⚠️ Exploratory answer — generated SQL, not a certified metric. Not verified for accuracy; review before using in a leadership report.\n\n"

    if not result.rows:
        return prefix + "The query ran successfully but returned no rows.", None

    if len(result.rows) == 1 and len(result.columns) == 1:
        row = result.rows[0]
        return prefix + f"{result.columns[0]}: {row[result.columns[0]]}", None

    lines = [", ".join(result.columns)]
    for row in result.rows:
        lines.append(", ".join(str(row[c]) for c in result.columns))
    if result.truncated:
        lines.append(f"... truncated at {sql_explorer.MAX_EXPLORE_ROWS} rows")
    text = prefix + "\n".join(lines)

    chart = None
    if len(result.columns) == 2:
        dim_col, value_col = result.columns
        try:
            values = [float(r[value_col]) for r in result.rows]
        except (TypeError, ValueError):
            values = None
        if values is not None:
            chart = px.bar(x=[r[dim_col] for r in result.rows], y=values, labels={"x": dim_col, "y": value_col})

    return text, chart


def _run_call_metric(args: dict) -> AgentResponse:
    metric = args.get("metric")
    group_by = args.get("group_by") or []
    filters = args.get("filters") or {}

    try:
        validate_metric_call(metric, {"group_by": group_by, **filters})
    except GuardrailViolation as e:
        return AgentResponse(type="refuse", text=f"Couldn't safely run that metric call: {e}")

    try:
        result = metrics_client.call_metric(metric, group_by=group_by, where=_build_where_clause(filters))
    except MetricQueryError as e:
        return AgentResponse(type="refuse", text=f"That metric query failed: {e}")

    result = enforce_row_limit(result)
    text, chart = _synthesize(metric, result, group_by)
    return AgentResponse(type="answer", text=text, metric_used=metric, chart=chart)


def answer(question: str, history: list[dict] | None = None) -> AgentResponse:
    if _is_future_looking(question):
        return AgentResponse(
            type="refuse",
            text="Forecasting and future predictions are out of scope — this agent only reports from historical certified metrics.",
        )

    q_lower = question.lower()
    # A question that explicitly names BOTH gross and net isn't ambiguous —
    # it's a comparison request. Only treat "multiple candidate metrics
    # matched" as ambiguity when neither qualifier was given at all.
    explicit_both_revenue = "gross" in q_lower and "net" in q_lower
    candidate_metrics = _keyword_match_metrics(question)
    if not explicit_both_revenue and check_ambiguity(question, candidate_metrics):
        return AgentResponse(
            type="clarify",
            text=(
                "That could mean gross revenue (before returns) or net revenue "
                "(after returns) — which one do you want?"
            ),
        )

    try:
        actions = _llm_pick_actions(question, history=history)
    except MetricQueryError as e:
        return AgentResponse(type="refuse", text=f"Couldn't process that question right now: {e}")

    metric_actions = [a for a in actions if a["tool"] == "call_metric"]
    if metric_actions:
        responses = [_run_call_metric(a["args"]) for a in metric_actions]
        failures = [r for r in responses if r.type == "refuse"]
        if failures:
            return failures[0]
        if len(responses) == 1:
            return responses[0]
        combined_text = "\n\n".join(r.text for r in responses)
        combined_metric = ", ".join(r.metric_used for r in responses)
        return AgentResponse(type="answer", text=combined_text, metric_used=combined_metric, chart=responses[0].chart)

    action = actions[0]

    if action["tool"] == "ask_clarifying_question":
        return AgentResponse(type="clarify", text=action["args"].get("question", "Could you clarify your question?"))

    if action["tool"] == "refuse":
        return AgentResponse(type="refuse", text=action["args"].get("reason", "This is out of scope for certified sales metrics."))

    if action["tool"] == "explore_with_sql":
        sql = action["args"].get("sql", "")
        try:
            result = sql_explorer.run_sql(sql)
        except SqlGuardrailViolation as e:
            return AgentResponse(type="refuse", text=f"Couldn't safely run that exploratory query: {e}")

        text, chart = _synthesize_sql_result(result)
        return AgentResponse(type="exploratory", text=text, chart=chart, sql_used=sql)

    return AgentResponse(type="refuse", text="Could not determine how to answer this from certified metrics.")
