"""
Exploratory SQL: a deliberately separate, lower-trust escape hatch for
questions that don't map to any certified metric (agent/metrics_client.py).

This is NOT the certified path, and every result from here comes back as an
"exploratory" AgentResponse — visibly flagged as unverified, never phrased
with the confidence of a certified metric answer. It exists because "we
don't have a metric for that" is sometimes a worse answer than a clearly
labeled, inspectable ad-hoc query. But it reintroduces exactly the failure
mode (a wrong join stated confidently) that the certified-metrics-only
design exists to avoid, so it's boxed in hard:

- Read-only DuckDB connection, always.
- Restricted to the marts schema (ALLOWED_TABLES below) — never the raw
  main_raw seed tables, which carry the unresolved messiness (duplicate
  customers, unlinked regions, overlapping targets) that marts/ exists to
  clean up. Reasoning about that messiness correctly is exactly what an
  LLM-authored ad-hoc join is least likely to get right.
- Single SELECT statement only, statically validated with sqlglot before
  execution — no DDL/DML, no ATTACH/COPY/PRAGMA/file-reading functions.
- Row-capped and time-limited like every other query path.
"""

import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

DB_PATH = Path(__file__).resolve().parent.parent / "warehouse.duckdb"

# The curated, documented marts — never the raw main_raw seed tables.
# fct_sales_quarantine is included deliberately: it lets the agent answer
# data-quality questions ("how much of our sales data failed validation and
# why") without needing a certified metric for that — still always labeled
# exploratory, never presented as a certified figure.
ALLOWED_TABLES = {
    "dim_customer", "dim_product", "dim_region", "dim_date",
    "fct_sales", "fct_target", "fct_sales_quarantine",
}

MAX_EXPLORE_ROWS = 200
QUERY_TIMEOUT_SECONDS = 10

BLOCKED_KEYWORDS = [
    "attach", "copy", "pragma", "install", "load", "export", "import",
    "glob", "read_csv", "read_parquet", "read_json", "httpfs", "secret", "main_raw",
]


class SqlGuardrailViolation(Exception):
    """Raised when a candidate query fails static validation or execution."""


@dataclass
class SqlResult:
    sql: str
    columns: list[str]
    rows: list[dict]
    truncated: bool


def _introspect_schema() -> dict[str, list[str]]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        schema = {}
        for table in sorted(ALLOWED_TABLES):
            cols = con.execute(f"PRAGMA table_info('{table}')").fetchall()
            schema[table] = [c[1] for c in cols]  # column 1 = name
        return schema
    finally:
        con.close()


def describe_schema() -> str:
    """Human-readable marts schema, given to the LLM as context for writing SQL."""
    schema = _introspect_schema()
    lines = [f"{table}({', '.join(cols)})" for table, cols in schema.items()]
    return "\n".join(lines)


def validate_sql(sql: str) -> None:
    lowered = sql.lower()
    for kw in BLOCKED_KEYWORDS:
        if kw in lowered:
            raise SqlGuardrailViolation(f"Query contains a disallowed keyword: '{kw}'.")

    try:
        statements = [s for s in sqlglot.parse(sql, dialect="duckdb") if s is not None]
    except Exception as e:
        raise SqlGuardrailViolation(f"Could not parse SQL: {e}") from e

    if len(statements) != 1:
        raise SqlGuardrailViolation("Exactly one SQL statement is allowed.")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise SqlGuardrailViolation("Only SELECT statements are allowed.")

    tables = {t.name.lower() for t in stmt.find_all(exp.Table)}
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        raise SqlGuardrailViolation(
            f"Query references non-mart tables: {sorted(disallowed)}. Allowed: {sorted(ALLOWED_TABLES)}"
        )


def run_sql(sql: str, limit: int = MAX_EXPLORE_ROWS) -> SqlResult:
    validate_sql(sql)
    row_limit = min(limit, MAX_EXPLORE_ROWS)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    outcome: dict = {}

    def _execute():
        try:
            cursor = con.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(row_limit + 1)
            outcome["columns"] = columns
            outcome["rows"] = rows
        except Exception as e:
            outcome["error"] = e

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()
    thread.join(timeout=QUERY_TIMEOUT_SECONDS)

    if thread.is_alive():
        con.interrupt()
        thread.join(timeout=2)
        con.close()
        raise SqlGuardrailViolation(f"Query exceeded the {QUERY_TIMEOUT_SECONDS}s timeout and was cancelled.")

    con.close()
    if "error" in outcome:
        raise SqlGuardrailViolation(f"Query failed: {outcome['error']}")

    rows = outcome["rows"]
    columns = outcome["columns"]
    truncated = len(rows) > row_limit
    rows = rows[:row_limit]
    return SqlResult(sql=sql, columns=columns, rows=[dict(zip(columns, r, strict=True)) for r in rows], truncated=truncated)
