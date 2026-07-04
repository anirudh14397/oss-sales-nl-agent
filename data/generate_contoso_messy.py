"""
Generates a synthetic Contoso-style retail sales star schema, with
deliberately injected imperfections that a real sales warehouse would have.

This is NOT meant to be perfectly clean data — the messiness is the point.
It's what makes the semantic layer and guardrails in this project meaningful
rather than decorative.

Spans DATASET_START through DATASET_END (5 calendar years by default).
fact_sales carries two distinct kinds of messiness, handled at different
layers:
- Structural messiness (customer dedup, region hierarchy, late-arriving
  facts) — handled by marts/ transformations, described in
  docs/failure_cases.md.
- Row-level data-quality anomalies (ANOMALY_RATE of rows: null/orphaned
  foreign keys, negative quantity/revenue, net > gross, plus a small rate of
  outright duplicate sales_key rows) — deliberately NOT cleaned here. These
  flow into the warehouse as real bad data and are caught downstream by
  dbt/models/intermediate/int_fact_sales_validated.sql, which quarantines
  them into marts/fct_sales_quarantine instead of either silently including
  them or crashing the build. See docs/failure_cases.md §9.

Output: CSV files under data/seed/, small enough to commit to git.

Usage:
    python data/generate_contoso_messy.py --scale small
"""

import argparse
import hashlib
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

OUT_DIR = Path(__file__).parent / "seed"

DATASET_START = date(2020, 1, 1)
DATASET_END = date(2024, 12, 31)
REGION_SPLIT_DATE = date(2024, 7, 1)  # mid-year hierarchy change, in the dataset's final year

REGIONS_V1 = ["North America", "EMEA", "APAC", "LATAM"]
# Region hierarchy change on REGION_SPLIT_DATE: APAC gets split into two sub-regions
REGIONS_V2 = ["North America", "EMEA", "APAC-North", "APAC-South", "LATAM"]

PRODUCT_CATEGORIES = ["Electronics", "Home & Garden", "Apparel", "Sporting Goods", "Office Supplies"]

# Row-level data-quality anomalies injected into fact_sales — see module
# docstring. Each anomaly type is roughly equally likely among corrupted rows.
ANOMALY_RATE = 0.03
DUPLICATE_KEY_RATE = 0.005


def md5_key(*parts: str) -> str:
    """Surrogate key generation, matching the MD5 pattern used in the real Fabric lakehouse."""
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def gen_dim_date(start: date, end: date) -> list[dict]:
    rows = []
    d = start
    while d <= end:
        rows.append({
            "date_key": d.isoformat(),
            "year": d.year,
            "quarter": (d.month - 1) // 3 + 1,
            "month": d.month,
            "month_name": d.strftime("%B"),
            "day": d.day,
            "fiscal_year": d.year if d.month >= 4 else d.year - 1,  # fiscal year starts April
        })
        d += timedelta(days=1)
    return rows


def gen_dim_customer(n: int, dup_rate: float = 0.03) -> list[dict]:
    rows = []
    for i in range(n):
        customer_id = f"CUST-{i:05d}"
        name = fake.company()
        rows.append({
            "customer_key": md5_key(customer_id),
            "customer_id": customer_id,
            "customer_name": name,
            "segment": random.choice(["Enterprise", "Mid-Market", "SMB"]),
            # Customer's home region, named per the REGIONS_V1 hierarchy (the
            # hierarchy in effect at signup time). Sales facts derive their
            # region_key from this at query time in marts, applying the
            # mid-year APAC split for transactions after the cutover.
            "region_name_v1": random.choice(REGIONS_V1),
            "signup_date": fake.date_between(start_date="-4y", end_date="-1y").isoformat(),
        })
    # Inject duplicate customers under slightly different IDs (common real-world mess:
    # same company entered twice by different sales reps, e.g. trailing whitespace,
    # "Corp" vs "Corporation", or a re-registration after a CRM migration)
    n_dupes = max(1, int(n * dup_rate))
    for i in range(n_dupes):
        original = random.choice(rows[: n])
        dupe_id = f"CUST-{n + i:05d}"
        rows.append({
            "customer_key": md5_key(dupe_id),
            "customer_id": dupe_id,
            "customer_name": original["customer_name"] + random.choice([" Corp", " Corporation", " Inc", ""]),
            "segment": original["segment"],
            "region_name_v1": original["region_name_v1"],
            "signup_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
        })
    return rows


