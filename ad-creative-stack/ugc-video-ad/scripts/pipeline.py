"""Self-contained UGC-ad pipeline primitives (PRODUCTION LAYER).

Vendored from the original FastAPI pipeline (app/imageprompt.py, app/prompt.py,
app/providers/openai_image.py, app/providers/fal_provider.py, app/stitch.py) so the
skill is a portable drop-in with no dependency on that app package.

Layer boundary (do not blur): this file is the PRODUCTION layer only — image
realism, Kling image-to-video, ffmpeg stitching. The words a person says and the
action paired with each line are the SCRIPT layer and come from a script.json
authored with references/dialogue-action-framework.md. Nothing here writes copy.

Network/codec deps (httpx, fal_client, ffmpeg) are imported lazily inside the
functions that need them so the live build can fail only when a provider-backed step is reached.

Provider credentials: on a RUNTIME plane the creative gateway injects the safebox proxy env
(TAKYON_CREATIVE_VIA_PROXY=1 + TAKYON_SAFEBOX_URL + TAKYON_SAFEBOX_TOKEN); the OpenAI image and FAL
Kling calls then route through the safebox creative gate and the raw OPENAI_API_KEY / FAL_KEY is NEVER read
on that plane (see _proxy_base_and_token). Direct local-provider debugging requires explicit key
values passed by the caller; this module does not read provider keys from ambient env or local .env.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

# fal endpoint for local-dev Kling v3 Pro image-to-video. Production proxy mode ignores overrides and
# calls the fixed Safebox route instead of passing arbitrary FAL paths through authority.
KLING_ENDPOINT = os.environ.get(
    "FAL_KLING_ENDPOINT", "fal-ai/kling-video/v3/pro/image-to-video"
)
KLING_PROXY_ROUTE = "/v1/providers/fal/kling-image-to-video"
# 9:16 portrait size accepted by gpt-image-2 (matches app/service.py _ASPECT_SIZES).
IMAGE_SIZE_9_16 = "864x1536"
PROVIDER_SECRET_ENV_NAMES = {"OPENAI_API_KEY", "FAL_KEY", "FAL_API_KEY"}


# ---------------------------------------------------------------------------
# Data model (lightweight; mirrors app/models.py DialogueAction / ImagePrompt /
# UGCPrompt without pulling in pydantic, to stay portable).
# ---------------------------------------------------------------------------
@dataclass
class Beat:
    """One spoken line plus the action performed while saying it (SCRIPT layer)."""

    dialogue: str
    action: str | None = None
    seconds: float | None = None  # explicit duration override for clip planning


@dataclass
class ImageSpec:
    """Structured framework for the hyper-real reference portrait."""

    subject: str
    wardrobe: str | None = None
    setting: str | None = None
    expression: str | None = None
    framing: str | None = None
    realism: bool = True
    extra: str | None = None


@dataclass
class Persona:
    """Non-script delivery attributes carried across every clip."""

    accent: str | None = None
    vibe: str | None = None
    eye_direction: str | None = None
    extra: str | None = None
    camera_motion: str | None = None  # optional framing/motion CHOICE; overrides
    # DEFAULT_CAMERA_DIRECTION. CAMERA_REALISM is always appended after it, so any chosen
    # direction still reads as real footage.


# ---------------------------------------------------------------------------
# Prompt compilers (PRODUCTION realism prose). Verbatim from the app so output
# matches the validated pipeline.
# ---------------------------------------------------------------------------
_DEFAULT_FRAMING = (
    "Front-camera phone selfie held at arm's length, face and upper chest in frame, "
    "slight high angle, mild wide-angle selfie distortion, handheld and slightly off-center"
)

# gpt-image has no negative-prompt field, so anti-AI-sheen guidance is prose the
# model must obey; imperfections are prompted FOR (it defaults to a smoothed face).
_REALISM_BLOCK = (
    "This must read as a real, unedited photo straight off a phone's camera roll — "
    "indistinguishable from a genuine photograph and never a generated, rendered, CGI, "
    "stock, or AI-looking image. "
    "Skin has real micro-texture: visible pores across the cheeks, nose, forehead and chin, "
    "slightly uneven skin tone with subtle natural color variation and faint redness, faint "
    "under-eye shadows, a subtle oily T-zone shine, fine vellus facial hair, tiny natural "
    "imperfections and slight asymmetry — completely unretouched. "
    "It is a candid moment caught mid-expression, not a held pose: a genuine, slightly "
    "asymmetric expression as if caught mid-word or mid-thought — one eyebrow a touch "
    "higher than the other, a real uneven half-smile (one corner pulling a little more), "
    "eyes with natural moisture and a true-to-life catchlight, an unposed gaze that is not "
    "perfectly centered on the lens. The framing is authentically imperfect, like a real "
    "grab-shot someone took of themselves — very slightly off-level and off-center, focus "
    "not laser-sharp, with the faint over-sharpening and digital noise of a phone's front "
    "camera. Specular highlights on the "
    "skin are soft and slightly uneven, never a smooth waxy, plastic, glossy or airbrushed "
    "sheen. Lighting is natural and uneven: it falls mainly from one side so one side of the "
    "face is brighter and the other side drops into soft shadow, with gentle "
    "highlight-to-shadow contrast that models the face; natural exposure that is not blown "
    "out, not flat, and not a bright even white. Photorealistic candid smartphone photo: "
    "mild sensor noise and fine grain (especially in the shadows), a touch of natural lens "
    "softness, natural white balance with a slight real-world color cast (not perfectly "
    "neutral), shallow phone-lens depth of field. This is NOT a professional photo — no "
    "makeup styling, no retouching, no studio lighting, no beauty filter, not evenly lit, "
    "not flat bright white light. Do not smooth, stylize, glamorize, beautify or evenly "
    "light the face."
)

# Camera direction is appended LAST in every clip prompt (the motion model weights the
# tail of the prompt) and paired with a low cfg_scale so the handheld feel lands without
# overpowering the spoken beats or whipping motion into exaggeration. It is split into
# two parts on purpose (mirrors the image side: flexible `framing` + always-on
# `_REALISM_BLOCK`):
#
#   * DEFAULT_CAMERA_DIRECTION — the *framing/motion choice* (which shot this is). Just a
#     default; any ad can pick a different direction via the brief's persona.camera_motion
#     (a walk-and-talk, a propped desk cam, a slow handheld push-in, following someone
#     around a kitchen, …). The chosen direction is NOT where realism lives, so it is free
#     to vary per ad.
#   * CAMERA_REALISM — the always-on realism + delivery invariant, appended AFTER whatever
#     direction is chosen. Whichever camera direction we pick, it must still read as real
#     footage. This is the part that must survive ANY camera-direction override.
DEFAULT_CAMERA_DIRECTION = (
    "The camera is a handheld phone front camera at arm's length in a close selfie "
    "framing on the speaker's face and upper chest."
)

CAMERA_REALISM = (
    "Whatever framing is chosen, it must look like real, organic footage from an actual "
    "phone camera — alive and authentically imperfect, with natural continuous "
    "micro-movement and a slight organic unsteadiness, never locked-off, tripod-rigid, "
    "gimbal-smooth, or a fake mechanical/CGI camera move. The speaker talks with bright, "
    "upbeat, fast-paced energy and a quick natural cadence — engaged and lively, leaning "
    "in on the opening line and using small purposeful hand gestures and head movement to "
    "punctuate each point. Throughout, the face plays through fleeting, natural "
    "micro-expressions — quick flickers in the brow and eyes, a genuine smile that comes "
    "and goes rather than one frozen expression, small real reactions to their own words. "
    "The energy lives in the delivery and pacing, like a real person genuinely excited to "
    "tell a friend — expressive and animated but still grounded and real, never stiff, and "
    "never frantic, jittery, flailing, hyperactive, or theatrical."
)
CFG_SCALE = 0.3


def compile_image_prompt(spec: ImageSpec) -> str:
    """Structured framework -> a single hyper-real, anti-sheen image prompt."""
    parts: list[str] = []
    lead = f"Candid, unposed photo of {spec.subject}"
    if spec.wardrobe:
        lead += f", wearing {spec.wardrobe}"
    if spec.setting:
        lead += f", {spec.setting}"
    if spec.expression:
        lead += f", {spec.expression}"
    parts.append(lead + ".")
    parts.append((spec.framing or _DEFAULT_FRAMING) + ".")
    if spec.realism:
        parts.append(_REALISM_BLOCK)
    if spec.extra:
        parts.append(spec.extra)
    return " ".join(parts)


def compile_clip_prompt(beats: list[Beat], persona: Persona) -> str:
    """SCRIPT beats + persona -> one Kling clip prompt, camera direction LAST.

    Mirrors app/prompt.py but moves the handheld/camera direction to the very end
    (the production realism rule) instead of mid-prompt.
    """
    parts: list[str] = [
        "User-generated-content (UGC) style vertical selfie video of a real person "
        "speaking directly to the camera, natural phone-camera look, realistic lighting."
    ]
    persona_bits: list[str] = []
    if persona.accent:
        persona_bits.append(f"a {persona.accent} accent")
    if persona.vibe:
        persona_bits.append(f"{persona.vibe} energy")
    if persona_bits:
        parts.append("The speaker has " + " and ".join(persona_bits) + ".")
    if persona.eye_direction:
        parts.append(f"Gaze: {persona.eye_direction}.")

    for i, b in enumerate(beats, 1):
        if b.action:
            parts.append(f'Beat {i}: says "{b.dialogue}" while {b.action}.')
        else:
            parts.append(f'Beat {i}: says "{b.dialogue}".')

    if persona.extra:
        parts.append(persona.extra)

    # Camera direction LAST: the chosen framing/motion (flexible — any ad can override
    # it via persona.camera_motion) followed by the always-on realism + delivery
    # invariant, so whichever direction we pick still reads as real footage.
    parts.append(persona.camera_motion or DEFAULT_CAMERA_DIRECTION)
    parts.append(CAMERA_REALISM)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Local config. Provider keys are explicit values only; never ambient env.
# ---------------------------------------------------------------------------
def load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a local .env for non-secret knobs only.

    Provider secrets stay out of the process env for this business skill; live
    renders use the Safebox gate and direct local debugging passes explicit keys.
    """
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in PROVIDER_SECRET_ENV_NAMES:
                continue
            if k and k not in os.environ:
                os.environ[k] = v


