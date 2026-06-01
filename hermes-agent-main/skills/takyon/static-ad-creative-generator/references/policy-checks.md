# Policy Checks

Two layers: **(A) build-time honesty rules** this skill enforces on itself, and **(B)
platform ad-policy red flags** for Meta and Reddit. `validate_spec.py` lints specs against
the machine-checkable signals at the bottom; the rest is human/agent judgment captured in
`qa.policy_risks`.

---

---

## A. Platform ad-policy red flags

### 1. Personal attributes (Meta — high risk)
Ads must not assert or imply that you know a person's protected/sensitive attribute:
race or ethnicity, religion, age, sexual orientation, gender identity, disability, **medical
or mental-health condition**, financial status, criminal history, or trade-union/political
membership.

- ❌ "Are you depressed?" · "Struggling with debt?" · "Meet other people with diabetes."
- ✅ "Find calm." · "Take control of your spending." · "A simpler way to manage your day."
- **Rule of thumb:** agitate the *situation*, address the *desire* — never label the *person*.
  Prefer "a simpler way to…" over "you/your `<condition>`".

### 2. Misleading or unrealistic claims
- No guarantees of results, exaggerated outcomes, or "get rich / cure / lose X lbs fast".
- No fabricated urgency (fake countdowns, false scarcity) — use real deadlines only.
- Claims with numbers must be substantiable and typical, not cherry-picked.

### 3. Before/after & body/health/finance imagery (restricted)
- Idealized before/after, zoomed "problem area" body parts, and implied dramatic
  health/weight/financial transformations are restricted on Meta.
- Show **product state** (cluttered → organized dashboard) rather than implied bodily or
  financial outcomes, unless the result is real, typical, and disclosed.

### 4. Misleading / non-functional UI
- No fake interactive elements in a static image: fake play buttons, fake "X"/close,
  fake cursors, fake system alerts/notifications, fake progress bars, or fake platform UI
  (a fake "Reels" play overlay, a fake comment box) that implies functionality the static
  image can't deliver.
- A realistic mock of *your own* product UI is fine; a control that baits a click it can't
  perform is not.

### 5. Sensational, shocking, or adult content
- No gore, shock imagery, sexually suggestive content, or harassment/punching-down humor.

### 6. Restricted & prohibited categories
- Extra rules apply to (or outright ban) alcohol, gambling, prescription/OTC drugs,
  supplements, financial products, dating, weapons, political/social issues. If the product
  is in one of these, flag it in `qa.policy_risks` and verify the current category policy.

### 7. Intellectual property
- No competitor logos, brand marks, characters, or licensed meme stills you lack rights to.
- Comparison claims naming a competitor must be true and current.

### 8. Reddit specifics
- No impersonation of real users/subreddits as authentic UGC; no fabricated upvote/award
  counts presented as real; disclose paid promotion as required; match subreddit norms.

---

## Pre-flight policy checklist (populate `qa.policy_risks`)

- [ ] No personal-attribute targeting (situation/desire framing, not "you + condition").
- [ ] No guaranteed/exaggerated outcomes; numbers are real and typical.
- [ ] Before/after shows product state or real+disclosed results only.
- [ ] No misleading/non-functional UI or third-party impersonation.
- [ ] Every testimonial / rating / logo / metric is real & rights-cleared, **or** labeled illustrative.
- [ ] No restricted-category violation (or it's flagged and verified).
- [ ] No unlicensed competitor/IP/meme assets.
- [ ] Reddit: no impersonation, sponsorship disclosed.

`qa.policy_risks` should be an **empty array only when every box is genuinely clear**;
otherwise list the residual risk and its mitigation.

---

## Machine-checkable lint signals (used by `validate_spec.py`)

The validator emits **warnings** (not hard failures) on these heuristics — an agent or
human must still confirm:

1. **Personal-attribute phrasing:** `copy.overlay_text` / `copy.primary_text` matching
   patterns like `are you <adjective>?`, `do you suffer`, `struggling with`, or naming a
   protected condition → warn.
2. **Absolute/guarantee language:** `guaranteed`, `100%`, `cure`, `instantly`, `risk-free`,
   `miracle`, `lose \d+ (lbs|pounds|kg)` → warn.
3. **Borrowed authority:** `as seen in`, `#1 doctor`, `clinically proven` without a cited
   source → warn.
4. **Proof-bearing angles unconfirmed:** if `strategy.angle` ∈ {`social_proof`,
   `reddit_native`, `imessage`, `fake_ui`, `testimonial`} **and** `qa.policy_risks` does not
   contain `real`, `rights-cleared`, `illustrative`, or `representative` → warn (you must
   declare whether the proof is real or labeled).
5. **Competitor/IP:** `product.must_not_show` should include fabricated third-party logos /
   endorsements; if a competitor name appears in copy, warn to verify the claim.
6. **Fake-UI controls:** `prompting.final_image_prompt` mentioning `play button`, `cursor`,
   `notification`, `close button` → warn (possible non-functional UI).

Keep these heuristics in sync with `scripts/validate_spec.py`.
