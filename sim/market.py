"""Tier A feature-control runner: declared tags in -> noisy receipts out.

Tier A deliberately scores learner-supplied feature tags. It is not evidence of
semantic adaptation and must not be compared with the Tier-B actual-content market.

Usage: python3 sim/market.py <world> <iteration_seed> <batch_spec.json>
Prints an iteration block (markdown, evidence.md format) + a JSON summary line.
Reads the sealed world file; NEVER prints hidden traits into receipts.
"""
import json, math, random, sys
from pathlib import Path

def beta_rate(rng, mean, conc=60):
    mean = min(.95, max(1e-4, mean))
    a, b = mean*conc, (1-mean)*conc
    x, y = rng.gammavariate(a,1), rng.gammavariate(b,1)
    return x/(x+y)

def binom(rng, n, p):
    return sum(1 for _ in range(int(n)) if rng.random() < p) if n < 400 else \
           max(0, min(int(n), round(rng.gauss(n*p, math.sqrt(max(n*p*(1-p),1e-9))))))

OBJ_BIAS = {  # how each optimization goal skews who gets found
  "clicks":    lambda d: .4 + 1.2*d["clickiness"],
  "pageviews": lambda d: .6 + .8*d["clickiness"],
  "leads":     lambda d: .5 + .5*d["clickiness"] + .5*d["buyiness"],
  "sales":     lambda d: .4 + 1.2*d["buyiness"],
}
BASE = dict(click=.016, load=.82, signup=.11, purchase=.16)  # funnel step ceilings

def simulate(world, seed, spec):
    W = json.loads(Path(f"sim/world-{world}/subpops-hidden.json").read_text())
    P = json.loads(Path(f"sim/world-{world}/platform.json").read_text())
    rng = random.Random(seed * 7919 + world)

    # expand auto-mode campaigns: platform allocator concentrates budget unevenly
    cells = []
    for c in spec["campaigns"]:
        if c.get("mode") == "auto":
            auds = c["audiences"]
            hot = rng.randrange(len(auds)); share = rng.uniform(.72, .92)
            for i, a in enumerate(auds):
                b = c["budget"] * (share if i == hot else (1-share)/(len(auds)-1))
                cells.append(dict(id=f"{c['id']}~{a}", objective=c["objective"], audience=a,
                                  budget=round(b,2), mode="auto"))
        else:
            cells.append(dict(id=c["id"], objective=c["objective"], audience=c["audience"],
                              budget=c["budget"], mode="fixed"))

    rows, funnel_tot = [], dict(visits=0, signups=0, demos=0, purchases=0)
    for cell in cells:
        cpm = P["cpm_usd"][cell["audience"]]
        per_ad_budget = cell["budget"] / len(spec["ads"])
        for ad in spec["ads"]:
            imps = per_ad_budget / cpm * 1000
            # exposure mix over sub-segments
            mix = []
            for A in W["archetypes"]:
                d = A["dims"]; ob = OBJ_BIAS[cell["objective"]](d)
                for S in A["subs"]:
                    w = A["weight"]*S["frac"]*S["reach"][cell["audience"]]*ob
                    mix.append((w, A, S))
            z = sum(w for w,_,_ in mix) or 1
            clicks = loads = signups = demos = purchases = 0
            for w, A, S in mix:
                n = imps * w / z
                if n < .5: continue
                d = A["dims"]; aff = d["proof_pref"][ad["proof"]]
                if ad["proof"] == "story" and not ad.get("named_story"): aff *= .6
                base = min(1.4, max(.05, d["base"] + S["delta_base"]))
                p_click = beta_rate(rng, BASE["click"]*base*aff)
                cl = binom(rng, n, p_click)
                mism = 1.0 if ad["proof"] == W["landing_style"] else (1 - d["mismatch_bounce"]*.6)
                ld = binom(rng, cl, min(.95, BASE["load"]*mism))
                p_su = beta_rate(rng, BASE["signup"]*base*aff)
                su = binom(rng, ld, p_su)
                dm = binom(rng, su, .45*d["buyiness"]+.05)
                p_buy = BASE["purchase"]*d["buyiness"]*aff
                if not ad.get("demo"): p_buy *= d["demo_gate"]
                p_buy *= d["trial_gate"]
                pu = binom(rng, su, beta_rate(rng, p_buy))
                clicks+=cl; loads+=ld; signups+=su; demos+=dm; purchases+=pu
            spend = round(per_ad_budget,2)
            trust = "trust" if (cell["mode"]=="fixed" and spend>=0.5) else \
                    ("NO-TRUST starved" if spend<0.5 else "NO-TRUST auto-window")
            ctr = clicks/imps*100 if imps else 0
            ldr = loads/clicks*100 if clicks else 0
            cpl = spend/signups if signups else None
            rows.append(dict(ad=ad["id"], cell=cell["id"], spend=spend, imps=int(imps),
                             ctr=round(ctr,2), load_rate=round(ldr,1), signups=signups,
                             cpl=round(cpl,2) if cpl else None, demos=demos,
                             purchases=purchases, trust=trust))
            funnel_tot["visits"]+=loads; funnel_tot["signups"]+=signups
            funnel_tot["demos"]+=demos; funnel_tot["purchases"]+=purchases

    spend_t = round(sum(r["spend"] for r in rows),2)
    rev = funnel_tot["purchases"]*W["price_usd"]
    roas = round(rev/spend_t,2) if spend_t else 0
    return dict(tier="A-feature-control", semantic_valid=False, rows=rows, funnel=funnel_tot,
                spend=spend_t, revenue=rev, roas=roas, cells=cells)

