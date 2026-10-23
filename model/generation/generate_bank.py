#!/usr/bin/env python3
"""
generate_bank.py  --  Gemini writes GATE CSE questions + solutions and stores them in Supabase.

Per batch (3 API calls for ~5 questions, to save free-tier quota):
   1. GENERATOR model   writes N questions with its own answer key                         (--gen-model)
   2. LITE model        solves them WITHOUT seeing the key  -> independent check           (--lite-model)
   3. LITE model        writes a short explanation for the questions whose key was confirmed
Key confirmed  -> status 'approved' (students can get it).   Key not confirmed -> 'pending' (stored, invisible to students, no explanation).
--hold sends everything as 'pending' so you approve later.

Colab:   !pip install -q google-genai requests
         Secrets (key icon, notebook access ON): GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    python generate_bank.py --ping                          # one tiny call per model: checks key + model names
    python generate_bank.py --status                        # what is still missing per subject (Supabase)
    python generate_bank.py --per-mark 100 --max-calls 60   # fill every subject to 100 one-mark + 100 two-mark questions
    python generate_bank.py --subject OS --per-mark 20      # one subject only
    python generate_bank.py --no-push                       # stage in the local SQLite file only (push later with database/push_to_supabase.py)
Run again any time: it continues from what is already in the bank, and stops cleanly when the Gemini quota runs out.
Model names/quotas change - if a call fails, check the names on the Gemini pricing page and pass --gen-model / --lite-model.
"""
import argparse, json, random, re, sqlite3, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE.parent
for p in (MODEL_DIR, MODEL_DIR / "database", HERE):
    sys.path.insert(0, str(p))
from common import (SUBJECTS, SUBJECT_TOPICS, TOPIC_TO_SUBJECT, TOPIC_WEIGHT, SCHEMA, validate_generated, insert_question,
                    norm_hash, shingles, jaccard)                                         # noqa: E402
import gemini_llm as llm                                                                  # noqa: E402
import push_to_supabase as pts                                                            # noqa: E402

DB = MODEL_DIR / "database" / "question_bank.db"
TYPE_MIX = [("MCQ", 0.60), ("NAT", 0.25), ("MSQ", 0.15)]
CODE_BY_NAME = {v[0]: k for k, v in SUBJECTS.items()}

FOCUS = {   # sub-areas to rotate through so batches do not repeat each other (edit freely)
    "General Aptitude": ["verbal: grammar/sentence completion", "verbal: reading comprehension (short passage)", "numerical: ratio and percentage", "numerical: time, speed, work", "logical reasoning / seating / sequences", "data interpretation from a small text table", "probability and counting (aptitude level)", "analogies and critical reasoning"],
    "Programming and Data Structures": ["C pointers and arrays", "recursion and tracing output", "stack/queue", "linked lists", "trees and BST", "heaps and hashing", "C operators/precedence/undefined-looking-but-defined behaviour", "time of simple loops"],
    "Discrete Mathematics": ["propositional and predicate logic", "sets, relations, functions", "partial orders and lattices", "graph theory", "combinatorics and recurrences", "groups"],
    "Linear Algebra": ["rank and nullity", "eigenvalues and eigenvectors", "determinants", "linear systems"],
    "Calculus": ["limits and continuity", "differentiation and maxima/minima", "definite integrals", "mean value theorems / series"],
    "Probability and Statistics": ["conditional probability and Bayes", "random variables, expectation, variance", "standard distributions", "counting-based probability"],
    "Algorithms": ["asymptotic analysis and recurrences", "sorting and searching", "divide and conquer", "greedy", "dynamic programming", "graph algorithms (BFS/DFS/MST/shortest path)", "NP-completeness / complexity classes"],
    "Operating Systems": ["CPU scheduling", "synchronisation: semaphores and monitors", "deadlock", "paging and virtual memory", "page replacement", "file systems and disk scheduling", "system calls and processes/threads"],
    "Computer Networks": ["subnetting and CIDR", "data link: framing, CRC, sliding window", "MAC protocols and Ethernet", "routing: distance vector/link state", "TCP: congestion and flow control", "application layer: DNS, HTTP, SMTP", "network performance: delay and throughput"],
    "Computer Organization and Architecture": ["pipelining and hazards", "cache memory and mapping", "number representation and arithmetic", "instruction formats and addressing modes", "I/O and DMA", "CPU/control unit, microprogramming"],
    "Theory of Computation": ["regular languages and DFA/NFA", "regular expressions and pumping lemma", "context-free grammars and PDA", "Turing machines and decidability", "closure properties"],
    "Databases": ["relational algebra and SQL", "normalisation and functional dependencies", "transactions and serializability", "B/B+ trees and indexing", "ER model and keys", "concurrency control and recovery"],
    "Compiler Design": ["lexical analysis", "LL(1) parsing and FIRST/FOLLOW", "LR parsing", "syntax-directed translation", "intermediate code and code optimisation", "runtime environments and storage"],
    "Digital Logic": ["boolean algebra and K-maps", "combinational circuits (MUX, decoder, adder)", "sequential circuits and flip-flops", "counters and registers", "number systems and codes", "minimisation / hazards"],
}


