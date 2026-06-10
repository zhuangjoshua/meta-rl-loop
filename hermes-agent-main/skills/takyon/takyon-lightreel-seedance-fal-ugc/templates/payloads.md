# Lightreel To Seedance To fal Payloads

## Runtime Input Shape

Use this normalized shape as the upstream company input:

```json
{
  "company": {
    "name": "dealloop.ai",
    "category": "bot-free meeting AI for macOS",
    "audience": "sales reps, account executives, founders, revenue teams",
    "core_pain": "meeting follow-up is messy, bot-based note takers are awkward, and decisions get lost after calls",
    "mechanism": "records and transcribes meetings without a bot joining, understands what is on screen, and can take actions during the call",
    "differentiators": [
      "no bot joins the meeting",
      "reads screen context",
      "can send Slack messages",
      "can update CRM",
      "can loop in teammates"
    ],
    "proof": [
      "works on macOS",
      "works across meeting platforms"
    ],
    "cta_goal": "drive trial or download intent"
  },
  "creative_constraints": {
    "duration_seconds": 10,
    "aspect_ratio": "9:16",
    "single_person_only": true,
    "camera_mode": "handheld iPhone selfie",
    "allow_funny_or_irreverent": true,
    "allow_lightly_skitty": true,
    "forbid_product_shots": true,
    "forbid_ui": true,
    "forbid_screen_recordings": true,
    "forbid_overlays": true,
    "forbid_second_character": true
  },
  "reference_image": {
    "storage_url": "https://example-bucket.s3.amazonaws.com/reference-images/<company>/<image>.png",
    "usage": "reference_only"
  }
}
```

## Lightreel Discovery Prompt Framework

Do not preselect the UGC format. Use a prompt like:

```text
Use this product brief, not assumptions.

Product: {name}
Facts: {mechanism and differentiators in plain English}

Do your own discovery work.

Task:
Find the strongest proven viral single-person UGC format for software ads that fits this product. You should choose the format yourself based on what has already worked.

Requirements:
- single person only
- creator recording themselves talking to camera
- can be funny, irreverent, chaotic, confessional, or lightly skitty
- should feel like native creator content, not polished brand ad
- no product shots
- no UI
- no screen recordings
- no on-screen text overlays
- no second character physically present
- optimize for {duration} seconds

Return:
1. the UGC format you chose
2. the kind of hooks that make that format work
3. one strong company-specific spoken script in that format
4. one Seedance-ready prompt for that script

Do not give me a generic testimonial. Find a format that actually performs.
```

## Expected Lightreel Output Shape

Treat Lightreel output like:

```json
{
  "chosen_format": "single-person corporate receipts rant",
  "hook_family": [
    "workplace betrayal",
    "petty leverage",
    "confessional hot take"
  ],
  "spoken_script": "Exact creator line to deliver on camera.",
  "seedance_prompt_draft": "First-pass prompt returned by Lightreel."
}
```

## Seedancify Rules

Take the Lightreel output and preserve:

- chosen format
- hook logic
- tonal posture
- spoken script

Then add Seedance-specific rules:

```text
Use @Image1 as a reference-only identity anchor for the main subject, not as a start frame to recreate literally.
Preserve the same face, age, skin tone, hair, outfit, and overall look throughout the clip.
Vertical 9:16 handheld iPhone selfie video.
One creator only.
Direct-to-camera the entire time.
Tight chest-up framing.
Natural daylight.
Subtle handheld micro-shake.
Authentic creator energy.
No product shots.
No UI.
No screen recordings.
No cutaways.
No on-screen text.
No captions.
No overlay graphics.
No second person.
No face drift.
No outfit drift.
No background morphing.
Spoken dialogue exactly: "{spoken_script}"
Do not treat @Image1 as a literal first frame composition. Use it only for subject identity and look consistency.
```

## fal Payload Template

Use the final Seedance-safe prompt and the reference image URL placeholder:

```json
{
  "model": "bytedance/seedance-2.0/reference-to-video",
  "input": {
    "prompt": "<final seedance-safe prompt>",
    "image_urls": [
      "https://example-bucket.s3.amazonaws.com/reference-images/<company>/<image>.png"
    ],
    "duration": "10",
    "resolution": "720p",
    "aspect_ratio": "9:16",
    "generate_audio": true
  }
}
```

## cURL Example: Lightreel

```bash
curl -X POST https://api.lightreel.ai/v1/chat \
  -H "Authorization: Bearer $LIGHTREEL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Use this product brief, not assumptions. Product: ... Facts: ... Do your own discovery work. Find the strongest proven viral single-person UGC format ..."
  }'
```

## cURL Example: fal

```bash
curl -X POST https://queue.fal.run/bytedance/seedance-2.0/reference-to-video \
  -H "Authorization: Key $FAL_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "<final seedance-safe prompt>",
    "image_urls": [
      "https://example-bucket.s3.amazonaws.com/reference-images/<company>/<image>.png"
    ],
    "duration": "10",
    "resolution": "720p",
    "aspect_ratio": "9:16",
    "generate_audio": true
  }'
```

## Notes

- Replace the object-storage URL placeholder with the runtime-provided reference image URL.
- On Seedance reference-to-video, reference images are passed as `image_urls` and referred to in prompts as `@Image1`, `@Image2`, etc.
- Keep secrets only in runtime environment variables.
- If the product constraints change, rerun the Lightreel discovery step instead of reusing a stale format.
