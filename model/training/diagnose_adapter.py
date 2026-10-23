#!/usr/bin/env python3
"""
diagnose_adapter.py  --  find out WHY generation is garbage. Prints a short report (a few lines), nothing huge.
Colab (fresh runtime):   %cd .../model/training   then   !python diagnose_adapter.py
"""
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=str(HERE / "lora_v1"))
    a = ap.parse_args()
    from unsloth import FastLanguageModel
    import torch, transformers
    transformers.logging.set_verbosity_error()
    model, tok = FastLanguageModel.from_pretrained(model_name=a.adapter, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    prompt = "Generate a GATE CSE question. Section: Computer Science. Subject: Operating Systems. Type: MCQ. Marks: 2."
    text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt").to(model.device)
    print("\n##### REPORT #####")
    print("template:", repr(text))
    print("prompt tokens:", inp.input_ids.shape[1], "| weights dtype:", next(model.parameters()).dtype)
    print("eos:", tok.eos_token, tok.eos_token_id, "| pad:", tok.pad_token, tok.pad_token_id)

    def nexttok(label):
        with torch.no_grad():
            lg = model(**inp).logits[0, -1].float()
        bad = ("NaN " if torch.isnan(lg).any() else "") + ("INF" if torch.isinf(lg).any() else "")
        v, i = torch.softmax(lg, -1).topk(5)
        print(f"{label:28s} {bad}top5:", [(tok.decode([int(t)]), round(float(x), 3)) for x, t in zip(v, i)])

    def gen(label, **kw):
        with torch.no_grad():
            out = model.generate(**inp, max_new_tokens=60, do_sample=False, max_length=None, pad_token_id=tok.pad_token_id, **kw)
        print(f"{label:28s}", repr(tok.decode(out[0, inp.input_ids.shape[1]:], skip_special_tokens=False))[:260])

    with model.disable_adapter():
        nexttok("BASE  next token"); gen("BASE  generate")
    nexttok("ADAPTER next token"); gen("ADAPTER generate"); gen("ADAPTER generate no-cache", use_cache=False)
    print("##### END #####")


if __name__ == "__main__":
    main()