# ───────────── prompts ─────────────
def gen_prompt(topic, qtype, marks, n, focus, examples):
    fmt = {"MCQ": 'exactly 4 options, exactly ONE correct; "answer" is one letter, e.g. "C"',
           "MSQ": 'exactly 4 options, ONE OR MORE correct; "answer" is a list of letters, e.g. ["A","C"]; the question must say it can have more than one correct option',
           "NAT": 'NO options; "answer" is one exact number (integer or decimal, no units, no text)'}[qtype]
    hard = "needs 2-3 steps of reasoning or a short calculation" if marks == 2 else "a single concept applied directly, solvable in about a minute"
    ex = ("\nStyle reference only (never copy or paraphrase these):\n" + "\n".join(f"- {e[:500]}" for e in examples) + "\n") if examples else ""
    return f"""You are a GATE CSE paper setter. Write {n} NEW, different, exam-quality questions.
Subject: {topic}. Focus area: {focus}. Type: {qtype} ({fmt}). Marks: {marks} ({hard}).
Rules: text only (no figures, diagrams or "shown below"); put any table/code/data inside the question text; self-contained and unambiguous; plausible wrong options; use $...$ for maths; correct answer must be certain - compute carefully.{ex}
Output ONLY a JSON array, no prose, of {n} objects: {{"question": "...", "options": ["..","..","..",".."], "answer": ...}}  (omit "options" for NAT)."""


def solve_prompt(items):
    body = "\n\n".join(f"[{i}] ({it['type']}, {it['marks']} marks)\n{it['question']}\n" +
                       ("\n".join(f"{k}) {v}" for k, v in it['options'].items()) if it['options'] else "(numerical answer)")
                       for i, it in enumerate(items, 1))
    return f"""Solve each GATE question independently and carefully. MCQ: one letter. MSQ: all correct letters as a list. NAT: one number.
{body}

Output ONLY a JSON array like [{{"id":1,"answer":"B"}},{{"id":2,"answer":["A","C"]}},{{"id":3,"answer":4.5}}] with one entry per question."""


def explain_prompt(items):
    body = "\n\n".join(f"[{i}] {it['question']}\n" + ("\n".join(f"{k}) {v}" for k, v in it['options'].items()) if it['options'] else "") +
                       f"\nCorrect answer: {it['answer_txt']}" for i, it in enumerate(items, 1))
    return f"""For each GATE question write a concise, correct solution (3-6 short lines, Markdown with $...$ maths) that arrives at the given answer and, for MCQ/MSQ, says briefly why the other options fail.
{body}

Output ONLY a JSON array like [{{"id":1,"explanation":"..."}}, ...] with one entry per question."""


