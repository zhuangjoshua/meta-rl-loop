# Editing & Stitching (PRODUCTION LAYER)

How clips are split, joined, cut, and finished. Production only — independent of the
[script layer](dialogue-action-framework.md).

## 1. >10s ads are built as ≤10s clips, stitched with continuity

A single Kling take longer than ~10s drifts: the co-generated **voice and lips desync
near the end**. So any ad longer than one clip is built as a **sequence of short clips**,
each holding **≤10s of speech**, then stitched.

`build_ad.py` does this automatically:

1. **Plan** — pack consecutive script beats into clips so each clip's estimated speech is
   `≤ max-clip` (default **10s**, at a brisk **~3 words/sec**, or a beat's explicit
   `seconds`). The fast pace packs more content per clip and makes Kling co-generate a
   quicker, more energetic read. Each clip is clamped to Kling's **3–10s** range.
2. **Continuity** — in the default `--transition-mode continuity`, clip 0 starts from the
   **reference image** and clip *N* starts from the **last frame of clip N-1**
   (`extract_last_frame`, a frame ~0.1s before the end). Same face, wardrobe, room, and
   light carry across the whole ad.
3. **Jumpcut option** — in `--transition-mode jumpcut`, every clip re-anchors from the
   **original reference image** instead, and the clip prompt varies framing per clip so the
   seams feel more like intentional creator edits than one flowing take.
4. **Stitch** — `concat_clips` normalizes every clip to clip-0's dimensions/fps and joins
   them, **keeping each clip's own synced audio** (short clips never hit the drift).

**The stitch seam is itself a motivated cut.** Because each clip is a fresh generation
resuming from the continuity frame, the subject's micro-position naturally pops at every
join — that discontinuity reads as an intentional jump cut, not a glitch. For most ads,
this plus the grain pass is all the editing you need.

## 2. Cuts must feel motivated — never a zoom ramp

When you add cuts in post, they must feel like a real edit on a real beat:

- **Use hard cuts between different static framings** (medium ↔ tight), landing on
  content beats and tracking the speaker's energy.
- **Never** use a slow synthetic zoom/pan push-in (Ken Burns on a still). A mechanical
  zoom-in/zoom-out "for no reason" is an obvious AI-slideshow tell and breaks the
  handheld illusion. If motion is wanted, it comes from the real handheld camera, not a
  programmatic ramp.

## 3. A real cut removes time — it is not just a reframe

Merely changing the crop on a **contiguous** take (no time removed) leaves motion running
straight through the cut, so it reads as a **hiccup / pause**, not an edit. A real jump
cut **removes a sliver of time** so the subject's position pops — that discontinuity is
what sells "edited."

`postpass.sh --jumpcuts` implements this with the silence-drop technique:

1. `silencedetect` finds the small silences **between phrases**.
2. Rebuild the video from the **speech spans only**, dropping the silent slivers (audio +
   video cut **together** — never clip speech, so lips stay synced inside each span).
3. Vary composition per span — **scale *and* position**, not just zoom — so each shot is
   visibly a different framing.

Result: position pops at each cut + tighter pacing, with continuity (same
person/room/wardrobe/voice). This is **optional** polish on top of the stitch-seam cuts.

## 4. NEVER upscale — deliver at native-crop size

A tight punch-in must be **native pixels**. Punching into a low-res frame and scaling
back up reads as **blur** (the classic mushy AI punch-in).

Rule enforced in `postpass.sh`: the delivery size equals the **tightest crop's native
size**, so tight shots are native and looser shots only ever **downscale** — uniformly
sharp, no soft segments. If you need sharp tight shots at a larger delivery size,
regenerate the source at a higher native resolution instead of upscaling a crop.

## 5. Grain / real-camera finishing pass (always)

The final pass adds the camera-roll texture that sells realism, applied to the whole
video: light film grain (`noise`), a tiny RGB channel shift (`rgbashift`), gentle
contrast/saturation (`eq`), and a soft `vignette`. This is `postpass.sh`'s default mode
and runs even without `--jumpcuts`.

## Quick reference

| Need | Do |
|------|-----|
| Ad > one clip | Auto: beats grouped ≤10s, continuity-stitched (`build_ad.py`) |
| Cut between framings | Hard cut on a beat; medium ↔ tight; **no zoom ramp** |
| Make a cut feel edited | Drop inter-phrase silence so position pops (`--jumpcuts`) |
| Tight punch-in | Native crop only — **never upscale**; deliver at the tight-crop size |
| Final texture | Grain/real-camera pass (default `postpass.sh`) |
