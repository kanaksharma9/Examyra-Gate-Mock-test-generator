#!/usr/bin/env python3
"""
make_db.py  --  create the question-bank database and load all real PYQs into it.

    python make_db.py                       # creates model/database/question_bank.db
    python make_db.py --db /path/to/x.db

PYQ answers come from the official keys, so they are stored with status='approved'.
Questions generated later (generate_questions.py) go into the SAME table with source='generated', status='pending'.
Safe to re-run: duplicates are ignored (unique hash).
"""
import argparse, json, sqlite3, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):                    # find common.py (normally model/common.py)
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder (next to preprocessing/, training/, database/).\n"
             f"Looked in {HERE} and its parents. Unzip gate_scripts.zip into your project root, or copy common.py there.")
from common import SCHEMA, insert_question, normalise_answer          # noqa: E402

OUT = HERE.parent / "preprocessing" / "output"


def pyq_answer(r):
    """build_dataset stores: MCQ 'C' or ['C','D'] | MSQ ['A','C'] | NAT '4.4 to 4.6' or '11'."""
    a = r["answer"]
    if r["type"] == "NAT":
        return normalise_answer(a, "NAT")
    return [a] if isinstance(a, str) else sorted(set(a))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE / "question_bank.db"))
    ap.add_argument("--pyq", default="")
    a = ap.parse_args()
    src = Path(a.pyq) if a.pyq else next(p for p in (OUT / "clean_questions_tagged.jsonl", OUT / "clean_questions.jsonl") if p.exists())
    con = sqlite3.connect(a.db)
    con.executescript(SCHEMA)
    added = skipped = 0
    for line in open(src, encoding="utf-8"):
        r = json.loads(line)
        ans = pyq_answer(r)
        if ans is None:
            skipped += 1; continue
        ok = insert_question(con, {"source": "pyq", "pyq_ref": r["id"], "year": r["year"], "section": r["section"],
                                   "subject": r.get("subject"), "type": r["type"], "marks": r["marks"],
                                   "question": r["question"], "options": r.get("options"), "answer": ans,
                                   "status": "approved"})
        added += ok; skipped += (not ok)
    con.commit()
    print(f"source: {src.name}\nadded {added}, skipped {skipped} (duplicates/unusable)  ->  {a.db}")
    for row in con.execute("SELECT source, section, type, COUNT(*) FROM questions GROUP BY 1,2,3 ORDER BY 1,2,3"):
        print("  ", row)
    con.close()


if __name__ == "__main__":
    main()