# ───────────── helpers ─────────────
def _fix_backslashes(t):
    """LaTeX like \\frac or \\leq inside JSON strings is an invalid escape: double every backslash that is not already a valid one."""
    return re.sub(r'\\(?!["\\/]|u[0-9a-fA-F]{4})', r'\\\\', t)


def parse_array(text, stats=None):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    s, e = text.find("["), text.rfind("]")
    if s < 0 or e <= s:
        if stats is not None: stats["parse_fail"] += 1
        return []
    for cand in (text[s:e + 1], _fix_backslashes(text[s:e + 1])):
        try:
            arr = json.loads(cand)
            if isinstance(arr, list):
                return arr
        except Exception:
            pass
    if stats is not None: stats["parse_fail"] += 1
    return []


def same_answer(a, b, qtype):
    if a is None or b is None:
        return False
    if qtype == "NAT":
        x, y = a["low"], b["low"]
        return abs(x - y) <= 1e-6 + 0.005 * max(abs(x), abs(y))
    return sorted(a) == sorted(b)


def answer_txt(ans, qtype):
    if qtype == "NAT":
        return f"{ans['low']:g}"
    return ", ".join(ans)


def pick_type(rng):
    r, acc = rng.random(), 0.0
    for t, w in TYPE_MIX:
        acc += w
        if r < acc:
            return t
    return "MCQ"


def local_counts(con):
    c = Counter()
    for subj, marks, n in con.execute("SELECT subject, marks, COUNT(*) FROM questions WHERE status IN ('approved','pending') GROUP BY 1,2"):
        c[(TOPIC_TO_SUBJECT.get(subj), marks)] += n
    return c


def remote_counts(url, key):
    c = Counter()
    for s in pts.rpc(url, key, "admin_bank_stats", {"p_target": 200}):
        code = CODE_BY_NAME.get(s["name"])
        c[(code, 1)] += s["one_mark"] + 0; c[(code, 2)] += s["two_mark"] + 0
    return c


def plan(counts, per_mark, only):
    """-> list of (code, marks, missing) sorted by biggest gap first"""
    out = [(code, m, per_mark - counts[(code, m)]) for code in SUBJECTS if not only or code in only for m in (1, 2)]
    return sorted([x for x in out if x[2] > 0], key=lambda x: -x[2])


