#!/usr/bin/env python3
"""
tag_subjects.py  --  add a GATE subject (Algorithms, OS, DBMS ...) to every question, then rewrite train/val.

Why: the model can only be steered ("give me a 2-mark Operating Systems NAT") if it was trained with subject tags.

Run on Colab (GPU, uses the same Qwen model to classify):   python tag_subjects.py
Run anywhere without GPU (keyword rules, rougher):          python tag_subjects.py --keywords-only

Reads   output/clean_questions.jsonl, output/train.jsonl, output/val.jsonl   (made by build_dataset.py)
Writes  output/clean_questions_tagged.jsonl   and overwrites train.jsonl / val.jsonl (old ones kept as *_untagged.jsonl)
Spot-check ~30 lines of clean_questions_tagged.jsonl afterwards; a few wrong tags are fine.
"""
import argparse, json, re, shutil, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):                    # find common.py (normally model/common.py)
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder (next to preprocessing/, training/, database/).\n"
             f"Looked in {HERE} and its parents. Unzip gate_scripts.zip into your project root, or copy common.py there.")
from common import CS_SUBJECTS, chat_record                                   # noqa: E402

KEYWORDS = {
    "Discrete Mathematics": ["set ", "relation", "lattice", "poset", "graph", "vertices", "edges", "propositional", "predicate",
                             "logically equivalent", "combinator", "permutation", "recurrence", "group ", "tautology", "spanning tree",
                             "chromatic", "planar", "hamiltonian", "bijection", "partial order", "first-order", "isomorphic"],
    "Linear Algebra": ["matrix", "matrices", "eigen", "determinant", "rank of", "vector space", "linear system", "null space", "invertible"],
    "Calculus": ["limit", "derivative", "integral", "differentiable", "continuous", "maxima", "minima", "taylor", "∫", "lim"],
    "Probability and Statistics": ["probability", "random variable", "expected value", "variance", "distribution", "standard deviation",
                                   "bayes", "dice", "coin", "mean of", "poisson", "binomial", "uniform"],
    "Digital Logic": ["boolean", "flip-flop", "multiplexer", "decoder", "k-map", "karnaugh", "combinational", "sequential circuit",
                      "gates", "logic gate", "adder", "counter", "minterm", "sum of products", "nand", "2's complement", "ieee 754", "fixed-point", "floating point"],
    "Computer Organization and Architecture": ["cache", "pipeline", "instruction", "cpu", "processor", "addressing mode", "dma", "memory hierarchy",
                                              "register", "clock cycle", "cpi", "interrupt", "risc", "bus", "main memory", "hit ratio", "speedup", "opcode", "disk"],
    "Programming and Data Structures": ["c program", "pointer", "array", "linked list", "stack", "queue", "binary tree", "heap", "hash",
                                       "recursion", "output of the following", "function", "struct", "printf", "bst", "binary search tree", "avl", "tree traversal"],
    "Algorithms": ["time complexity", "algorithm", "dynamic programming", "greedy", "divide and conquer", "sorting", "quicksort", "mergesort",
                   "shortest path", "dijkstra", "asymptotic", "big-o", "np-complete", "np-hard", "minimum spanning", "kruskal", "prim", "bfs", "dfs", "master theorem", "θ("],
    "Theory of Computation": ["dfa", "nfa", "regular language", "context-free", "pushdown", "turing machine", "decidable", "undecidable",
                              "regular expression", "pumping", "grammar", "recursively enumerable", "finite automata", "language l"],
    "Compiler Design": ["compiler", "lexical", "parser", "parsing", "lr(", "ll(", "first and follow", "syntax-directed", "three address", "intermediate code",
                        "register allocation", "code optimization", "basic block", "activation record", "ssa", "yacc", "symbol table"],
    "Operating Systems": ["process", "thread", "deadlock", "semaphore", "scheduling", "page fault", "paging", "virtual memory", "mutex", "context switch",
                          "file system", "inode", "disk scheduling", "demand paging", "tlb", "round robin", "critical section", "banker", "fork", "system call"],
    "Databases": ["sql", "relation r", "relational", "functional dependenc", "normal form", "bcnf", "3nf", "transaction", "serializab", "b+ tree",
                  "query", "schema", "primary key", "foreign key", "tuple", "join", "e-r", "entity", "concurrency control", "two-phase locking", "index"],
    "Computer Networks": ["tcp", "udp", "ip address", "subnet", "router", "routing", "ethernet", "packet", "bandwidth", "sliding window", "csma",
                          "network", "http", "dns", "congestion", "latency", "mac address", "crc", "checksum", "cidr", "link-state", "distance vector", "throughput"],
}


