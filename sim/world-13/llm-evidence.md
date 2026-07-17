
━━ ITERATION 1 — policy v0 · sim world-13 seed 1301 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with the core benefit: stop rebuilding the same intak
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with a concrete outcome: teams cut form-building time
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo). Named customer story: Maya Chen, ops lead at a 12-person a
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($29.7,sales,broad,auto), sales-auto~interest_biztools($10.3,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.30   80.0        0      —      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.06  100.0        0      —      0     0  [trust]
it1-story             pv-broad                   13.33  0.84   57.1        0      —      0     0  [trust]
it1-benefit           leads-broad                13.33  0.72   75.0        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.72   91.7        1  13.33      1     0  [trust]
it1-story             leads-broad                13.33  0.84   78.6        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.48   87.5        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it1-story             sales-broad                13.33  1.14   68.4        1  13.33      1     0  [trust]
it1-benefit           sales-biztools             13.33  0.21  100.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.42   50.0        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  0.42   75.0        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            9.90  0.08    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            9.90  0.73   44.4        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            9.90  0.48   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  3.43  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools  3.43  0.41  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools  3.43  1.63   75.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 85 · sign-ups 2 · demos 2 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-13 seed 1302 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo). Named customer story: Maya Chen, ops lead at a 12-person a
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with adoption numbers: 4,200 teams build their intake
  it2-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Named customer story: Diego Ramos, clinic manager, narrate
CAMPAIGN CELLS: sales-broad($50,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed), leads-broad($30,leads,broad,fixed), clicks-broad($20,clicks,broad,fixed), sales-auto~broad($22.4,sales,broad,auto), sales-auto~interest_biztools($7.6,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-story             sales-broad                16.67  0.43   66.7        0      —      0     0  [trust]
it2-count             sales-broad                16.67  0.72   66.7        1  16.67      1     0  [trust]
it2-story-demo        sales-broad                16.67  0.43   77.8        0      —      0     0  [trust]
it2-story             sales-biztools             10.00  1.40   90.0        0      —      0     0  [trust]
it2-count             sales-biztools             10.00  0.98   71.4        0      —      0     0  [trust]
it2-story-demo        sales-biztools             10.00  0.98   85.7        1   10.0      1     0  [trust]
it2-story             sales-niche                13.33  0.36   75.0        1  13.33      0     0  [trust]
it2-count             sales-niche                13.33  0.45   40.0        0      —      0     0  [trust]
it2-story-demo        sales-niche                13.33  2.61   62.1        3   4.44      1     0  [trust]
it2-story             leads-broad                10.00  0.96   83.3        2    5.0      0     0  [trust]
it2-count             leads-broad                10.00  0.88   54.5        0      —      0     0  [trust]
it2-story-demo        leads-broad                10.00  0.40   60.0        0      —      0     0  [trust]
it2-story             clicks-broad                6.67  0.48   75.0        0      —      0     0  [trust]
it2-count             clicks-broad                6.67  0.36   66.7        0      —      0     0  [trust]
it2-story-demo        clicks-broad                6.67  0.96   75.0        0      —      0     0  [trust]
it2-story             sales-auto~broad            7.47  0.64   83.3        1   7.47      0     1  [NO-TRUST auto-window]
it2-count             sales-auto~broad            7.47  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~broad            7.47  1.29   75.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  2.53  0.55    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  2.53  1.66   33.3        1   2.53      0     1  [NO-TRUST auto-window]
it2-story-demo        sales-auto~interest_biztools  2.53  0.55  100.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 112 · sign-ups 10 · demos 3 · purchases 2
ITERATION 2 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 58, "roas": 0.29}

