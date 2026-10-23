#!/usr/bin/env python3
"""
ocr_scanned.py  --  turn scanned / image-only GATE PDFs into text that build_dataset.py can read.

It finds every scanned PDF in q_papers/ (little or no text layer), transcribes each page, and saves
    model/preprocessing/ocr_cache/<pdf-name>.txt
build_dataset.py picks these files up automatically on its next run. Nothing else to configure.

Two engines:
  qwen_vl    (default; Colab free T4 GPU)   reads the page like a human, writes math/code properly
  tesseract  (CPU, emergency fallback)      TESTED: finds question numbers but mangles code/math and drops
                                            option letters on boxed layouts (2021). build_dataset.py ignores
                                            tesseract text unless you pass --allow-tesseract.

Colab (recommended).  Put the whole project folder on Google Drive, then in a GPU notebook:
    from google.colab import drive; drive.mount('/content/drive')
    !pip install -q pdfplumber pillow transformers accelerate
    %cd /content/drive/MyDrive/gate_project/model/preprocessing
    !python ocr_scanned.py --pages 1-3 --only 2017-Set-1     # quick trial first: check ocr_cache/ output
    !python ocr_scanned.py                                    # then everything
If a page comes out blank or gibberish on the T4, add:  --dtype float32   (slower but safe)

Options:  --only 2021        only PDFs whose name contains "2021"
          --force            redo pages that are already cached
          --dpi 200          render resolution (300 default for tesseract, 200 for qwen_vl)
Progress is saved page by page, so if Colab disconnects just run the same command again.
"""
import argparse, re, sys
from pathlib import Path
import pdfplumber

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / "ocr_cache"

PROMPT = (
    "Transcribe this GATE exam page exactly as plain text.\n"
    "Rules:\n"
    "1. Start every question with 'Q.<number>'. If the page shows 'Question Number : N', write 'Q.N'.\n"
    "2. Put each option on its own line like '(A) ...', '(B) ...'.\n"
    "3. Write ALL mathematics in LaTeX between $...$ (subscripts, fractions, summations, set symbols).\n"
    "4. Keep program code and tables line by line.\n"
    "5. Ignore watermarks, logos, page headers/footers and the 'Correct/Wrong' marks line.\n"
    "6. If a question contains a diagram/figure/graph you cannot write as text, put [FIGURE] where it is.\n"
    "Output only the transcription, no comments."
)

PROMPT_ONE = (
    "Transcribe this single GATE exam question exactly as plain text. Do NOT write a question number.\n"
    "Put each option on its own line like '(A) ...', '(B) ...'. Write ALL mathematics in LaTeX between $...$. "
    "Keep program code and tables line by line. Ignore watermarks and logos. "
    "If it contains a diagram/figure you cannot write as text, put [FIGURE] where it is. "
    "Output only the transcription."
)
EMPTY = "[OCR_EMPTY]"


def is_scanned(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        chars = sum(len(p.extract_text() or "") for p in pdf.pages)
        return chars / max(len(pdf.pages), 1) < 400


def render(page, dpi):
    return page.to_image(resolution=dpi).original.convert("RGB")


def render_crop(page, y0, y1, dpi):
    box = (0, max(y0, 0), page.width, min(y1, page.height))
    return page.crop(box).to_image(resolution=dpi).original.convert("RGB")


def question_bands(page):
    """2017/2020 style pages: every question starts with a real-text header 'Question Number : N  Correct..'.
    Use those header positions to cut the page into one band per question -> [(number, y0, y1), ...]"""
    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    heads = []
    for i, w in enumerate(words[:-1]):
        if w["text"] == "Question" and words[i + 1]["text"].lower().startswith("num"):
            line = " ".join(x["text"] for x in words if abs(x["top"] - w["top"]) < 3)
            m = re.search(r":\s*(\d+)", line)
            heads.append((w["bottom"], int(m.group(1)) if m else None))
    if not heads:
        return []
    offs = [n - k for k, (_, n) in enumerate(heads) if n is not None]
    base = max(offs) if offs else 1          # broken digits only ever make a number smaller -> take the max
    bands = []
    for k, (bottom, _) in enumerate(heads):
        nxt = heads[k + 1][0] - 18 if k + 1 < len(heads) else page.height
        bands.append((base + k, bottom + 2, nxt))
    return bands


# ───────────────────────── engines ─────────────────────────
def make_tesseract():
    import pytesseract
    from PIL import ImageOps

    def run(img, prompt=None):
        g = ImageOps.autocontrast(img.convert("L"))
        g = g.point(lambda v: 255 if v > 175 else 0)             # wipes faint watermarks
        return pytesseract.image_to_string(g, config="--oem 1 --psm 6")
    return run


def make_qwen_vl(model_name, dtype="float16"):
    import torch
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_name, torch_dtype=getattr(torch, dtype), device_map="auto")
    proc = AutoProcessor.from_pretrained(model_name, min_pixels=256 * 28 * 28, max_pixels=1600 * 28 * 28)

    def run(img, prompt=None):
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt or PROMPT}]}]
        text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inp = proc(text=[text], images=[img], return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inp, max_new_tokens=1800, do_sample=False)
        return proc.batch_decode(out[:, inp.input_ids.shape[1]:], skip_special_tokens=True)[0]
    return run


