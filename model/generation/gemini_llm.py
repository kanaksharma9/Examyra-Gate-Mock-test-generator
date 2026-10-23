"""
gemini_llm.py  --  thin Gemini wrapper: API key lookup, throttling, HARD per-call timeout, retries, clean stop when quota runs out.

Key:  Colab Secrets (key icon) named GEMINI_API_KEY (notebook access ON), or the environment variable GEMINI_API_KEY.
      Free key: https://aistudio.google.com/apikey   Never paste the key into code or a chat.   pip install -q google-genai
Env GEMINI_API=interactions makes it use client.interactions.create first; default is client.models.generate_content (stable API).
"""
import os, re, sys, threading, time

CALL_TIMEOUT = 90         # seconds: a single request that takes longer is abandoned and retried


class QuotaExhausted(Exception):
    pass


MIN_INTERVAL = 8.0
CALLS = 0
_client, _last = None, 0.0


def set_rpm(rpm):
    global MIN_INTERVAL
    MIN_INTERVAL = 60.0 / max(float(rpm), 0.1)


def _key():
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not k:
        try:
            from google.colab import userdata
            k = userdata.get("GEMINI_API_KEY")
        except Exception:
            pass
    if not k:
        sys.exit("GEMINI_API_KEY not found. Add it to Colab Secrets (notebook access ON) or set the environment variable.")
    os.environ["GEMINI_API_KEY"] = k
    return k


def client():
    global _client
    if _client is None:
        _key()
        from google import genai
        try:
            from google.genai import types
            _client = genai.Client(http_options=types.HttpOptions(timeout=CALL_TIMEOUT * 1000))   # ms
        except Exception:
            _client = genai.Client()
    return _client


def _call(model, prompt, as_json=False):
    c = client()
    order = ["interactions", "generate"] if os.environ.get("GEMINI_API") == "interactions" else ["generate", "interactions"]
    err = None
    for kind in order:
        try:
            if kind == "generate":
                cfg = None
                if as_json:
                    from google.genai import types
                    cfg = types.GenerateContentConfig(response_mime_type="application/json")   # Gemini then escapes LaTeX backslashes correctly
                return c.models.generate_content(model=model, contents=prompt, config=cfg).text or ""
            return c.interactions.create(model=model, input=prompt).output_text or ""
        except AttributeError as e:                      # this SDK version does not have that API -> try the other
            err = e
    raise err


def _with_timeout(fn, seconds):
    """run fn in a daemon thread so a hung network call can never block us for more than `seconds`."""
    box = {}
    def run():
        try:
            box["v"] = fn()
        except BaseException as e:
            box["e"] = e
    t = threading.Thread(target=run, daemon=True); t.start(); t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"no reply from Gemini after {seconds}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def ask(model, prompt, retries=5, as_json=False):
    global _last, CALLS
    last_err, kind = "", ""
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            text = _with_timeout(lambda: _call(model, prompt, as_json), CALL_TIMEOUT + 10)
            CALLS += 1
            return text
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            low = last_err.lower()
            if "429" in low or "resource_exhausted" in low or "quota" in low:
                m = re.search(r"retry(?:Delay)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*s", last_err, re.I)
                pause = min(float(m.group(1)) + 1 if m else 15 * 2 ** attempt, 120); kind = "quota"
            elif any(t in low for t in ("503", "500", "unavailable", "overloaded", "internal", "timeout", "deadline", "connection", "no reply")):
                pause = 5 * 2 ** attempt; kind = "server"
            else:
                raise RuntimeError(f"Gemini call failed (check the model name '{model}' / key): {last_err[:500]}")
            print(f"   [{model}] try {attempt + 1}/{retries} failed ({kind}): {last_err[:160]} -> waiting {pause:.0f}s", flush=True)
            time.sleep(pause)
    if kind == "quota":
        raise QuotaExhausted(last_err[:300])
    raise RuntimeError(f"Gemini kept failing: {last_err[:300]}")
