#!/usr/bin/env python3
"""
train_qwen.py  --  QLoRA fine-tune of Qwen on your GATE train.jsonl / val.jsonl.  Free Colab T4 is enough.

Colab setup (Runtime > Change runtime type > T4 GPU), then in cells:
    from google.colab import drive; drive.mount('/content/drive')
    !pip install -q unsloth
    %cd /content/drive/MyDrive/gate_project/model/training
    !python train_qwen.py                      # trains, saves the adapter to <project>/model/training/lora_v1
    !python train_qwen.py --export-gguf        # (later) also makes a .gguf for Ollama / llama.cpp

Disconnected? Run the same command again: it resumes from the last checkpoint (saved every 25 steps).

What to watch in the log:
    train loss falling smoothly, 'eval_loss' falling then flattening  -> good
    eval_loss going UP while train loss keeps falling                 -> overfitting; use --epochs 1 or 2
"""
import argparse, os, shutil
from pathlib import Path

# Unsloth "padding-free" packing needs flash-attn/xformers kernels to keep sequences apart. On the Colab T4 here xformers
# cannot load, and a first run with padding-free ON produced an adapter that only emits <tool_call> tokens. Keep it off.
os.environ.setdefault("UNSLOTH_DISABLE_AUTO_PADDING_FREE", "1")

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "preprocessing" / "output"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="unsloth/Qwen3-4B-Instruct-2507-unsloth-bnb-4bit",
                    help="any Unsloth 4-bit Qwen instruct model; 4B is the safe size for a T4")
    ap.add_argument("--data", default=str(DATA)); ap.add_argument("--out", default=str(HERE / "lora_v1"))
    ap.add_argument("--epochs", type=float, default=2); ap.add_argument("--max-seq", type=int, default=1536)
    ap.add_argument("--lr", type=float, default=2e-4); ap.add_argument("--r", type=int, default=16)
    ap.add_argument("--export-gguf", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="delete the old adapter + checkpoints first (otherwise training resumes from them)")
    a = ap.parse_args()
    out = Path(a.out); ckpt = out.parent / (out.name + "_checkpoints")
    if a.fresh:
        for d in (out, ckpt):
            shutil.rmtree(d, ignore_errors=True)
        print("removed old adapter and checkpoints")

    from unsloth import FastLanguageModel                      # must be imported before transformers/trl
    from unsloth.chat_templates import train_on_responses_only
    import torch
    from datasets import load_dataset
    from trl import SFTTrainer, SFTConfig

    model, tok = FastLanguageModel.from_pretrained(model_name=a.model, max_seq_length=a.max_seq,
                                                   load_in_4bit=True, dtype=None)
    model = FastLanguageModel.get_peft_model(
        model, r=a.r, lora_alpha=a.r, lora_dropout=0, bias="none", random_state=3407,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth")

    ds = load_dataset("json", data_files={"train": f"{a.data}/train.jsonl", "validation": f"{a.data}/val.jsonl"})
    # Built by hand on purpose: this model's chat template adds an empty "<think></think>" block in front of every answer in
    # training text, which taught the adapter to emit that rarely-trained special token first (-> endless <tool_call> garbage).
    # This layout is byte-for-byte what apply_chat_template(add_generation_prompt=True) gives at generation time, plus the answer.
    def to_text(ex):
        user, answer = ex["messages"][0]["content"], ex["messages"][1]["content"]
        return {"text": f"<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n{answer}<|im_end|>\n"}
    ds = ds.map(to_text, remove_columns=ds["train"].column_names)
    lens = sorted(len(tok(t).input_ids) for t in ds["train"]["text"])
    print(f"train {len(ds['train'])} | val {len(ds['validation'])} | tokens/example: median {lens[len(lens)//2]}, "
          f"max {lens[-1]} (limit {a.max_seq}; longer ones get cut)")
    has_val = len(ds["validation"]) > 0

    bf16 = torch.cuda.is_bf16_supported()
    cfg = dict(dataset_text_field="text", per_device_train_batch_size=2, gradient_accumulation_steps=4,
               num_train_epochs=a.epochs, learning_rate=a.lr, warmup_steps=10, lr_scheduler_type="linear",
               optim="adamw_8bit", weight_decay=0.01, fp16=not bf16, bf16=bf16, logging_steps=5,
               save_steps=25, save_total_limit=2, output_dir=str(ckpt), report_to="none", seed=3407)
    if has_val:
        cfg.update(eval_strategy="steps", eval_steps=25, per_device_eval_batch_size=2)

    def make_cfg():                                            # TRL renamed max_seq_length -> max_length
        for extra in ({"padding_free": False}, {}):
            for key in ("max_seq_length", "max_length"):
                try:
                    return SFTConfig(**cfg, **extra, **{key: a.max_seq})
                except TypeError:
                    continue
        raise RuntimeError("could not build SFTConfig - paste me the error")

    kw = dict(model=model, train_dataset=ds["train"], eval_dataset=ds["validation"] if has_val else None, args=make_cfg())
    try:
        trainer = SFTTrainer(tokenizer=tok, **kw)
    except TypeError:
        trainer = SFTTrainer(processing_class=tok, **kw)
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")      # loss only on the question JSON

    try:                                                       # sanity check: the loss must cover ONLY the answer JSON
        lab = trainer.train_dataset[0]["labels"]
        shown = tok.decode([x for x in lab if x != -100])
        print("\nTRAINED-ON TEXT of example 0 (must be the question JSON only, no 'Generate a GATE...' prompt):\n", shown[:300])
        if not shown.strip().startswith("{"):
            print("WARNING: the training target does not start with '{' - the target is not the bare question JSON. Stop and send me this log.")
    except Exception as e:
        print("(could not inspect labels:", e, ")")

    resume = ckpt.exists() and any(ckpt.glob("checkpoint-*"))
    trainer.train(resume_from_checkpoint=True if resume else None)

    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out)); tok.save_pretrained(str(out))
    print(f"\nadapter saved to {out}")

    FastLanguageModel.for_inference(model)                     # quick look at what it writes now
    for prompt in ["Generate a GATE CSE question. Section: Computer Science. Subject: Operating Systems. Type: MCQ. Marks: 2.",
                   "Generate a GATE CSE question. Section: Computer Science. Subject: Algorithms. Type: NAT. Marks: 1."]:
        text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
        inp = tok(text, return_tensors="pt").to(model.device)
        gen = model.generate(**inp, max_new_tokens=500, max_length=None, do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                             pad_token_id=tok.pad_token_id)
        txt = tok.decode(gen[0, inp.input_ids.shape[1]:], skip_special_tokens=True)
        print("\n>>", prompt, "\n", txt[:700])
        if not txt.strip().startswith("{"):
            print("!!! SAMPLE LOOKS BROKEN (should start with '{'). Do not use this adapter; send me this log.")

    if a.export_gguf:
        model.save_pretrained_gguf(str(out.parent / (out.name + "_gguf")), tok, quantization_method="q4_k_m")
        print("GGUF written next to the adapter")


if __name__ == "__main__":
    main()
