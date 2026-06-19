import base64
import json
import os
import sys
import urllib.request
import urllib.error

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("NO_KEY", file=sys.stderr)
    sys.exit(2)

MODELS = ["gemini-2.5-flash-image", "gemini-2.5-flash-image-preview"]


def generate(prompt, out_path):
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    data = json.dumps(body).encode()
    last_err = None
    for model in MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": API_KEY,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = f"{model}: HTTP {e.code} {e.read().decode()[:400]}"
            continue
        except Exception as e:  # noqa
            last_err = f"{model}: {e}"
            continue

        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            last_err = f"{model}: no candidates {json.dumps(payload)[:400]}"
            continue

        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                with open(out_path, "wb") as f:
                    f.write(base64.b64decode(inline["data"]))
                print(f"OK {model} -> {out_path}")
                return True
        last_err = f"{model}: no image part {json.dumps(payload)[:400]}"
    print(f"FAIL {last_err}", file=sys.stderr)
    return False


if __name__ == "__main__":
    spec_path = sys.argv[1]
    with open(spec_path) as f:
        specs = json.load(f)
    ok = 0
    for s in specs:
        if generate(s["prompt"], s["out"]):
            ok += 1
    print(f"DONE {ok}/{len(specs)}")
    sys.exit(0 if ok == len(specs) else 1)