# ───────────── one batch ─────────────
def run_batch(con, gen_model, lite_model, code, marks, n, rng, seen, hold, stats):
    topics = SUBJECT_TOPICS[code]
    topic = rng.choices(topics, [TOPIC_WEIGHT.get(t, 1) for t in topics])[0] if len(topics) > 1 else topics[0]
    qtype = pick_type(rng)
    focus = rng.choice(FOCUS[topic])
    ex = [r[0] for r in con.execute("SELECT question FROM questions WHERE source='pyq' AND subject=? AND marks=? ORDER BY RANDOM() LIMIT 2", (topic, marks))]

    raw = parse_array(llm.ask(gen_model, gen_prompt(topic, qtype, marks, n, focus, ex), as_json=True), stats)
    items = []
    for obj in raw:
        v, why = validate_generated(obj if isinstance(obj, dict) else None, qtype)
        if not v:
            stats["invalid:" + why] += 1; continue
        h = norm_hash(v["question"], v["options"])
        sh = shingles(v["question"])
        if h in seen["hash"] or any(jaccard(sh, o) > 0.6 for o in seen["sh"].get(topic, [])):
            stats["duplicate"] += 1; continue
        seen["hash"].add(h); seen["sh"].setdefault(topic, []).append(sh)
        items.append({"topic": topic, "type": qtype, "marks": marks, "question": v["question"], "options": v["options"], "answer": v["answer"]})
    if not items:
        return []

    # independent solve (the solver never sees the key)
    solved = {}
    for e in parse_array(llm.ask(lite_model, solve_prompt(items), as_json=True), stats):
        if isinstance(e, dict) and isinstance(e.get("id"), int):
            solved[e["id"]] = e.get("answer")
    for i, it in enumerate(items, 1):
        from common import normalise_answer
        sa = normalise_answer(solved[i], qtype) if i in solved else None
        it["agree"] = same_answer(sa, it["answer"], qtype)
        stats["key_confirmed" if it["agree"] else "key_not_confirmed"] += 1

    good = [it for it in items if it["agree"]]
    expl = {}
    if good:
        for it in good:
            it["answer_txt"] = answer_txt(it["answer"], qtype)
        for e in parse_array(llm.ask(lite_model, explain_prompt(good), as_json=True), stats):
            if isinstance(e, dict) and isinstance(e.get("id"), int) and isinstance(e.get("explanation"), str):
                expl[e["id"]] = e["explanation"].strip()

    rows, gi = [], 0
    for it in items:
        if it["agree"]:
            gi += 1
        explanation = expl.get(gi) if it["agree"] else None
        status = "approved" if (it["agree"] and explanation and not hold) else "pending"
        rec = {"source": "generated", "section": "GA" if topic == "General Aptitude" else "CS", "subject": topic, "topic": topic,
               "type": qtype, "marks": marks, "question": it["question"], "options": it["options"], "answer": it["answer"],
               "explanation": explanation, "status": status, "verify_agree": 1.0 if it["agree"] else 0.0, "model": gen_model}
        if insert_question(con, rec):
            con.commit()
            r = con.execute("SELECT * FROM questions WHERE hash=?", (norm_hash(it["question"], it["options"]),)).fetchone()
            row, why = pts.to_row(dict(r), status)
            if row:
                rows.append(row)
            stats["stored_" + status] += 1
    return rows


def push(url, key, rows, gen_model):
    res = pts.rpc(url, key, "admin_upsert_questions",
                  {"p_rows": [{k: v for k, v in r.items() if k != "_model"} for r in rows],
                   "p_batch": {"model_version": gen_model, "note": "generate_bank.py"}})
    for b in res.get("invalid", [])[:3]:
        print("   rejected by database:", b)
    return res


