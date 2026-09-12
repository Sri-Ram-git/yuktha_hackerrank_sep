"""Compare deterministic predictions with public sample labels (not used by main)."""
import csv
from pathlib import Path
from main import Engine, read_csv, num

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["amount_safe_to_pay", "affordability_status", "recommended_payment_method",
          "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed"]

def main():
    engine = Engine(); report = []
    traces = []
    snapshots = []
    for expected in read_csv("sample_requests.csv"):
        got = engine.generate(expected)
        request_date = __import__('main').dt(expected['request_date'])
        profile = engine.profiles[expected['user_id']]
        patterns = engine.patterns(expected['user_id'], request_date, profile['home_currency'])
        flows = engine.flows(expected['user_id'], request_date)
        direct = []
        excluded = []
        for e in engine.by_user[expected['user_id']]:
            if request_date <= e['date'] <= request_date + __import__('datetime').timedelta(days=90):
                if e['status'] in {'failed','cancelled','unrealized'} or not e['amount'] or (e['direction']=='credit' and e['status']=='pending') or e['event_type']=='investment_valuation':
                    excluded.append(f"{e['event_id']}:{e['status']}:{e['event_type']}")
                else: direct.append(f"{e['date']}:{e['event_id']}:{e['direction']}:{e['amount']}")
        traces.append({'request_id': expected['request_id'], 'request_date': expected['request_date'],
          'balance': profile['current_available_balance'], 'minimum': profile['minimum_balance_to_keep'],
          'requested': expected['requested_amount'], 'expected_safe': expected['amount_safe_to_pay'],
          'calculated_safe': got['amount_safe_to_pay'], 'future_cashflows': '|'.join(f'{d}:{v:.2f}' for d,v in sorted(flows.items())),
          'direct_future_events': '|'.join(direct), 'recurring': '|'.join(f"{x['event_id']}:{x['description']}:{'monthly' if x['monthly'] else str(x['gap'])+'d'}" for x in patterns),
          'excluded_future_events': '|'.join(excluded) or 'none'})
        nearby=[]
        for e in engine.by_user[expected['user_id']]:
            if abs((e['date']-request_date).days) <= 35:
                state = 'historical' if e['date'] < request_date else ('current' if e['date'] == request_date else 'future')
                included = e['date'] >= request_date and e['status'] not in {'failed','cancelled','unrealized'} and bool(e['amount']) and not (e['direction']=='credit' and e['status']=='pending')
                nearby.append(f"{e['date']}:{e['event_id']}:{state}:{'included' if included else 'excluded'}:{e['status']}:{e['direction']}:{e['amount']}:{e['event_type']}/{e['category']}:{e['description']}")
        before=[e for e in engine.by_user[expected['user_id']] if e['date'] <= request_date]
        after=[e for e in engine.by_user[expected['user_id']] if e['date'] > request_date]
        snapshots.append({'user_id':expected['user_id'],'request_id':expected['request_id'],'request_date':expected['request_date'],
          'current_balance':profile['current_available_balance'],'minimum_balance':profile['minimum_balance_to_keep'],
          'latest_event_on_or_before':f"{before[-1]['date']}:{before[-1]['event_id']}" if before else 'none',
          'earliest_event_after':f"{after[0]['date']}:{after[0]['event_id']}" if after else 'none',
          'near_request_events':' | '.join(nearby),'projected_balance_before_purchase':f"{num(profile['current_available_balance']) + sum(flows.values()):.2f}",
          'expected_safe':expected['amount_safe_to_pay'],'calculated_safe':got['amount_safe_to_pay']})
        row = {"request_id": expected["request_id"]}
        for field in FIELDS:
            row[field] = "MATCH" if got[field] == expected[field] else f"expected={expected[field]} | got={got[field]}"
        report.append(row)
    target = ROOT / "evaluation" / "sample_diagnostics.csv"
    target.parent.mkdir(exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["request_id"] + FIELDS); w.writeheader(); w.writerows(report)
    trace_target = ROOT / "evaluation" / "sample_cashflow_trace.csv"
    with trace_target.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=traces[0].keys()); w.writeheader(); w.writerows(traces)
    snapshot_target = ROOT / "evaluation" / "sample_snapshot_trace.csv"
    with snapshot_target.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=snapshots[0].keys()); w.writeheader(); w.writerows(snapshots)
    matches = sum(all(r[f] == "MATCH" for f in FIELDS) for r in report)
    print(f"{matches}/{len(report)} samples exactly matched; report: {target}; trace: {trace_target}; snapshot: {snapshot_target}")

if __name__ == "__main__": main()