━━ ITERATION 3 — policy v2 · sim world-13 seed 1303 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: live screen recording of Formflow turning a mes
  it3-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording building a client-onboarding f
  it3-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording of a form going live and submi
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($60,sales,interest_niche,fixed), sales-biztools($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story-demo-a      pv-broad                   10.00  0.56   85.7        0      —      0     0  [trust]
it3-story-demo-b      pv-broad                   10.00  1.36   88.2        1   10.0      0     0  [trust]
it3-count-demo        pv-broad                   10.00  0.56   85.7        0      —      0     0  [trust]
it3-story-demo-a      leads-broad                10.00  0.48   66.7        1   10.0      0     0  [trust]
it3-story-demo-b      leads-broad                10.00  0.40   80.0        0      —      0     0  [trust]
it3-count-demo        leads-broad                10.00  0.24    0.0        0      —      0     0  [trust]
it3-story-demo-a      sales-broad                13.33  0.36   66.7        0      —      0     0  [trust]
it3-story-demo-b      sales-broad                13.33  0.96   75.0        1  13.33      1     1  [trust]
it3-count-demo        sales-broad                13.33  0.48   50.0        0      —      0     0  [trust]
it3-story-demo-a      sales-niche                20.00  0.54   44.4        0      —      0     0  [trust]
it3-story-demo-b      sales-niche                20.00  0.78   69.2        1   20.0      0     0  [trust]
it3-count-demo        sales-niche                20.00  0.72   50.0        1   20.0      1     0  [trust]
it3-story-demo-a      sales-biztools             13.33  0.84   87.5        2   6.67      1     0  [trust]
it3-story-demo-b      sales-biztools             13.33  0.52  100.0        0      —      0     0  [trust]
it3-count-demo        sales-biztools             13.33  0.73   71.4        0      —      0     0  [trust]
SITE FUNNEL: visits 91 · sign-ups 7 · demos 3 · purchases 1
ITERATION 3 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 4 — policy v3 · sim world-13 seed 1304 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: live screen recording turning a messy intake do
  it4-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording building a client-onboarding f
  it4-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording of a form going live and submi
CAMPAIGN CELLS: sales-broad($60,sales,broad,fixed), sales-biztools($60,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed), leads-control($20,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-demo-a      sales-broad                20.00  0.76   68.4        3   6.67      1     0  [trust]
it4-story-demo-b      sales-broad                20.00  0.80   60.0        1   20.0      1     0  [trust]
it4-count-demo        sales-broad                20.00  0.56   50.0        1   20.0      0     0  [trust]
it4-story-demo-a      sales-biztools             20.00  1.47   61.9        0      —      0     0  [trust]
it4-story-demo-b      sales-biztools             20.00  1.19   82.4        1   20.0      0     0  [trust]
it4-count-demo        sales-biztools             20.00  0.77   90.9        1   20.0      1     0  [trust]
it4-story-demo-a      sales-niche                20.00  1.38   82.6        1   20.0      0     0  [trust]
it4-story-demo-b      sales-niche                20.00  0.48   87.5        2   10.0      1     0  [trust]
it4-count-demo        sales-niche                20.00  0.60  100.0        1   20.0      0     1  [trust]
it4-story-demo-a      leads-control               6.67  0.36   66.7        1   6.67      1     0  [trust]
it4-story-demo-b      leads-control               6.67  1.44   83.3        0      —      0     0  [trust]
it4-count-demo        leads-control               6.67  0.36   33.3        0      —      0     0  [trust]
SITE FUNNEL: visits 118 · sign-ups 12 · demos 5 · purchases 1
ITERATION 4 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 5 — policy v4 · sim world-13 seed 1305 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: live screen recording turning a messy intake do
  it5-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording of a form going live and submi
CAMPAIGN CELLS: sales-broad($60,sales,broad,fixed), sales-niche($70,sales,interest_niche,fixed), sales-biztools($50,sales,interest_biztools,fixed), pv-control($20,pageviews,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-demo        sales-broad                30.00  1.33   62.0        3   10.0      0     0  [trust]
it5-count-demo        sales-broad                30.00  0.80   60.0        2   15.0      0     0  [trust]
it5-story-demo        sales-niche                35.00  0.86   76.0        3  11.67      2     1  [trust]
it5-count-demo        sales-niche                35.00  0.38   90.9        0      —      0     0  [trust]
it5-story-demo        sales-biztools             25.00  0.34   66.7        0      —      0     0  [trust]
it5-count-demo        sales-biztools             25.00  1.34   79.2        1   25.0      0     0  [trust]
it5-story-demo        pv-control                 10.00  0.48  100.0        0      —      0     0  [trust]
it5-count-demo        pv-control                 10.00  1.12   71.4        1   10.0      1     0  [trust]
SITE FUNNEL: visits 117 · sign-ups 10 · demos 3 · purchases 1
ITERATION 5 TOTALS: spend $200.0 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 5, "spend": 200.0, "revenue": 29, "roas": 0.14}

━━ ITERATION 6 — policy v5 · sim world-13 seed 1306 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: live screen recording turning a messy intake do
  it6-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording of a form going live and submi
CAMPAIGN CELLS: sales-niche($100,sales,interest_niche,fixed), sales-broad($80,sales,broad,fixed), leads-control($20,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-story-demo        sales-niche                50.00  0.82   61.8        2   25.0      0     0  [trust]
it6-count-demo        sales-niche                50.00  0.24   30.0        0      —      0     0  [trust]
it6-story-demo        sales-broad                40.00  0.74   67.6        1   40.0      1     0  [trust]
it6-count-demo        sales-broad                40.00  1.48   75.7        9   4.44      5     1  [trust]
it6-story-demo        leads-control              10.00  0.80   50.0        0      —      0     0  [trust]
it6-count-demo        leads-control              10.00  0.96   83.3        1   10.0      0     0  [trust]
SITE FUNNEL: visits 120 · sign-ups 13 · demos 6 · purchases 1
ITERATION 6 TOTALS: spend $200.0 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 6, "spend": 200.0, "revenue": 29, "roas": 0.14}

━━ ITERATION 7 — policy v6 · sim world-13 seed 1307 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Demo core: live screen recording turning a messy intake do
  it7-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Demo core: screen recording of a form going live and submi
CAMPAIGN CELLS: sales-broad($100,sales,broad,fixed), sales-niche($80,sales,interest_niche,fixed), pv-control($20,pageviews,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-story-demo        sales-broad                50.00  0.53   51.5        0      —      0     0  [trust]
it7-count-demo        sales-broad                50.00  0.48   70.0        0      —      0     0  [trust]
it7-story-demo        sales-niche                40.00  1.20   72.5        2   20.0      2     0  [trust]
it7-count-demo        sales-niche                40.00  0.45   73.3        1   40.0      1     0  [trust]
it7-story-demo        pv-control                 10.00  0.56   85.7        1   10.0      0     0  [trust]
it7-count-demo        pv-control                 10.00  1.28   75.0        1   10.0      0     0  [trust]
SITE FUNNEL: visits 96 · sign-ups 5 · demos 3 · purchases 0
ITERATION 7 TOTALS: spend $200.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 200.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-13 seed 1308 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-demo-fresh: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Fresh execution. Demo core: new screen recording — importi
  it8-count-demo-fresh: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Fresh execution. Demo core: new screen recording — analyti
CAMPAIGN CELLS: sales-broad($100,sales,broad,fixed), sales-niche($80,sales,interest_niche,fixed), leads-control($20,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-demo-fresh  sales-broad                50.00  0.94   71.2        5   10.0      3     0  [trust]
it8-count-demo-fresh  sales-broad                50.00  0.94   69.5        2   25.0      0     0  [trust]
it8-story-demo-fresh  sales-niche                40.00  0.54   66.7        0      —      0     0  [trust]
it8-count-demo-fresh  sales-niche                40.00  0.60   50.0        0      —      0     0  [trust]
it8-story-demo-fresh  leads-control              10.00  1.04   53.8        0      —      0     0  [trust]
it8-count-demo-fresh  leads-control              10.00  0.72   55.6        0      —      0     0  [trust]
SITE FUNNEL: visits 117 · sign-ups 7 · demos 3 · purchases 0
ITERATION 8 TOTALS: spend $200.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 200.0, "revenue": 0, "roas": 0.0}
