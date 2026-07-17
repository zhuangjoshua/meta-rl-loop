# Evidence — ad history (Formflow lineage 2, seeded from the original POLICY.md)

GOAL: maximize verified new-customer acquisition per dollar (ROAS primary, CAC
secondary). Formflow, a $29/mo form builder. Declared primary outcome: verified
purchase → the CBO settings-bundle twins the Sales bundle.

Policy full text: `policy-v4.md` (918 lines, sha 8d36e00532d6) — the operator's
original `meta-policy-v4`, unmodified. The gradient reads it in full.

All data below is FABRICATED for loop testing. No real ads, no real spend.

═══════════════════════════════════════════════════════════════════════════════
ERA 1 — POLICY meta-policy-v4 (seed, no thesis) · BATCH 1 · 21 ads
═══════════════════════════════════════════════════════════════════════════════

## Full generation prompts (copy artifact + render prompt, verbatim)

PROMPT A — lead ("biased control"; bias source: writer's taste — v4 gives none)
  copy artifact:
    hook: "Intake auto-syncs to your CRM — no copy-paste"
    script: "I used to copy client details out of my intake forms into the CRM
      by hand, every week. Formflow just… does it. Intake auto-syncs to your
      CRM — no copy-paste. Client fills the form, the record's already there
      when I open it. Genuinely never going back."
    CTA: SIGN_UP · destination formflow.com
  render prompt:
    "Vertical 9:16 UGC selfie video, single person: female consultant, ~30s,
    home office, natural light, handheld, energetic but credible. Speaks the
    script direct to camera; at 'the record's already there' a brief
    over-shoulder glance at a laptop showing a CRM contact page. No burned-in
    captions, no music, natural room tone, ~28s."

PROMPT B — copy experiment (differs from A ONLY in words)
  copy artifact:
    hook: "2,300 consultancies run client intake on Formflow"
    script: "Quick question — what do 2,300 consultancies know that you don't?
      They all moved client intake to Formflow. Not because it's pretty —
      because it's what the busiest firms standardized on. 2,300 consultancies
      run client intake on Formflow. See what they saw."
    CTA: SIGN_UP · destination formflow.com
  render prompt:
    "IDENTICAL production spec to PROMPT A (same persona type, setting, energy,
    camera, length, no captions/music); only the spoken script differs. At
    'what the busiest firms standardized on' the same over-shoulder laptop
    glance, screen showing a logo-wall/testimonial page."

PROMPT C — style experiment (same words as A, differs ONLY in delivery)
  copy artifact: identical bytes to PROMPT A's copy artifact.
  render prompt:
    "Same persona/setting/length as PROMPT A but CALM delivery: seated, static
    camera, soft tone, measured pace. Same glance cue, same ending. Everything
    else identical."

## Full settings (7 bundles; shared frame: US only, automated placements,
## lowest-cost bidding, impressions billing, one 7-day window, landing page
## formflow.com, ugc_video, staged paused + one activation barrier per policy)

  S1 "Clicks"          OUTCOME_TRAFFIC · optimize link clicks   · broad US · $4.00 fixed
  S2 "Pageviews"       OUTCOME_TRAFFIC · optimize landing views · broad US · $4.00 fixed
  S3 "Leads"           OUTCOME_LEADS   · optimize verified lead · broad US · $4.00 fixed
  S4 "Sales-broad"     OUTCOME_SALES   · optimize purchase (pixel+custom conv) · broad US · $2.00 fixed
  S5 "Sales-interest"  OUTCOME_SALES   · optimize purchase · business-tools interest · $2.00 fixed
  S6 "SalesAuto-broad"    = S4 under one $4 campaign-level budget (Meta allocates)
  S7 "SalesAuto-interest" = S5 under that same campaign budget

