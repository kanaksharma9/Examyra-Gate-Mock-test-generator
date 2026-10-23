#!/usr/bin/env python3
"""
build_dataset.py  --  GATE CSE PDFs  ->  train.jsonl / val.jsonl  (for QLoRA fine-tuning)

Folder layout (run from anywhere):
    <project>/
        q_papers/              all question-paper PDFs
        answer_keys/           all answer-key PDFs
        model/preprocessing/   <- this file lives here; outputs go to ./output/

Run:
    pip install pdfplumber
    python build_dataset.py                    # auto: pair papers<->keys, split by year
    python build_dataset.py --val-years 2024   # choose validation year(s) yourself
    python build_dataset.py --dry-run          # only show the pairing table, write nothing

Outputs (./output):
    clean_questions.jsonl   every accepted question, flat fields (base for the later "proper dataset")
    train.jsonl / val.jsonl chat-format records for training  {"messages":[user, assistant], "meta":{...}}
    rejected.jsonl          questions that were dropped + the reason (review these by hand)
    pairing.csv             which key was matched to which paper (check it!)
    report.txt              counts, skipped/scanned files, warnings

If auto-pairing gets one wrong, create ./pairs.csv (next to this file) with lines:
    paper_filename,key_filename
and those pairs will override the automatic ones.
"""
import argparse, csv, hashlib, json, re, sys
from collections import Counter
from pathlib import Path
import pdfplumber

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # <project>/

# ───────────────────────────── text cleaning ─────────────────────────────
NOISE = [
    r"^Page \d+ of \d+$", r"^Organizing Institute.*$", r".*Copyright ©.*$",
    r"^CS\s?\(?(Set\s?[A-Z0-9])?\)?\s*\d+/\d+$", r"^\d+/\d+$", r"^CS\s*\d+/\d+$",
    r"^END OF THE QUESTION PAPER$", r"^GATE ?-?20\d\d .*$", r"^GATE20\d\d.*$",
    r"^Computer Science and Information Technology.*$", r"^General Aptitude \(GA\)$",
    r"^Q\.\d+\s*[–—-]\s*Q\.\d+.*$", r"^\(for each wrong.*$",
    r"^GATE ?-?20\d\d\s*$", r"^IIT [A-Za-z]+$", r"^CS: Computer Sc.*$", r"^GA - General Aptitude$",
    r".*Graduate Aptitude Test.*", r".*Organi[sz]ing Institute.*", r"^\(?CS,? ?Set-?\d.*$",
]
NOISE_RE = [re.compile(p) for p in NOISE]
Q_START = re.compile(r"^Q\.\s?(?:No\.?\s*)?(\d{1,2})(?!\d)\s*(.*)$")        # Q.5 | Q. 5 | Q.No. 5
OPT = re.compile(r"\(([A-D])\)\s*")
VISUAL = re.compile(r"(figure|fig\.|diagram|shown (below|above|in)|following (graph|tree|circuit|network|image|picture)|\bimage\b|\[FIGURE\])", re.I)


def pdf_lines(pdf_path, max_pages=None):
    out, chars = [], 0
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages[:max_pages] if max_pages else pdf.pages
        for p in pages:
            t = p.extract_text(x_tolerance=1.5, y_tolerance=3) or ""     # x_tolerance fixes 2016 "missing spaces"
            chars += len(t)
            for ln in t.splitlines():
                s = ln.strip()
                if s and not any(r.match(s) for r in NOISE_RE):
                    out.append(s)
        return out, chars, len(pdf.pages)


def fix_dropcap(s):                          # "L et" -> "Let"
    return re.sub(r"^([A-Z]) ([a-z]{2,})", r"\1\2", s)


def explode_inline(lines):
    """OCR sometimes glues the next question onto the last option: '(D) ... Q.1 The statement...'"""
    out = []
    for ln in lines:
        m = re.match(r"^(\([A-D]\).*?\S)\s+(Q\.\s?\d{1,2}\s+\S.*)$", ln)
        out += [m.group(1), m.group(2)] if m else [ln]
    return out


BANNER_TAIL = re.compile(r"^([–—-]|to\b)\s*Q\.?\s*\d+|^(Multiple (Choice|Select)|Numerical Answer|Carry\b)", re.I)