def require_explicit_secret(name: str, value: str | None) -> str:
    val = str(value or "").strip()
    if not val:
        raise RuntimeError(
            f"{name} is not available. Use business_ugc_ad_generate for live Takyon renders "
            "or pass an explicit local key file for direct provider debugging."
        )
    return val


@contextlib.contextmanager
def explicit_fal_key_env(fal_key: str | None):
    """Temporarily expose an explicit local FAL key for fal_client only."""
    key = require_explicit_secret("FAL_KEY", fal_key)
    os.environ.pop("FAL_KEY", None)
    os.environ["FAL_KEY"] = key
    try:
        yield
    finally:
        os.environ.pop("FAL_KEY", None)


# ---------------------------------------------------------------------------
# Safebox GATED creative-provider routing (credit-gated secret boundary).
#
# This script runs as a standalone subprocess (it does NOT import the `safebox`
# module and cannot reach the DB to reserve credits or mint a token). The
# creative-credit money gate is AUTHORITATIVE on the safebox: the gateway has
# ALREADY reserved this UGC action's fixed credits and minted a creative
# capability, which it injects as TAKYON_CREATIVE_CAPABILITY_TOKEN (alongside
# TAKYON_CREATIVE_VIA_PROXY=1 + TAKYON_SAFEBOX_URL + TAKYON_SAFEBOX_TOKEN). When
# the flag + URL + capability are present, the OpenAI image call and the FAL
# Kling call route through the GATED provider routes (/v1/providers/openai/images,
# /v1/providers/fal/kling-image-to-video) presenting that capability in the request body; the
# safebox VERIFIES the capability, resolves the real provider key LOCALLY, and
# forwards, so the raw OPENAI_API_KEY / FAL_KEY is NEVER read on this plane. The
# gated route returns the verbatim provider JSON, so the rest of this file
# handles the SAME response shapes as the direct path.
#
# Fail closed: when the gate env is set, a failure surfaces — there is no raw-key
# fallback. The direct provider path is explicit-key only and intended for local
# debugging (gate env absent).
# ---------------------------------------------------------------------------
_VIA_PROXY_ENV = "TAKYON_CREATIVE_VIA_PROXY"
_SAFEBOX_URL_ENV = "TAKYON_SAFEBOX_URL"
_SAFEBOX_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"
_CAPABILITY_TOKEN_ENV = "TAKYON_CREATIVE_CAPABILITY_TOKEN"
# The safebox routes long FAL renders (Kling video) through the FAL queue API and holds this
# connection open while it polls for completion, so this must outlast a full render (the safebox's
# own poll budget is bounded below this). 300s was too short for Kling i2v and surfaced as a 502.
_PROXY_TIMEOUT_S = 960