def run(world, seed, specpath):
    spec = json.loads(Path(specpath).read_text())
    W = json.loads(Path(f"sim/world-{world}/subpops-hidden.json").read_text())
    R = simulate(world, seed, spec)
    rows, funnel_tot, spend_t, rev, roas, cells = R["rows"], R["funnel"], R["spend"], R["revenue"], R["roas"], R["cells"]
    it = spec["iteration"]

    md = [f"\n━━ ITERATION {it} — policy {spec['policy']} · sim world-{world} seed {seed} ━━"]
    md.append("ADS (features as tagged in the batch spec; full prompts in the spec file):")
    for ad in spec["ads"]:
        md.append(f"  {ad['id']}: proof={ad['proof']} named_story={ad.get('named_story',False)} "
                  f"demo={ad.get('demo',False)} | {ad['prompt'][:90]}")
    md.append("CAMPAIGN CELLS: " + ", ".join(f"{c['id']}(${c['budget']},{c['objective']},{c['audience']},{c['mode']})" for c in cells))
    md.append(f"{'ad':22}{'cell':26}{'spend':>6} {'CTR%':>5} {'load%':>6} {'signups':>8} {'CPL':>6} {'demos':>6} {'buys':>5}  trust")
    for r in rows:
        md.append(f"{r['ad']:22}{r['cell']:26}{r['spend']:>6.2f} {r['ctr']:>5.2f} {r['load_rate']:>6.1f} "
                  f"{r['signups']:>8} {str(r['cpl'] or '—'):>6} {r['demos']:>6} {r['purchases']:>5}  [{r['trust']}]")
    md.append(f"SITE FUNNEL: visits {funnel_tot['visits']} · sign-ups {funnel_tot['signups']} · "
              f"demos {funnel_tot['demos']} · purchases {funnel_tot['purchases']}")
    md.append(f"ITERATION {it} TOTALS: spend ${spend_t} · settled revenue ${rev} · ROAS {roas}")
    print("\n".join(md))
    print("@@SUMMARY " + json.dumps(dict(tier="A-feature-control", semantic_valid=False,
                                         iteration=it, spend=spend_t, revenue=rev, roas=roas)))

if __name__ == "__main__":
    run(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
