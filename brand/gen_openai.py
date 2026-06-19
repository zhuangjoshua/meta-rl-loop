import os, json, base64, sys, urllib.request, urllib.error

key = os.environ["OPENAI_API_KEY"]

SPECS = [
    {
        "out": "/Users/Zygote/Downloads/takyon/brand/coscale_icon_dark.png",
        "size": "1024x1024",
        "prompt": (
            "App icon logo, 1:1 square. The ENTIRE frame is filled edge to edge with a "
            "solid deep near-black background, color hex #0B0B14. Centered on it sits a "
            "single glowing rounded-square tile filled with deep electric indigo #4F46E5, "
            "occupying about 70 percent of the frame, with a soft outer glow. Inside the "
            "indigo tile: four ascending vertical rounded bars in pure white of increasing "
            "height from left (short) to right (tall), forming a bar chart, and a thin "
            "bright cyan #22D3EE line crossing the tops of the bars rising left to right, "
            "ending in a small upward arrowhead at the top right. Minimal flat geometric "
            "premium fintech SaaS brand mark, crisp, very high contrast, perfectly centered, "
            "NO text, NO letters, NO words, NO numbers."
        ),
    },
    {
        "out": "/Users/Zygote/Downloads/takyon/brand/coscale_lockup_dark.png",
        "size": "1536x1024",
        "prompt": (
            "Horizontal logo lockup on a solid deep near-black background #0B0B14 that fills "
            "the entire frame edge to edge. On the left: a rounded-square deep indigo #4F46E5 "
            "app icon containing four ascending white bars with a bright cyan #22D3EE rising "
            "trend line ending in a small up arrow. To the right of the icon: the single word "
            "spelled exactly C o s c a l e as 'Coscale' in a clean modern geometric sans-serif, "
            "the letters 'Co' in bright cyan #22D3EE and 'scale' in soft white, large, crisp, "
            "correct spelling, good kerning. Flat premium vector style, dark mode, high "
            "contrast, balanced spacing, professional tech startup brand. Nothing else."
        ),
    },
    {
        "out": "/Users/Zygote/Downloads/takyon/brand/coscale_emblem_dark.png",
        "size": "1024x1024",
        "prompt": (
            "Abstract logo symbol, 1:1 square, on a solid deep near-black background #0B0B14 "
            "filling the entire frame. A single elegant geometric monogram: the letter C formed "
            "as a thick open ring in deep indigo #4F46E5, broken open on the right side, with a "
            "bright cyan #22D3EE arrow sweeping upward out of the opening to suggest growth and "
            "scaling. Minimal, flat, geometric, glowing softly, premium AI brand mark, perfectly "
            "centered, lots of negative space, NO words, NO extra text."
        ),
    },
]


def gen(spec):
    body = {
        "model": "gpt-image-1",
        "prompt": spec["prompt"],
        "size": spec["size"],
        "quality": "high",
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
        d = r["data"][0]
        with open(spec["out"], "wb") as f:
            f.write(base64.b64decode(d["b64_json"]))
        print("OK", spec["out"])
        return True
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:400])
    except Exception as e:
        print("ERR", e)
    return False


ok = sum(gen(s) for s in SPECS)
print(f"DONE {ok}/{len(SPECS)}")
sys.exit(0 if ok == len(SPECS) else 1)
