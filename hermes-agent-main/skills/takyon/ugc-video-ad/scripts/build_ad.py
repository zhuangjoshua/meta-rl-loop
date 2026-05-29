#!/usr/bin/env python3
"""build_ad.py — orchestrator for the ugc-video-ad skill.

Turns a brief (+ a script.json of dialogue+action beats) into a finished UGC ad:

  reference image (gpt-image-2)  ->  beats grouped into <=10s clips  ->
  per-clip Kling i2v with continuity chaining or jump-cut re-anchoring  ->  ffmpeg concat  ->
  grain/jump-cut post  ->  publish under product/ugc-ads/<slug>/

The >10s requirement: any ad longer than one Kling clip is built as a sequence of
clips each <=10s of speech. In continuity mode, clip N starts from the last frame
of clip N-1 so the same person carries through smoothly. In jumpcut mode, each clip
re-anchors from the original reference image and varies framing so the joins feel
edited rather than smoothed together.

Layer separation: the WORDS + ACTIONS come from the SCRIPT layer (script.json,
authored via references/dialogue-action-framework.md). Everything this script does
is the PRODUCTION layer (references/realism-framework.md, editing-and-stitching.md).

`--dry-run` performs planning ONLY — it prints the clip plan, every compiled Kling
prompt, and the gpt-image prompt, and makes ZERO API calls (no spend). Use it to
verify splitting + the two-layer separation for free.

This script writes durable files into product/ugc-ads/<slug>/ and PRINTS the
business_ugc_ad_write tool payload for the agent to commit. It never fakes that
tool call (see SKILL.md Rules).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline as pl  # noqa: E402

WORDS_PER_SECOND = 3.0  # brisk, energetic UGC pace (fast-creator cadence). Higher =>
                        # more words packed per <=10s clip AND a shorter clip for the
                        # same words, so Kling co-generates faster speech. Tune via --wps.
MIN_CLIP = 3            # Kling v3 Pro floor
MAX_CLIP = 10           # the skill's per-clip ceiling (>10s => split + stitch)
JUMPCUT_CAMERA_VARIANTS = (
    "The camera is a handheld phone front camera at arm's length in a medium close selfie framing with the speaker's face, shoulders, and upper chest in frame.",
    "The camera is the same phone front camera held slightly closer for a tighter close-up framing from forehead to upper chest, with the speaker a little off-center.",
    "The camera is the same phone front camera held a little farther back for a looser framing that shows the face, shoulders, upper chest, and more of the desk or room behind the speaker.",
    "The camera is the same phone front camera but framed from a subtly lower angle with the phone propped near desk height, giving a distinct close talking-head shot on the face and shoulders.",
)


# ---------------------------------------------------------------------------
# Brief / script loading
# ---------------------------------------------------------------------------
def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _beats_from(obj) -> list[pl.Beat]:
    """Accept {beats:[...]}, a bare [...] list, or app-shaped {dialogue_action:[...]}."""
    if isinstance(obj, dict):
        raw = obj.get("beats") or obj.get("dialogue_action") or []
    elif isinstance(obj, list):
        raw = obj
    else:
        raw = []
    beats = []
    for b in raw:
        beats.append(
            pl.Beat(
                dialogue=b["dialogue"],
                action=b.get("action"),
                seconds=b.get("seconds"),
            )
        )
    return beats


def load_inputs(brief_path: str, script_path: str | None):
    brief = _load_json(brief_path)
    if script_path:
        script = _load_json(script_path)
    elif "script" in brief:
        script = brief["script"]
    else:
        raise SystemExit(
            "No script found: pass --script script.json or embed a 'script' key in the brief."
        )
    beats = _beats_from(script)
    if not beats:
        raise SystemExit("Script has no dialogue_action beats.")

    image = pl.ImageSpec(
        subject=brief["subject"],
        wardrobe=brief.get("wardrobe"),
        setting=brief.get("setting"),
        expression=brief.get("expression"),
        framing=brief.get("framing"),
        realism=brief.get("realism", True),
        extra=brief.get("image_extra"),
    )
    p = brief.get("persona") or {}
    persona = pl.Persona(
        accent=p.get("accent"),
        vibe=p.get("vibe"),
        eye_direction=p.get("eye_direction"),
        extra=p.get("extra"),
        camera_motion=p.get("camera_motion"),
    )
    return brief, script, beats, image, persona


# ---------------------------------------------------------------------------
# The >10s splitting: pack consecutive beats into clips of <= MAX_CLIP seconds
# ---------------------------------------------------------------------------
def estimate_seconds(text: str, wps: float) -> float:
    return max(1.0, len(text.split()) / wps)


def plan_clips(beats: list[pl.Beat], wps: float, min_clip: int, max_clip: int) -> list[dict]:
    def finalize(group, est):
        dur = int(max(min_clip, min(max_clip, math.ceil(est))))
        over = est > max_clip + 0.001
        return {"beats": group, "est": round(est, 2), "duration": dur, "over": over}

    clips: list[dict] = []
    cur: list[pl.Beat] = []
    cur_est = 0.0
    for b in beats:
        bsec = b.seconds if b.seconds else estimate_seconds(b.dialogue, wps)
        if cur and cur_est + bsec > max_clip:
            clips.append(finalize(cur, cur_est))
            cur, cur_est = [], 0.0
        cur.append(b)
        cur_est += bsec
    if cur:
        clips.append(finalize(cur, cur_est))
    return clips


def slugify(*parts: str) -> str:
    s = "-".join(p for p in parts if p)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "ugc-ad"


def clip_persona(persona: pl.Persona, clip_idx: int, transition_mode: str) -> pl.Persona:
    if transition_mode != "jumpcut":
        return persona
    variant = JUMPCUT_CAMERA_VARIANTS[clip_idx % len(JUMPCUT_CAMERA_VARIANTS)]
    if persona.camera_motion:
        motion = (
            f"{persona.camera_motion} "
            f"Treat this clip as a distinct hard-cut framing from the previous one: {variant}"
        )
    else:
        motion = variant
    return replace(persona, camera_motion=motion)


def clip_start_label(clip_idx: int, transition_mode: str) -> str:
    if clip_idx == 0:
        return "reference image"
    if transition_mode == "jumpcut":
        return "reference image (jumpcut re-anchor)"
    return f"last frame of clip {clip_idx - 1} (continuity)"


# ---------------------------------------------------------------------------
# Dry-run / planning report
# ---------------------------------------------------------------------------
def print_plan(brief, image_prompt, clips, persona, slug, transition_mode):
    line = "=" * 72
    print(line)
    print(f"UGC AD PLAN  ·  business={brief.get('business')}  product={brief.get('product')}")
    print(f"slug={slug}   publish -> product/ugc-ads/{slug}/")
    total = sum(c["duration"] for c in clips)
    print(f"clips={len(clips)}   total≈{total}s   (each clip <= {MAX_CLIP}s of speech)")
    print(f"transition_mode={transition_mode}")
    print(line)
    print("\n[gpt-image-2 REFERENCE PROMPT]  (9:16, anti-sheen realism)\n")
    print(image_prompt)
    for i, c in enumerate(clips):
        prompt = pl.compile_clip_prompt(c["beats"], clip_persona(persona, i, transition_mode))
        flag = "  ⚠ speech > cap, clamped (tighten the script)" if c["over"] else ""
        print(f"\n{line}\n[CLIP {i}]  est={c['est']}s  ->  duration={c['duration']}s{flag}")
        start = clip_start_label(i, transition_mode)
        print(f"  start_image = {start}   cfg_scale={pl.CFG_SCALE}   generate_audio=True")
        for j, b in enumerate(c["beats"], 1):
            act = f"  | action: {b.action}" if b.action else ""
            print(f"  beat {j}: \"{b.dialogue}\"{act}")
        print("\n  [compiled Kling prompt]\n  " + prompt.replace("\n", "\n  "))
    print(f"\n{line}\nDRY RUN — no API calls were made, no files written, zero spend.\n{line}")


# ---------------------------------------------------------------------------
# Live generation
# ---------------------------------------------------------------------------
def generate(brief, script, beats, image, persona, clips, slug, args):
    if not pl.ffmpeg_available():
        raise SystemExit("ffmpeg/ffprobe not found on PATH (required for stitching).")
    pl.load_dotenv(args.env_file)

    work = args.workdir or tempfile.mkdtemp(prefix=f"ugc_{slug}_")
    os.makedirs(work, exist_ok=True)
    print(f"workdir: {work}")

    image_prompt = pl.compile_image_prompt(image)
    ref_path = os.path.join(work, "reference.png")
    print("generating reference image (gpt-image-2)…")
    pl.generate_image(image_prompt, ref_path, size=pl.IMAGE_SIZE_9_16)

    clip_paths: list[str] = []
    start_image = ref_path
    apply_jumpcuts = args.jumpcuts or args.transition_mode == "jumpcut"
    if args.transition_mode == "jumpcut":
        print("jumpcut mode: every clip re-anchors from the original reference image and uses a distinct framing.")
    for i, c in enumerate(clips):
        clip_start_image = ref_path if args.transition_mode == "jumpcut" else start_image
        prompt = pl.compile_clip_prompt(c["beats"], clip_persona(persona, i, args.transition_mode))
        print(f"clip {i}: uploading {clip_start_label(i, args.transition_mode)} + generating {c['duration']}s Kling clip…")
        image_url = pl.upload_image(clip_start_image)
        video_url = pl.generate_clip(image_url, prompt, c["duration"], generate_audio=True)
        cp = os.path.join(work, f"c{i}.mp4")
        pl.download(video_url, cp)
        clip_paths.append(cp)
        if args.transition_mode == "continuity" and i < len(clips) - 1:
            start_image = pl.extract_last_frame(cp, os.path.join(work, f"last{i}.png"))

    stitched = os.path.join(work, "stitched.mp4")
    print(f"stitching {len(clip_paths)} clip(s)…")
    pl.concat_clips(clip_paths, stitched)

    final = stitched
    if not args.skip_post:
        post_out = os.path.join(work, "ad.mp4")
        postpass = os.path.join(os.path.dirname(os.path.abspath(__file__)), "postpass.sh")
        cmd = ["bash", postpass, stitched, post_out]
        if apply_jumpcuts:
            cmd.append("--jumpcuts")
        print("running postpass (grain/real-camera" + (" + jump cuts" if apply_jumpcuts else "") + ")…")
        subprocess.run(cmd, check=True)
        final = post_out

    # Publish into product/ugc-ads/<slug>/ (durable local filesystem state).
    pub_dir = os.path.join(args.out_root, "ugc-ads", slug)
    os.makedirs(pub_dir, exist_ok=True)
    ad_path = os.path.join(pub_dir, "ad.mp4")
    _copy(final, ad_path)
    _copy(ref_path, os.path.join(pub_dir, "reference.png"))
    with open(os.path.join(pub_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2)

    seconds = _duration_seconds(ad_path)
    rel_ad = os.path.relpath(ad_path)
    print(f"\nDONE -> {rel_ad}  ({seconds:.2f}s, {len(clips)} clips)")
    _print_tool_payload(brief, slug, rel_ad, seconds, len(clips), script)


def _copy(src: str, dst: str) -> None:
    import shutil

    shutil.copyfile(src, dst)


def _duration_seconds(path: str) -> float:
    try:
        info = pl.probe(path)
        for s in info.get("streams", []):
            if s.get("codec_type") == "video" and s.get("duration"):
                return float(s["duration"])
    except Exception:
        pass
    return 0.0


def _print_tool_payload(brief, slug, rel_ad, seconds, n_clips, script):
    """Print the EXACT business_ugc_ad_write args for the agent to commit.

    This script does not (and must not) call the tool itself — recording durable
    Takyon state is the agent's job via the business tool. See SKILL.md Rules.
    """
    payload = {
        "business": brief.get("business"),
        "value": {
            "slug": slug,
            "path": rel_ad,
            "seconds": round(seconds, 2),
            "n_clips": n_clips,
            "script": script,
        },
        "idempotency_key": f"{slug}-{int(seconds)}s-{n_clips}c",
    }
    print("\n--- business_ugc_ad_write payload (agent must call this tool) ---")
    print(json.dumps(payload, indent=2))
    print("--- end payload ---")


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a UGC video ad from a brief + script.")
    ap.add_argument("--brief", required=True, help="brief.json (production inputs; may embed 'script')")
    ap.add_argument("--script", help="script.json of dialogue_action beats (else brief['script'])")
    ap.add_argument("--out-root", default="product", help="publication root (default: product)")
    ap.add_argument("--slug", help="override the publication slug")
    ap.add_argument("--dry-run", action="store_true", help="plan only; no API calls, no spend")
    ap.add_argument("--jumpcuts", action="store_true", help="extra silence-drop reframe cuts in postpass")
    ap.add_argument(
        "--transition-mode",
        choices=("continuity", "jumpcut"),
        default="continuity",
        help="continuity = chain each clip from the previous last frame; jumpcut = re-anchor each clip from the original reference image and vary framing",
    )
    ap.add_argument("--skip-post", action="store_true", help="skip grain/jump-cut post pass")
    ap.add_argument("--workdir", help="scratch dir for clips (default: a temp dir)")
    ap.add_argument("--env-file", default=".env", help="local .env to load (default: ./.env)")
    ap.add_argument("--wps", type=float, default=WORDS_PER_SECOND,
                    help="words/sec for clip planning (brisk UGC pace; raise to pack more / speak faster)")
    ap.add_argument("--max-clip", type=int, default=MAX_CLIP, help="max seconds of speech per clip")
    ap.add_argument("--min-clip", type=int, default=MIN_CLIP, help="min clip seconds (model floor)")
    args = ap.parse_args(argv)

    brief, script, beats, image, persona = load_inputs(args.brief, args.script)
    clips = plan_clips(beats, args.wps, args.min_clip, args.max_clip)
    slug = args.slug or slugify(brief.get("business", ""), brief.get("product", ""))
    image_prompt = pl.compile_image_prompt(image)

    if args.dry_run:
        print_plan(brief, image_prompt, clips, persona, slug, args.transition_mode)
        return 0

    generate(brief, script, beats, image, persona, clips, slug, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
