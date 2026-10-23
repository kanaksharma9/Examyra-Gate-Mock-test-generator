#!/usr/bin/env python3
"""
review_bank.py  --  look at generated questions and approve / reject them in the terminal.

    python review_bank.py --stats                    # counts per status / subject
    python review_bank.py                            # review pending questions, best solver-agreement first
    python review_bank.py --subject "Operating Systems" --limit 30
    python review_bank.py --bulk-approve 1.0         # approve every pending question whose solver agreed 100% (still unreviewed!)

Keys:  a = approve   r = reject   s = skip   c = change the answer key   q = quit
Only status='approved' rows should ever reach students.
"""
import argparse, json, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):                    # find common.py (normally model/common.py)
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder (next to preprocessing/, training/, database/).\n"
             f"Looked in {HERE} and its parents. Unzip gate_scripts.zip into your project root, or copy common.py there.")
from common import normalise_answer                                           # noqa: E402


def show(r):
    id_, subj, typ, marks, q, opts, ans, expl, agree = r
    print("\n" + "=" * 78)
    print(f"#{id_}  {subj}  |  {typ}  |  {marks} mark(s)  |  solver agreement: {agree if agree is not None else 'n/a'}")
    print("-" * 78); print(q)
    if opts:
        for k, v in json.loads(opts).items(): print(f"  ({k}) {v}")
    print(f"\nANSWER KEY: {ans}")
    if expl: print(f"EXPLANATION: {expl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE / "question_bank.db")); ap.add_argument("--stats", action="store_true")
    ap.add_argument("--subject"); ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--bulk-approve", type=float)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)

    if a.stats:
        for row in con.execute("SELECT source, status, COUNT(*) FROM questions GROUP BY 1,2"): print(row)
        print("\napproved per subject / type:")
        for row in con.execute("SELECT subject, type, COUNT(*) FROM questions WHERE status='approved' GROUP BY 1,2 ORDER BY 1,2"): print("  ", row)
        return
    if a.bulk_approve is not None:
        n = con.execute("UPDATE questions SET status='approved' WHERE source='generated' AND status='pending' "
                        "AND verify_agree >= ?", (a.bulk_approve,)).rowcount
        con.commit(); print(f"approved {n} questions"); return

    sql = ("SELECT id,subject,type,marks,question,options_json,answer_json,explanation,verify_agree FROM questions "
           "WHERE source='generated' AND status='pending' " + ("AND subject=? " if a.subject else "") +
           "ORDER BY verify_agree DESC NULLS LAST, id LIMIT ?")
    rows = con.execute(sql, ([a.subject] if a.subject else []) + [a.limit]).fetchall()
    print(f"{len(rows)} questions to review")
    for r in rows:
        show(r)
        while True:
            k = input("[a]pprove  [r]eject  [s]kip  [c]hange key  [q]uit > ").strip().lower()
            if k == "a": con.execute("UPDATE questions SET status='approved' WHERE id=?", (r[0],)); break
            if k == "r": con.execute("UPDATE questions SET status='rejected' WHERE id=?", (r[0],)); break
            if k == "s": break
            if k == "q": con.commit(); return
            if k == "c":
                new = normalise_answer(input("  new answer (e.g. C | A,C | 4.5 | 4.4 to 4.6): "), r[2])
                if new is None: print("  could not read that answer"); continue
                con.execute("UPDATE questions SET answer_json=?, status='approved' WHERE id=?", (json.dumps(new), r[0])); break
        con.commit()


if __name__ == "__main__":
    main()
