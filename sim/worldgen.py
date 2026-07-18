"""Generate sealed sim worlds: general decision archetypes -> sub-segments.

Usage: python3 sim/worldgen.py <world_number> <seed>
Writes sim/world-N/subpops-hidden.json (SEALED - the gradient never reads it)
and sim/world-N/platform.json (the advertiser-visible surface).
"""
import json, random, sys
from pathlib import Path

# 10 general archetype templates. dims:
#   proof_pref: responsiveness multiplier per proof style of the ad's words
#   demo_gate:  purchase multiplier when the ad does NOT show the product working
#   trial_gate: purchase multiplier given the (fixed) card-required $29/mo offer
#   base:       overall responsiveness scale
#   mismatch_bounce: extra bounce prob when ad style mismatches landing style
#   clickiness / buyiness: delivery-targeting affinities (who optimizers find)
TEMPLATES = {
 "peer_proof":     dict(proof_pref=dict(benefit=.3, outcome=.6, count=.4, story=1.4), demo_gate=.55, trial_gate=.75, base=.9, mismatch_bounce=.35, clickiness=.5, buyiness=.9),
 "skeptic":        dict(proof_pref=dict(benefit=.1, outcome=.5, count=.2, story=.7),  demo_gate=.15, trial_gate=.85, base=.6, mismatch_bounce=.6,  clickiness=.3, buyiness=.8),
 "herd":           dict(proof_pref=dict(benefit=.4, outcome=.4, count=1.3, story=.6), demo_gate=.8,  trial_gate=.7,  base=.8, mismatch_bounce=.2,  clickiness=.7, buyiness=.6),
 "bargain":        dict(proof_pref=dict(benefit=.5, outcome=.6, count=.6, story=.6),  demo_gate=.7,  trial_gate=.25, base=.7, mismatch_bounce=.3,  clickiness=.8, buyiness=.4),
 "novelty":        dict(proof_pref=dict(benefit=.9, outcome=.8, count=.5, story=.8),  demo_gate=.9,  trial_gate=.6,  base=1.2, mismatch_bounce=.15, clickiness=1.3, buyiness=.3),
 "authority":      dict(proof_pref=dict(benefit=.2, outcome=.6, count=.9, story=.8),  demo_gate=.6,  trial_gate=.8,  base=.7, mismatch_bounce=.4,  clickiness=.4, buyiness=.7),
 "committee":      dict(proof_pref=dict(benefit=.2, outcome=.7, count=.8, story=.7),  demo_gate=.4,  trial_gate=.9,  base=.4, mismatch_bounce=.5,  clickiness=.2, buyiness=.5),
 "scroller":       dict(proof_pref=dict(benefit=.6, outcome=.5, count=.5, story=.6),  demo_gate=.8,  trial_gate=.5,  base=.5, mismatch_bounce=.25, clickiness=1.0, buyiness=.1),
 "pragmatist":     dict(proof_pref=dict(benefit=.3, outcome=1.3, count=.4, story=.7), demo_gate=.5,  trial_gate=.8,  base=.8, mismatch_bounce=.3,  clickiness=.5, buyiness=.8),
 "impulse":        dict(proof_pref=dict(benefit=1.2, outcome=.6, count=.5, story=.7), demo_gate=1.0, trial_gate=.55, base=1.0, mismatch_bounce=.1, clickiness=1.1, buyiness=.5),
}
AUDIENCES = ["broad", "interest_biztools", "interest_niche"]

