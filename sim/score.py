"""The reveal: open the sealed world, print the analytic truth + oracle.
Usage: python3 sim/score.py <world>   (run ONLY after a world's iterations end)"""
import json, sys, itertools
from pathlib import Path

BASE = dict(click=.016, load=.82, signup=.11, purchase=.16)
OBJ_BIAS = {"clicks": lambda d:.4+1.2*d["clickiness"], "pageviews": lambda d:.6+.8*d["clickiness"],
            "leads": lambda d:.5+.5*d["clickiness"]+.5*d["buyiness"], "sales": lambda d:.4+1.2*d["buyiness"]}

def run(world):
    W = json.loads(Path(f"sim/world-{world}/subpops-hidden.json").read_text())
    P = json.loads(Path(f"sim/world-{world}/platform.json").read_text())
    print(f"══ REVEAL: world-{world} (seed {W['seed']}) ══")
    print(f"{'archetype':12}{'weight':>7}{'buyiness':>9}{'demo_gate':>10}{'trial_gate':>11}  best-proof")
    for A in sorted(W["archetypes"], key=lambda a:-a["weight"]):
        d = A["dims"]; bp = max(d["proof_pref"], key=d["proof_pref"].get)
        print(f"{A['name']:12}{A['weight']:>7.1%}{d['buyiness']:>9.2f}{d['demo_gate']:>10.2f}{d['trial_gate']:>11.2f}  {bp} ({d['proof_pref'][bp]:.2f})")
    bw = sum(A["weight"]*A["dims"]["buyiness"] for A in W["archetypes"])
    for s in ("benefit","outcome","count","story"):
        v = sum(A["weight"]*A["dims"]["buyiness"]*A["dims"]["proof_pref"][s] for A in W["archetypes"])/bw
        print(f"buyer-weighted proof preference {s:8}: {v:.3f}")
    dg = sum(A["weight"]*A["dims"]["buyiness"]*A["dims"]["demo_gate"] for A in W["archetypes"])/bw
    tg = sum(A["weight"]*A["dims"]["buyiness"]*A["dims"]["trial_gate"] for A in W["archetypes"])/bw
    print(f"buyer-weighted demo_gate {dg:.2f} (purchase multiplier WITHOUT demo) · trial_gate {tg:.2f}")
    # oracle: expected purchases for $20 in one (ad-features, campaign) config
    best = []
    for proof, demo, obj, aud in itertools.product(("benefit","outcome","count","story"),(False,True),
                                                   ("leads","sales","pageviews"), ("broad","interest_biztools","interest_niche")):
        imps = 20.0/P["cpm_usd"][aud]*1000
        mix = [(A["weight"]*S["frac"]*S["reach"][aud]*OBJ_BIAS[obj](A["dims"]), A, S)
               for A in W["archetypes"] for S in A["subs"]]
        z = sum(w for w,_,_ in mix); buys = 0.0
        for w, A, S in mix:
            d = A["dims"]; aff = d["proof_pref"][proof] * (1.0 if proof!="story" else 1.0)
            base = min(1.4, max(.05, d["base"]+S["delta_base"]))
            n = imps*w/z
            mism = 1.0 if proof==W["landing_style"] else (1-d["mismatch_bounce"]*.6)
            p = (BASE["click"]*base*aff) * min(.95,BASE["load"]*mism) * (BASE["signup"]*base*aff) \
                * (BASE["purchase"]*d["buyiness"]*aff * (1.0 if demo else d["demo_gate"]) * d["trial_gate"])
            buys += n*p
        best.append((buys, proof, demo, obj, aud))
    best.sort(reverse=True)
    print("\nORACLE — expected purchases from $20 in the single best config:")
    for b,proof,demo,obj,aud in best[:5]:
        print(f"  {b:.3f} buys (${20/b:.0f}/buy)  proof={proof} demo={demo} obj={obj} aud={aud}")
    print(f"  (worst of top-20: {best[19][0]:.3f} — spread shows how much config matters)")

if __name__ == "__main__":
    run(int(sys.argv[1]))
