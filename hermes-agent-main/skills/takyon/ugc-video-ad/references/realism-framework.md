# Realism Framework (PRODUCTION LAYER)

How the ad **looks and moves**. This is the original UGC-workflow realism layer — the
hard-won settings that make a generated person read as a real human filming themselves.
It is fully independent of the [script layer](dialogue-action-framework.md): copy never
sets these knobs, and these knobs never rewrite copy.

The whole game is defeating the "AI tell." Two levers matter most: **the reference
image's skin/lighting**, and **restraint in the motion**.

## 1. The reference image is where realism is won or lost

`build_ad.py` makes one 9:16 reference still with **gpt-image-2** (`864x1536`) from the
brief's `subject` / `wardrobe` / `setting` / `expression`, compiled through the anti-sheen
realism block in `scripts/pipeline.py` (`_REALISM_BLOCK`). The video model faithfully
animates whatever that image gives it, so a fake-looking reference ⇒ a fake-looking ad.

**Skin (the #1 differentiator).** gpt-image-2 has no negative prompt, so imperfections
are prompted *for*: visible pores across cheeks/nose/forehead, slightly uneven tone,
faint redness, soft under-eye shadows, a subtle oily T-zone, fine vellus hair, tiny
asymmetry — **completely unretouched**. Highlights are soft and uneven, **never** a
waxy/plastic/glossy/airbrushed sheen.

**Lighting.** Natural and **uneven** — it falls mainly from one side, one cheek brighter
and the other dropping into soft shadow, with gentle highlight-to-shadow contrast.
> **Flat, even, bright-white light is the single biggest AI tell.** Never light the face
> flatly or evenly. Always give it a real directional source (a window, a lamp).

**Real ≠ ugly.** The goal is an attractive, real-looking everyday person. Realism comes
from skin texture + directional light + natural posture — **not** from unattractive
features. Write `subject` as someone genuinely good-looking *and* real.

**Camera-roll feel.** Mild sensor noise/grain (especially in shadow), a touch of lens
softness, natural (not perfectly neutral) white balance, shallow phone-lens depth. It is
*not* a professional photo: no studio light, no makeup styling, no beauty filter.

**Caught mid-expression, not a held pose.** A posed, perfectly-centered, symmetric face
is a tell — real grab-shots catch a person *between* expressions. So the reference is
prompted as a candid moment: a slightly asymmetric expression as if caught mid-word
(one eyebrow a touch higher, an uneven half-smile, eyes with real moisture and a true
catchlight), an unposed gaze not perfectly centered on the lens, and **authentically
imperfect framing** — very slightly off-level and off-center, focus not laser-sharp.
This is the still-image half of "humans have microexpressions"; the video half is in
§2. (Encoded in `pipeline._REALISM_BLOCK`.) Keep it *subtle* — off by a hair, not a
crooked, sloppy snapshot.

## 2. Motion: energy in the delivery, not flailing in the body

The ad should feel **lively and energetic** — a real creator who's genuinely excited,
talking fast and bright, leaning in on the hook. But put that energy in the **right
place**:

- **Energy lives in delivery + pacing** — a brisk, upbeat vocal read (the script layer
  writes dense, fast copy; clips are planned at ~3 words/sec) and a punchy hook, plus
  **small, purposeful** gestures and lean-ins that *punctuate* points.
- **Not in constant body motion.** The failure mode is still **over-animation** — if the
  body never stops moving, hands flail, or the head bobs nonstop, the person reads as
  **manic / "on drugs" / theatrical**. Gestures land on emphasis, then settle.
- **Micro-expressions, not one frozen face.** The video half of human imperfection: the
  face should play through *fleeting* micro-expressions — quick flickers in the brow and
  eyes, a genuine smile that comes and goes rather than one held grin, small real
  reactions to the person's own words. These live in the **face**, not the body, so they
  add life without adding flailing.

So: animated and engaged, **grounded and real** — never stiff, but never frantic,
jittery, or hyperactive. The energy you feel should come from *how the person talks* and
the live play of their face, not from how much they wiggle.
(`pipeline.CAMERA_REALISM` encodes exactly this balance.)

## 3. Camera direction goes LAST, at low cfg_scale — and the *direction* is flexible

In each clip prompt the camera description is appended **last** and paired with
**`cfg_scale=0.3`**. The motion model weights the tail of the prompt, so putting the
camera feel last lets it land without overpowering the spoken beats, and the low cfg
keeps motion from being whipped into exaggeration.

**The camera direction is not one fixed shot — it's a free choice per ad.** Don't assume
every ad is the same arm's-length selfie. The framing/motion is a knob you pick to fit
the ad: an arm's-length selfie, a walk-and-talk down a street, a phone propped on a desk
at a slight angle, a slow handheld push-in, following someone around a kitchen, etc. Set
it per ad via the brief's `persona.camera_motion`; leaving it null uses the default
arm's-length selfie (`pipeline.DEFAULT_CAMERA_DIRECTION`).

**Whichever direction you choose, realism is the invariant.** This mirrors the image side
(flexible `framing` + always-on `_REALISM_BLOCK`): the chosen camera direction is always
followed by `pipeline.CAMERA_REALISM`, so any framing still reads as **real, organic
phone footage** — natural micro-movement and slight organic unsteadiness, **never**
locked-off, tripod-rigid, gimbal-smooth, or a fake mechanical/CGI camera move. Pick the
direction freely; the "real type" feel is non-negotiable and applied automatically.

## 4. One unified voice is the ad's spine

Each clip co-generates its own synced voice + lips. Keep a **single, consistent speaker
and voice** across the whole ad (one `persona`, one reference identity carried by
continuity). The unbroken voice is what makes stitched clips feel like one take. (Why
clips stay short and stitched: Kling's long single takes drift voice/lips near the end —
see [editing-and-stitching.md](editing-and-stitching.md).)

## 5. Product shows in-scene, on a real device — never a UI cutaway

If the product appears, it appears **in the camera, on a real device the person is
holding or glancing at** (a phone in hand, a laptop on the desk). **Never** cut to a
fullscreen UI screenshot / screen recording — that breaks the handheld-selfie illusion
instantly. Put the product moment in a beat's `action` ("tips her head toward the open
laptop"), not in a separate screen-capture shot.

## Model note

This skill uses **Kling v3 Pro image-to-video** (via fal.ai) so a **specific reference
identity** carries across clips via continuity. (Text-to-video models can yield even more
realistic skin but give a *different* person each generation and can't be driven from a
chosen face — out of scope here.)

## The anti-AI-tell checklist

- [ ] Reference skin shows pores/texture/redness; **no waxy sheen**.
- [ ] Lighting is directional and uneven; **not flat, even, or bright white**.
- [ ] Reference is **caught mid-expression** (slight asymmetry, unposed gaze, faintly
      imperfect framing) — **not** a posed, symmetric, dead-centered face.
- [ ] Subject is attractive **and** real (real ≠ ugly).
- [ ] Energy is in the **delivery** (brisk, bright, punchy hook) + purposeful gestures
      + **fleeting facial micro-expressions**; body is grounded — **not**
      flailing/jittery/hyperactive/theatrical.
- [ ] Camera direction is **last** in the prompt; `cfg_scale=0.3`.
- [ ] One consistent voice/identity across all clips.
- [ ] Product (if shown) is in-scene on a real device; **no UI screenshot cutaways**.