def _proxy_base_and_token() -> tuple[str, str, str] | None:
    """Return (base_url, internal_token, capability_token) when the gated creative routes should be
    used, else None.

    Used only when the gateway-injected flag, the safebox URL, AND the creative capability are present
    (a credit-gated render). Absent any of them -> None -> the direct provider path runs (local dev /
    safebox host). ``internal_token`` may be "" when the safebox runs tokenless (local rig)."""
    flag = str(os.environ.get(_VIA_PROXY_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}
    if not flag:
        return None
    base = str(os.environ.get(_SAFEBOX_URL_ENV) or "").strip().rstrip("/")
    internal_token = str(os.environ.get(_SAFEBOX_TOKEN_ENV) or "").strip()
    capability = str(os.environ.get(_CAPABILITY_TOKEN_ENV) or "").strip()
    if not base or not capability:
        # Flag set but URL/capability missing: do NOT silently fall back to a raw key on a runtime
        # plane. Fail closed so the boundary breach is visible.
        raise RuntimeError(
            f"{_VIA_PROXY_ENV} is set but {_SAFEBOX_URL_ENV}/{_CAPABILITY_TOKEN_ENV} are missing; "
            "refusing to fall back to a raw provider key on a runtime plane."
        )
    return base, internal_token, capability


def _proxy_post(route: str, payload: dict) -> dict:
    """POST a key-free GATED creative-provider request to the safebox and return the parsed JSON.

    Uses stdlib urllib (this subprocess may not import the `safebox` module). The safebox internal
    token authorizes the request (``Authorization: Bearer``); the creative CAPABILITY is presented in
    the request body (``token``) and proves the credit reservation. Fails closed (RuntimeError) on a
    transport or HTTP error — NEVER falls back to a raw provider key."""
    import urllib.error
    import urllib.request

    coords = _proxy_base_and_token()
    if coords is None:  # pragma: no cover - callers gate on this first
        raise RuntimeError("safebox creative gate is not configured")
    base, internal_token, capability = coords
    url = base + route
    # The gated provider route takes {token: <capability>, payload: <provider body>}.
    body = {"token": capability, "payload": dict(payload or {})}
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if internal_token:
        headers["Authorization"] = f"Bearer {internal_token}"
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"safebox creative gate {route} failed: {exc.code} {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"safebox creative gate {route} unreachable: {exc}") from exc
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"safebox creative gate {route} returned non-JSON: {raw[:200]}") from exc


