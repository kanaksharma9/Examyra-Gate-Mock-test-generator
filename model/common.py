"""Shared helpers for the GATE project (prompt format, validation, DB schema). No heavy imports on purpose."""
import hashlib, json, re

SECTION_NAME = {"GA": "General Aptitude", "CS": "Computer Science"}
CS_SUBJECTS = [
    "Discrete Mathematics", "Linear Algebra", "Calculus", "Probability and Statistics",
    "Digital Logic", "Computer Organization and Architecture", "Programming and Data Structures",
    "Algorithms", "Theory of Computation", "Compiler Design", "Operating Systems", "Databases",
    "Computer Networks",
]
ALL_SUBJECTS = ["General Aptitude"] + CS_SUBJECTS

# The exam has 11 SUBJECTS (database table 'subjects'); the 14 labels the model is trained with are TOPICS of those subjects.
SUBJECTS = {   # code: (name, section, marks_min, marks_max)   <- your weightage table
    "GA": ("General Aptitude", "GA", 15, 15), "PDS": ("Programming and Data Structures", "CS", 10, 15),
    "MATH": ("Engineering Mathematics", "CS", 13, 15), "ALGO": ("Algorithms", "CS", 7, 12),
    "OS": ("Operating Systems", "CS", 8, 12), "CN": ("Computer Networks", "CS", 8, 12),
    "COA": ("Computer Organization and Architecture", "CS", 7, 10), "TOC": ("Theory of Computation", "CS", 7, 10),
    "DBMS": ("Databases", "CS", 6, 10), "CD": ("Compiler Design", "CS", 4, 6), "DL": ("Digital Logic", "CS", 4, 6)}
TOPIC_TO_SUBJECT = {
    "General Aptitude": "GA", "Programming and Data Structures": "PDS", "Discrete Mathematics": "MATH",
    "Linear Algebra": "MATH", "Calculus": "MATH", "Probability and Statistics": "MATH", "Algorithms": "ALGO",
    "Operating Systems": "OS", "Computer Networks": "CN", "Computer Organization and Architecture": "COA",
    "Theory of Computation": "TOC", "Databases": "DBMS", "Compiler Design": "CD", "Digital Logic": "DL"}
SUBJECT_TOPICS = {}
for _t, _c in TOPIC_TO_SUBJECT.items():
    SUBJECT_TOPICS.setdefault(_c, []).append(_t)
# how the 4 maths topics share Engineering Mathematics (rough GATE split - edit freely)
TOPIC_WEIGHT = {"Discrete Mathematics": 5, "Linear Algebra": 2, "Calculus": 2, "Probability and Statistics": 3}


# ───────────── training prompt / target (must match what the model is trained on) ─────────────
def build_prompt(section, qtype, marks, subject=None):
    parts = ["Generate a GATE CSE question.", f"Section: {SECTION_NAME[section]}."]
    if subject and subject != "General Aptitude":
        parts.append(f"Subject: {subject}.")
    parts += [f"Type: {qtype}.", f"Marks: {marks}."]
    return " ".join(parts)


def build_target(r):
    """r: record with question, options (dict A-D or None), answer, optional explanation."""
    out = {"question": r["question"]}
    if r.get("options"):
        out["options"] = [f"({k}) {v}" for k, v in r["options"].items()]
    out["answer"] = r["answer"]
    if r.get("explanation"):
        out["explanation"] = r["explanation"]
    return json.dumps(out, ensure_ascii=False)


def chat_record(r):
    return {"messages": [{"role": "user", "content": build_prompt(r["section"], r["type"], r["marks"], r.get("subject"))},
                         {"role": "assistant", "content": build_target(r)}],
            "meta": {"id": r["id"], "year": r["year"], "type": r["type"], "section": r["section"],
                     "subject": r.get("subject")}}


# ───────────── reading what the model generated ─────────────
def parse_model_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except Exception:
        return None


def _num(x):
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", str(x))
    return float(m.group(1)) if m else None


