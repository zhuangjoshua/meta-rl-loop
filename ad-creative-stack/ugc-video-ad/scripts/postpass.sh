#!/usr/bin/env bash
# postpass.sh IN OUT [--jumpcuts]   — PRODUCTION post pass for a stitched UGC ad.
#
# Default: a grain / real-camera realism pass (subtle sensor noise, tiny channel
# shift, gentle contrast/sat, soft vignette). This is the always-safe step and is
# what the orchestrator runs by default. In the Kling pipeline the *cuts* already
# come from the stitch seams (each clip is a separate generation, so the subject's
# position naturally pops at every join = a motivated jump cut), so grain alone is
# usually all the post you need.
#
# --jumpcuts: optional EXTRA cuts inside the take using the silence-drop technique
# (generalized from sora_assemble.sh). It finds the small silences between phrases
# and rebuilds the video from the SPEECH spans only, dropping the silent slivers so
# the subject's position pops at each cut (the signature of a real edit, not a
# pause). Composition varies per shot (scale AND position) for an edited feel.
#
# NO-UPSCALE RULE (hard): every crop is delivered at the TIGHTEST crop's native
# size, so the tight shots are native pixels and looser shots only ever downscale.
# Upscaling a crop back up reads as blur — never do it.
set -euo pipefail

IN="${1:?usage: postpass.sh IN OUT [--jumpcuts]}"
OUT="${2:?usage: postpass.sh IN OUT [--jumpcuts]}"
MODE="${3:-}"

FPS="${POST_FPS:-30}"
SILENCE_DB="${SILENCE_DB:--30dB}"   # quieter than this counts as silence
SILENCE_DUR="${SILENCE_DUR:-0.15}"  # min silence length to treat as a phrase gap
ENC=(-c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p)
AAC=(-c:a aac -b:a 160k -ar 48000)
GRAIN="noise=alls=6:allf=t,rgbashift=rh=-1:bh=1,eq=contrast=1.04:saturation=1.03,vignette=PI/6,setsar=1"

grain_only () {
  ffmpeg -y -i "$IN" -vf "${GRAIN},fps=${FPS}" "${ENC[@]}" "${AAC[@]}" "$OUT" -loglevel error
  echo "DONE -> $OUT (grain/real-camera pass)"
}

if [[ "$MODE" != "--jumpcuts" ]]; then
  grain_only
  exit 0
fi

# ---- --jumpcuts: silence-drop reframe cuts ----
read -r W H < <(ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height -of csv=p=0 "$IN" | tr ',' ' ')
DUR=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$IN")

# Delivery size = tightest crop (0.80) rounded to even -> tight shots are native.
read -r DW DH < <(awk -v W="$W" -v H="$H" \
  'BEGIN{printf "%d %d\n", int(W*0.80/2)*2, int(H*0.80/2)*2}')

# Compositions cycled across shots: (cw ch x y) as fractions. cw>=0.80 so the
# crop is always >= delivery -> downscale or native, never upscale.
comp_cw=(0.94 0.80 0.80 0.90)
comp_ch=(0.94 0.80 0.80 0.90)
comp_x=(0.03 0.06 0.10 0.09)
comp_y=(0.03 0.10 0.04 0.06)
NCOMP=${#comp_cw[@]}

crop_for () {  # idx -> "cw:ch:x:y" (even, clamped inside the frame)
  local i=$(( $1 % NCOMP ))
  awk -v W="$W" -v H="$H" -v fcw="${comp_cw[$i]}" -v fch="${comp_ch[$i]}" \
      -v fx="${comp_x[$i]}" -v fy="${comp_y[$i]}" 'BEGIN{
    cw=int(W*fcw/2)*2; ch=int(H*fch/2)*2;
    x=int(W*fx/2)*2;   y=int(H*fy/2)*2;
    if (x+cw>W) x=W-cw; if (y+ch>H) y=H-ch; if (x<0) x=0; if (y<0) y=0;
    printf "%d:%d:%d:%d", cw, ch, x, y;
  }'
}

# Detect silences, then emit the KEPT speech spans (complement of the silences).
LOG=$(mktemp); trap 'rm -f "$LOG"' EXIT
ffmpeg -i "$IN" -af "silencedetect=noise=${SILENCE_DB}:d=${SILENCE_DUR}" -f null - 2>"$LOG" || true
SPANS=$(awk -v dur="$DUR" '
  /silence_start:/ { v=$0; sub(/.*silence_start:[ ]*/,"",v); sub(/[ |].*/,"",v); starts[ns++]=v+0 }
  /silence_end:/   { v=$0; sub(/.*silence_end:[ ]*/,"",v);   sub(/[ |].*/,"",v); ends[ne++]=v+0 }
  END {
    prev=0;
    for (i=0;i<ns;i++){ if (starts[i]-prev>0.25) printf "%.3f,%.3f\n", prev, starts[i]; prev=ends[i]; }
    if (dur-prev>0.25) printf "%.3f,%.3f\n", prev, dur;
  }' "$LOG")

if [[ -z "$SPANS" ]]; then
  echo "no phrase silences found (threshold ${SILENCE_DB}/${SILENCE_DUR}); falling back to grain-only."
  grain_only
  exit 0
fi

T=$(mktemp -d); trap 'rm -f "$LOG"; rm -rf "$T"' EXIT
LIST="$T/list.txt"; : > "$LIST"
i=0
while IFS=, read -r S E; do
  [[ -z "$S" ]] && continue
  CROP=$(crop_for "$i")
  # Seek by DURATION (-ss then -t) so audio+video stay aligned and there is no
  # -ss/-to timeline ambiguity across ffmpeg builds.
  LEN=$(awk -v s="$S" -v e="$E" 'BEGIN{printf "%.3f\n", e-s}')
  # -nostdin: this ffmpeg runs inside a `while read` loop whose stdin is the
  # SPANS herestring; without it ffmpeg drains that stdin and corrupts later
  # iterations (the next read starts mid-line).
  ffmpeg -nostdin -y -i "$IN" -ss "$S" -t "$LEN" \
    -vf "crop=${CROP},scale=${DW}:${DH},setsar=1,fps=${FPS}" \
    "${ENC[@]}" "${AAC[@]}" "$T/k$i.mp4" -loglevel error
  echo "file '$T/k$i.mp4'" >> "$LIST"
  i=$((i+1))
done <<< "$SPANS"

# Concat the speech spans + grain pass in one re-encode.
ffmpeg -y -f concat -safe 0 -i "$LIST" \
  -vf "${GRAIN},fps=${FPS}" "${ENC[@]}" "${AAC[@]}" "$OUT" -loglevel error
echo "DONE -> $OUT (${i} jump-cut shots, silence-dropped, grain pass; delivery ${DW}x${DH})"
