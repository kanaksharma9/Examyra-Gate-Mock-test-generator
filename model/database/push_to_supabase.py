#!/usr/bin/env python3
"""
push_to_supabase.py  --  copy the local question bank (SQLite) into Supabase.

Setup (once):  run backend/supabase/migrations/001..003 and seed/*.sql in the Supabase SQL editor.
Keys: Supabase dashboard > Project Settings > API:  Project URL  and the  service_role  key (SECRET - never put it in the Next.js
frontend or in git). On Colab store both in the Secrets panel (key icon) as SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.

    python push_to_supabase.py --stats                 # what Supabase holds / still needs per subject
    python push_to_supabase.py --dry-run --dump rows.json   # build + validate the rows, send nothing
    python push_to_supabase.py                         # push status='approved' rows
    python push_to_supabase.py --include-pending       # also push pending rows (they stay 'pending' there = invisible to students)

Safe to run again and again: Supabase ignores rows it already has (same content_hash), fills in solutions that were missing,
and promotes pending -> approved. It talks HTTPS (PostgREST), which works from Colab; I believe the free-tier direct database
port is IPv6-only, so a psycopg connection from Colab may not work.
"""
import argparse, json, os, sqlite3, sys, time
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):                    # find common.py (normally model/common.py)
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder (next to preprocessing/, training/, database/).")
from common import TOPIC_TO_SUBJECT                                          # noqa: E402

DB = HERE / "question_bank.db"
CHUNK = 200


def to_row(r, status):
    """SQLite row (dict) -> the JSON object admin_upsert_questions expects. Returns (row, None) or (None, reason)."""
    if r["subject"] not in TOPIC_TO_SUBJECT:
        return None, f"unknown subject label {r['subject']!r}"
    ans = json.loads(r["answer_json"])
    opts = json.loads(r["options_json"]) if r["options_json"] else None
    if r["type"] == "NAT":
        if not (isinstance(ans, dict) and "low" in ans and "high" in ans): return None, "bad NAT answer"
        opts = None
    else:
        if not (isinstance(opts, dict) and len(opts) == 4): return None, "options are not A-D"
        if not (isinstance(ans, list) and ans and all(a in "ABCD" for a in ans)): return None, "bad letter answer"
    return {"topic": r["subject"], "source": r["source"], "pyq_year": r["year"], "pyq_ref": r["pyq_ref"], "type": r["type"],
            "marks": r["marks"], "body_md": r["question"], "options": opts, "answer": ans,
            "solution_md": r["explanation"] or None, "solution_source": "model" if r["explanation"] else None,
            "verify_agree": r["verify_agree"], "status": status, "content_hash": r["hash"], "_model": r["model"]}, None


def load_rows(db, include_pending):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    statuses = ("approved", "pending") if include_pending else ("approved",)
    rows = [dict(x) for x in con.execute(f"SELECT * FROM questions WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id", statuses)]
    out, bad = [], Counter()
    for r in rows:
        row, why = to_row(r, r["status"])
        if row: out.append(row)
        else: bad[why] += 1
    return out, bad


def credentials():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        try:                                                     # Google Colab Secrets
            from google.colab import userdata
            url, key = userdata.get("SUPABASE_URL"), userdata.get("SUPABASE_SERVICE_ROLE_KEY")
        except Exception:
            pass
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (environment variables, or Colab Secrets).")
    return url.rstrip("/"), key


def rpc(url, key, fn, payload, retries=5):
    """call a Postgres function over HTTPS. Retries are safe because every admin function is idempotent."""
    import requests
    for attempt in range(retries):
        try:
            r = requests.post(f"{url}/rest/v1/rpc/{fn}", json=payload, timeout=90,
                              headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            if r.status_code < 300:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt); continue
            sys.exit(f"{fn} failed ({r.status_code}): {r.text[:500]}")
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(2 ** attempt)
    sys.exit(f"{fn}: gave up after {retries} tries (network). Run the same command again - it continues safely.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB)); ap.add_argument("--include-pending", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--dump", help="write the rows to this JSON file")
    ap.add_argument("--stats", action="store_true"); ap.add_argument("--target", type=int, default=200)
    a = ap.parse_args()

    if a.stats:
        url, key = credentials()
        print(f"{'subject':36s} {'approved':>8s} {'1-mark':>7s} {'2-mark':>7s} {'pending':>8s} {'flagged':>8s} {'w/ solution':>11s} {'still needed':>12s}")
        for s in rpc(url, key, "admin_bank_stats", {"p_target": a.target}):
            print(f"{s['name']:36s} {s['approved']:>8d} {s['one_mark']:>7d} {s['two_mark']:>7d} {s['pending']:>8d} {s['flagged']:>8d} {s['with_solution']:>11d} {s['still_needed']:>12d}")
        return

    rows, bad = load_rows(a.db, a.include_pending)
    print(f"{len(rows)} rows ready" + (f"; skipped {sum(bad.values())}: {dict(bad)}" if bad else ""))
    print("by source/type:", dict(Counter((r['source'], r['type']) for r in rows)))
    if a.dump:
        Path(a.dump).write_text(json.dumps([{k: v for k, v in r.items() if k != "_model"} for r in rows], ensure_ascii=False))
        print("rows written to", a.dump)
    if a.dry_run or not rows:
        return
    url, key = credentials()
    total = Counter()
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        models = Counter(r["_model"] for r in chunk if r["source"] == "generated" and r["_model"])
        batch = {"model_version": models.most_common(1)[0][0], "note": f"push {date.today()}"} if models else None
        res = rpc(url, key, "admin_upsert_questions",
                  {"p_rows": [{k: v for k, v in r.items() if k != "_model"} for r in chunk], "p_batch": batch})
        for k in ("inserted", "duplicates", "solutions_filled"): total[k] += res[k]
        for b in res["invalid"][:3]: print("   rejected by database:", b)
        total["invalid"] += len(res["invalid"])
        print(f"  {min(i + CHUNK, len(rows))}/{len(rows)}  new {total['inserted']}  already there {total['duplicates']}  "
              f"solutions filled {total['solutions_filled']}  invalid {total['invalid']}")
    print("done:", dict(total))


if __name__ == "__main__":
    main()
