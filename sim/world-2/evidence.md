
━━ ITERATION 1 — policy v0 (seed: original doctrine, taste control) · sim world-2 seed 201 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-selfie: proof=benefit named_story=False demo=False | UGC selfie, female consultant, energetic: 'Beautiful forms in minutes - no code. Pick a te
  outcome-selfie: proof=outcome named_story=False demo=False | IDENTICAL production; only words differ: 'Intake auto-syncs to your CRM - no copy-paste. C
  benefit-calm: proof=benefit named_story=False demo=False | Same words as benefit-selfie; CALM seated delivery. Style experiment.
CAMPAIGN CELLS: clicks($40,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($20,sales,broad,fixed), sales-interest($20,sales,interest_biztools,fixed), sales-auto~broad($29.09,sales,broad,auto), sales-auto~interest_biztools($10.91,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-selfie        clicks                     13.33  0.54   88.9        1  13.33      1     0  [trust]
outcome-selfie        clicks                     13.33  0.60   90.0        2   6.67      0     0  [trust]
benefit-calm          clicks                     13.33  1.62   70.4        2   6.67      1     0  [trust]
benefit-selfie        pageviews                  13.33  1.32   81.8        0      —      0     0  [trust]
outcome-selfie        pageviews                  13.33  0.60   90.0        1  13.33      0     0  [trust]
benefit-calm          pageviews                  13.33  1.62   88.9        0      —      0     0  [trust]
benefit-selfie        leads                      13.33  0.66  100.0        1  13.33      0     0  [trust]
outcome-selfie        leads                      13.33  1.44   70.8        3   4.44      2     0  [trust]
benefit-calm          leads                      13.33  2.22   89.2        5   2.67      2     0  [trust]
benefit-selfie        sales-broad                 6.67  0.84   71.4        0      —      0     0  [trust]
outcome-selfie        sales-broad                 6.67  1.56   76.9        1   6.67      1     0  [trust]
benefit-calm          sales-broad                 6.67  0.84   85.7        1   6.67      0     0  [trust]
benefit-selfie        sales-interest              6.67  0.63   66.7        0      —      0     0  [trust]
outcome-selfie        sales-interest              6.67  0.63   66.7        1   6.67      0     0  [trust]
benefit-calm          sales-interest              6.67  1.26   83.3        2   3.33      2     0  [trust]
benefit-selfie        sales-auto~broad            9.70  0.08  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome-selfie        sales-auto~broad            9.70  2.31   67.9        1    9.7      0     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~broad            9.70  0.74  100.0        0      —      0     0  [NO-TRUST auto-window]
benefit-selfie        sales-auto~interest_biztools  3.64  0.38    0.0        0      —      0     0  [NO-TRUST auto-window]
outcome-selfie        sales-auto~interest_biztools  3.64  0.77  100.0        0      —      0     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~interest_biztools  3.64  0.38    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 209 · sign-ups 21 · demos 9 · purchases 0
ITERATION 1 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 (calm register doctrine) · sim world-2 seed 202 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-calm: proof=benefit named_story=False demo=False | Calm seated delivery, credible tone: 'Beautiful forms in minutes - no code...' (incumbent 
  outcome-calm: proof=outcome named_story=False demo=False | IDENTICAL calm production; only words differ: 'Intake auto-syncs to your CRM - no copy-pas
  benefit-calm-demo: proof=benefit named_story=False demo=True | Same benefit words, calm VO; screen-recording shows a form being built and branded in 60s 
CAMPAIGN CELLS: clicks($40,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($20,sales,broad,fixed), sales-interest($20,sales,interest_biztools,fixed), sales-auto~broad($32.12,sales,broad,auto), sales-auto~interest_biztools($7.88,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-calm          clicks                     13.33  1.26   81.0        2   6.67      1     0  [trust]
outcome-calm          clicks                     13.33  1.86   77.4        3   4.44      0     0  [trust]
benefit-calm-demo     clicks                     13.33  0.60   80.0        2   6.67      1     0  [trust]
benefit-calm          pageviews                  13.33  1.50   80.0        2   6.67      2     0  [trust]
outcome-calm          pageviews                  13.33  0.36   83.3        1  13.33      1     0  [trust]
benefit-calm-demo     pageviews                  13.33  0.90   80.0        1  13.33      0     0  [trust]
benefit-calm          leads                      13.33  1.02   88.2        1  13.33      1     0  [trust]
outcome-calm          leads                      13.33  1.08   66.7        0      —      0     0  [trust]
benefit-calm-demo     leads                      13.33  0.30   80.0        1  13.33      0     0  [trust]
benefit-calm          sales-broad                 6.67  0.24  100.0        0      —      0     0  [trust]
outcome-calm          sales-broad                 6.67  1.92   75.0        1   6.67      1     0  [trust]
benefit-calm-demo     sales-broad                 6.67  0.72   83.3        0      —      0     0  [trust]
benefit-calm          sales-interest              6.67  1.26  100.0        0      —      0     0  [trust]
outcome-calm          sales-interest              6.67  0.63  100.0        1   6.67      0     0  [trust]
benefit-calm-demo     sales-interest              6.67  1.26   83.3        1   6.67      0     0  [trust]
benefit-calm          sales-auto~broad           10.71  0.37   80.0        0      —      0     0  [NO-TRUST auto-window]
outcome-calm          sales-auto~broad           10.71  0.82   90.9        2   5.36      1     0  [NO-TRUST auto-window]
benefit-calm-demo     sales-auto~broad           10.71  1.27   88.2        1  10.71      1     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~interest_biztools  2.63  0.53  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome-calm          sales-auto~interest_biztools  2.63  1.60  100.0        1   2.63      0     0  [NO-TRUST auto-window]
benefit-calm-demo     sales-auto~interest_biztools  2.63  1.60   66.7        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 185 · sign-ups 20 · demos 9 · purchases 0
ITERATION 2 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 (goal-heavy portfolio, calm register) · sim world-2 seed 203 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-calm: proof=benefit named_story=False demo=False | Calm credible benefit words (incumbent)
  outcome-calm: proof=outcome named_story=False demo=False | Calm, outcome words: CRM auto-sync, no copy-paste (copy axis, retest)
  benefit-calm-demo: proof=benefit named_story=False demo=True | Calm benefit words over 60s screen-demo of form being built (demo axis held per goal-layer
CAMPAIGN CELLS: clicks($20,clicks,broad,fixed), pageviews($20,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($60,sales,broad,fixed), sales-interest($60,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-calm          clicks                      6.67  1.32   72.7        0      —      0     0  [trust]
outcome-calm          clicks                      6.67  1.20   50.0        1   6.67      0     0  [trust]
benefit-calm-demo     clicks                      6.67  0.36   66.7        0      —      0     0  [trust]
benefit-calm          pageviews                   6.67  0.36   33.3        0      —      0     0  [trust]
outcome-calm          pageviews                   6.67  1.32   63.6        0      —      0     0  [trust]
benefit-calm-demo     pageviews                   6.67  0.36  100.0        0      —      0     0  [trust]
benefit-calm          leads                      13.33  0.48   62.5        1  13.33      0     0  [trust]
outcome-calm          leads                      13.33  1.56   76.9        2   6.67      1     0  [trust]
benefit-calm-demo     leads                      13.33  0.78   69.2        1  13.33      1     0  [trust]
benefit-calm          sales-broad                20.00  0.52   76.9        1   20.0      0     0  [trust]
outcome-calm          sales-broad                20.00  1.00   64.0        0      —      0     0  [trust]
benefit-calm-demo     sales-broad                20.00  0.96   70.8        1   20.0      0     0  [trust]
benefit-calm          sales-interest             20.00  0.56   87.5        1   20.0      0     0  [trust]
outcome-calm          sales-interest             20.00  0.42   66.7        1   20.0      1     0  [trust]
benefit-calm-demo     sales-interest             20.00  0.35   80.0        1   20.0      1     0  [trust]
SITE FUNNEL: visits 118 · sign-ups 10 · demos 4 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 (audience exploration, goal-heavy) · sim world-2 seed 204 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-calm: proof=benefit named_story=False demo=False | Calm credible benefit words (incumbent)
  outcome-calm: proof=outcome named_story=False demo=False | Calm outcome words (copy axis held)
  benefit-calm-demo: proof=benefit named_story=False demo=True | Calm benefit words over 60s product screen-demo (demo axis held)
CAMPAIGN CELLS: pageviews($30,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-interest($40,sales,interest_biztools,fixed), sales-niche($50,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-calm          pageviews                  10.00  0.32   75.0        0      —      0     0  [trust]
outcome-calm          pageviews                  10.00  0.64   75.0        0      —      0     0  [trust]
benefit-calm-demo     pageviews                  10.00  0.88   63.6        0      —      0     0  [trust]
benefit-calm          leads                      13.33  1.74   79.3        3   4.44      0     0  [trust]
outcome-calm          leads                      13.33  1.14   73.7        3   4.44      1     0  [trust]
benefit-calm-demo     leads                      13.33  1.68   92.9        3   4.44      0     0  [trust]
benefit-calm          sales-broad                13.33  0.60   80.0        1  13.33      0     0  [trust]
outcome-calm          sales-broad                13.33  0.42   85.7        1  13.33      1     0  [trust]
benefit-calm-demo     sales-broad                13.33  0.30   80.0        0      —      0     0  [trust]
benefit-calm          sales-interest             13.33  1.05   70.0        0      —      0     0  [trust]
outcome-calm          sales-interest             13.33  1.26   75.0        1  13.33      0     0  [trust]
benefit-calm-demo     sales-interest             13.33  1.57   80.0        2   6.67      1     0  [trust]
benefit-calm          sales-niche                16.67  0.43   66.7        0      —      0     0  [trust]
outcome-calm          sales-niche                16.67  1.37   78.9        1  16.67      1     1  [trust]
benefit-calm-demo     sales-niche                16.67  0.29   75.0        0      —      0     0  [trust]
SITE FUNNEL: visits 147 · sign-ups 15 · demos 4 · purchases 1
ITERATION 4 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 5 — policy v4 (niche confirmation) · sim world-2 seed 205 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-calm: proof=outcome named_story=False demo=False | Calm outcome words (promoted lead: 4/4 directional + the purchase)
  benefit-calm: proof=benefit named_story=False demo=False | Calm benefit words (former lead, now challenger)
  outcome-calm-demo: proof=outcome named_story=False demo=True | Outcome words over 60s product screen-demo (demo axis moved onto the lead words)
CAMPAIGN CELLS: pageviews($20,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($80,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-calm          pageviews                   6.67  0.84   71.4        1   6.67      0     0  [trust]
benefit-calm          pageviews                   6.67  1.32   72.7        0      —      0     0  [trust]
outcome-calm-demo     pageviews                   6.67  0.60   60.0        1   6.67      1     0  [trust]
outcome-calm          leads                      13.33  2.16   75.0        0      —      0     0  [trust]
benefit-calm          leads                      13.33  1.02   82.4        0      —      0     0  [trust]
outcome-calm-demo     leads                      13.33  0.30   80.0        0      —      0     0  [trust]
outcome-calm          sales-broad                10.00  2.48   64.5        3   3.33      0     0  [trust]
benefit-calm          sales-broad                10.00  1.44   66.7        3   3.33      0     0  [trust]
outcome-calm-demo     sales-broad                10.00  1.44   55.6        1   10.0      0     0  [trust]
outcome-calm          sales-interest             10.00  1.40   70.0        0      —      0     0  [trust]
benefit-calm          sales-interest             10.00  0.70   80.0        1   10.0      1     0  [trust]
outcome-calm-demo     sales-interest             10.00  0.14  100.0        0      —      0     0  [trust]
outcome-calm          sales-niche                26.67  0.95   66.7        3   8.89      1     0  [trust]
benefit-calm          sales-niche                26.67  0.40   77.8        1  26.67      0     0  [trust]
outcome-calm-demo     sales-niche                26.67  0.77   88.2        5   5.33      3     4  [trust]
SITE FUNNEL: visits 151 · sign-ups 19 · demos 6 · purchases 4
ITERATION 5 TOTALS: spend $200.01 · settled revenue $116 · ROAS 0.58
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 116, "roas": 0.58}

━━ ITERATION 6 — policy v5 (demo-led rule) · sim world-2 seed 206 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-calm-demo: proof=outcome named_story=False demo=True | Lead: outcome words, calm VO, 60s product screen-demo (the 4-buy cell's creative)
  benefit-calm-demo: proof=benefit named_story=False demo=True | Copy axis under demo: benefit words, same calm VO, same demo footage
  outcome-calm-nodemo: proof=outcome named_story=False demo=False | Declared hypothesis row: lead words without demo (the beaten variant, kept as control)
CAMPAIGN CELLS: pageviews($20,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($80,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-calm-demo     pageviews                   6.67  0.96   62.5        1   6.67      1     0  [trust]
benefit-calm-demo     pageviews                   6.67  0.84   85.7        2   3.33      1     0  [trust]
outcome-calm-nodemo   pageviews                   6.67  1.80   80.0        1   6.67      1     0  [trust]
outcome-calm-demo     leads                      13.33  0.90   66.7        2   6.67      0     0  [trust]
benefit-calm-demo     leads                      13.33  0.12  100.0        1  13.33      1     0  [trust]
outcome-calm-nodemo   leads                      13.33  1.14   84.2        0      —      0     0  [trust]
outcome-calm-demo     sales-broad                10.00  1.20   66.7        1   10.0      0     0  [trust]
benefit-calm-demo     sales-broad                10.00  0.16   50.0        0      —      0     0  [trust]
outcome-calm-nodemo   sales-broad                10.00  1.52   63.2        0      —      0     0  [trust]
outcome-calm-demo     sales-interest             10.00  1.12   87.5        0      —      0     0  [trust]
benefit-calm-demo     sales-interest             10.00  0.28   50.0        1   10.0      1     0  [trust]
outcome-calm-nodemo   sales-interest             10.00  0.14    0.0        0      —      0     0  [trust]
outcome-calm-demo     sales-niche                26.67  1.22   63.0        1  26.67      1     0  [trust]
benefit-calm-demo     sales-niche                26.67  0.95   71.4        2  13.34      1     0  [trust]
outcome-calm-nodemo   sales-niche                26.67  0.58   46.2        0      —      0     0  [trust]
SITE FUNNEL: visits 120 · sign-ups 12 · demos 7 · purchases 0
ITERATION 6 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 (concentration) · sim world-2 seed 207 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-calm-demo: proof=outcome named_story=False demo=True | Lead: outcome words, calm VO, 60s screen-demo
  benefit-calm-demo: proof=benefit named_story=False demo=True | Copy axis: benefit words, same demo footage
  count-calm-demo: proof=count named_story=False demo=True | Copy axis widened: adoption-count words ('2,300 firms run intake here') over the same demo
CAMPAIGN CELLS: leads($40,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($100,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-calm-demo     leads                      13.33  1.56   69.2        2   6.67      2     0  [trust]
benefit-calm-demo     leads                      13.33  1.02   88.2        1  13.33      0     0  [trust]
count-calm-demo       leads                      13.33  1.62   88.9        2   6.67      1     0  [trust]
outcome-calm-demo     sales-broad                10.00  0.56   71.4        1   10.0      1     0  [trust]
benefit-calm-demo     sales-broad                10.00  1.28   75.0        4    2.5      2     1  [trust]
count-calm-demo       sales-broad                10.00  1.28   37.5        0      —      0     0  [trust]
outcome-calm-demo     sales-interest             10.00  0.56   75.0        0      —      0     0  [trust]
benefit-calm-demo     sales-interest             10.00  1.40   90.0        1   10.0      0     0  [trust]
count-calm-demo       sales-interest             10.00  0.84   33.3        0      —      0     0  [trust]
outcome-calm-demo     sales-niche                33.33  1.33   59.5        5   6.67      4     0  [trust]
benefit-calm-demo     sales-niche                33.33  0.68   89.5        2  16.66      1     0  [trust]
count-calm-demo       sales-niche                33.33  0.32   88.9        1  33.33      0     1  [trust]
SITE FUNNEL: visits 141 · sign-ups 19 · demos 11 · purchases 2
ITERATION 7 TOTALS: spend $199.98 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 58, "roas": 0.29}

━━ ITERATION 8 — policy v7 (consolidated rules) · sim world-2 seed 208 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-calm-demo: proof=outcome named_story=False demo=True | Lead per v7 rules: outcome words, calm VO, 60s screen-demo
  story-calm-demo: proof=story named_story=True demo=True | Copy axis, last untested style: named-customer story ('Rivera Consulting cut intake 80%') 
  outcome-calm-demo-v2: proof=outcome named_story=False demo=True | Production axis: same lead words, demo re-cut to open ON the proof moment (first 3s)
CAMPAIGN CELLS: leads($40,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($100,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-calm-demo     leads                      13.33  0.42   42.9        1  13.33      0     0  [trust]
story-calm-demo       leads                      13.33  1.20   75.0        1  13.33      0     0  [trust]
outcome-calm-demo-v2  leads                      13.33  1.02   88.2        1  13.33      1     1  [trust]
outcome-calm-demo     sales-broad                10.00  0.48  100.0        0      —      0     0  [trust]
story-calm-demo       sales-broad                10.00  0.32   50.0        0      —      0     0  [trust]
outcome-calm-demo-v2  sales-broad                10.00  2.40   90.0        2    5.0      0     0  [trust]
outcome-calm-demo     sales-interest             10.00  1.40   80.0        0      —      0     0  [trust]
story-calm-demo       sales-interest             10.00  0.42   66.7        0      —      0     0  [trust]
outcome-calm-demo-v2  sales-interest             10.00  0.84   66.7        0      —      0     0  [trust]
outcome-calm-demo     sales-niche                33.33  1.62   68.9        4   8.33      3     1  [trust]
story-calm-demo       sales-niche                33.33  0.97   51.9        2  16.66      0     0  [trust]
outcome-calm-demo-v2  sales-niche                33.33  0.61   82.4        1  33.33      1     0  [trust]
SITE FUNNEL: visits 141 · sign-ups 12 · demos 5 · purchases 2
ITERATION 8 TOTALS: spend $199.98 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 58, "roas": 0.29}
