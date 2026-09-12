"""Programmatic validation script for output.csv in HackerRank Orchestrate September 2026 Buy or Wait challenge."""
import csv
import math
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "output.csv"
REQ_PATH = ROOT / "dataset" / "requests.csv"
OPT_PATH = ROOT / "dataset" / "request_payment_options.csv"

EXPECTED_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation"
]

ALLOWED_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable"
}

ALLOWED_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended"
}

def parse_date(d_str):
    try:
        return datetime.strptime(d_str, "%Y-%m-%d").date()
    except Exception:
        return None

def validate():
    print(f"Validating {OUT_PATH}...")
    if not OUT_PATH.exists():
        print(f"ERROR: {OUT_PATH} does not exist!")
        sys.exit(1)

    with REQ_PATH.open(encoding="utf-8-sig") as f:
        requests = list(csv.DictReader(f))
    req_dict = {r["request_id"]: r for r in requests}

    with OUT_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # A. ROW COUNT
    if len(rows) != 250:
        print(f"ERROR: Expected exactly 250 rows, found {len(rows)}")
        sys.exit(1)
    print("[OK] Row count check passed (exactly 250 rows)")

    # B. COLUMN NAMES & ORDER
    if fieldnames != EXPECTED_COLUMNS:
        print(f"ERROR: Column headers mismatch!\nExpected: {EXPECTED_COLUMNS}\nFound: {fieldnames}")
        sys.exit(1)
    print("[OK] Column names and ordering check passed")

    # C. REQUEST ID CHECK
    output_req_ids = [r["request_id"] for r in rows]
    if len(output_req_ids) != len(set(output_req_ids)):
        print("ERROR: Duplicate request IDs found in output.csv!")
        sys.exit(1)
    if set(output_req_ids) != set(req_dict.keys()):
        print("ERROR: Output request IDs do not match dataset/requests.csv IDs!")
        sys.exit(1)
    print("[OK] Request ID uniqueness and completeness check passed")

    # D. ROW BY ROW VALIDATION
    errors = 0
    for i, r in enumerate(rows, 1):
        req_id = r["request_id"]
        req = req_dict[req_id]
        requested_amt = float(req["requested_amount"])

        # Status check
        if r["affordability_status"] not in ALLOWED_STATUSES:
            print(f"Row {i} ({req_id}): Invalid affordability_status '{r['affordability_status']}'")
            errors += 1

        # Method check
        if r["recommended_payment_method"] not in ALLOWED_METHODS:
            print(f"Row {i} ({req_id}): Invalid recommended_payment_method '{r['recommended_payment_method']}'")
            errors += 1

        # Numeric check amount_safe_to_pay
        try:
            safe_amt = float(r["amount_safe_to_pay"])
            if math.isnan(safe_amt) or math.isinf(safe_amt):
                print(f"Row {i} ({req_id}): amount_safe_to_pay is NaN or Inf")
                errors += 1
            if safe_amt < 0:
                print(f"Row {i} ({req_id}): Negative amount_safe_to_pay '{safe_amt}'")
                errors += 1
            if safe_amt > requested_amt + 0.01:
                print(f"Row {i} ({req_id}): amount_safe_to_pay '{safe_amt}' exceeds requested amount '{requested_amt}'")
                errors += 1
        except ValueError:
            print(f"Row {i} ({req_id}): Non-numeric amount_safe_to_pay '{r['amount_safe_to_pay']}'")
            errors += 1

        # Earliest date check
        earliest_str = r["earliest_date_for_full_payment"]
        if earliest_str:
            if not parse_date(earliest_str):
                print(f"Row {i} ({req_id}): Malformed earliest_date_for_full_payment '{earliest_str}'")
                errors += 1

        # Payment plan check
        plan_str = r["payment_plan"]
        if plan_str != "none":
            items = plan_str.split("|")
            plan_dates = []
            plan_total = 0.0
            for item in items:
                if ":" not in item:
                    print(f"Row {i} ({req_id}): Malformed payment_plan item '{item}'")
                    errors += 1
                    continue
                d_s, a_s = item.split(":", 1)
                d_val = parse_date(d_s)
                if not d_val:
                    print(f"Row {i} ({req_id}): Invalid date in payment_plan '{d_s}'")
                    errors += 1
                else:
                    plan_dates.append(d_val)
                try:
                    plan_total += float(a_s)
                except ValueError:
                    print(f"Row {i} ({req_id}): Invalid amount in payment_plan '{a_s}'")
                    errors += 1

            if plan_dates != sorted(plan_dates):
                print(f"Row {i} ({req_id}): Payment plan dates are not in chronological order")
                errors += 1

            if r["recommended_payment_method"] == "partial_payment":
                if len(items) != 2:
                    print(f"Row {i} ({req_id}): partial_payment requires exactly 2 payments, found {len(items)}")
                    errors += 1

        # Spending changes check
        changes_str = r["spending_changes_needed"]
        if changes_str != "none":
            ch_items = changes_str.split("|")
            if len(ch_items) > 3:
                print(f"Row {i} ({req_id}): Too many spending changes ({len(ch_items)} > 3)")
                errors += 1
            event_ids = set()
            for ch in ch_items:
                if ch.startswith("stop:"):
                    eid = ch.split(":")[1]
                elif ch.startswith("reduce_to:"):
                    eid = ch.split(":")[1]
                else:
                    print(f"Row {i} ({req_id}): Invalid spending change format '{ch}'")
                    errors += 1
                    continue
                if eid in event_ids:
                    print(f"Row {i} ({req_id}): Duplicate event_id in spending changes '{eid}'")
                    errors += 1
                event_ids.add(eid)

        # Decision explanation check
        if not r["decision_explanation"].strip():
            print(f"Row {i} ({req_id}): Empty decision_explanation")
            errors += 1

    if errors > 0:
        print(f"\n❌ Validation failed with {errors} errors.")
        sys.exit(1)
    else:
        print("[OK] All 250 requests passed strict structural & financial validation!")

if __name__ == "__main__":
    validate()