# These descriptions are hidden from the policy learner and shown only to the Tier-B
# judge.  They encode decision behavior rather than demographic stereotypes, so the
# same world generator remains useful across products.
PERSONA_PROFILES = {
 "peer_proof": dict(
   decision_style="Looks for a relatable peer who had the same problem and can describe the before/after in concrete terms.",
   trust_signals=["named customer", "specific workflow", "credible limitation or tradeoff"],
   rejection_triggers=["anonymous testimonial", "generic superlative", "results without context"]),
 "skeptic": dict(
   decision_style="Assumes marketing claims are exaggerated and wants to inspect the mechanism before believing the outcome.",
   trust_signals=["visible product demonstration", "precise mechanism", "falsifiable claim"],
   rejection_triggers=["unsupported promise", "vague transformation", "manufactured urgency"]),
 "herd": dict(
   decision_style="Uses broad adoption and category momentum as evidence that a choice is safe.",
   trust_signals=["credible adoption count", "recognizable community", "clear category norm"],
   rejection_triggers=["isolated anecdote", "uncited number", "fringe positioning"]),
 "bargain": dict(
   decision_style="Compares the immediate cost with a concrete near-term saving and avoids uncertain commitments.",
   trust_signals=["specific savings", "transparent price", "easy exit"],
   rejection_triggers=["hidden commitment", "premium framing", "unclear time to value"]),
 "novelty": dict(
   decision_style="Explores new tools quickly when the experience looks distinctive and immediately useful.",
   trust_signals=["fresh mechanism", "fast visual payoff", "clear first action"],
   rejection_triggers=["conventional corporate copy", "long setup", "feature inventory"]),
 "authority": dict(
   decision_style="Defers to credible expertise, standards, and evidence that the method is professionally accepted.",
   trust_signals=["relevant expert", "methodology", "verifiable credentials"],
   rejection_triggers=["casual unsupported advice", "irrelevant celebrity", "anti-expert framing"]),
 "committee": dict(
   decision_style="Needs a choice that can be explained to coworkers and defended against operational and financial objections.",
   trust_signals=["shared workflow", "risk controls", "clear implementation path"],
   rejection_triggers=["single-user framing", "missing security or process detail", "impulse CTA"]),
 "scroller": dict(
   decision_style="Offers very little attention and responds only when the first moment is concrete, legible, and personally relevant.",
   trust_signals=["immediate visual clarity", "one simple benefit", "low-friction next step"],
   rejection_triggers=["slow opening", "dense explanation", "multiple competing claims"]),
 "pragmatist": dict(
   decision_style="Wants a dependable improvement to an existing workflow and judges the product by operational fit.",
   trust_signals=["specific outcome", "real workflow", "implementation detail"],
   rejection_triggers=["aspirational lifestyle copy", "unclear integration", "novelty without utility"]),
 "impulse": dict(
   decision_style="Acts on an immediately vivid benefit when the next step feels easy and the downside feels bounded.",
   trust_signals=["fast payoff", "simple CTA", "low perceived risk"],
   rejection_triggers=["delayed value", "complex comparison", "high-friction signup"]),
}

DECISION_CONTEXTS = [
 "evaluating alone during a short break and unwilling to research for long",
 "actively comparing two alternatives after a painful recent workflow failure",
 "collecting evidence for a recommendation to a small team",
 "curious but not yet convinced the problem deserves a paid tool",
 "under deadline pressure and willing to switch only if setup looks immediate",
 "returning after seeing similar claims several times without acting",
]

def gen(world, seed):
    rng = random.Random(seed)
    # Dirichlet-ish weights: different worlds -> different dominant archetypes
    raw = {n: rng.gammavariate(1.1, 1.0) for n in TEMPLATES}
    z = sum(raw.values())
    archetypes = []
    for name, tpl in TEMPLATES.items():
        d = json.loads(json.dumps(tpl))
        for k in ("demo_gate","trial_gate","base","mismatch_bounce","clickiness","buyiness"):
            d[k] = round(min(1.5, max(.05, d[k] * rng.uniform(.7, 1.3))), 3)
        d["proof_pref"] = {s: round(min(1.6, max(.05, v * rng.uniform(.7, 1.3))), 3)
                           for s, v in d["proof_pref"].items()}
        nsub = rng.choice([2,2,3])
        fr = [rng.gammavariate(2,1) for _ in range(nsub)]; fz = sum(fr)
        subs = []
        for i in range(nsub):
            reach = {a: round(rng.uniform(.05, 1.0), 2) for a in AUDIENCES}
            reach["broad"] = round(max(reach["broad"], .4), 2)  # broad reaches most of everyone
            subs.append(dict(name=f"{name}_s{i+1}", frac=round(fr[i]/fz, 3),
                             delta_base=round(rng.uniform(-.2, .2), 2), reach=reach))
        for sub in subs:
            sub["decision_context"] = rng.choice(DECISION_CONTEXTS)
        archetypes.append(dict(
            name=name,
            weight=round(raw[name]/z, 4),
            persona=PERSONA_PROFILES[name],
            dims=d,
            subs=subs,
        ))
    hidden = dict(schema="takyon.hidden-market.v2", world=world, seed=seed, landing_style="benefit", price_usd=29,
                  offer="card_required_trial", archetypes=archetypes)
    out = Path(f"sim/world-{world}"); out.mkdir(parents=True, exist_ok=True)
    (out/"subpops-hidden.json").write_text(json.dumps(hidden, indent=1))
    platform = dict(
      audiences={"broad":"~180M, all adults US", "interest_biztools":"~2.1M, business-tools interest",
                 "interest_niche":"~600k, niche professional communities"},
      cpm_usd={"broad":8, "interest_biztools":14, "interest_niche":12},
      objectives=["clicks","pageviews","leads","sales"], budget_modes=["fixed","auto"],
      note="Landing page leads with generic-benefit copy; $29/mo, card-required trial.")
    (out/"platform.json").write_text(json.dumps(platform, indent=1))
    print(f"world-{world} seed {seed}: sealed. (no hints printed; truth lives only in the file)")

if __name__ == "__main__":
    gen(int(sys.argv[1]), int(sys.argv[2]))