def split_numbered(lines):
    nums, blocks, cur = [], [], None
    for ln in explode_inline(lines):
        m = Q_START.match(ln)
        if m and BANNER_TAIL.match(m.group(2)):                 # section banner, not a question
            m = None
        if m:
            if cur: blocks.append(cur)
            cur = [fix_dropcap(m.group(2))]; nums.append(int(m.group(1)))
        elif cur is not None:
            cur.append(ln)
    if cur: blocks.append(cur)
    return nums, ["\n".join(b).strip() for b in blocks]


def drop_repeats(nums, blocks):
    """A page transcribed twice makes the numbering go backwards (..Q.10, Q.7, Q.8..). Inside one section a
    Q.number may only appear once; the later copy is dropped. A new section starts when numbering returns to 1."""
    keep_n, keep_b, dropped, seen, prev = [], [], [], set(), None
    for n, bl in zip(nums, blocks):
        if n == 1 and prev is not None and prev >= 5:
            seen = set()
        if n in seen:
            dropped.append(n); continue
        seen.add(n); prev = n
        keep_n.append(n); keep_b.append(bl)
    return keep_n, keep_b, dropped


def split_questions(lines):
    return split_numbered(lines)[1]


def diagnose(p):
    """Explain WHY a paper has the wrong number of questions (missing / repeated numbers)."""
    msgs, prev = [], None
    for i, (n, (stem, _)) in enumerate(zip(p["nums"], p["parsed"])):
        if prev is not None and n != prev + 1 and not (n == 1 and prev == 10):
            kind = f"missing Q.{prev+1}" + (f"-Q.{n-1}" if n - 1 > prev + 1 else "") if n > prev + 1 else "number repeated/restarted"
            msgs.append(f"      after Q.{prev} came Q.{n} ({kind}): {stem[:70]!r}")
        prev = n
    return msgs[:12]


def parse_block(body):
    idx = [(m.start(), m.group(1)) for m in OPT.finditer(body)]
    letters = [l for _, l in idx]
    for s in range(len(letters)):
        if letters[s:s + 4] == list("ABCD"):
            seq = idx[s:s + 4]
            stem = body[:seq[0][0]].strip()
            opts = {}
            for i, (pos, l) in enumerate(seq):
                end = seq[i + 1][0] if i < 3 else len(body)
                opts[l] = OPT.sub("", body[pos:end], count=1).strip()
            return stem, opts
    return body.strip(), None


