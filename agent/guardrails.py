"""
Guardrails: the safety layer between the agent and the warehouse.

Read-only enforcement (CLAUDE.md rule #4) does not live here as a connection
object — it's enforced one layer down, in agent/metrics_client.py, which
always queries through the dbt "agent" profile target (dbt/profiles.yml),
opened with read_only: true at the DuckDB connection level. There is no
connection object to intercept in this module because metrics_client never
hands one out; every query goes through `mf query` in a subprocess.
"""

from agent.metrics_client import CERTIFIED_METRICS, MAX_QUERY_ROWS, MetricResult


class GuardrailViolation(Exception):
    """Raised when a metric call or result violates a guardrail."""


def check_ambiguity(question: str, matched_metrics: list[str]) -> bool:
    """True if `question` plausibly maps to more than one certified metric.

    Ambiguity detection happens upstream, in whatever retrieval step produces
    matched_metrics (e.g. "revenue" keyword-matching both sales_gross_revenue
    and sales_net_revenue) — this function just makes the resulting decision
    explicit and testable on its own.
    """
    return len(set(matched_metrics)) > 1


def _dimension_base(name: str) -> str:
    # Strips granularity suffixes, e.g. "metric_time__quarter" -> "metric_time".
    return name.split("__")[0] if "__" not in name else "__".join(name.split("__")[:-1])


def validate_metric_call(metric_name: str, filters: dict) -> None:
    """Raise if metric_name isn't certified, or filters/group_by reference a
    dimension that doesn't exist for that metric.

    `filters` is expected in the shape the orchestrator builds it:
        {"group_by": [<dimension>, ...], <dimension>: <value>, ...}
    """
    if metric_name not in CERTIFIED_METRICS:
        raise GuardrailViolation(
            f"'{metric_name}' is not a certified metric. Allowed: {sorted(CERTIFIED_METRICS)}"
        )

    allowed = CERTIFIED_METRICS[metric_name]["dimensions"]
    allowed_bases = {_dimension_base(d) for d in allowed} | {d for d in allowed}

    filters = filters or {}
    group_by = filters.get("group_by") or []
    where_dims = [k for k in filters if k != "group_by"]

    for dim in list(group_by) + where_dims:
        if _dimension_base(dim) not in allowed_bases and dim not in allowed_bases:
            raise GuardrailViolation(
                f"Dimension '{dim}' is not valid for metric '{metric_name}'. "
                f"Allowed dimensions: {allowed}"
            )


def enforce_row_limit(result: MetricResult) -> MetricResult:
    """Truncate results over MAX_QUERY_ROWS. metrics_client already caps the
    underlying query with --limit; this is a defense-in-depth check on the
    result object itself, in case a caller constructs one another way.
    """
    if len(result.rows) > MAX_QUERY_ROWS:
        result.rows = result.rows[:MAX_QUERY_ROWS]
    return result