## The 21 ads (prompt × settings) and their metrics

  ad01 A×S1  $1.34 · CTR 1.9% · 18 clicks · 0 leads ............ done  [trust]
  ad02 B×S1  $1.33 · CTR 2.0% · 19 clicks · 0 leads ............ done  [trust]
  ad03 C×S1  $1.32 · CTR 1.8% · 17 clicks · 0 leads ............ done  [trust]
  ad04 A×S2  $1.33 · 80% loaded · 1 lead @ $1.33 ............... done  [trust]
  ad05 B×S2  $1.34 · 83% loaded · 3 leads @ $0.45 .............. KEEPER [trust]
  ad06 C×S2  $1.33 · 81% loaded · 1 lead @ $1.33 ............... done  [trust]
  ad07 A×S3  $1.33 · 2 leads @ $0.67 · 1 demo ................. done  [trust]
  ad08 B×S3  $1.34 · 4 leads @ $0.34 · 3 demos ................ KEEPER [trust]
  ad09 C×S3  $1.32 · 1 lead @ $1.32 · 0 demos ................. done  [trust]
  ad10 A×S4  $0.67 · 0 purchases ............................... done  [trust]
  ad11 B×S4  $0.66 · 1 purchase · $29 settled .................. KEEPER [trust]
  ad12 C×S4  $0.67 · 0 purchases ............................... done  [trust]
  ad13 A×S5  $0.67 · 1 purchase · $29 settled .................. KEEPER [trust]
  ad14 B×S5  $0.66 · 1 purchase · $29 settled · +1 in lag ...... KEEPER [partial]
  ad15 C×S5  $0.67 · 0 purchases ............................... done  [trust]
  ad16 A×S6  $0.28 · starved by Meta allocation ................ done  [NO-TRUST]
  ad17 B×S6  $0.30 · starved ................................... done  [NO-TRUST]
  ad18 C×S6  $0.27 · starved ................................... done  [NO-TRUST]
  ad19 A×S7  $1.02 · 0 purchases · window short ................ done  [NO-TRUST]
  ad20 B×S7  $1.11 · 0 purchases · window short ................ done  [NO-TRUST]
  ad21 C×S7  $1.03 · 0 purchases · window short ................ done  [NO-TRUST]

  ANOMALY: Meta's campaign-level allocation (S6/S7) routed 79% of its $4 to the
  interest ad set and starved broad — while fixed-budget purchases came from
  BOTH audiences (S4: 1, S5: 2 settled). Allocation behavior recorded; no-trust
  this era (starved arms, short window).

  ERA 1 totals: spend $19.83 · settled revenue $87 (+$29 in lag, uncounted) ·
  settled ROAS 2.19 · keepers: ad05, ad08, ad11, ad14 (PROMPT B) + ad13 (A×S5)

  THESIS 1 DRAWN FROM ERA 1 (wake 1 · schedule hot, target 0.92 · draw: set 5 of 6):
  "Adoption-proof copy beat outcome copy with production held identical: B swept
  lead economics in every trustworthy bundle (3v1, 4v2 leads at ~1/3 the cost,
  demos 3:1) and edges settled purchases 2:1; the style pair (C vs A) was a wash.
  Bias lead copy toward verifiable adoption-proof claims. Sized honestly: strong
  at the lead level; thin (3 settled events) at the purchase level."
  ADOPTED -> policy-v5.md (930 lines, sha c09f1fcadb2d, parent 8d36e00532d6):
  both copy rows proof-anchored (control = strongest adoption proof; challenger =
  a different proof form); non-proof angles only as declared hypothesis rows.
  Edit targets: matrix step 3 · Biased Meta priors · Copy policy > Selection.

  STANDING ITEMS: allocation anomaly (S6/S7 starved broad while fixed-budget
  purchases came from both audiences) — era 1, no-trust, unaddressed. A's lone
  S5 purchase — single event, watch only.

═══════════════════════════════════════════════════════════════════════════════
ERA 2 — POLICY meta-policy-v5 (thesis 1, set 5) · BATCH 2 · 21 ads
═══════════════════════════════════════════════════════════════════════════════

## Full generation prompts (per v5: control = strongest adoption proof;
## challenger = a DIFFERENT proof form; third row = production experiment)