# ───────────────────────── cleanup ─────────────────────────
def tidy(text):
    out = []
    for ln in text.splitlines():
        s = ln.strip().strip("|¦_[]{} ").strip()
        if not s or not re.search(r"[A-Za-z0-9]", s):
            continue
        m = re.match(r"Question\s*N\w*\s*[:;]?\s*(\d+)", s, re.I)               # 2017/2020 style header
        if m:
            out.append(f"Q.{m.group(1)}"); continue
        if re.search(r"Correct\s*:\s*\d.*Wrong", s):
            continue
        s = re.sub(r"^Q\.?\s?(\d{1,2})\b[\s.:|]*", r"Q.\1 ", s)                   # "Q3 " / "Q 3|" -> "Q.3 "
        s = re.sub(r"^[\(\[]?([A-D])[\)\]]\s*[|]?\s*", r"(\1) ", s)               # "A)" "[A]" -> "(A) "
        out.append(s.replace("|", " ").rstrip())
    # a lone "Q.n" line gets glued to the next line so the splitter sees "Q.n text"
    joined, i = [], 0
    while i < len(out):
        if re.fullmatch(r"Q\.\d+", out[i]) and i + 1 < len(out):
            joined.append(out[i] + " " + out[i + 1]); i += 2
        else:
            joined.append(out[i]); i += 1
    return "\n".join(joined)


# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["tesseract", "qwen_vl"], default="qwen_vl")
    ap.add_argument("--dtype", default="float16", choices=["float16", "float32", "bfloat16"])
    ap.add_argument("--pages", default="", help="e.g. 1-3 (trial run)")
    ap.add_argument("--model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    ap.add_argument("--root", default=str(ROOT)); ap.add_argument("--only", default="")
    ap.add_argument("--dpi", type=int); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    dpi = a.dpi or (300 if a.engine == "tesseract" else 200)
    if a.pages and "-" not in a.pages: a.pages += "-" + a.pages
    pdfs = [f for f in sorted((Path(a.root) / "q_papers").glob("*.pdf")) if a.only in f.name and is_scanned(f)]
    if not pdfs:
        print("no scanned PDFs found"); return
    print("scanned PDFs:", *[f.name for f in pdfs], sep="\n  ")
    run = make_tesseract() if a.engine == "tesseract" else make_qwen_vl(a.model, a.dtype)
    CACHE.mkdir(exist_ok=True)

    for f in pdfs:
        pdir = CACHE / f.stem; pdir.mkdir(exist_ok=True)
        with pdfplumber.open(f) as pdf:
            n = len(pdf.pages)
            lo, hi = (map(int, a.pages.split("-"))) if a.pages else (1, n)
            for i, page in enumerate(pdf.pages, 1):
                if not lo <= i <= hi: continue
                pf = pdir / f"page_{i:03d}.txt"
                if pf.exists() and not a.force and pf.stat().st_size > 30 and EMPTY not in pf.read_text(encoding="utf-8"):
                    continue                                   # done already (blank/failed pages are redone)
                bands = question_bands(page)
                if bands:                                      # one question at a time = no dense-page failures
                    chunks = []
                    for num, y0, y1 in bands:
                        txt = tidy(run(render_crop(page, y0, y1, dpi), PROMPT_ONE))
                        if len(re.findall(r"[A-Za-z0-9]", txt)) < 8:        # blank -> retry bigger
                            txt = tidy(run(render_crop(page, y0, y1, int(dpi * 1.5)), PROMPT_ONE))
                        if len(re.findall(r"[A-Za-z0-9]", txt)) < 8:
                            print(f"  WARNING {f.name} page {i}: Q.{num} came out blank", flush=True); txt = EMPTY
                        lines = txt.splitlines() or [EMPTY]
                        lines[0] = f"Q.{num} " + lines[0]
                        chunks.append("\n".join(lines))
                    out_txt = "\n".join(chunks)
                else:
                    out_txt = tidy(run(render(page, dpi)))
                    if len(re.findall(r"[A-Za-z0-9]", out_txt)) < 8:
                        print(f"  WARNING {f.name} page {i}: blank output", flush=True); out_txt = EMPTY
                pf.write_text(out_txt, encoding="utf-8")
                print(f"  {f.name}: page {i}/{n}" + (f"  ({len(bands)} questions)" if bands else ""), flush=True)
        parts = [p.read_text(encoding="utf-8") for p in sorted(pdir.glob("page_*.txt"))]
        (CACHE / f"{f.stem}.txt").write_text(f"# engine={a.engine}\n" + "\n".join(parts), encoding="utf-8")
        print(f"saved {CACHE / (f.stem + '.txt')}")
    print("\nNow re-run:  python build_dataset.py")


if __name__ == "__main__":
    main()
