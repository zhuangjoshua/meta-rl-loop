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
        archetypes.append(dict(name=name, weight=round(raw[name]/z, 4), dims=d, subs=subs))
    hidden = dict(world=world, seed=seed, landing_style="benefit", price_usd=29,
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
