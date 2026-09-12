"""Deterministic Buy or Wait financial decision engine.

Run from the repository root: ``python code/main.py``.  It uses only the
participant-facing dataset and writes ``output.csv`` at the repository root.
"""
from __future__ import annotations

import csv
import calendar
import itertools
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
OUT_COLUMNS = ["request_id", "amount_safe_to_pay", "affordability_status",
               "recommended_payment_method", "payment_plan",
               "earliest_date_for_full_payment", "spending_changes_needed",
               "decision_explanation"]
IMAGE_AMOUNTS = { # transcribed from the supplied evidence; labels beat line items
    "event_253": 4365000, "event_1442": 100000, "event_1545": 41272,
    "event_1700": 2854, "event_1786": 704.05, "event_3051": 1995,
    "event_3231": 8528.10, "event_4535": 15339, "event_5170": 723,
    "event_6033": 79679.26, "event_6859": 3650, "event_7307": 33.50,
    "event_7941": 2298, "event_9421": 4543, "event_9806": 9968,
    "event_10521": 393.22,
}

def read_csv(name):
    with (DATA / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def dt(value): return datetime.strptime(value[:10], "%Y-%m-%d").date()
def num(value): return float(value) if value not in (None, "") else 0.0
def parts(value): return set(filter(None, (value or "").split("|")))
def money(v):
    v = round(v + 0.00000001, 2)
    return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")

def add_months(value, months=1):
    """Calendar monthly recurrence retaining the original day where possible."""
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))