# ───────────────────────────── answer keys ─────────────────────────────
def parse_key(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    rows = []
    for ln in text.splitlines():
        t = ln.split()
        ti = next((i for i, w in enumerate(t) if w in ("MCQ", "MSQ", "NAT")), None)
        if ti is None or not t[0].isdigit():
            continue
        rest = t[ti + 1:]
        si = next((i for i, w in enumerate(rest) if re.fullmatch(r"GA|CS(-\d)?", w)), None)
        if si is None:
            continue
        tail = [w for w in rest[si + 1:] if not re.fullmatch(r"\d/3", w)]       # drop negative-mark column
        if len(tail) >= 2 and tail[-1] == "0" and tail[-2] in ("1", "2"):
            tail = tail[:-1]
        if not tail or tail[-1] not in ("1", "2"):
            continue
        rows.append({"type": t[ti], "section": "GA" if rest[si] == "GA" else "CS",
                     "marks": int(tail[-1]), "raw": " ".join(tail[:-1])})
    return rows


def key_answer(row):
    raw, typ = row["raw"], row["type"]
    if typ == "NAT":
        nums = re.findall(r"-?\d+\.?\d*", raw)
        if not nums: return None
        lo, hi = float(nums[0]), float(nums[-1])
        f = lambda x: str(int(x)) if x == int(x) else str(x)
        return f(lo) if lo == hi else f"{f(lo)} to {f(hi)}"
    letters = re.findall(r"\b[A-D]\b", raw)
    if not letters: return None
    if typ == "MCQ":
        return letters if " OR " in raw else letters[0]          # list => several answers accepted
    return sorted(set(letters))


# ───────────────────────────── metadata guessing ─────────────────────────────
def guess_year(name, text):
    m = re.search(r"GATE\s?-?\s?(20\d\d)", text) or re.search(r"(20\d\d)", name)
    if m: return int(m.group(1))
    m = re.search(r"CS[12](\d\d)S\d", name)                    # CS124S5 -> 2024
    return 2000 + int(m.group(1)) if m else None


def guess_set(name, text=""):
    for pat in (r"Set-?\s?(\d)(?!\d)", r"CS-?\s?(\d)(?!\d)", r"CS(\d)\d\dS\d", r"Keys?(\d)(?!\d)"):
        m = re.search(pat, name, re.I)
        if m: return int(m.group(1))
    m = re.search(r"\(CS[ ,-]*(\d)\)|\bSet (\d) \(CS", text)
    return int(next(g for g in m.groups() if g)) if m else None


# ───────────────────────────── load everything ─────────────────────────────
def read_ocr_cache(stem):
    f = HERE / "ocr_cache" / f"{stem}.txt"
    if not f.exists(): return None, None
    raw = f.read_text(encoding="utf-8").splitlines()
    engine = raw[0].split("=", 1)[1] if raw and raw[0].startswith("# engine=") else "ocr"
    return [l.strip() for l in raw if l.strip() and not l.startswith("#")
            and not any(r.match(l.strip()) for r in NOISE_RE)], engine


def load_papers(folder, report):
    papers = []
    for f in sorted(Path(folder).glob("*.pdf")):
        lines, chars, npages = pdf_lines(f)
        ocr = None
        if chars / max(npages, 1) < 400:
            lines, ocr = read_ocr_cache(f.stem)
            if lines is None:
                report.append(f"SKIPPED (scanned, no OCR cache yet -> run ocr_scanned.py): {f.name}  [{chars} chars in {npages} pages]")
                continue
        year, sset = guess_year(f.name, " ".join(lines[:40])), guess_set(f.name, " ".join(lines[:40]))
        nums, blocks = split_numbered(lines)
        nums, blocks, dropped = drop_repeats(nums, blocks)
        if dropped:
            report.append(f"NOTE {f.name}: removed {len(dropped)} repeated question(s) (same Q.number + same text): Q.{dropped}")
        parsed = [parse_block(b) for b in blocks]
        papers.append({"file": f.name, "year": year, "set": sset, "parsed": parsed, "nums": nums, "ocr": ocr})
    return papers


def load_keys(folder):
    keys = []
    for f in sorted(Path(folder).glob("*.pdf")):
        with pdfplumber.open(f) as pdf:
            head = " ".join((p.extract_text() or "") for p in pdf.pages[:1])[:600]
        keys.append({"file": f.name, "year": guess_year(f.name, head), "set": guess_set(f.name, head),
                     "rows": parse_key(f)})
    return keys


def type_agreement(p, k):
    flags = [(o is not None) == (r["type"] != "NAT") for (_, o), r in zip(p["parsed"], k["rows"])]
    run = best = 0
    for f in flags:
        run = 0 if f else run + 1; best = max(best, run)      # long streak = questions shifted out of step
    return sum(flags) / max(len(flags), 1), best


def pair_score(p, k):
    if len(p["parsed"]) != len(k["rows"]) or not k["rows"]: return None
    if p["year"] and k["year"] and p["year"] != k["year"]: return None
    if p["set"] and k["set"] and p["set"] != k["set"]: return None
    match, streak = type_agreement(p, k)
    need = 0.90 if (p["year"] and p["year"] == k["year"]) else 0.97     # same year -> allow a few OCR slips
    if match < need or streak > 2: return None
    return match * 10 + (3 if p["year"] and p["year"] == k["year"] else 0) + (2 if p["set"] and p["set"] == k["set"] else 0)


def why_not(p, k):
    if len(p["parsed"]) != len(k["rows"]): return f"count {len(p['parsed'])} questions vs {len(k['rows'])} key rows"
    if p["year"] and k["year"] and p["year"] != k["year"]: return f"year {p['year']} vs {k['year']}"
    if p["set"] and k["set"] and p["set"] != k["set"]: return f"set {p['set']} vs {k['set']}"
    m, streak = type_agreement(p, k)
    bad = round((1 - m) * len(k["rows"]))
    need = 0.90 if (p["year"] and p["year"] == k["year"]) else 0.97
    if m < need or streak > 2: return f"{bad} of {len(k['rows'])} question types disagree, longest streak {streak} (questions shifted?)"
    return "ok"


def make_pairs(papers, keys, overrides):
    pairs, used_p, used_k = [], set(), set()
    byp = {p["file"]: p for p in papers}; byk = {k["file"]: k for k in keys}
    for pf, kf in overrides:
        if pf in byp and kf in byk:
            pairs.append((byp[pf], byk[kf], 99.0)); used_p.add(pf); used_k.add(kf)
    cands = sorted(((pair_score(p, k), p, k) for p in papers for k in keys
                    if p["file"] not in used_p and k["file"] not in used_k), key=lambda x: -(x[0] or -1))
    for s, p, k in cands:
        if s is None or p["file"] in used_p or k["file"] in used_k: continue
        pairs.append((p, k, s)); used_p.add(p["file"]); used_k.add(k["file"])
    return pairs, [p for p in papers if p["file"] not in used_p], [k for k in keys if k["file"] not in used_k]


# ───────────────────────────── records, QA, split ─────────────────────────────
def norm_hash(stem, opts):
    s = re.sub(r"[^a-z0-9]", "", (stem + "".join((opts or {}).values())).lower())
    return hashlib.md5(s.encode()).hexdigest()


OK_CHARS = set(".,;:()[]{}<>+-*/=^_%$'\"?!&|\\@#~`’‘“”–—…∈∉∪∩⇒⇔→←≤≥≠≈×÷√∑∏π∞≡∀∃¬∧∨⊕⊙Σ∅Φφθλμ°")


def garbled(text):
    bad = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in OK_CHARS))
    return bad / max(len(text), 1) > 0.06