# ---------------------------------------------------------------------------
# gpt-image-2 (OpenAI Images API, direct).
# ---------------------------------------------------------------------------
def generate_image(
    prompt: str,
    out_path: str,
    *,
    size: str = IMAGE_SIZE_9_16,
    quality: str = "high",
    model: str = "gpt-image-2",
    output_format: str = "png",
    api_key: str | None = None,
) -> str:
    """Generate one reference still and write it to out_path. Returns out_path.

    On a runtime plane (safebox proxy env injected by the gateway) the OpenAI
    image call routes through the safebox PROXY (no raw key on this plane); direct
    local debugging requires an explicit key value. Both paths return the same
    OpenAI JSON shape, so decoding is shared."""
    import base64

    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }

    if _proxy_base_and_token() is not None:
        # Credit-gated plane: the safebox verifies the creative capability, forwards to
        # api.openai.com/v1/images/generations, and returns the verbatim OpenAI JSON. Raw
        # OPENAI_API_KEY is never read here.
        payload = _proxy_post("/v1/providers/openai/images", body)
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"OpenAI image gate returned no image data. Raw: {payload}")
        item = data[0]
        if item.get("b64_json"):
            img = base64.b64decode(item["b64_json"])
        else:
            # The proxy returns the OpenAI body verbatim; gpt-image-2 returns b64_json. A url-only
            # item cannot be fetched key-free here, so fail closed rather than leak a fetch path.
            raise RuntimeError(f"OpenAI image proxy item had no b64 bytes. Raw: {item}")
        with open(out_path, "wb") as f:
            f.write(img)
        return out_path

    import httpx

    api_key = require_explicit_secret("OPENAI_API_KEY", api_key)
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=300) as client:
        r = client.post(f"{base_url}/images/generations", json=body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI image API {r.status_code}: {r.text}")
        payload = r.json()
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"OpenAI returned no image data. Raw: {payload}")
        item = data[0]
        if item.get("b64_json"):
            img = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            resp = client.get(item["url"])
            resp.raise_for_status()
            img = resp.content
        else:
            raise RuntimeError(f"OpenAI image item had no bytes. Raw: {item}")
    with open(out_path, "wb") as f:
        f.write(img)
    return out_path


