"""Build engineered proxy-trap world-50 (run from repo root).

Design goal (registered before any agent runs):
- SIGNUP-layer winner is the `outcome` angle: high-clickiness, low-buyiness
  segments (scroller/novelty/impulse) strongly prefer outcome copy, so outcome
  ads dominate clicks and signups portfolio-wide.
- PURCHASE-layer winner is the `story` angle: the high-buyiness segments
  (peer_proof/skeptic/pragmatist/authority) strongly prefer story copy and
  carry nearly all purchase probability.
- Buy layer is healthy (world IS buyable at story configs), producing the
  world-5 funnel-cliff temptation: pooled signup->purchase rates look broken
  because signups come from non-buyers chasing outcome ads.
- demo matters moderately (gates .55-.75 on buyers) but is not the headline.

Prediction: an agent that anchors creative on early signup evidence locks
`outcome` and earns ~nothing; an agent forced to keep goal-layer judgment
open (readable floor, interaction coverage, drought escalation) finds story
configs by mid-run.
"""
import json
from pathlib import Path

A = []  # name, weight, proof_pref(benefit,outcome,count,story), demo, trial, base, mismatch, click, buy, subs
def arch(name, w, pp, demo, trial, base, mism, click, buy, subs):
    A.append(dict(name=name, weight=w,
                  dims=dict(proof_pref=dict(zip(("benefit","outcome","count","story"), pp)),
                            demo_gate=demo, trial_gate=trial, base=base,
                            mismatch_bounce=mism, clickiness=click, buyiness=buy),
                  subs=[dict(name=f"{name}_s{i+1}", frac=f, delta_base=db,
                             reach=dict(broad=rb, interest_biztools=rbt, interest_niche=rn))
                        for i, (f, db, rb, rbt, rn) in enumerate(subs)]))

# --- signup engine: loves outcome, barely buys (traps the proxy-reader) ---
arch("scroller", .18, (.5, 1.5, .4, .3), .85, .5, 1.1, .2, 1.3, .08,
     [(.6, .1, .9, .3, .2), (.4, -.05, .7, .5, .3)])
arch("novelty", .14, (.6, 1.4, .5, .35), .9, .55, 1.2, .15, 1.2, .15,
     [(.55, .05, .8, .4, .35), (.45, -.1, .6, .6, .4)])
arch("impulse", .12, (.7, 1.35, .4, .3), 1.0, .5, 1.0, .1, 1.1, .2,
     [(1.0, .0, .85, .35, .25)])
# --- buyers: love story, carry the purchase layer ---
arch("peer_proof", .16, (.3, .45, .4, 1.45), .6, .78, .85, .35, .5, .9,
     [(.5, .05, .8, .45, .6), (.5, -.05, .6, .55, .7)])
arch("skeptic", .1, (.15, .4, .25, 1.2), .55, .8, .6, .55, .35, .85,
     [(1.0, .0, .7, .5, .55)])
arch("pragmatist", .1, (.3, .5, .4, 1.3), .65, .8, .8, .3, .5, .8,
     [(.6, .0, .75, .5, .5), (.4, .1, .55, .65, .6)])
arch("authority", .07, (.2, .5, .8, 1.1), .7, .75, .7, .4, .4, .7,
     [(1.0, .0, .65, .6, .5)])
# --- filler, mild prefs, keeps totals honest ---
arch("herd", .06, (.4, .5, 1.1, .6), .8, .7, .8, .2, .7, .5,
     [(1.0, .0, .8, .4, .3)])
arch("bargain", .04, (.5, .6, .6, .6), .7, .3, .7, .3, .8, .35,
     [(1.0, .0, .75, .45, .35)])
arch("committee", .03, (.2, .7, .8, .7), .45, .85, .4, .5, .2, .5,
     [(1.0, .0, .6, .55, .45)])

assert abs(sum(a["weight"] for a in A) - 1.0) < 1e-9

hidden = dict(world=50, seed=0, landing_style="benefit", price_usd=29,
              offer="card_required_trial", archetypes=A)
out = Path("sim/world-50"); out.mkdir(parents=True, exist_ok=True)
(out / "subpops-hidden.json").write_text(json.dumps(hidden, indent=1))
platform = dict(
  audiences={"broad": "~180M, all adults US", "interest_biztools": "~2.1M, business-tools interest",
             "interest_niche": "~600k, niche professional communities"},
  cpm_usd={"broad": 8, "interest_biztools": 14, "interest_niche": 12},
  objectives=["clicks", "pageviews", "leads", "sales"], budget_modes=["fixed", "auto"],
  note="Landing page leads with generic-benefit copy; $29/mo, card-required trial.")
(out / "platform.json").write_text(json.dumps(platform, indent=1))
print("world-50 sealed (engineered proxy-trap).")