def normalise_answer(ans, qtype):
    """-> list of letters for MCQ/MSQ, {'low','high'} for NAT, or None if unusable."""
    if qtype == "NAT":
        if isinstance(ans, (int, float)):
            return {"low": float(ans), "high": float(ans)}
        nums = re.findall(r"-?\d+(?:\.\d+)?", str(ans))
        if not nums or len(nums) > 2:
            return None
        lo, hi = float(nums[0]), float(nums[-1])
        return {"low": min(lo, hi), "high": max(lo, hi)}
    letters = re.findall(r"\b[A-D]\b", ans.upper() if isinstance(ans, str) else " ".join(map(str, ans)).upper())
    letters = sorted(set(letters))
    if not letters or (qtype == "MCQ" and len(letters) != 1):
        return None
    return letters


def validate_generated(obj, qtype):
    """-> (clean dict | None, reason). clean = {question, options(dict|None), answer(normalised), explanation}"""
    if not isinstance(obj, dict):
        return None, "NOT_JSON"
    q = obj.get("question")
    if not isinstance(q, str) or len(q.strip()) < 20:
        return None, "BAD_QUESTION"
    if re.search(r"\[FIGURE\]|\bfigure\b|\bdiagram\b|shown (below|above|in)", q, re.I):
        return None, "NEEDS_IMAGE"
    opts = None
    if qtype in ("MCQ", "MSQ"):
        raw = obj.get("options")
        if not isinstance(raw, list) or len(raw) != 4 or not all(isinstance(o, str) and o.strip() for o in raw):
            return None, "BAD_OPTIONS"
        texts = [re.sub(r"^\(?[A-Da-d][\).]\s*", "", o.strip()) for o in raw]
        if len({t.lower() for t in texts}) < 4:
            return None, "DUPLICATE_OPTIONS"
        opts = dict(zip("ABCD", texts))
    elif obj.get("options"):
        return None, "NAT_HAS_OPTIONS"
    ans = normalise_answer(obj.get("answer"), qtype)
    if ans is None:
        return None, "BAD_ANSWER"
    return {"question": q.strip(), "options": opts, "answer": ans,
            "explanation": (obj.get("explanation") or None)}, None


# ───────────── dedupe helpers ─────────────
def norm_hash(stem, opts=None):
    s = re.sub(r"[^a-z0-9]", "", (stem + "".join((opts or {}).values())).lower())
    return hashlib.md5(s.encode()).hexdigest()


def shingles(text, n=5):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(max(len(w) - n + 1, 1))}


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


# ───────────── database schema (SQLite; the same columns work in Postgres) ─────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL CHECK (source IN ('pyq','generated')),
    pyq_ref       TEXT,                 -- e.g. 2024_set1_q17
    year          INTEGER,              -- PYQs only
    section       TEXT NOT NULL CHECK (section IN ('GA','CS')),
    subject       TEXT,
    topic         TEXT,
    type          TEXT NOT NULL CHECK (type IN ('MCQ','MSQ','NAT')),
    marks         INTEGER NOT NULL CHECK (marks IN (1,2)),
    question      TEXT NOT NULL,        -- Markdown + $LaTeX$
    options_json  TEXT,                 -- {"A":"..","B":"..","C":"..","D":".."}  (null for NAT)
    answer_json   TEXT NOT NULL,        -- MCQ: ["C"] (several = any accepted) | MSQ: ["A","C"] (all must match) | NAT: {"low":4.4,"high":4.6}
    explanation   TEXT,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    verify_agree  REAL,                 -- 0..1 self-consistency score (generated only)
    model         TEXT,
    hash          TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_q_pick ON questions (status, section, subject, type, marks);
"""


def insert_question(con, rec):
    """rec: source, section, type, marks, question, options(dict|None), answer(normalised); optional others."""
    h = norm_hash(rec["question"], rec.get("options"))
    cur = con.execute(
        """INSERT OR IGNORE INTO questions
           (source,pyq_ref,year,section,subject,topic,type,marks,question,options_json,answer_json,explanation,
            status,verify_agree,model,hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rec["source"], rec.get("pyq_ref"), rec.get("year"), rec["section"], rec.get("subject"), rec.get("topic"),
         rec["type"], rec["marks"], rec["question"],
         json.dumps(rec["options"], ensure_ascii=False) if rec.get("options") else None,
         json.dumps(rec["answer"]), rec.get("explanation"), rec.get("status", "pending"),
         rec.get("verify_agree"), rec.get("model"), h))
    return cur.rowcount == 1          # False = duplicate, ignored