ALLOW_TESS = False


def reject_reason(stem, opts, typ, ocr=None):
    if len(stem) < 15: return "EMPTY_STEM"
    if ocr == "tesseract" and not ALLOW_TESS: return "OCR_TESSERACT_UNRELIABLE"
    if ocr and garbled(stem + " ".join((opts or {}).values())): return "OCR_GARBLED"
    if typ != "NAT" and not opts: return "OPTIONS_NOT_FOUND"
    if typ == "NAT" and opts: return "TYPE_MISMATCH"
    if opts:
        if any(not v for v in opts.values()): return "EMPTY_OPTION"
        if "\n" in opts["C"] and len(opts["D"]) < 25 and opts["D"][:1].islower(): return "OPTION_SPILL"
    if VISUAL.search(stem): return "NEEDS_IMAGE"
    return None


def build_prompt(r):
    parts = [f"Generate a GATE CSE question.", f"Section: {'General Aptitude' if r['section']=='GA' else 'Computer Science'}.",
             f"Type: {r['type']}.", f"Marks: {r['marks']}."]
    if r.get("subject"): parts.insert(2, f"Subject: {r['subject']}.")
    if r.get("topic"):   parts.insert(3, f"Topic: {r['topic']}.")
    return " ".join(parts)


def build_target(r):
    out = {"question": r["question"]}
    if r["options"]: out["options"] = [f"({k}) {v}" for k, v in r["options"].items()]
    out["answer"] = r["answer"]
    if r.get("explanation"): out["explanation"] = r["explanation"]
    return json.dumps(out, ensure_ascii=False)