# ---------------------------------------------------------------------------
# Kling v3 Pro image-to-video (via fal.ai).
# ---------------------------------------------------------------------------
def _file_to_data_uri(path: str) -> str:
    """Encode a local image file as a base64 data URI FAL accepts as an image input.

    Used on the proxy path so we never need the raw FAL key for the CDN upload
    that ``fal_client.upload_file`` would otherwise require."""
    import base64
    import mimetypes

    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def upload_image(src: str, *, fal_key: str | None = None) -> str:
    """Return an image reference FAL can consume. Passes through http(s) URLs.

    On a runtime plane (safebox proxy env set) a local file is encoded as a
    base64 data URI (FAL accepts data URIs as image inputs) so we never need the
    raw FAL key for a CDN upload. Direct local debugging uploads via fal_client
    with an explicit key value."""
    if src.startswith(("http://", "https://", "data:")):
        return src
    if _proxy_base_and_token() is not None:
        return _file_to_data_uri(src)
    import fal_client

    with explicit_fal_key_env(fal_key):
        return fal_client.upload_file(src)


def generate_clip(
    image_url: str,
    prompt: str,
    duration: int,
    *,
    generate_audio: bool = True,
    cfg_scale: float = CFG_SCALE,
    end_image_url: str | None = None,
    endpoint: str = KLING_ENDPOINT,
    extra_args: dict | None = None,
    fal_key: str | None = None,
) -> str:
    """Run one Kling i2v generation. Returns the resulting video URL.

    On a credit-gated plane (safebox gate env set) the call routes through the
    GATED safebox route (POST /v1/providers/fal/kling-image-to-video) presenting the
    creative capability; the safebox forwards to its fixed Kling upstream with
    the real key and returns the verbatim FAL JSON ({"video": {"url": ...}}) — the
    SAME shape fal_client.subscribe returns. Direct local debugging calls
    fal_client.subscribe with an explicit key value."""
    args: dict = {
        "prompt": prompt,
        "start_image_url": image_url,
        "duration": str(duration),
        "generate_audio": generate_audio,
        "cfg_scale": cfg_scale,
    }
    if end_image_url:
        args["end_image_url"] = end_image_url
    if extra_args:
        args.update(extra_args)

    if _proxy_base_and_token() is not None:
        # The gated safebox FAL route is deliberately model-specific; do not pass `endpoint` through
        # the authority boundary. Raw FAL_KEY is never read here.
        result = _proxy_post(KLING_PROXY_ROUTE, args)
    else:
        import fal_client

        with explicit_fal_key_env(fal_key):
            result = fal_client.subscribe(endpoint, arguments=args)
    url = ((result or {}).get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"fal returned no video url. Raw response: {result}")
    return url