def keyword_scores(text):
    t = " " + text.lower() + " "
    ranked = sorted(((sum(t.count(k) for k in kws), s) for s, kws in KEYWORDS.items()), reverse=True)
    return ranked[0][1], ranked[0][0], ranked[1][0]          # best subject, its score, runner-up score


def keyword_subject(text):
    best, sc, _ = keyword_scores(text)
    return best if sc > 0 else None


def confident(sc, second):
    """keywords alone decide when the winner is clear; otherwise the model is asked"""
    return sc >= 2 and sc >= 2 * second or (sc >= 1 and second == 0)


def make_llm_tagger(model_name):
    from unsloth import FastLanguageModel
    import torch
    import transformers
    transformers.logging.set_verbosity_error()               # silences the repeated max_new_tokens/max_length warning
    model, tok = FastLanguageModel.from_pretrained(model_name=model_name, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    menu = "\n".join(f"- {s}" for s in CS_SUBJECTS)

    def tag(q):
        prompt = (f"Classify this GATE Computer Science question into exactly ONE subject from this list:\n{menu}\n\n"
                  f"Question:\n{q[:1500]}\n\nReply with the subject name only, copied exactly from the list.")
        text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
        inp = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inp, max_new_tokens=16, max_length=None, do_sample=False)
        ans = tok.decode(out[0, inp.input_ids.shape[1]:], skip_special_tokens=True).strip().lower()
        for s in CS_SUBJECTS:                                  # exact name first, then a contained name
            if ans.startswith(s.lower()) or s.lower() in ans:
                return s
        return None
    return tag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="unsloth/Qwen3-4B-Instruct-2507-unsloth-bnb-4bit")
    ap.add_argument("--keywords-only", action="store_true")
    ap.add_argument("--out", default=str(HERE / "output"))
    a = ap.parse_args()
    out = Path(a.out)
    recs = [json.loads(l) for l in open(out / "clean_questions.jsonl", encoding="utf-8")]
    val_ids = {json.loads(l)["meta"]["id"] for l in open(out / "val.jsonl", encoding="utf-8")}
    llm = None if a.keywords_only else make_llm_tagger(a.model)

    how = Counter()
    for i, r in enumerate(recs, 1):
        if r["section"] == "GA":
            r["subject"] = "General Aptitude"; continue
        text = r["question"] + " " + " ".join((r.get("options") or {}).values())
        kw, sc, second = keyword_scores(text)
        if sc > 0 and (llm is None or confident(sc, second)):
            r["subject"] = kw; how["keywords"] += 1
        else:
            guess = llm(text) if llm else None
            r["subject"] = guess or (kw if sc > 0 else "Programming and Data Structures")
            how["model" if guess else "fallback"] += 1
        if i % 50 == 0: print(f"  tagged {i}/{len(recs)}", flush=True)

    with open(out / "clean_questions_tagged.jsonl", "w", encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for name in ("train", "val"):
        if (out / f"{name}.jsonl").exists() and not (out / f"{name}_untagged.jsonl").exists():
            shutil.copy(out / f"{name}.jsonl", out / f"{name}_untagged.jsonl")
    with open(out / "train.jsonl", "w", encoding="utf-8") as ft, open(out / "val.jsonl", "w", encoding="utf-8") as fv:
        for r in recs:
            (fv if r["id"] in val_ids else ft).write(json.dumps(chat_record(r), ensure_ascii=False) + "\n")
    print("decided by:", dict(how))
    print("subjects:", dict(Counter(r["subject"] for r in recs).most_common()))
    print("rewrote train.jsonl / val.jsonl with subject tags")


if __name__ == "__main__":
    main()