def chat(r):
    return {"messages": [{"role": "user", "content": build_prompt(r)},
                         {"role": "assistant", "content": build_target(r)}],
            "meta": {"id": r["id"], "year": r["year"], "type": r["type"], "section": r["section"]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT)); ap.add_argument("--val-years", default="auto",
                    help="comma list e.g. 2024 or 2023,2024; 'auto' = latest year with data")
    ap.add_argument("--keep-images", action="store_true", help="keep questions that refer to a figure (text only!)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-tesseract", action="store_true", help="train on tesseract OCR text (not recommended)")
    a = ap.parse_args()
    global ALLOW_TESS
    ALLOW_TESS = a.allow_tesseract
    root = Path(a.root); out = HERE / "output"; report = []

    papers = load_papers(root / "q_papers", report)
    keys = load_keys(root / "answer_keys")
    overrides = []
    if (HERE / "pairs.csv").exists():
        overrides = [tuple(x.strip() for x in row[:2]) for row in csv.reader(open(HERE / "pairs.csv")) if len(row) >= 2]
    pairs, loose_p, loose_k = make_pairs(papers, keys, overrides)

    print(f"\n{'PAPER':42} {'KEY':28} year set  Qs  score")
    for p, k, s in pairs:
        print(f"{p['file']:42} {k['file']:28} {p['year']}  {p['set']}   {len(p['parsed'])}  {s:.1f}")
    for p in loose_p: report.append(f"NO KEY FOUND (not used for training): {p['file']}  [{len(p['parsed'])} questions parsed]")
    for k in loose_k: report.append(f"KEY WITHOUT A MATCHING PAPER: {k['file']}  [{len(k['rows'])} rows]")
    print("\n" + "\n".join(report))
    for p in loose_p:                                   # tell the user WHY nothing matched
        for k in loose_k:
            r = why_not(p, k)
            if r != "ok" and (p["year"] == k["year"] or not k["year"] or not p["year"]):
                print(f"  not paired: {p['file']} (year {p['year']}, set {p['set']}) x {k['file']} (year {k['year']}, set {k['set']}): {r}")
    for p in loose_p:
        if p["nums"] and len(p["parsed"]) != 65:
            print(f"\n  DIAGNOSIS {p['file']}: {len(p['parsed'])} questions found (expected 65)")
            print("\n".join(diagnose(p)) or "      numbering looks continuous - check for lost/extra Q.n lines")
        elif not p["parsed"]:
            print(f"\n  DIAGNOSIS {p['file']}: no 'Q.n' markers found at all - paste me the first 25 lines of its text/ocr_cache file")
    if a.dry_run: return
    out.mkdir(exist_ok=True)

    with open(out / "pairing.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["paper", "key", "year", "set", "score"])
        for p, k, s in pairs: w.writerow([p["file"], k["file"], p["year"], p["set"], round(s, 2)])

    accepted, rejected, seen = [], [], {}
    for p, k, _ in sorted(pairs, key=lambda x: (x[0]["year"] or 0, x[0]["file"])):
        pid = f"{p['year']}_set{p['set'] or 0}"
        for i, ((stem, opts), row) in enumerate(zip(p["parsed"], k["rows"]), 1):
            rec = {"id": f"{pid}_q{i:02d}", "year": p["year"], "source": p["file"], "type": row["type"],
                   "section": row["section"], "marks": row["marks"], "question": stem, "options": opts,
                   "answer": key_answer(row), "ocr": p["ocr"]}
            why = reject_reason(stem, opts, row["type"], p["ocr"]) or ("NO_ANSWER" if rec["answer"] is None else None)
            if why == "NEEDS_IMAGE" and a.keep_images: why = None
            if not why:
                h = norm_hash(stem, opts)
                if h in seen: why = f"DUPLICATE_OF_{seen[h]}"        # Set-1/Set-2 share most questions
                else: seen[h] = rec["id"]
            (rejected if why else accepted).append({**rec, "reason": why} if why else rec)

    years = sorted({r["year"] for r in accepted})
    val_years = [years[-1]] if a.val_years == "auto" and years else [int(x) for x in a.val_years.split(",") if x]
    train = [r for r in accepted if r["year"] not in val_years]
    val = [r for r in accepted if r["year"] in val_years]
    if len(years) < 2: report.append("WARNING: only one year with answer keys -> no real year-based split possible.")

    def dump(name, rows, fn=lambda r: r):
        with open(out / name, "w", encoding="utf-8") as f:
            for r in rows: f.write(json.dumps(fn(r), ensure_ascii=False) + "\n")
    dump("clean_questions.jsonl", accepted); dump("rejected.jsonl", rejected)
    dump("train.jsonl", train, chat); dump("val.jsonl", val, chat)

    why_count = Counter(r["reason"].split("_OF_")[0] for r in rejected)
    summary = [f"accepted {len(accepted)} | train {len(train)} | val {len(val)} (val years: {val_years})",
               f"years in data: {years}", f"types: {dict(Counter(r['type'] for r in accepted))}",
               f"rejected {len(rejected)}: {dict(why_count)}"]
    (out / "report.txt").write_text("\n".join(summary + [""] + report), encoding="utf-8")
    print("\n" + "\n".join(summary) + f"\nwritten to {out}")


if __name__ == "__main__":
    main()
