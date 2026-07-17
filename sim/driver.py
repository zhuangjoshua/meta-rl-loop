"""Scripted gradient: the five-world doctrine as an algorithm, for statistical runs.

Encodes the rules the prose gradient accumulated: coverage sweep first; matched-cell
(composition-rule) comparisons; per-dollar judgment; auto-mode cut after 2 unreadable
eras; demo held until funded expectation >= 3 events (demotion floor); goal-events-
per-dollar leads selection with proxy fallback ONLY within matched cells; dose-scaled
budget shifts via the noise schedule; sales listener floors.

This measures the DOCTRINE, not the LLM gradient. Usage:
  python3 sim/driver.py <world> [iters] [budget]   -> per-iteration JSON lines
"""
import json, math, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import importlib.util
_spec = importlib.util.spec_from_file_location("market", Path(__file__).parent/"market.py")
market = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(market)

FAMS = ["benefit","outcome","count","story"]
AUDS = ["broad","interest_biztools","interest_niche"]

def ad(fam, demo=False):
    return dict(id=f"{fam}{'-demo' if demo else ''}", proof=fam,
                named_story=(fam=="story"), demo=demo, prompt=f"{fam} words{' + screen-demo' if demo else ''}")

def dose(world, it):
    tau0, decay, floor, width = 1.0, .92, .05, .18
    t = (max(floor, tau0*decay**it)-floor)/(tau0-floor)
    w = [math.exp(-((i/6)-t)**2/(2*width**2)) for i in range(7)]
    rng = random.Random(world*1000+it); z=sum(w); pick=rng.random()*z; acc=0
    for i,x in enumerate(w):
        acc+=x
        if pick<=acc: return i   # 0=keep, 1..6 = dose
    return 6

