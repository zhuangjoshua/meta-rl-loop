import os, json, base64, io, sys, urllib.request, urllib.error
from PIL import Image

key = os.environ["OPENAI_API_KEY"]
BG = (11, 11, 20, 255)  # #0B0B14

SPECS = [
    {
        "name": "coscale_icon",
        "size": "1024x1024",
        "prompt": (
            "A logo icon with a fully TRANSPARENT background (alpha). Centered in the frame, "
            "a rounded-square tile filled with deep electric indigo #4F46E5 with a soft outer "
            "glow, occupying about 72 percent of the frame. Inside the tile: four ascending "
            "vertical rounded bars in pure white of increasing height from left (short) to "
            "right (tall) forming a bar chart, and a thin bright cyan #22D3EE line crossing "
            "the tops of the bars rising left to right, ending in a small upward arrowhead at "
            "the top right. Flat geometric premium fintech SaaS brand mark, crisp, very high "
            "contrast. Only the tile is visible; everything outside the tile is transparent. "
            "NO text, NO letters, NO words, NO numbers."
        ),
    },
    {
        "name": "coscale_lockup",
        "size": "1536x1024",
        "prompt": (
            "A horizontal logo lockup with a fully TRANSPARENT background (alpha). On the left: "
            "a rounded-square deep indigo #4F46E5 tile containing four ascending white bars and "
            "a bright cyan #22D3EE rising trend line ending in a small up arrow. To the right of "
            "the tile: the single word spelled exactly C o s c a l e as 'Coscale' in a clean "
            "modern geometric sans-serif, with the letters 'Co' in bright cyan #22D3EE and "
            "'scale' in pure white, large, crisp, correct spelling and kerning. Flat premium "
            "vector style. Everything outside the icon and the word is transparent. Nothing else."
        ),
    },
    {
        "name": "coscale_emblem",
        "size": "1024x1024",
        "prompt": (
            "A logo symbol with a fully TRANSPARENT background (alpha). Centered: an elegant "
            "geometric monogram of the letter C drawn as a thick open ring in deep electric "
            "indigo #4F46E5, broken open on the right side, with a bright cyan #22D3EE arrow "
            "sweeping upward out of the opening to suggest growth and scaling. Minimal, flat, "
            "geometric, soft glow, premium AI brand mark. Everything outside the mark is "
            "transparent. NO words, NO extra text."
        ),
    },
]


def gen(spec):
    body = {
        "model": "gpt-image-1",
        "prompt": spec["prompt"],
        "size": spec["size"],
        "quality": "high",
        "background": "transparent",
        "output_format": "png",
        "n": 1,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=300).read())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:400]); return False
    except Exception as e:
        print("ERR", e); return False

    raw = base64.b64decode(r["data"][0]["b64_json"])
    fg = Image.open(io.BytesIO(raw)).convert("RGBA")
    trans_path = f"/Users/Zygote/Downloads/takyon/brand/{spec['name']}_transparent.png"
    fg.save(trans_path)

    canvas = Image.new("RGBA", fg.size, BG)
    canvas.alpha_composite(fg)
    dark_path = f"/Users/Zygote/Downloads/takyon/brand/{spec['name']}_dark.png"
    canvas.convert("RGB").save(dark_path)
    print("OK", dark_path)
    return True


ok = sum(gen(s) for s in SPECS)
print(f"DONE {ok}/{len(SPECS)}")
sys.exit(0 if ok == len(SPECS) else 1)