PROMPT D "FIRMS-COUNT" — lead (the reigning aggregate-proof words)
  copy artifact: identical bytes to batch 1's PROMPT B artifact
    (hook "2,300 consultancies run client intake on Formflow", full script as
    recorded in Era 1).
  render prompt: identical spec to batch 1's PROMPT B render prompt.

PROMPT E "RIVERA-STORY" — proof-form experiment (differs from D ONLY in words:
  a NAMED customer's story with concrete numbers — a different form of proof)
  copy artifact:
    hook: "How Rivera Consulting cut intake time 80% with Formflow"
    script: "Maya Rivera runs a six-person consultancy. Client intake used to
      eat four hours a week — forms, emails, retyping into the CRM. She moved
      it to Formflow in an afternoon. Now it's under 45 minutes, and nothing
      gets retyped. That's an 80% cut, every single week. Her words: 'it's the
      one tool I'd panic without.' See what Rivera saw."
    CTA: SIGN_UP · destination formflow.com
  render prompt:
    "IDENTICAL production spec to PROMPT D (same persona type, setting, energy,
    camera, length, no captions/music); only the spoken script differs. At
    'moved it to Formflow in an afternoon' the same over-shoulder laptop
    glance, screen showing a client dashboard."

PROMPT F "FIRMS-COUNT-MALE" — production experiment (same words as D; differs
  ONLY in who delivers them)
  copy artifact: identical bytes to PROMPT D's copy artifact.
  render prompt:
    "Same spec as PROMPT D but the creator is a male accountant, ~40s, office
    setting, same energy, same camera/length/no-captions rules. Everything
    else identical."

## Settings: S1–S7 identical to Era 1 (same bundles, budgets, shared frame).

## The 21 ads

  ad22 D×S1  $1.33 · CTR 2.1% · 19 clicks · 0 leads ............ done  [trust]
  ad23 E×S1  $1.32 · CTR 1.9% · 18 clicks · 0 leads ............ done  [trust]
  ad24 F×S1  $1.34 · CTR 2.0% · 18 clicks · 0 leads ............ done  [trust]
  ad25 D×S2  $1.33 · 82% loaded · 3 leads @ $0.44 .............. KEEPER [trust]
  ad26 E×S2  $1.34 · 84% loaded · 3 leads @ $0.45 .............. KEEPER [trust]
  ad27 F×S2  $1.33 · 82% loaded · 2 leads @ $0.67 .............. done  [trust]
  ad28 D×S3  $1.34 · 4 leads @ $0.34 · 2 demos ................ KEEPER [trust]
  ad29 E×S3  $1.33 · 4 leads @ $0.33 · 4 demos ................ KEEPER [trust]
  ad30 F×S3  $1.32 · 3 leads @ $0.44 · 2 demos ................ done  [trust]
  ad31 D×S4  $0.67 · 0 purchases ............................... done  [trust]
  ad32 E×S4  $0.66 · 2 purchases · $58 settled ................. KEEPER [trust]
  ad33 F×S4  $0.67 · 1 purchase · $29 settled .................. KEEPER [trust]
  ad34 D×S5  $0.66 · 1 purchase · $29 settled .................. KEEPER [trust]
  ad35 E×S5  $0.67 · 1 purchase · $29 settled · +1 in lag ...... KEEPER [partial]
  ad36 F×S5  $0.67 · 0 purchases ............................... done  [trust]
  ad37 D×S6  $0.26 · starved ................................... done  [NO-TRUST]
  ad38 E×S6  $0.24 · starved ................................... done  [NO-TRUST]
  ad39 F×S6  $0.29 · starved ................................... done  [NO-TRUST]
  ad40 D×S7  $1.07 · 0 purchases · window short ................ done  [NO-TRUST]
  ad41 E×S7  $1.09 · 0 purchases · window short ................ done  [NO-TRUST]
  ad42 F×S7  $1.05 · 0 purchases · window short ................ done  [NO-TRUST]

  ANOMALY (2nd consecutive era, same signature): Meta's campaign-level budget
  again starved one ad set (81% to interest) while fixed-budget purchases came
  from both audiences. Still no-trust individually — but now REPLICATED.

  ERA 2 totals: spend $19.82 · settled revenue $145 (+$29 lag, uncounted) ·
  settled ROAS 3.13 · keepers: ad25/26/28/29/32/33/34/35
  ERA LADDER: 2.19 (v4) → 3.13 (v5). Thesis 1's report card: positive — deepen.

  THESIS 2 DRAWN FROM ERA 2 (wake 2 · target 0.84 · draw: set 4 of 6):
  "Named-customer story proof out-converts aggregate-count proof: lead economics
  tied, demos 4:2, settled purchases 3:1 with production identical. Lead with
  named stories; counts are support. Sized as a rule, not an identity (4 settled
  purchase events on the axis). Persona experiment: wash — parked. Allocation
  anomaly: REPLICATED 2nd era — now the strongest unaddressed signal on file."
  ADOPTED -> policy-v6.md (see file header for hash, parent c09f1fcadb2d):
  aggregate-count copy retired from the lead slot (support/challenger only);
  successors default to story-led. Edit targets: matrix step 3 · priors ·
  Copy policy > Selection.