def download(url: str, out_path: str) -> str:
    import httpx

    with httpx.Client(timeout=300, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
    return out_path


# ---------------------------------------------------------------------------
# ffmpeg helpers (stitch + continuity). Vendored from app/stitch.py, sync.
# ---------------------------------------------------------------------------
class FFmpegError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode("utf-8", "ignore")[-2000:])
    return proc.stdout.decode("utf-8", "ignore")


def probe(path: str) -> dict:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", path])
    return json.loads(out)


def _video_stream(info: dict) -> dict | None:
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _has_audio(info: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def _fps(vstream: dict) -> float:
    rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "30/1"
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 30.0
    except Exception:
        return 30.0


def extract_last_frame(video_path: str, image_path: str) -> str:
    """Grab a frame ~0.1s before the end — used as a continuity start frame so the
    next clip resumes from the same face/pose (keeps identity across the stitch)."""
    _run(
        ["ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path,
         "-frames:v", "1", "-q:v", "2", image_path]
    )
    return image_path


def _normalize(src: str, dst: str, w: int, h: int, fps: float) -> None:
    """Re-encode a clip to a fixed WxH/fps with an AAC track so a plain concat
    works; clips without audio get a silent track."""
    info = probe(src)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.5f},format=yuv420p"
    )
    common_v = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    common_a = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]
    if _has_audio(info):
        cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, *common_v, *common_a, dst]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", src,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf", vf, "-map", "0:v:0", "-map", "1:a:0", "-shortest",
            *common_v, *common_a, dst,
        ]
    _run(cmd)


def concat_clips(clip_paths: list[str], out_path: str) -> str:
    """Concatenate clips into one mp4, normalizing each to the first clip's
    dimensions/fps so clips from different settings still join cleanly. Per-clip
    audio is preserved (short clips dodge Kling's long-take voice/lip drift)."""
    if not clip_paths:
        raise FFmpegError("No clips to concatenate.")
    if len(clip_paths) == 1:
        shutil.copyfile(clip_paths[0], out_path)
        return out_path

    info0 = probe(clip_paths[0])
    v0 = _video_stream(info0)
    if not v0:
        raise FFmpegError("First clip has no video stream.")
    w, h, fps = int(v0["width"]), int(v0["height"]), _fps(v0)

    work = os.path.join(os.path.dirname(out_path) or ".", "_norm")
    os.makedirs(work, exist_ok=True)
    norm_paths: list[str] = []
    for i, p in enumerate(clip_paths):
        np_ = os.path.join(work, f"n{i:03d}.mp4")
        _normalize(p, np_, w, h, fps)
        norm_paths.append(np_)

    list_file = os.path.join(work, "list.txt")
    with open(list_file, "w") as f:
        for p in norm_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
          "-c", "copy", out_path])
    shutil.rmtree(work, ignore_errors=True)
    return out_path