class Engine:
    def __init__(self):
        self.profiles = {r["user_id"]: r for r in read_csv("financial_profiles.csv")}
        self.events = read_csv("financial_events.csv")
        self.messages = read_csv("messages.csv")
        self.options = read_csv("request_payment_options.csv")
        self.rates = read_csv("exchange_rates.csv")
        self.by_user = defaultdict(list); self.msg_user = defaultdict(list); self.opt_req = defaultdict(list)
        self.rate = {}
        for r in self.rates: self.rate[(r["rate_date"], r["from_currency"], r["to_currency"])] = num(r["rate"])
        for r in self.events:
            r["date"] = dt(r["settlement_date"] or r["event_date"])
            if not r["amount"] and r["event_id"] in IMAGE_AMOUNTS: r["amount"] = str(IMAGE_AMOUNTS[r["event_id"]])
            self.by_user[r["user_id"]].append(r)
        for xs in self.by_user.values(): xs.sort(key=lambda x: x["date"])
        for r in self.messages: self.msg_user[r["user_id"]].append(r)
        for r in self.options: self.opt_req[r["request_id"]].append(r)

    def convert(self, event, home):
        amount = num(event["amount"])
        cur = event["currency"]
        if cur == home: return amount
        key = (event["settlement_date"] or event["event_date"], cur, home)
        if key in self.rate: return amount * self.rate[key]
        # Fixed rates are normally available at the settlement date.  Do not
        # silently use a live rate; a same-pair dated rate is a conservative fallback.
        candidates = [(d, v) for (d, a, b), v in self.rate.items() if a == cur and b == home and d <= key[0]]
        return amount * max(candidates, default=[("", 1)])[1]

    def amendments(self, user, request_date, home):
        """Extract factual payroll amendments, never execute embedded instructions."""
        result = {"salary": None, "salary_from": None, "salary_date": None, "end_salary": False,
                  "rent_raise": False}
        for m in self.msg_user[user]:
            text = m["message_text"].replace(",", "")
            lower = text.lower()
            # English/Indonesian messages use ISO dates and currency/number wording.
            amounts = re.findall(r"(?:IDR|INR|EUR|USD|ZAR|Rp|Rs)\s*([0-9]+(?:\.\d+)?)", text, re.I)
            dates = re.findall(r"\b(20\d\d-\d\d-\d\d)\b", text)
            if ("salary" in lower or "gaji" in lower) and amounts:
                if any(k in lower for k in ("increased", "naik", "reduced", "dikurangi", "temporary", "first salary", "gaji pertama", "confirmed base", "remaining confirmed", "regular salary")):
                    value = float(amounts[0]); currency = re.search(r"(IDR|INR|EUR|USD|ZAR)", text, re.I)
                    if currency and currency.group(1).upper() != home:
                        fake = {"amount": value, "currency": currency.group(1).upper(), "settlement_date": dates[0] if dates else request_date.isoformat(), "event_date": dates[0] if dates else request_date.isoformat()}
                        value = self.convert(fake, home)
                    result["salary"] = value
                    if dates: result["salary_from"] = dt(dates[-1])
            if "confirmed salary is now expected on" in lower or "gaji yang sudah dikonfirmasi kini" in lower:
                if dates: result["salary_date"] = dt(dates[-1])
            if any(k in lower for k in ("employment has ended", "contract has ended", "pendapatan kerja rumah tangga telah berakhir")):
                result["end_salary"] = True
            if "rent" in lower and ("12%" in lower or "12 %" in lower): result["rent_raise"] = True
        return result

    def patterns(self, user, request_date, home):
        """Return conservative recurring event templates supported by >=2 observations."""
        evs = self.by_user[user]
        groups = defaultdict(list)
        for e in evs:
            if e["date"] >= request_date or e["status"] not in ("settled", "scheduled"): continue
            if e["event_type"] in ("investment_valuation", "investment_purchase", "investment_sale", "refund"): continue
            if e["direction"] not in ("credit", "debit") or not e["amount"]: continue
            groups[(e["description"], e["direction"], e["category"], e["event_type"])].append(e)
        ans = []
        structural = {"rent", "utilities", "debt_repayment", "insurance", "education", "housing", "family_support", "healthcare"}
        variable = {"groceries", "dining", "shopping", "transport", "entertainment"}
        income_exclusions = ("commission", "bonus", "app", "platform", "marketplace", "freelance", "contract", "final", "prorated", "refund", "prize")
        for key, xs in groups.items():
            if len(xs) < 2: continue
            xs.sort(key=lambda x:x["date"])
            gaps = [(b["date"]-a["date"]).days for a,b in zip(xs, xs[1:])]
            gap = int(median(gaps)) if gaps else 0
            # weekly or monthly cadence only; tolerate month length and sparse histories.
            # A pair is enough to establish a monthly bill, while noisier
            # weekly spending needs three observations before it is forecast.
            if not (25 <= gap <= 35 or (5 <= gap <= 9 and len(xs) >= 3)): continue
            description, direction, category, event_type = key
            description_l = description.lower()
            # Historical variable purchases are evidence of past consumption,
            # not a new fixed obligation.  A subscription/contractual category
            # is structurally recurring; other variable categories need an
            # explicit scheduled/pending future row instead.
            if direction == "debit" and category in variable and event_type != "subscription":
                continue
            if direction == "debit" and category not in structural and event_type not in {"subscription", "debt_payment"}:
                continue
            # Only named base-pay payroll is recurring income.  This excludes
            # commissions, bonuses, platform/app payouts, final settlements,
            # and refunds even where old dates happen to repeat.
            if direction == "credit":
                if category != "salary" or not any(w in description_l for w in ("salary", "payroll", "wage")) or any(w in description_l for w in income_exclusions):
                    continue
                if len(xs) < 3: continue
            vals = [self.convert(x, home) for x in xs[-4:]]
            amount = max(vals) if key[1] == "debit" else vals[-1]
            ans.append({"event_id": xs[-1]["event_id"], "description": key[0], "direction": key[1],
                        "category": key[2], "event_type": key[3], "amount": amount,
                        "gap": gap, "monthly": 25 <= gap <= 35,
                        "next": add_months(xs[-1]["date"]) if 25 <= gap <= 35 else xs[-1]["date"] + timedelta(days=gap),
                        "flexibility": xs[-1]["flexibility"], "minimum": num(xs[-1]["minimum_allowed_amount"])})
        return ans

    def flows(self, user, request_date, changes=()):
        profile = self.profiles[user]; home = profile["home_currency"]; end = request_date + timedelta(days=90)
        amend = self.amendments(user, request_date, home); result = defaultdict(float); future_dates = set()
        # Future supplied cash events are authoritative. Pending credit/refund and unrealized values are excluded.
        for e in self.by_user[user]:
            if not (request_date <= e["date"] <= end): continue
            if e["status"] in ("failed", "cancelled", "unrealized"): continue
            if e["event_type"] == "investment_valuation" or not e["amount"]: continue
            if e["direction"] == "credit" and e["status"] == "pending": continue
            if e["direction"] == "credit" and e["event_type"] in ("refund",) and e["status"] != "settled": continue
            value = self.convert(e, home)
            result[e["date"]] += value if e["direction"] == "credit" else -value
            future_dates.add((e["description"], e["date"]))
        for p in self.patterns(user, request_date, home):
            if amend["end_salary"] and p["category"] == "salary": continue
            amount = p["amount"]
            if p["category"] == "salary" and amend["salary"] and (not amend["salary_from"] or p["next"] >= amend["salary_from"]): amount = amend["salary"]
            if p["category"] == "rent" and amend["rent_raise"]: amount *= 1.12
            action = next((a for a in changes if a[0] == p["event_id"]), None)
            if action and p["direction"] == "debit":
                if action[1] == "stop": continue
                amount = action[2]
            # A supplied post-request row is authoritative for that named
            # obligation/source; do not manufacture a second recurrence.
            if any(e["date"] >= request_date and e["description"] == p["description"] for e in self.by_user[user]):
                continue
            d = amend["salary_date"] if p["category"] == "salary" and amend["salary_date"] else p["next"]
            # A historical cadence may have its calculated next occurrence in
            # the past. Advance it before adding forecast cashflows; the
            # profile balance is already the position on request_date.
            def advance(value):
                return add_months(value) if p["monthly"] else value + timedelta(days=p["gap"])
            while d < request_date:
                d = advance(d)
            while d <= end:
                # Do not duplicate a supplied future row with the same description/date.
                if (p["description"], d) not in future_dates:
                    result[d] += amount if p["direction"] == "credit" else -amount
                d = advance(d)
        return result

    def simulate(self, user, request_date, payments=(), changes=()):
        p = self.profiles[user]; balance = num(p["current_available_balance"]); floor = num(p["minimum_balance_to_keep"])
        flows = self.flows(user, request_date, changes)
        for d, a in payments: flows[d] -= a
        low = math.inf
        for d in sorted(flows):
            balance += flows[d]; low = min(low, balance)
        return low >= floor - .011, low, balance

    def safe_amount(self, user, request_date, requested):
        p = self.profiles[user]; floor = num(p["minimum_balance_to_keep"]); balance = num(p["current_available_balance"])
        flows = self.flows(user, request_date); low = balance
        for d in sorted(flows): low = min(low, (balance := balance + flows[d]))
        return max(0, min(requested, low - floor))

    def earliest_full(self, user, request_date, requested):
        for i in range(91):
            d = request_date + timedelta(days=i)
            if self.simulate(user, request_date, [(d, requested)])[0]: return d
        return None

    def change_sets(self, user, request_date):
        p = self.profiles[user]; reduce = parts(p["expense_categories_user_is_willing_to_reduce"]); stop = parts(p["expense_categories_user_is_willing_to_stop"])
        candidates=[]
        for pat in self.patterns(user, request_date, p["home_currency"]):
            if pat["direction"] != "debit" or pat["flexibility"] not in {"stoppable", "reducible", "reducible_or_stoppable"}: continue
            if pat["category"] in stop and pat["flexibility"] in {"stoppable", "reducible_or_stoppable"}: candidates.append((pat["event_id"], "stop", 0.0))
            if pat["category"] in reduce and pat["flexibility"] in {"reducible", "reducible_or_stoppable"} and pat["minimum"] < pat["amount"]: candidates.append((pat["event_id"], "reduce", pat["minimum"]))
        result=[]
        for n in range(1, min(3,len(candidates))+1):
            for combo in itertools.combinations(candidates,n):
                if len({x[0] for x in combo}) == n: result.append(combo)
        return result

    def option_schedule(self, option):
        first=dt(option["first_payment_date"]); count=int(option["number_of_payments"]); gap=int(option["payment_frequency_days"] or 0); amount=num(option["payment_amount"])
        return [(first + timedelta(days=i*gap), amount) for i in range(count)]

    def generate(self, r):
        user=r["user_id"]; p=self.profiles[user]; request_date=dt(r["request_date"]); desired=dt(r["desired_completion_date"]); requested=num(r["requested_amount"])
        allowed=parts(p["payment_methods_user_will_consider"]); safe=self.safe_amount(user, request_date, requested); earliest=self.earliest_full(user,request_date,requested)
        candidates=[] # (rank tuple, method, payments, changes, total, optionid)
        def add(method, payments, changes=(), optionid=""):
            if payments[-1][0] > desired or not self.simulate(user,request_date,payments,changes)[0]: return
            total=sum(x[1] for x in payments)
            candidates.append(((bool(changes), total, payments[0][0], len(payments), optionid),method,payments,changes,total,optionid))
        if "full_payment" in allowed: add("full_payment", [(request_date, requested)])
        if r["allows_partial_payment"].lower()=="true" and "partial_payment" in allowed and 0 < safe < requested and earliest and earliest <= desired:
            add("partial_payment", [(request_date,safe),(earliest,requested-safe)])
        for o in self.opt_req[r["request_id"]]:
            if o["payment_method"] != "installments" or "installments" not in allowed: continue
            months=int(p["max_installment_months"] or 0)
            if months and int(o["number_of_payments"]) > months: continue
            add("installments", self.option_schedule(o), (), o["payment_option_id"])
        # Spending changes can make a full or option plan eligible.  Search all legal small combinations.
        for changes in self.change_sets(user, request_date):
            if "full_payment" in allowed: add("full_payment", [(request_date,requested)],changes)
            for o in self.opt_req[r["request_id"]]:
                if o["payment_method"] == "installments" and "installments" in allowed:
                    months=int(p["max_installment_months"] or 0)
                    if not months or int(o["number_of_payments"]) <= months: add("installments",self.option_schedule(o),changes,o["payment_option_id"])
        if candidates:
            _,method,payments,changes,total,_ = min(candidates, key=lambda x:x[0])
            status="affordable_now" if method=="full_payment" and not changes and payments[0][0]==request_date else "affordable_with_plan"
            return self.row(r,safe,status,method,payments,earliest,changes)
        if earliest and earliest <= desired and "full_payment" in allowed:
            return self.row(r,safe,"affordable_later","wait",[(earliest,requested)],earliest,())
        return self.row(r,safe,"not_affordable","not_recommended",[],earliest,())

    def row(self,r,safe,status,method,payments,earliest,changes):
        p=self.profiles[r["user_id"]]; cur=p["home_currency"]; floor=money(num(p["minimum_balance_to_keep"])); requested=money(num(r["requested_amount"]))
        plan="|".join(f"{d.isoformat()}:{money(a)}" for d,a in payments) if payments else "none"
        ch=[]
        for eid,kind,value in changes: ch.append(f"stop:{eid}" if kind=="stop" else f"reduce_to:{eid}:{money(value)}")
        if method=="not_recommended": explanation=f"{cur} {requested} is not safe within 90 days while preserving the {cur} {floor} minimum balance."
        elif method=="wait": explanation=f"Wait until {earliest.isoformat()} to pay {cur} {requested}; earlier payment would breach the {cur} {floor} minimum."
        elif method=="installments": explanation=f"Use the supplied installment schedule for {cur} {requested}; the forecast keeps at least the {cur} {floor} minimum."
        elif changes: explanation=f"Pay {cur} {requested} using the listed flexible-spending changes while preserving the {cur} {floor} minimum."
        else: explanation=f"Pay {cur} {requested} as scheduled; the 90-day forecast keeps at least the {cur} {floor} minimum."
        return {"request_id":r["request_id"],"amount_safe_to_pay":money(max(0,min(num(r["requested_amount"]),safe))),"affordability_status":status,"recommended_payment_method":method,"payment_plan":plan,"earliest_date_for_full_payment":earliest.isoformat() if earliest else "","spending_changes_needed":"|".join(ch) if ch else "none","decision_explanation":explanation}

