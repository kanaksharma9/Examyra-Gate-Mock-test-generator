#!/usr/bin/env python3
"""
generate_solutions.py  --  write a worked solution for every question in the local bank that has none.

Colab (GPU):
    %cd /content/drive/MyDrive/gate_project/model/training
    !python generate_solutions.py --only pyq --limit 100      # try 100 real PYQs first, read a few
    !python generate_solutions.py                              # then everything that is still empty (re-runnable)

How it works: the base Qwen model is given the question AND its answer key and asked to explain, step by step, why that answer
is right. If the explanation finishes with a different answer than the key, it is thrown away (the question stays without a
solution and is retried next run). Solutions are written by a small model: usually fine for direct questions, weaker on long
calculations. They are stored as solution_source='model'; the student-side "report" button is the safety net.
"""
import argparse, json, re, sqlite3, sys, time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):                    # find common.py (normally model/common.py)
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder (next to preprocessing/, training/, database/).")
from generate_questions import matches_key                                  # noqa: E402

DB = HERE.parent / "database" / "question_bank.db"
BASE = "unsloth/Qwen3-4B-Instruct-2507-unsloth-bnb-4bit"

PROMPT = ("You are a GATE Computer Science teacher. Below is a question and its CORRECT answer.\n"
          "Write a clear step-by-step solution (at most 150 words) that a student can follow and that arrives at exactly this answer. "
          "Use $LaTeX$ for maths. Do not mention that you were given the answer. "
          "End with a last line exactly of the form 'ANSWER: X'.\n\n"
          "Question:\n{q}\n{opts}\n\nCorrect answer: {ans}")


def answer_text(key, qtype):
    if qtype == "NAT":
        return str(key["low"]) if key["low"] == key["high"] else f"between {key['low']} and {key['high']}"
    return " or ".join(key) if qtype == "MCQ" else ", ".join(key)


def pending_rows(con, only=None, limit=0):
    where = "(explanation IS NULL OR explanation = '') AND status IN ('approved','pending')"
    if only in ("pyq", "generated"):
        where += f" AND source = '{only}'"
    sql = f"SELECT id, type, question, options_json, answer_json FROM questions WHERE {where} ORDER BY (source='pyq') DESC, id" + (f" LIMIT {int(limit)}" if limit else "")
    return con.execute(sql).fetchall()


def fill_solutions(con, gen_fn, rows, batch=8, log=print):
    stats = Counter(); t0 = time.time()
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        prompts, keys = [], []
        for id_, qtype, q, opts, ans in chunk:
            key = json.loads(ans); keys.append(key)
            o = "\n".join(f"({k}) {v}" for k, v in json.loads(opts).items()) if opts else ""
            prompts.append(PROMPT.format(q=q, opts=o, ans=answer_text(key, qtype)))
        outs = gen_fn(prompts)
        for (id_, qtype, *_), key, out in zip(chunk, keys, outs):
            m = re.findall(r"ANSWER:\s*(.+)", out)
            body = re.split(r"\n?\s*ANSWER:", out)[0].strip()
            if not m or not matches_key(m[-1], key, qtype):
                stats["answer_mismatch"] += 1; continue
            if len(body) < 40:
                stats["too_short"] += 1; continue
            con.execute("UPDATE questions SET explanation = ? WHERE id = ?", (body[:1500], id_)); stats["filled"] += 1
        con.commit()
        log(f"  {min(i + batch, len(rows))}/{len(rows)}  filled {stats['filled']}  ({time.time() - t0:.0f}s)")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB)); ap.add_argument("--model", default=BASE)
    ap.add_argument("--only", choices=["pyq", "generated"]); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    rows = pending_rows(con, a.only, a.limit)
    print(f"{len(rows)} questions without a solution")
    if not rows:
        return
    from unsloth import FastLanguageModel
    model, tok = FastLanguageModel.from_pretrained(model_name=a.model, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model); tok.padding_side = "left"

    def gen(prompts):
        texts = [tok.apply_chat_template([{"role": "user", "content": p}], tokenize=False, add_generation_prompt=True) for p in prompts]
        inp = tok(texts, return_tensors="pt", padding=True).to(model.device)
        out = model.generate(**inp, max_new_tokens=500, do_sample=True, temperature=0.4, top_p=0.9, top_k=20, pad_token_id=tok.pad_token_id)
        return tok.batch_decode(out[:, inp.input_ids.shape[1]:], skip_special_tokens=True)

    print("result:", dict(fill_solutions(con, gen, rows, a.batch)))


if __name__ == "__main__":
    main()
