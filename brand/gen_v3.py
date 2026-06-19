import os, json, base64, io, sys, urllib.request, urllib.error
from PIL import Image

key = os.environ["OPENAI_API_KEY"]
BG = (11, 11, 20, 255)  # #0B0B14

SPECS = [
    {
        "name": "v3_blossom",
        "size": "1024x1024",
        "prompt": (
            "A minimalist abstract tech logo symbol on a fully TRANSPARENT background, in the "
            "visual language of a leading AI research company mark: a single continuous looping "
            "ribbon line that weaves into a symmetrical six-fold blossom / knot with radial "
            "symmetry, uniform rounded stroke weight, smooth interlacing curves, perfectly "
            "centered with generous negative space. Pure flat white strokes, monochrome. Clean, "
            "geometric, iconic, premium. No text, no letters, no numbers, no gradient, no "
            "shadow, no 3D, no photo."
        ),
    },
    {
        "name": "v3_spark",
        "size": "1024x1024",
        "prompt": (
            "A minimalist AI 'spark' logo symbol on a fully TRANSPARENT background, like a modern "
            "AI assistant glyph: one four-pointed sparkle star with gracefully concave pinched "
            "curved sides, perfectly symmetrical and centered, accompanied by a single smaller "
            "four-pointed sparkle to the upper right. Smooth gradient fill from deep indigo "
            "#4F46E5 to bright cyan #22D3EE. Flat, soft, premium, iconic. No text, no letters, "
            "no shadow, no 3D."
        ),
    },
    {
        "name": "v3_burst",
        "size": "1024x1024",
        "prompt": (
            "A minimalist radial logo symbol on a fully TRANSPARENT background in the spirit of a "
            "premium AI brand mark: eight rounded tapered petal blades arranged in a symmetrical "
            "radial sunburst around a central point, evenly spaced, centered, generous negative "
            "space. Solid flat bright cyan #22D3EE, monochrome single colour. Geometric, clean, "
            "iconic. No text, no letters, no gradient, no shadow, no 3D."
        ),
    },
    {
        "name": "v3_lockup",
        "size": "1536x1024",
        "prompt": (
            "A clean modern logo lockup on a fully TRANSPARENT background in the style of top AI "
            "startups: on the left a small minimalist four-pointed sparkle mark filled with an "
            "indigo #4F46E5 to cyan #22D3EE gradient; to its right the single lowercase word "
            "spelled exactly c-o-s-c-a-l-e as 'coscale' in a refined geometric grotesque "
            "sans-serif, pure white, even weight, tight precise kerning, correct spelling, "
            "perfectly horizontal. Minimal, premium, balanced spacing. No tagline, no shadow, "
            "no underline."
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
    fg.save(f"/Users/Zygote/Downloads/takyon/brand/{spec['name']}_transparent.png")
    canvas = Image.new("RGBA", fg.size, BG)
    canvas.alpha_composite(fg)
    out = f"/Users/Zygote/Downloads/takyon/brand/{spec['name']}_dark.png"
    canvas.convert("RGB").save(out)
    print("OK", out)
    return True


ok = sum(gen(s) for s in SPECS)
print(f"DONE {ok}/{len(SPECS)}")
sys.exit(0 if ok == len(SPECS) else 1)