def gen_dim_product(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        product_id = f"PROD-{i:04d}"
        rows.append({
            "product_key": md5_key(product_id),
            "product_id": product_id,
            "product_name": fake.catch_phrase(),
            "category": random.choice(PRODUCT_CATEGORIES),
            "unit_cost": round(random.uniform(5, 500), 2),
        })
    return rows


def gen_dim_region(start: date, split_date: date, mid_year_change: bool) -> list[dict]:
    """Two 'versions' of the region dimension to simulate a hierarchy change partway
    through the dataset. Rows are timestamped with valid_from so downstream models
    must handle this explicitly.

    valid_from for the v1 rows must match the dataset's actual start date, not be
    hardcoded — otherwise every sale before whatever date is hardcoded here would
    silently fail the date-range join in marts/fct_sales.sql (a bug this project
    hit for real when the date range was extended; see docs/failure_cases.md §8/§9
    for other cases of the same underlying lesson: a workaround that isn't the
    actual fix stays latent until something exposes it)."""
    rows = []
    valid_to = (split_date - timedelta(days=1)).isoformat() if mid_year_change else None
    for r in REGIONS_V1:
        rows.append({"region_key": md5_key("v1", r), "region_name": r, "valid_from": start.isoformat(), "valid_to": valid_to})
    if mid_year_change:
        for r in REGIONS_V2:
            rows.append({"region_key": md5_key("v2", r), "region_name": r, "valid_from": split_date.isoformat(), "valid_to": None})
    return rows


def _corrupt_null_customer_key(row: dict, **_) -> None:
    row["customer_key"] = None


def _corrupt_null_product_key(row: dict, **_) -> None:
    row["product_key"] = None


def _corrupt_orphan_customer_key(row: dict, i: int, **_) -> None:
    # A fabricated key guaranteed not to exist in dim_customer — simulates a
    # mistyped ID or a customer record deleted after the sale was recorded.
    row["customer_key"] = md5_key("orphan-customer", i)


def _corrupt_orphan_product_key(row: dict, i: int, **_) -> None:
    row["product_key"] = md5_key("orphan-product", i)


def _corrupt_negative_quantity(row: dict, **_) -> None:
    row["quantity"] = -abs(row["quantity"])


def _corrupt_negative_gross_revenue(row: dict, **_) -> None:
    row["gross_revenue"] = -abs(row["gross_revenue"])


def _corrupt_net_exceeds_gross(row: dict, **_) -> None:
    # Simulates a returns_amount calculation bug (e.g. a sign error) that lets
    # net revenue exceed gross — should never happen if returns_amount >= 0.
    row["returns_amount"] = round(-abs(row["gross_revenue"]) * 0.2, 2)
    row["net_revenue"] = round(row["gross_revenue"] - row["returns_amount"], 2)


ANOMALY_TYPES = [
    _corrupt_null_customer_key,
    _corrupt_null_product_key,
    _corrupt_orphan_customer_key,
    _corrupt_orphan_product_key,
    _corrupt_negative_quantity,
    _corrupt_negative_gross_revenue,
    _corrupt_net_exceeds_gross,
]


def gen_fact_sales(
    n: int,
    customers: list[dict],
    products: list[dict],
    dates: list[dict],
    late_arriving_rate: float = 0.02,
    anomaly_rate: float = ANOMALY_RATE,
    duplicate_key_rate: float = DUPLICATE_KEY_RATE,
) -> list[dict]:
    rows = []
    for i in range(n):
        cust = random.choice(customers)
        prod = random.choice(products)
        d = random.choice(dates)
        qty = random.randint(1, 50)
        gross_amount = round(qty * prod["unit_cost"] * random.uniform(1.2, 2.0), 2)
        return_rate = random.choices([0, round(random.uniform(0.01, 0.15), 3)], weights=[0.85, 0.15])[0]
        returns_amount = round(gross_amount * return_rate, 2)

        # Late-arriving facts: the recorded transaction_date is in the past quarter,
        # but the load_date (when it landed in the warehouse) is a period later.
        # This is what breaks naive "as of load date" reporting.
        is_late = random.random() < late_arriving_rate
        load_date = d["date_key"]
        if is_late:
            base = date.fromisoformat(d["date_key"])
            load_date = (base + timedelta(days=random.randint(35, 70))).isoformat()

        rows.append({
            "sales_key": md5_key("sale", i),
            "customer_key": cust["customer_key"],
            "product_key": prod["product_key"],
            "date_key": d["date_key"],
            "load_date": load_date,
            "quantity": qty,
            "gross_revenue": gross_amount,
            "returns_amount": returns_amount,
            "net_revenue": round(gross_amount - returns_amount, 2),
        })

    # Row-level data-quality anomalies — deliberately NOT cleaned here. See
    # module docstring; caught downstream by int_fact_sales_validated.sql.
    for i, row in enumerate(rows):
        if random.random() < anomaly_rate:
            corrupt = random.choice(ANOMALY_TYPES)
            corrupt(row, i=i)

    # Duplicate sales_key rows: simulates an ETL replay/reprocessing bug that
    # re-inserted a record under the same primary key with different values.
    n_dupes = max(1, int(n * duplicate_key_rate))
    for _ in range(n_dupes):
        original = random.choice(rows[:n])  # only duplicate originally-clean keys
        dupe = dict(original)
        dupe["quantity"] = random.randint(1, 50)
        dupe["gross_revenue"] = round(dupe["quantity"] * random.uniform(10, 200), 2)
        dupe["net_revenue"] = dupe["gross_revenue"]
        dupe["returns_amount"] = 0
        rows.append(dupe)  # same sales_key as `original` — the point of this anomaly

    return rows


def gen_fact_target(dates: list[dict], regions: list[dict]) -> list[dict]:
    rows = []
    seen_quarters = sorted({(d["year"], d["quarter"]) for d in dates})
    for year, quarter in seen_quarters:
        for r in regions:
            if r["valid_to"] is not None and f"{year}-{quarter*3:02d}-01" > r["valid_to"]:
                continue
            rows.append({
                "target_key": md5_key("target", year, quarter, r["region_key"]),
                "region_key": r["region_key"],
                "year": year,
                "quarter": quarter,
                "target_revenue": round(random.uniform(500_000, 2_000_000), 2),
            })
    return rows


def write_csv(rows: list[dict], name: str):
    import csv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows):>6} rows -> {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["small", "medium"], default="small")
    args = parser.parse_args()

    # 5 years of history (DATASET_START..DATASET_END) instead of 1 — volumes
    # scaled up accordingly, not just left at the old single-year counts.
    n_customers, n_products, n_sales = (400, 100, 25_000) if args.scale == "small" else (3000, 400, 250_000)

    dates = gen_dim_date(DATASET_START, DATASET_END)
    customers = gen_dim_customer(n_customers)
    products = gen_dim_product(n_products)
    regions = gen_dim_region(DATASET_START, REGION_SPLIT_DATE, mid_year_change=True)
    sales = gen_fact_sales(n_sales, customers, products, dates)
    targets = gen_fact_target(dates, regions)

    # Prefixed raw_ so these seed node names never collide with the dbt mart
    # models of the same conceptual name (dbt resource names are global —
    # a seed and a model can't share one without ambiguous ref() resolution).
    write_csv(dates, "raw_dim_date")
    write_csv(customers, "raw_dim_customer")
    write_csv(products, "raw_dim_product")
    write_csv(regions, "raw_dim_region")
    write_csv(sales, "raw_fact_sales")
    write_csv(targets, "raw_fact_target")

    print(f"\nDataset spans {DATASET_START} through {DATASET_END} ({DATASET_END.year - DATASET_START.year + 1} years).")
    print("\nStructural messiness (handled in marts/, see docs/failure_cases.md):")
    print("  - duplicate customers under different CUST-IDs (dim_customer)")
    print("  - gross_revenue vs net_revenue distinction (fact_sales)")
    print("  - late-arriving facts: transaction date_key vs load_date differ (fact_sales)")
    print(f"  - region hierarchy change on {REGION_SPLIT_DATE}: APAC -> APAC-North/APAC-South (dim_region)")
    print("\nRow-level anomalies, NOT cleaned here — quarantined downstream by")
    print("dbt/models/intermediate/int_fact_sales_validated.sql (see docs/failure_cases.md §9):")
    print(f"  - ~{ANOMALY_RATE:.0%} of fact_sales rows: null/orphaned FKs, negative quantity/revenue, net > gross")
    print(f"  - ~{DUPLICATE_KEY_RATE:.1%} of fact_sales rows: duplicate sales_key (simulated ETL replay)")


if __name__ == "__main__":
    main()
