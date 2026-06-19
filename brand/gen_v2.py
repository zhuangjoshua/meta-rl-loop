import os, json, base64, io, sys, urllib.request, urllib.error
from PIL import Image

key = os.environ["OPENAI_API_KEY"]
BG = (11, 11, 20, 255)  # #0B0B14

BAN = (" Absolutely NO bar charts, NO line graphs, NO growth charts, NO upward arrows, "
       "NO generic stock iconography, NO 3D, NO glossy bevel, NO drop shadow, NO photo. "
       "Flat, precise, vector, designed by a world-class branding studio.")

SPECS = [
    {
        "name": "v2_concentric_c",
        "size": "1024x1024",
        "prompt": (
            "A minimalist brand symbol on a fully TRANSPARENT background. The letter C built "
            "from three clean concentric arc strokes nested inside one another at increasing "
            "radius, all opening toward the right, evenly spaced, equal stroke weight, rounded "
            "ends. The arcs step in colour from deep indigo #4F46E5 (outer) through periwinkle "
            "to bright cyan #22D3EE (inner), visualising scale through repetition. Centered, "
            "geometric, premium, modern fintech identity. No text, no letters other than this "
            "implied C." + BAN
        ),
    },
    {
        "name": "v2_monogram_co",
        "size": "1024x1024",
        "prompt": (
            "A bold geometric monogram on a fully TRANSPARENT background that fuses the letters "
            "C and O into a single mark: a thick solid ring (the O) with a clean wedge notch cut "
            "out of its right side so it also reads as a C. One confident continuous shape, flat "
            "solid fill in deep indigo #4F46E5 with a single bright cyan #22D3EE accent edge on "
            "the inner curve. Centered, balanced, distinctive, premium tech brand. No extra text."
            + BAN
        ),
    },
    {
        "name": "v2_orbit_node",
        "size": "1024x1024",
        "prompt": (
            "A minimalist abstract logo symbol on a fully TRANSPARENT background suggesting an "
            "autonomous agent that expands: a solid indigo #4F46E5 circular node at the centre, "
            "encircled by one thin clean cyan #22D3EE elliptical orbit ring tilted slightly off "
            "horizontal, with a single small cyan dot sitting on the ring. Crisp geometry, "
            "generous negative space, premium, modern, centered. No text." + BAN
        ),
    },
    {
        "name": "v2_wordmark",
        "size": "1536x1024",
        "prompt": (
            "A clean modern WORDMARK logo on a fully TRANSPARENT background: the single lowercase "
            "word spelled exactly c-o-s-c-a-l-e as 'coscale' in a custom geometric sans-serif, "
            "even stroke weight, tight precise kerning, perfectly horizontal. The first two "
            "letters 'co' in bright cyan #22D3EE, the remaining 'scale' in soft white. Swiss "
            "minimal typography, confident, premium SaaS brand. Correct spelling, no icon, no "
            "underline." + BAN
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
