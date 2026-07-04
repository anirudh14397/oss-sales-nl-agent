"""
Metrics client: the ONLY way the agent may query the warehouse.

Wraps `mf query` (dbt MetricFlow) against the dbt semantic layer defined in
dbt/models/marts/_semantic_models.yml. The agent never writes or executes raw
SQL against base tables (CLAUDE.md hard design rule #1) — every number it
returns is traceable to one of the certified metrics below plus the filters
passed to it (rule #2).

Read-only enforcement: queries run against the dbt profile's "agent" target
(dbt/profiles.yml), which opens the DuckDB file with read_only: true at the
connection level — not just as an application-level convention.
"""

import csv
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"

# Mirrors the `metrics:` block in dbt/models/marts/_semantic_models.yml.
# sales_returns_amount and sales_target_revenue are intentionally excluded —
# they're internal base metrics that back the ratio metrics below, not
# something the agent should call directly.
CERTIFIED_METRICS = {
    "sales_gross_revenue": {
        "description": "Total gross revenue before returns. Distinct from sales_net_revenue.",
        "dimensions": ["customer__segment", "customer__customer_id", "product__category", "region__region_name", "metric_time"],
    },
    "sales_net_revenue": {
        "description": "Gross revenue minus returns. The default 'actuals' revenue figure.",
        "dimensions": ["customer__segment", "customer__customer_id", "product__category", "region__region_name", "metric_time"],
    },
    "sales_returns_rate": {
        "description": "Returns as a fraction of gross revenue.",
        "dimensions": ["customer__segment", "customer__customer_id", "product__category", "region__region_name", "metric_time"],
    },
    "sales_target_attainment": {
        "description": "Net revenue as a fraction of target, for the same region and quarter.",
        "dimensions": ["region__region_name", "metric_time"],
    },
}

MAX_QUERY_ROWS = 500


class MetricQueryError(RuntimeError):
    """Raised when a metric call is invalid or the underlying mf query fails."""


@dataclass
class MetricResult:
    metric: str
    columns: list[str]
    rows: list[dict]


def call_metric(
    metric_name: str,
    group_by: list[str] | None = None,
    where: str | None = None,
    limit: int = MAX_QUERY_ROWS,
) -> MetricResult:
    """Call a certified metric through dbt MetricFlow.

    group_by: dimension/entity names as MetricFlow expects them, e.g.
        "region__region_name", "product__category", "metric_time__quarter".
    where: a MetricFlow where clause, e.g.
        "{{ TimeDimension('metric_time', 'quarter') }} = '2024-04-01'".
        This is MetricFlow's templated filter language, not raw SQL — it can
        only reference dimensions/entities that exist on the metric.
    """
    if metric_name not in CERTIFIED_METRICS:
        raise MetricQueryError(
            f"'{metric_name}' is not a certified metric. Allowed: {sorted(CERTIFIED_METRICS)}"
        )

    row_limit = min(limit, MAX_QUERY_ROWS)
    group_by = group_by or []

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        csv_path = tmp.name

    cmd = ["mf", "query", "--metrics", metric_name, "--quiet", "--csv", csv_path, "--limit", str(row_limit)]
    if group_by:
        cmd += ["--group-by", ",".join(group_by)]
    if where:
        cmd += ["--where", where]

    env = {**os.environ, "DBT_PROFILES_DIR": str(DBT_DIR), "DBT_TARGET": "agent"}

    try:
        proc = subprocess.run(cmd, cwd=DBT_DIR, env=env, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise MetricQueryError(f"mf query failed for '{metric_name}': {proc.stderr.strip() or proc.stdout.strip()}")

        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames or []
            rows = list(reader)
    finally:
        Path(csv_path).unlink(missing_ok=True)

    return MetricResult(metric=metric_name, columns=columns, rows=rows[:row_limit])
