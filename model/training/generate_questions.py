#!/usr/bin/env python3
"""
generate_questions.py  --  use the fine-tuned model to fill the question bank (SQLite).

Colab (after train_qwen.py):
    %cd /content/drive/MyDrive/gate_project/model/training
    !python generate_questions.py --count 200            # small first batch, then inspect
    !python generate_questions.py --count 2000           # the real run (re-runnable; adds more each time)

For every question it: generates JSON -> validates the format -> rejects near-copies of real PYQs and of earlier
generations -> (optionally) lets the base model solve it 3 times and stores how often the answers agree
-> inserts into model/database/question_bank.db with status='pending'.

Nothing generated is shown to students until you set status='approved' (see review_bank.py).
The 'verify_agree' score is only a hint - a 4B model is a weak solver. NAT answers especially need your eyes.
"""
import argparse, json, random, re, sqlite3, sys, time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):                    # find common.py (normally model/common.py)
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder (next to preprocessing/, training/, database/).\n"
             f"Looked in {HERE} and its parents. Unzip gate_scripts.zip into your project root, or copy common.py there.")
from common import (SCHEMA, SUBJECTS, SUBJECT_TOPICS, TOPIC_TO_SUBJECT, TOPIC_WEIGHT, build_prompt,   # noqa: E402
                    insert_question, jaccard, parse_model_json, shingles, validate_generated)

DB = HERE.parent / "database" / "question_bank.db"
DATA = HERE.parent / "preprocessing" / "output"

TYPE_MIX = {"MCQ": 0.53, "NAT": 0.32, "MSQ": 0.15}          # from your PYQ data (core subjects; GA is MCQ only)
ONE_MARK_SHARE = 30 / 65                                      # 30 of the 65 questions in a paper are 1-mark


def _slot(rnd, code, marks):
    """one generation job (section, topic-label, type, marks) for a subject code"""
    section = SUBJECTS[code][1]
    topics = SUBJECT_TOPICS[code]
    topic = rnd.choices(topics, weights=[TOPIC_WEIGHT.get(t, 1) for t in topics])[0]
    qtype = "MCQ" if code == "GA" else rnd.choices(list(TYPE_MIX), weights=list(TYPE_MIX.values()))[0]
    return (section, topic, qtype, marks)


def make_plan(n, seed=1):
    """random plan following the weightage mid-points (use when you do not care about quotas)"""
    rnd = random.Random(seed)
    codes = list(SUBJECTS); w = [(lo + hi) / 2 for _, _, lo, hi in SUBJECTS.values()]
    return [_slot(rnd, c, 1 if rnd.random() < ONE_MARK_SHARE else 2)
            for c in rnd.choices(codes, weights=w, k=n)]


def bank_counts(con, approved_only=False):
    """{(subject code, marks): how many questions the bank already holds}"""
    status = "('approved')" if approved_only else "('approved','pending')"
    out = Counter()
    for label, marks, n in con.execute(f"SELECT subject, marks, COUNT(*) FROM questions WHERE status IN {status} GROUP BY 1,2"):
        if label in TOPIC_TO_SUBJECT:
            out[(TOPIC_TO_SUBJECT[label], marks)] += n
    return out


def deficits(con, target, approved_only=False):
    have = bank_counts(con, approved_only)
    one = round(target * ONE_MARK_SHARE)
    need = {}
    for code in SUBJECTS:
        for marks, goal in {1: one, 2: target - one}.items():
            need[(code, marks)] = max(0, goal - have[(code, marks)])
    return need, have


def make_fill_plan(con, target, count, seed=1, approved_only=False, overshoot=1.0):
    """Jobs that bring every (subject, marks) class up to `target` questions in total (PYQs + generated).
    Generates at most `count` jobs per run, spread in proportion to what is missing. Re-run to continue."""
    rnd = random.Random(seed)
    need, _ = deficits(con, target, approved_only)
    pool = [k for k, v in need.items() for _ in range(int(v * overshoot + 0.999))]
    rnd.shuffle(pool)
    return [_slot(rnd, code, marks) for code, marks in pool[:count]], need