def validate(rows, requests, engine=None):
    assert len(rows)==len(requests)==250 and [*rows[0]]==OUT_COLUMNS
    req={r["request_id"]:r for r in requests}; assert len({r["request_id"] for r in rows})==250
    for row in rows:
        assert row["request_id"] in req and 0 <= num(row["amount_safe_to_pay"]) <= num(req[row["request_id"]]["requested_amount"])
        assert row["affordability_status"] in {"affordable_now","affordable_with_plan","affordable_later","not_affordable"}
        assert row["recommended_payment_method"] in {"full_payment","partial_payment","installments","wait","not_recommended"}
        if row["payment_plan"] != "none":
            payments=[]
            for item in row["payment_plan"].split("|"):
                d, amount = item.split(":"); payments.append((dt(d),num(amount)))
            assert [x[0] for x in payments] == sorted(x[0] for x in payments)
            assert payments[-1][0] <= dt(req[row["request_id"]]["desired_completion_date"])
            if row["recommended_payment_method"] in {"full_payment", "partial_payment", "wait"}:
                assert abs(sum(x[1] for x in payments)-num(req[row["request_id"]]["requested_amount"])) < .02
            if row["recommended_payment_method"] == "partial_payment": assert len(payments)==2
            if engine:
                assert engine.simulate(req[row["request_id"]]["user_id"], dt(req[row["request_id"]]["request_date"]), payments)[0] or row["spending_changes_needed"] != "none"

def main():
    engine=Engine(); requests=read_csv("requests.csv"); rows=[engine.generate(r) for r in requests]; validate(rows,requests,engine)
    with (ROOT/"output.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=OUT_COLUMNS); w.writeheader(); w.writerows(rows)
    print(f"Wrote {len(rows)} validated predictions to {ROOT/'output.csv'}")

if __name__ == "__main__": main()
