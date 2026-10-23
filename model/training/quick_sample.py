#!/usr/bin/env python3
"""
quick_sample.py  --  load the trained adapter in a FRESH process and print a few raw generations.
Colab:   %cd .../model/training   then   !python quick_sample.py
Tells you (1) whether the model writes real questions and (2) what share of outputs parse as the expected JSON.
"""
import argparse, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE, *HERE.parents):
    if (_d / "common.py").exists():
        sys.path.insert(0, str(_d)); break
else:
    sys.exit("common.py not found. It must sit in the 'model' folder.")
from common import build_prompt, parse_model_json                              # noqa: E402

CASES = [("CS", "MCQ", 2, "Operating Systems"), ("CS", "NAT", 1, "Algorithms"), ("CS", "MSQ", 2, "Databases"),
         ("CS", "MCQ", 1, "Computer Networks"), ("CS", "NAT", 2, "Digital Logic"), ("GA", "MCQ", 1, None)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=str(HERE / "lora_v1")); ap.add_argument("--tokens", type=int, default=600)
    a = ap.parse_args()
    from unsloth import FastLanguageModel
    import torch, transformers
    transformers.logging.set_verbosity_error()
    model, tok = FastLanguageModel.from_pretrained(model_name=a.adapter, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    ok = 0
    for sec, typ, marks, subj in CASES:
        prompt = build_prompt(sec, typ, marks, subj)
        text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
        inp = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inp, max_new_tokens=a.tokens, max_length=None, do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                                 pad_token_id=tok.pad_token_id)
        gen = tok.decode(out[0, inp.input_ids.shape[1]:], skip_special_tokens=True)
        parsed = parse_model_json(gen)
        ok += parsed is not None
        print("=" * 80, f"\n{prompt}\nparsed as JSON: {parsed is not None}\n{gen[:900]}")
    print(f"\n{ok}/{len(CASES)} outputs parsed as JSON")


if __name__ == "__main__":
    main()