═══════════════════════════════════════════════════════════════════════════════
ERA 3 — POLICY meta-policy-v6 (thesis 2, set 4) · BATCH 3 · 21 ads
═══════════════════════════════════════════════════════════════════════════════

## Business context (NEW under widened evidence diet)

LANDING PAGE (formflow.com, live text at batch time):
  headline: "Beautiful forms your clients will love"
  subhead: "No-code form builder for consultants. Templates, logic, integrations."
  sections: template gallery → feature grid (logic jumps, CRM sync, branding)
  → pricing ($29/mo, 14-day trial, card required) → three logo testimonials.
  NOTE: page leads with aesthetics; the ads' proof story (Rivera) appears
  nowhere on the page.
PRODUCT FACTS (verified): CRM sync (HubSpot/Salesforce) real; logic jumps real;
  Rivera Consulting case study real (4h -> 45min, published, checkable);
  2,300-customer count real as of last audit.
PRICING: $29/mo only; no annual, no tiers.

## Site funnel (NEW; destination analytics, batch window)

  visits from ads 412 · sign-ups 19 (4.6%) · demos booked 9 · purchases 4
  bounce rate by traffic source: Clicks campaign 74% · Pageviews 22% ·
  Leads 24% · Sales campaigns 26%
  demo -> purchase: 4 of 9 (healthy). sign-up -> demo: 9 of 19 (healthy).

## Standing-items ledger (NEW; with ages)

  1. Meta-allocated budget anomaly — starves arms, routes against measured
     purchase origin. Ages: era 1, 2, 3 (three consecutive). Never trustworthy.
  2. Clicks campaign yield — 0 goal events ever (see pattern rows below).
     Ages: era 1, 2, 3.
  3. Landing page/ad message mismatch — page leads aesthetics, ads lead proof.
     First recorded era 3 (page text only now in evidence).

## Full generation prompts

PROMPT "RIVERA-STORY" — lead (reigning champion, per v6 story-led rule)
  copy artifact + render prompt: identical bytes to Era 2's RIVERA-STORY.
PROMPT "OKAFOR-STORY" — proof-form experiment (a DIFFERENT named story;
  differs from lead ONLY in which customer story)
  copy artifact:
    hook: "How Okafor Design cut client onboarding from 3 days to 1 morning"
    script: "Tunde Okafor runs a 6-person design agency. Onboarding a client
      took three days of forms and follow-up emails. With Formflow it's one
      morning: intake, contract details, brand files — one link. 'We start
      work the same day now.' One of 2,300 firms that switched."
    CTA: SIGN_UP · destination formflow.com
  render prompt: "IDENTICAL production spec to RIVERA-STORY; only script differs;
    laptop-glance shows an onboarding checklist completing."
