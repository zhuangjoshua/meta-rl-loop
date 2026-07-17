
━━ ITERATION 1 — policy v0 · sim world-11 seed 1101 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with the core benefit: stop rebuilding the same intak
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with a concrete outcome: teams cut form-handling time
  it1-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with adoption numbers: thousands of teams run their i
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($29.33,sales,broad,auto), sales-auto~interest_biztools($10.67,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.78   92.3        1  13.33      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.84   78.6        2   6.67      1     0  [trust]
it1-count             pv-broad                   13.33  0.54   66.7        0      —      0     0  [trust]
it1-benefit           leads-broad                13.33  0.48   75.0        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  1.02   64.7        1  13.33      0     0  [trust]
it1-count             leads-broad                13.33  0.48   87.5        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.36  100.0        1  13.33      1     0  [trust]
it1-outcome           sales-broad                13.33  1.38   65.2        3   4.44      1     0  [trust]
it1-count             sales-broad                13.33  0.78   46.2        0      —      0     0  [trust]
it1-benefit           sales-biztools             13.33  1.05   60.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.73   85.7        1  13.33      0     0  [trust]
it1-count             sales-biztools             13.33  0.84   50.0        1  13.33      0     0  [trust]
it1-benefit           sales-auto~broad            9.78  0.16   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            9.78  0.74   66.7        1   9.78      0     0  [NO-TRUST auto-window]
it1-count             sales-auto~broad            9.78  0.49   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  3.56  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools  3.56  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-count             sales-auto~interest_biztools  3.56  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 106 · sign-ups 11 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-11 seed 1102 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with a concrete outcome: teams cut form-handling time
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Same outcome-first hook — hours to minutes, no lost submis
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo). Named customer story: Maya Chen, ops lead at a 12-person a
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($4.25,sales,broad,auto), sales-auto~interest_biztools($25.75,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-outcome           pv-broad                   10.00  0.80   60.0        0      —      0     0  [trust]
it2-outcome-demo      pv-broad                   10.00  1.36   82.4        1   10.0      0     0  [trust]
it2-story             pv-broad                   10.00  1.12   71.4        0      —      0     0  [trust]
it2-outcome           leads-broad                10.00  0.88   81.8        2    5.0      0     0  [trust]
it2-outcome-demo      leads-broad                10.00  1.20   53.3        2    5.0      0     0  [trust]
it2-story             leads-broad                10.00  0.40   80.0        1   10.0      1     0  [trust]
it2-outcome           sales-broad                13.33  0.54   66.7        1  13.33      0     0  [trust]
it2-outcome-demo      sales-broad                13.33  0.96   68.8        0      —      0     0  [trust]
it2-story             sales-broad                13.33  1.92   43.8        0      —      0     0  [trust]
it2-outcome           sales-biztools             10.00  1.12   87.5        1   10.0      0     0  [trust]
it2-outcome-demo      sales-biztools             10.00  0.84   50.0        0      —      0     0  [trust]
it2-story             sales-biztools             10.00  1.40   60.0        0      —      0     0  [trust]
it2-outcome           sales-niche                13.33  0.81   66.7        0      —      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  1.62   72.2        2   6.67      0     0  [trust]
it2-story             sales-niche                13.33  1.08   83.3        1  13.33      0     0  [trust]
it2-outcome           sales-auto~broad            1.42  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad            1.42  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~broad            1.42  0.56  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome           sales-auto~interest_biztools  8.58  0.82   80.0        1   8.58      1     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  8.58  0.33  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  8.58  1.96   75.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 143 · sign-ups 12 · demos 2 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-11 seed 1103 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with a concrete outcome: teams cut form-handling time
  it3-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Outcome-first hook — hours to minutes, no lost submissions
  it3-outcome-money: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with a money outcome: teams reclaim a full workday pe
CAMPAIGN CELLS: sales-broad($55,sales,broad,fixed), sales-biztools($50,sales,interest_biztools,fixed), sales-niche($50,sales,interest_niche,fixed), clicks-broad($15,clicks,broad,fixed), pv-broad($15,pageviews,broad,fixed), leads-broad($15,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-outcome           sales-broad                18.33  1.09   64.0        0      —      0     0  [trust]
it3-outcome-demo      sales-broad                18.33  1.40   65.6        2   9.16      1     0  [trust]
it3-outcome-money     sales-broad                18.33  0.44   80.0        0      —      0     0  [trust]
it3-outcome           sales-biztools             16.67  1.76   71.4        3   5.56      1     0  [trust]
it3-outcome-demo      sales-biztools             16.67  1.01   91.7        2   8.34      1     0  [trust]
it3-outcome-money     sales-biztools             16.67  0.25   66.7        0      —      0     0  [trust]
it3-outcome           sales-niche                16.67  1.30   77.8        2   8.34      0     0  [trust]
it3-outcome-demo      sales-niche                16.67  0.14   50.0        0      —      0     0  [trust]
it3-outcome-money     sales-niche                16.67  0.65   77.8        0      —      0     0  [trust]
it3-outcome           clicks-broad                5.00  1.92   25.0        0      —      0     0  [trust]
it3-outcome-demo      clicks-broad                5.00  0.32   50.0        0      —      0     0  [trust]
it3-outcome-money     clicks-broad                5.00  1.92   66.7        0      —      0     0  [trust]
it3-outcome           pv-broad                    5.00  0.96   50.0        0      —      0     0  [trust]
it3-outcome-demo      pv-broad                    5.00  0.32  100.0        0      —      0     0  [trust]
it3-outcome-money     pv-broad                    5.00  0.64  100.0        0      —      0     0  [trust]
it3-outcome           leads-broad                 5.00  1.12   71.4        0      —      0     0  [trust]
it3-outcome-demo      leads-broad                 5.00  0.96   83.3        1    5.0      0     0  [trust]
it3-outcome-money     leads-broad                 5.00  0.16  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 127 · sign-ups 10 · demos 3 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-11 seed 1104 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-time: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Lived before/after: hours of form admin becomes minutes. O
  it4-loss: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Opens 
  it4-drudge: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: nobody rebuilds the same intake form a
CAMPAIGN CELLS: sales-broad($55,sales,broad,fixed), sales-biztools($60,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed), pv-broad($15,pageviews,broad,fixed), leads-broad($15,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-time              sales-broad                18.33  1.40   78.1        3   6.11      0     0  [trust]
it4-loss              sales-broad                18.33  0.35   87.5        0      —      0     0  [trust]
it4-drudge            sales-broad                18.33  0.35   75.0        1  18.33      0     0  [trust]
it4-time              sales-biztools             20.00  0.70   70.0        1   20.0      1     0  [trust]
it4-loss              sales-biztools             20.00  0.56   87.5        3   6.67      0     0  [trust]
it4-drudge            sales-biztools             20.00  0.28   75.0        1   20.0      0     0  [trust]
it4-time              sales-niche                18.33  1.31   75.0        1  18.33      0     0  [trust]
it4-loss              sales-niche                18.33  0.39   83.3        1  18.33      0     0  [trust]
it4-drudge            sales-niche                18.33  0.65   70.0        0      —      0     0  [trust]
it4-time              pv-broad                    5.00  0.96   66.7        0      —      0     0  [trust]
it4-loss              pv-broad                    5.00  0.16  100.0        0      —      0     0  [trust]
it4-drudge            pv-broad                    5.00  0.48   66.7        0      —      0     0  [trust]
it4-time              leads-broad                 5.00  0.64   75.0        0      —      0     0  [trust]
it4-loss              leads-broad                 5.00  1.44   55.6        0      —      0     0  [trust]
it4-drudge            leads-broad                 5.00  1.12   57.1        0      —      0     0  [trust]
SITE FUNNEL: visits 101 · sign-ups 11 · demos 1 · purchases 0
ITERATION 4 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-11 seed 1105 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-time-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Lived before/after: hours of form admin becomes minutes. O
  it5-time: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: hours of form admin becomes minutes. O
  it5-loss: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Opens 
CAMPAIGN CELLS: sales-broad($70,sales,broad,fixed), sales-biztools($80,sales,interest_biztools,fixed), sales-niche($50,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-time-demo         sales-broad                23.33  0.62   55.6        1  23.33      1     0  [trust]
it5-time              sales-broad                23.33  0.58   82.4        2  11.66      1     0  [trust]
it5-loss              sales-broad                23.33  0.82   62.5        1  23.33      0     0  [trust]
it5-time-demo         sales-biztools             26.67  1.78   67.6        1  26.67      0     0  [trust]
it5-time              sales-biztools             26.67  1.05   90.0        1  26.67      0     0  [trust]
it5-loss              sales-biztools             26.67  1.26   62.5        2  13.34      1     0  [trust]
it5-time-demo         sales-niche                16.67  0.22   33.3        0      —      0     0  [trust]
it5-time              sales-niche                16.67  1.37   47.4        1  16.67      0     0  [trust]
it5-loss              sales-niche                16.67  0.58  100.0        1  16.67      0     0  [trust]
SITE FUNNEL: visits 113 · sign-ups 10 · demos 3 · purchases 0
ITERATION 5 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 · sim world-11 seed 1106 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-time: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: hours of form admin becomes minutes. O
  it6-loss: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Opens 
  it6-loss-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Same l
CAMPAIGN CELLS: sales-broad($75,sales,broad,fixed), sales-biztools($85,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-time              sales-broad                25.00  0.48   86.7        2   12.5      1     0  [trust]
it6-loss              sales-broad                25.00  0.51   75.0        4   6.25      0     0  [trust]
it6-loss-demo         sales-broad                25.00  0.29   66.7        1   25.0      0     0  [trust]
it6-time              sales-biztools             28.33  1.04   61.9        0      —      0     0  [trust]
it6-loss              sales-biztools             28.33  0.40   62.5        0      —      0     0  [trust]
it6-loss-demo         sales-biztools             28.33  1.14   73.9        2  14.16      0     0  [trust]
it6-time              sales-niche                13.33  0.90   70.0        0      —      0     0  [trust]
it6-loss              sales-niche                13.33  1.26   57.1        2   6.67      0     0  [trust]
it6-loss-demo         sales-niche                13.33  0.45   60.0        0      —      0     0  [trust]
SITE FUNNEL: visits 84 · sign-ups 11 · demos 1 · purchases 0
ITERATION 6 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-11 seed 1107 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-time: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: hours of form admin becomes minutes. O
  it7-loss: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Opens 
  it7-loss-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Same l
CAMPAIGN CELLS: sales-broad($75,sales,broad,fixed), sales-biztools($85,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-time              sales-broad                25.00  1.82   66.7        1   25.0      0     0  [trust]
it7-loss              sales-broad                25.00  0.83   80.8        0      —      0     0  [trust]
it7-loss-demo         sales-broad                25.00  0.45   71.4        0      —      0     0  [trust]
it7-time              sales-biztools             28.33  0.44   77.8        1  28.33      0     0  [trust]
it7-loss              sales-biztools             28.33  0.40   62.5        1  28.33      0     0  [trust]
it7-loss-demo         sales-biztools             28.33  0.84   76.5        2  14.16      0     0  [trust]
it7-time              sales-niche                13.33  1.44   62.5        1  13.33      0     0  [trust]
it7-loss              sales-niche                13.33  2.25   56.0        1  13.33      0     0  [trust]
it7-loss-demo         sales-niche                13.33  0.72   50.0        0      —      0     0  [trust]
SITE FUNNEL: visits 122 · sign-ups 7 · demos 0 · purchases 0
ITERATION 7 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-11 seed 1108 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-time-fresh: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Lived before/after: hours of form admin becomes minutes. F
  it8-loss-fresh: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: submissions stop going missing. Fresh 
  it8-drudge-fresh: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lived before/after: nobody rebuilds the same intake form a
CAMPAIGN CELLS: sales-broad($75,sales,broad,fixed), sales-biztools($85,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-time-fresh        sales-broad                25.00  0.96   83.3        4   6.25      2     0  [trust]
it8-loss-fresh        sales-broad                25.00  0.38   83.3        1   25.0      0     0  [trust]
it8-drudge-fresh      sales-broad                25.00  0.77   70.8        1   25.0      0     0  [trust]
it8-time-fresh        sales-biztools             28.33  0.40   87.5        1  28.33      0     0  [trust]
it8-loss-fresh        sales-biztools             28.33  1.58   71.9        1  28.33      0     0  [trust]
it8-drudge-fresh      sales-biztools             28.33  0.40   75.0        0      —      0     0  [trust]
it8-time-fresh        sales-niche                13.33  1.08   50.0        0      —      0     0  [trust]
it8-loss-fresh        sales-niche                13.33  0.18   50.0        0      —      0     0  [trust]
it8-drudge-fresh      sales-niche                13.33  1.98   68.2        1  13.33      0     0  [trust]
SITE FUNNEL: visits 110 · sign-ups 9 · demos 2 · purchases 0
ITERATION 8 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 0, "roas": 0.0}