def run_world(world, iters=8, budget=200.0):
    # cumulative ledgers (matched-cell: keyed by context)
    fam_stats = {f: dict(spend=0.0, signups=0, buys=0, demo_spend=0.0, demo_buys=0) for f in FAMS}
    aud_stats = {a: dict(spend=0.0, buys=0) for a in AUDS}
    auto_unreadable = 0
    out = []

    for it in range(1, iters+1):
        d = dose(world, it)
        # ── batch design per doctrine ──
        if it == 1:
            ads = [ad("benefit"), ad("outcome"), ad("story")]
        elif it == 2:
            best = max(FAMS[:3], key=lambda f: fam_stats[f]["signups"]/max(fam_stats[f]["spend"],1))
            ads = [ad(best), ad(best, demo=True), ad("count")]   # complete the sweep + demo
        else:
            # lead: goal events per dollar in SALES cells (matched); proxy fallback within family stats
            def fam_score(f):
                s = fam_stats[f]
                goal = s["buys"]/max(s["spend"],1)
                proxy = s["signups"]/max(s["spend"],1)
                return (goal, proxy)
            ranked = sorted(FAMS, key=fam_score, reverse=True)
            lead, second = ranked[0], ranked[1]
            # demo row: held until funded expectation >= 3 with zero buys -> demote
            demo_spend = sum(fam_stats[f]["demo_spend"] for f in FAMS)
            demo_buys = sum(fam_stats[f]["demo_buys"] for f in FAMS)
            port_rate = max(sum(s["buys"] for s in fam_stats.values()) /
                            max(sum(s["spend"] for s in fam_stats.values()),1), 1e-5)
            demo_demoted = (demo_spend*port_rate >= 3 and demo_buys == 0)
            third = ad(lead, demo=True) if not demo_demoted else ad(ranked[2])
            ads = [ad(lead), ad(second), third]

        # campaigns: start standard; cut auto after 2 unreadable eras; dose-scaled shift
        # toward best per-dollar sales audience with $20 listener floors
        base_alloc = dict(pageviews=40.0, leads=30.0)
        sales_alloc = {a: 30.0 for a in AUDS}
        use_auto = auto_unreadable < 2
        auto_budget = 40.0 if use_auto else 0.0
        if not use_auto:
            for a in AUDS: sales_alloc[a] += 40.0/3
        if it >= 3:
            best_aud = max(AUDS, key=lambda a: aud_stats[a]["buys"]/max(aud_stats[a]["spend"],1))
            shift = d * 5.0
            for a in AUDS:
                if a != best_aud and sales_alloc[a] - shift/2 >= 20.0:
                    sales_alloc[a] -= shift/2; sales_alloc[best_aud] += shift/2
        total = sum(base_alloc.values()) + sum(sales_alloc.values()) + auto_budget
        scale = budget/total
        campaigns = ([dict(id="pageviews",objective="pageviews",audience="broad",budget=round(base_alloc["pageviews"]*scale,2),mode="fixed"),
                      dict(id="leads",objective="leads",audience="broad",budget=round(base_alloc["leads"]*scale,2),mode="fixed")]
                     + [dict(id=f"sales-{a}",objective="sales",audience=a,budget=round(sales_alloc[a]*scale,2),mode="fixed") for a in AUDS]
                     + ([dict(id="sales-auto",objective="sales",audiences=["broad","interest_biztools"],budget=round(auto_budget*scale,2),mode="auto")] if use_auto else []))

        spec = dict(iteration=it, policy=f"scripted-v{it}", ads=ads, campaigns=campaigns)
        R = market.simulate(world, world*100+it, spec)

        # ── ledger updates (trust cells only; composition rule: stats keyed by context) ──
        auto_rows = [r for r in R["rows"] if "auto" in r["cell"]]
        if use_auto and all("NO-TRUST" in r["trust"] for r in auto_rows) and auto_rows:
            auto_unreadable += 1
        for r in R["rows"]:
            if "NO-TRUST" in r["trust"]: continue
            fam = next(a["proof"] for a in ads if a["id"]==r["ad"])
            is_demo = next(a.get("demo",False) for a in ads if a["id"]==r["ad"])
            st = fam_stats[fam]
            st["spend"] += r["spend"]; st["signups"] += r["signups"]; st["buys"] += r["purchases"]
            if is_demo: st["demo_spend"] += r["spend"]; st["demo_buys"] += r["purchases"]
            if r["cell"].startswith("sales-"):
                a_name = r["cell"].replace("sales-","")
                if a_name in aud_stats:
                    aud_stats[a_name]["spend"] += r["spend"]; aud_stats[a_name]["buys"] += r["purchases"]
        out.append(dict(world=world, iteration=it, spend=R["spend"], revenue=R["revenue"],
                        roas=R["roas"], spec=spec))
    return out

def run_baseline(world, iters=8, budget=200.0):
    spec = dict(iteration=1, policy="frozen",
        ads=[ad("benefit"), ad("outcome"), ad("story")],
        campaigns=[dict(id="pageviews",objective="pageviews",audience="broad",budget=budget*0.2,mode="fixed"),
                   dict(id="leads",objective="leads",audience="broad",budget=budget*0.15,mode="fixed")]
                  + [dict(id=f"sales-{a}",objective="sales",audience=a,budget=budget*0.15,mode="fixed") for a in AUDS]
                  + [dict(id="sales-auto",objective="sales",audiences=["broad","interest_biztools"],budget=budget*0.2,mode="auto")])
    return [dict(world=world, iteration=i+1, **{k: market.simulate(world, world*100+900+i, spec)[k] for k in ("spend","revenue","roas")})
            for i in range(iters)]

if __name__ == "__main__":
    w = int(sys.argv[1]); iters = int(sys.argv[2]) if len(sys.argv)>2 else 8
    b = float(sys.argv[3]) if len(sys.argv)>3 else 200.0
    for row in run_world(w, iters, b): print(json.dumps({k:v for k,v in row.items() if k!="spec"}))