PROMPT "RIVERA-FOUNDER-CAMEO" — production experiment (same words as lead;
  differs ONLY in who appears: 3s cameo of 'Maya Rivera' herself at the quote)
  copy artifact: identical bytes to RIVERA-STORY.
  render prompt: "Same spec as RIVERA-STORY, but at the quote line, cut to a
    second woman (labeled 'Maya Rivera, Rivera Consulting') delivering the
    quote to camera; creator carries the rest."

## Settings: the same 7 bundles as Eras 1-2, unchanged.

## The 21 ads — now with delivery diagnostics (freq · CPM · learning)

  Clicks campaign:    RIVERA $1.33 CTR2.0% 0 leads · freq1.1 CPM$8 learning-limited [trust]
                      OKAFOR $1.34 CTR2.1% 0 leads · freq1.1 CPM$8 learning-limited [trust]
                      CAMEO  $1.32 CTR1.9% 0 leads · freq1.2 CPM$9 learning-limited [trust]
  Pageviews campaign: RIVERA $1.33 · 85% loaded · 3 leads @ $0.44 · freq1.3 CPM$11 KEEPER [trust]
                      OKAFOR $1.34 · 84% loaded · 3 leads @ $0.45 · freq1.3 CPM$11 KEEPER [trust]
                      CAMEO  $1.33 · 83% loaded · 2 leads @ $0.67 · freq1.3 CPM$12 live [trust]
  Leads campaign:     RIVERA $1.32 · 4 leads @ $0.33 · 2 demos · freq1.4 CPM$12 KEEPER [trust]
                      OKAFOR $1.35 · 4 leads @ $0.34 · 3 demos · freq1.4 CPM$12 KEEPER [trust]
                      CAMEO  $1.33 · 3 leads @ $0.44 · 1 demo · freq1.5 CPM$13 live [trust]
  Sales-broad:        RIVERA $0.67 · 1 purchase $29 · freq1.6 CPM$14 KEEPER [trust]
                      OKAFOR $0.66 · 1 purchase $29 · freq1.6 CPM$14 KEEPER [trust]
                      CAMEO  $0.67 · 0 purchases · freq1.7 CPM$15 done [trust]
  Sales-interest:     RIVERA $0.66 · 1 purchase $29 +1 in lag · freq1.7 CPM$16 KEEPER [partial]
                      OKAFOR $0.67 · 0 purchases · freq1.7 CPM$16 done [trust]
                      CAMEO  $0.67 · 1 purchase $29 · freq1.8 CPM$16 KEEPER [trust]
  Meta-allocated pair: all six ads starved or short-window again (81% to
    interest ad set) — NO-TRUST, 3rd consecutive era.

  ERA 3 totals: spend $19.88 · settled $145 · ROAS 3.65 ·
  ERA LADDER: 2.19 -> 3.13 -> 3.65

## Cross-era pattern rows (assembled for the tournament)

  CLICKS CAMPAIGN, ALL ERAS: 9 ads · $12.02 total · 0 leads · 0 purchases ·
  74% bounce on its traffic · learning-limited every era · trustworthy reads
  throughout. It is 20% of every batch's budget; the policy already forbids
  its metrics from informing claim selection.

  THESIS 3 DRAWN FROM ERAS 1-3 (wake 3 · target 0.77 · draw: set 4 · DESIGN class):
  "The portfolio pays a permanent tax for information it refuses to use: the
  Clicks campaign consumed 20% of every batch across three eras ($12.02, 9 ads)
  with zero goal events and 74% bounce, while the policy already excludes click
  metrics from decisions. Stop buying click-appeal measurement."
  ADOPTED -> policy-v7.md (952 lines, sha 6eeb0d88ead3, parent policy-v6):
  four-profile portfolio; Clicks removed (hypothesis-card re-entry only); freed
  slot+share = second Sales configuration on a fresh verified audience.
  EXECUTION NOTE: batch wrapper v1 requires five profiles; batches blocked_runtime
  until a four-profile wrapper exists.
  PARKED: page/ad message mismatch (pattern class; proposed instrument: one batch
  with the landing headline matched to the proof story) · budget anomaly (3rd era,
  never one trustworthy read).
