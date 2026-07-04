"""
Generates a synthetic Contoso-style retail sales star schema, with
deliberately injected imperfections that a real sales warehouse would have.

This is NOT meant to be perfectly clean data — the messiness is the point.
It's what makes the semantic layer and guardrails in this project meaningful
rather than decorative.

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

REGIONS_V1 = ["North America", "EMEA", "APAC", "LATAM"]
# Mid-year region hierarchy change: APAC gets split into two sub-regions
REGIONS_V2 = ["North America", "EMEA", "APAC-North", "APAC-South", "LATAM"]

PRODUCT_CATEGORIES = ["Electronics", "Home & Garden", "Apparel", "Sporting Goods", "Office Supplies"]


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


def gen_dim_region(mid_year_change: bool) -> list[dict]:
    """Two 'versions' of the region dimension to simulate a hierarchy change mid-year.
    Rows are timestamped with valid_from so downstream models must handle this explicitly."""
    rows = []
    for r in REGIONS_V1:
        rows.append({"region_key": md5_key("v1", r), "region_name": r, "valid_from": "2024-01-01", "valid_to": "2024-06-30" if mid_year_change else None})
    if mid_year_change:
        for r in REGIONS_V2:
            rows.append({"region_key": md5_key("v2", r), "region_name": r, "valid_from": "2024-07-01", "valid_to": None})
    return rows


def gen_fact_sales(
    n: int,
    customers: list[dict],
    products: list[dict],
    dates: list[dict],
    late_arriving_rate: float = 0.02,
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
            from datetime import date as _date
            base = _date.fromisoformat(d["date_key"])
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

    n_customers, n_products, n_sales = (200, 60, 5000) if args.scale == "small" else (2000, 300, 50000)

    dates = gen_dim_date(date(2024, 1, 1), date(2024, 12, 31))
    customers = gen_dim_customer(n_customers)
    products = gen_dim_product(n_products)
    regions = gen_dim_region(mid_year_change=True)
    sales = gen_fact_sales(n_sales, customers, products, dates)
    targets = gen_fact_target(dates, regions)

    write_csv(dates, "dim_date")
    write_csv(customers, "dim_customer")
    write_csv(products, "dim_product")
    write_csv(regions, "dim_region")
    write_csv(sales, "fact_sales")
    write_csv(targets, "fact_target")

    print("\nInjected imperfections to test against:")
    print("  - duplicate customers under different CUST-IDs (dim_customer)")
    print("  - gross_revenue vs net_revenue distinction (fact_sales)")
    print("  - late-arriving facts: transaction date_key vs load_date differ (fact_sales)")
    print("  - mid-year region hierarchy change: APAC -> APAC-North/APAC-South (dim_region)")


if __name__ == "__main__":
    main()