# ───────────── model side (GPU) ─────────────
def load_model(adapter):
    from unsloth import FastLanguageModel
    model, tok = FastLanguageModel.from_pretrained(model_name=adapter, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    tok.padding_side = "left"
    return model, tok


def make_gen_fn(model, tok):
    def gen(prompts, max_new_tokens=700, temperature=0.8, top_p=0.9, top_k=20):
        texts = [tok.apply_chat_template([{"role": "user", "content": p}], tokenize=False, add_generation_prompt=True)
                 for p in prompts]
        inp = tok(texts, return_tensors="pt", padding=True).to(model.device)
        out = model.generate(**inp, max_new_tokens=max_new_tokens, do_sample=True, temperature=temperature,
                             top_p=top_p, top_k=top_k, pad_token_id=tok.pad_token_id)
        return tok.batch_decode(out[:, inp.input_ids.shape[1]:], skip_special_tokens=True)
    return gen


SOLVE = ("Solve this GATE question. Reason briefly, then end with a last line exactly of the form 'FINAL: X' "
         "where X is {fmt}.\n\n{q}\n{opts}")
FMT = {"MCQ": "one option letter (A-D)", "MSQ": "all correct option letters separated by commas, e.g. A,C",
       "NAT": "a single number"}


def _clean_solution(text):
    """reasoning shown to students: everything before the last 'FINAL:' line"""
    body = re.split(r"\n?\s*FINAL:", text)[0].strip() if "FINAL:" in text else text.strip()
    return body[:1500] if len(body) >= 40 else None


def solver_votes(model, gen_fn, clean, qtype, n):
    """The base model solves the question n times. Returns (agreement 0..1, a solution text from an agreeing run) or None."""
    opts = "\n".join(f"({k}) {v}" for k, v in (clean["options"] or {}).items())
    prompt = SOLVE.format(fmt=FMT[qtype], q=clean["question"], opts=opts)
    try:
        with model.disable_adapter():                          # plain base model = independent solver
            outs = gen_fn([prompt] * n, max_new_tokens=700, temperature=0.6)
    except Exception as e:                                     # e.g. adapter API differs -> skip, don't crash
        print("  (verification skipped:", e, ")")
        return None
    return score_votes(outs, clean["answer"], qtype)


def matches_key(got, key, qtype):
    """does the text `got` (e.g. 'B' / 'A,C' / '4.5') equal the answer key?  MCQ keys with several letters = any accepted"""
    if qtype == "NAT":
        x = re.search(r"-?\d+(?:\.\d+)?", got.replace(",", ""))
        tol = 0.01 * max(abs(key["high"]), 1)
        return bool(x) and key["low"] - tol <= float(x.group()) <= key["high"] + tol
    letters = sorted(set(re.findall(r"\b[A-D]\b", got.upper())))
    return letters == key or (qtype == "MCQ" and len(letters) == 1 and letters[0] in key)


def score_votes(outs, key, qtype):
    agree, solution = 0, None
    for o in outs:
        m = re.findall(r"FINAL:\s*(.+)", o)
        if not m:
            continue
        if matches_key(m[-1], key, qtype):
            agree += 1
            solution = solution or _clean_solution(o)
    return agree / len(outs), solution


# ───────────── pipeline (no GPU needed; gen_fn is injected so it can be tested) ─────────────
def known_shingles(con):
    seen = []
    for f in ("train.jsonl", "val.jsonl"):
        p = DATA / f
        if p.exists():
            for line in open(p, encoding="utf-8"):
                a = json.loads(line)["messages"][1]["content"]
                obj = parse_model_json(a)
                if obj and obj.get("question"):
                    seen.append(shingles(obj["question"]))
    for (q,) in con.execute("SELECT question FROM questions"):
        seen.append(shingles(q))
    return seen


def run(con, plan, gen_fn, verify_fn, batch=4, max_sim=0.6, temperature=0.8, model_name="", log=print, min_agree=0.0):
    seen = known_shingles(con)
    stats = Counter(); t0 = time.time()
    for i in range(0, len(plan), batch):
        chunk = plan[i:i + batch]
        raws = gen_fn([build_prompt(s, t, m, sub) for s, sub, t, m in chunk], temperature=temperature)
        for (section, subject, qtype, marks), raw in zip(chunk, raws):
            clean, why = validate_generated(parse_model_json(raw), qtype)
            sh = None
            if clean:
                sh = shingles(clean["question"])
                if any(jaccard(sh, s) >= max_sim for s in seen):
                    clean, why = None, "TOO_SIMILAR_TO_EXISTING"
            if not clean:
                stats[why] += 1; continue
            res = verify_fn(clean, qtype) if verify_fn else None
            agree, solution = res if res else (None, None)
            if agree is not None and agree < min_agree:
                stats["LOW_SOLVER_AGREEMENT"] += 1; continue
            rec = {"source": "generated", "section": section, "subject": subject, "type": qtype, "marks": marks, **clean,
                   "verify_agree": agree, "model": model_name, "status": "pending"}
            rec["explanation"] = clean.get("explanation") or solution          # solver reasoning (only from runs that matched the key)
            ok = insert_question(con, rec)
            stats["inserted" if ok else "DUPLICATE_HASH"] += 1
            if ok: seen.append(sh)
        con.commit()
        done = min(i + batch, len(plan))
        log(f"  {done}/{len(plan)}  inserted {stats['inserted']}  ({time.time()-t0:.0f}s)")
    return stats


def print_status(con, target, approved_only=False):
    need, have = deficits(con, target, approved_only)
    print(f"\nBank vs goal of {target} per subject (split into 1-mark / 2-mark like a real paper):")
    print(f"  {'subject':6s} {'1-mark':>14s} {'2-mark':>14s}   missing")
    for code in SUBJECTS:
        print(f"  {code:6s} {have[(code,1)]:>6d} /{round(target*ONE_MARK_SHARE):>4d}   {have[(code,2)]:>6d} /{target-round(target*ONE_MARK_SHARE):>4d}"
              f"   {need[(code,1)] + need[(code,2)]}")
    print(f"  total missing: {sum(need.values())}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=str(HERE / "lora_v1")); ap.add_argument("--db", default=str(DB))
    ap.add_argument("--count", type=int, default=200); ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.8); ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument("--verify", type=int, default=3, help="solver samples per question (0 = off)")
    ap.add_argument("--max-sim", type=float, default=0.6, help="reject if 5-gram overlap with a PYQ/earlier question >= this")
    ap.add_argument("--fill-to", type=int, default=0, help="top every subject up to N questions in the bank (PYQs count). "
                    "--count then caps how many jobs this run does; re-run until the table says nothing is missing")
    ap.add_argument("--approved-only", action="store_true", help="with --fill-to: count only approved questions, not pending ones")
    ap.add_argument("--min-agree", type=float, default=0.0, help="drop generated questions whose solver agreement is below this (e.g. 0.34)")
    ap.add_argument("--overshoot", type=float, default=1.5, help="with --fill-to: make this many times the missing amount (some get rejected)")
    ap.add_argument("--status", action="store_true", help="only print what the bank holds / is missing, then exit")
    a = ap.parse_args()

    con = sqlite3.connect(a.db); con.executescript(SCHEMA)
    if a.status or a.fill_to:
        print_status(con, a.fill_to or 200, a.approved_only)
        if a.status: return
    model, tok = load_model(a.adapter)
    gen_fn = make_gen_fn(model, tok)
    verify_fn = (lambda clean, qtype: solver_votes(model, gen_fn, clean, qtype, a.verify)) if a.verify else None
    if a.fill_to:
        plan, need = make_fill_plan(con, a.fill_to, a.count, a.seed, a.approved_only, a.overshoot)
        if not plan:
            print(f"Every subject already has >= {a.fill_to} questions. Nothing to generate."); return
        print(f"this run: {len(plan)} generation jobs")
    else:
        plan = make_plan(a.count, a.seed)
    stats = run(con, plan, gen_fn, verify_fn, a.batch, a.max_sim, a.temperature, Path(a.adapter).name, min_agree=a.min_agree)
    tried = sum(stats.values())
    print("\nresult:", dict(stats))
    print(f"valid & new: {stats['inserted']}/{tried} = {100*stats['inserted']/max(tried,1):.0f}%   (db: {a.db})")


if __name__ == "__main__":
    main()