def backfill(con, lite_model, url, key, hold, stats, max_calls):
    """rows whose key WAS confirmed but that got no explanation (earlier JSON-parsing bug): write explanations, approve, push."""
    rows = [dict(r) for r in con.execute("SELECT * FROM questions WHERE source='generated' AND verify_agree=1 AND explanation IS NULL")]
    print(f"{len(rows)} confirmed questions without a solution")
    for i in range(0, len(rows), 5):
        if llm.CALLS + 1 > max_calls:
            break
        chunk = rows[i:i + 5]
        items = [{"question": r["question"], "options": json.loads(r["options_json"]) if r["options_json"] else None,
                  "answer_txt": answer_txt(json.loads(r["answer_json"]), r["type"])} for r in chunk]
        expl = {}
        for e in parse_array(llm.ask(lite_model, explain_prompt(items), as_json=True), stats):
            if isinstance(e, dict) and isinstance(e.get("id"), int) and isinstance(e.get("explanation"), str):
                expl[e["id"]] = e["explanation"].strip()
        out = []
        for j, r in enumerate(chunk, 1):
            if j not in expl:
                continue
            status = "pending" if hold else "approved"
            con.execute("UPDATE questions SET explanation=?, status=? WHERE id=?", (expl[j], status, r["id"])); con.commit()
            row, why = pts.to_row(dict(con.execute("SELECT * FROM questions WHERE id=?", (r["id"],)).fetchone()), status)
            if row:
                out.append(row)
            stats["backfilled"] += 1
        if out and url:
            push(url, key, out, "backfill")
        print(f"  backfilled {stats['backfilled']}/{len(rows)}  (calls {llm.CALLS})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen-model", default="gemini-3.8-flash", help="question writer (the stronger model)")
    ap.add_argument("--lite-model", default="gemini-3.5-flash-lite", help="solver check + explanations (the lite model)")
    ap.add_argument("--per-mark", type=int, default=100, help="target number of 1-mark AND of 2-mark questions per subject")
    ap.add_argument("--subject", nargs="*", help="subject codes: " + " ".join(SUBJECTS))
    ap.add_argument("--batch", type=int, default=5, help="questions per generator call")
    ap.add_argument("--max-calls", type=int, default=60, help="stop after this many Gemini calls (protects the free quota)")
    ap.add_argument("--rpm", type=float, default=7, help="max Gemini calls per minute (lower it if you see 429 errors)")
    ap.add_argument("--hold", action="store_true", help="store everything as pending (approve later)")
    ap.add_argument("--no-push", action="store_true", help="only write the local SQLite bank")
    ap.add_argument("--status", action="store_true"); ap.add_argument("--ping", action="store_true")
    ap.add_argument("--backfill", action="store_true", help="write missing solutions for confirmed-key questions (fixes rows left pending by the old parser)")
    ap.add_argument("--db", default=str(DB)); ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    llm.set_rpm(a.rpm)

    if a.ping:
        for m in (a.gen_model, a.lite_model):
            print(m, "->", llm.ask(m, "Reply with the single word: ok").strip()[:40])
        return
    url = key = None
    if not a.no_push:
        url, key = pts.credentials()
    con = sqlite3.connect(a.db); con.row_factory = sqlite3.Row; con.executescript(SCHEMA)
    if a.backfill:
        st = Counter(); backfill(con, a.lite_model, url, key, a.hold, st, a.max_calls); print(dict(st)); return
    counts = remote_counts(url, key) if url else local_counts(con)
    todo = plan(counts, a.per_mark, set(a.subject or []))
    if a.status or not todo:
        print(f"{'subject':8s} {'marks':>5s} {'have':>5s} {'missing':>8s}   (target {a.per_mark} per mark value)")
        for code in SUBJECTS:
            for m in (1, 2):
                print(f"{code:8s} {m:>5d} {counts[(code, m)]:>5d} {max(a.per_mark - counts[(code, m)], 0):>8d}")
        return

    rng = random.Random(a.seed)
    seen = {"hash": {r[0] for r in con.execute("SELECT hash FROM questions")}, "sh": {}}
    for topic, q in con.execute("SELECT subject, question FROM questions"):
        seen["sh"].setdefault(topic, []).append(shingles(q))
    stats, pushed, start = Counter(), Counter(), llm.CALLS
    print(f"missing: {sum(x[2] for x in todo)} questions across {len(todo)} subject/mark groups; call cap {a.max_calls}")
    try:
        while todo and llm.CALLS - start + 3 <= a.max_calls:
            code, marks, missing = todo[0]
            rows = run_batch(con, a.gen_model, a.lite_model, code, marks, min(a.batch, missing), rng, seen, a.hold, stats)
            todo[0] = (code, marks, missing - len(rows) if rows else missing - 1)    # a barren batch still counts a little, so we never loop forever
            todo = sorted([x for x in todo if x[2] > 0], key=lambda x: -x[2])
            if rows and url:
                res = push(url, key, rows, a.gen_model)
                pushed["inserted"] += res["inserted"]; pushed["duplicates"] += res["duplicates"]
            print(f"  calls {llm.CALLS - start:3d}/{a.max_calls}  {code} {marks}m  stored {stats['stored_approved']} approved / {stats['stored_pending']} pending"
                  f"   key confirmed {stats['key_confirmed']} / not {stats['key_not_confirmed']}")
    except llm.QuotaExhausted as e:
        print("\nGemini quota used up for now (resets daily, midnight Pacific). Everything so far is saved. Run again later.\n", str(e)[:200])
    except KeyboardInterrupt:
        print("\nstopped; everything so far is saved.")
    print("\nsummary:", dict(stats), "| pushed:", dict(pushed), "| Gemini calls:", llm.CALLS - start)


if __name__ == "__main__":
    main()
