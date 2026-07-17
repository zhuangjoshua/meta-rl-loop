
━━ ITERATION 1 — policy v0 (seed, taste control) · sim world-3 seed 301 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-selfie: proof=benefit named_story=False demo=False | UGC selfie, female consultant, energetic: 'Beautiful forms in minutes - no code. Pick a te
  outcome-selfie: proof=outcome named_story=False demo=False | IDENTICAL production; only words differ: 'Intake auto-syncs to your CRM - no copy-paste. C
  benefit-calm: proof=benefit named_story=False demo=False | Same words as benefit-selfie; CALM seated delivery. Style experiment.
CAMPAIGN CELLS: clicks($40,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($20,sales,broad,fixed), sales-interest($20,sales,interest_biztools,fixed), sales-auto~broad($32.06,sales,broad,auto), sales-auto~interest_biztools($7.94,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-selfie        clicks                     13.33  2.10   80.0        1  13.33      0     0  [trust]
outcome-selfie        clicks                     13.33  0.90   73.3        1  13.33      0     1  [trust]
benefit-calm          clicks                     13.33  1.80   80.0        5   2.67      2     0  [trust]
benefit-selfie        pageviews                  13.33  0.66  100.0        0      —      0     0  [trust]
outcome-selfie        pageviews                  13.33  0.90   60.0        2   6.67      0     0  [trust]
benefit-calm          pageviews                  13.33  1.32   77.3        4   3.33      2     1  [trust]
benefit-selfie        leads                      13.33  1.14   84.2        2   6.67      1     0  [trust]
outcome-selfie        leads                      13.33  0.36   83.3        0      —      0     0  [trust]
benefit-calm          leads                      13.33  0.48   87.5        3   4.44      1     0  [trust]
benefit-selfie        sales-broad                 6.67  1.20   80.0        0      —      0     0  [trust]
outcome-selfie        sales-broad                 6.67  0.12    0.0        0      —      0     0  [trust]
benefit-calm          sales-broad                 6.67  0.60   80.0        0      —      0     0  [trust]
benefit-selfie        sales-interest              6.67  1.26   66.7        0      —      0     0  [trust]
outcome-selfie        sales-interest              6.67  0.84   50.0        0      —      0     0  [trust]
benefit-calm          sales-interest              6.67  1.89   77.8        2   3.33      0     0  [trust]
benefit-selfie        sales-auto~broad           10.69  0.60   75.0        0      —      0     0  [NO-TRUST auto-window]
outcome-selfie        sales-auto~broad           10.69  0.90   75.0        0      —      0     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~broad           10.69  1.12   80.0        6   1.78      1     0  [NO-TRUST auto-window]
benefit-selfie        sales-auto~interest_biztools  2.65  1.06  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome-selfie        sales-auto~interest_biztools  2.65  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~interest_biztools  2.65  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 182 · sign-ups 26 · demos 7 · purchases 2
ITERATION 1 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 1, "spend": 200.01, "revenue": 58, "roas": 0.29}

━━ ITERATION 2 — policy v1 (calm default, replication mandated) · sim world-3 seed 302 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-calm: proof=benefit named_story=False demo=False | Calm benefit words (incumbent)
  benefit-energetic: proof=benefit named_story=False demo=False | REPLICATION ROW: identical benefit words, energetic delivery (style pair, second test)
  outcome-calm: proof=outcome named_story=False demo=False | Copy axis: outcome words, calm delivery
CAMPAIGN CELLS: clicks($40,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($20,sales,broad,fixed), sales-interest($20,sales,interest_biztools,fixed), sales-auto~broad($10.37,sales,broad,auto), sales-auto~interest_biztools($29.63,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-calm          clicks                     13.33  0.72  100.0        2   6.67      0     0  [trust]
benefit-energetic     clicks                     13.33  0.90   80.0        2   6.67      0     0  [trust]
outcome-calm          clicks                     13.33  1.44   58.3        1  13.33      0     0  [trust]
benefit-calm          pageviews                  13.33  0.90   73.3        1  13.33      0     1  [trust]
benefit-energetic     pageviews                  13.33  0.96   68.8        0      —      0     0  [trust]
outcome-calm          pageviews                  13.33  0.78   84.6        3   4.44      0     0  [trust]
benefit-calm          leads                      13.33  0.18  100.0        0      —      0     0  [trust]
benefit-energetic     leads                      13.33  0.66   81.8        0      —      0     0  [trust]
outcome-calm          leads                      13.33  1.62   74.1        4   3.33      2     0  [trust]
benefit-calm          sales-broad                 6.67  1.56   84.6        1   6.67      1     0  [trust]
benefit-energetic     sales-broad                 6.67  0.12    0.0        0      —      0     0  [trust]
outcome-calm          sales-broad                 6.67  0.96   87.5        1   6.67      0     0  [trust]
benefit-calm          sales-interest              6.67  0.21  100.0        0      —      0     0  [trust]
benefit-energetic     sales-interest              6.67  0.21  100.0        0      —      0     0  [trust]
outcome-calm          sales-interest              6.67  1.05   60.0        0      —      0     0  [trust]
benefit-calm          sales-auto~broad            3.46  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
benefit-energetic     sales-auto~broad            3.46  0.46  100.0        1   3.46      0     0  [NO-TRUST auto-window]
outcome-calm          sales-auto~broad            3.46  1.62   57.1        1   3.46      0     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~interest_biztools  9.88  0.43  100.0        1   9.88      0     0  [NO-TRUST auto-window]
benefit-energetic     sales-auto~interest_biztools  9.88  0.99   71.4        0      —      0     0  [NO-TRUST auto-window]
outcome-calm          sales-auto~interest_biztools  9.88  0.85   66.7        1   9.88      1     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 144 · sign-ups 19 · demos 4 · purchases 1
ITERATION 2 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 3 — policy v2 (style closed, demo probe in) · sim world-3 seed 303 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome: proof=outcome named_story=False demo=False | Lead: outcome words, single register
  benefit: proof=benefit named_story=False demo=False | Challenger: benefit words
  outcome-demo: proof=outcome named_story=False demo=True | Goal-layer probe: outcome words over 60s product screen-demo
CAMPAIGN CELLS: clicks($40,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($20,sales,broad,fixed), sales-interest($20,sales,interest_biztools,fixed), sales-auto~broad($4.32,sales,broad,auto), sales-auto~interest_biztools($35.68,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome               clicks                     13.33  0.96   81.2        1  13.33      0     0  [trust]
benefit               clicks                     13.33  1.80   86.7        3   4.44      1     0  [trust]
outcome-demo          clicks                     13.33  0.48   75.0        0      —      0     0  [trust]
outcome               pageviews                  13.33  0.96   81.2        0      —      0     0  [trust]
benefit               pageviews                  13.33  1.38   87.0        2   6.67      0     1  [trust]
outcome-demo          pageviews                  13.33  0.60   50.0        0      —      0     0  [trust]
outcome               leads                      13.33  0.36   83.3        2   6.67      0     0  [trust]
benefit               leads                      13.33  1.86   93.5        4   3.33      0     0  [trust]
outcome-demo          leads                      13.33  0.90   66.7        2   6.67      0     0  [trust]
outcome               sales-broad                 6.67  1.56   69.2        1   6.67      0     0  [trust]
benefit               sales-broad                 6.67  0.96   87.5        1   6.67      0     0  [trust]
outcome-demo          sales-broad                 6.67  0.84   85.7        0      —      0     0  [trust]
outcome               sales-interest              6.67  1.68   75.0        1   6.67      1     0  [trust]
benefit               sales-interest              6.67  0.00    0.0        0      —      0     0  [trust]
outcome-demo          sales-interest              6.67  1.05   80.0        0      —      0     0  [trust]
outcome               sales-auto~broad            1.44  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
benefit               sales-auto~broad            1.44  0.56  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome-demo          sales-auto~broad            1.44  1.67  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome               sales-auto~interest_biztools 11.89  0.82   85.7        0      —      0     0  [NO-TRUST auto-window]
benefit               sales-auto~interest_biztools 11.89  0.94  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome-demo          sales-auto~interest_biztools 11.89  0.82   85.7        1  11.89      1     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 183 · sign-ups 18 · demos 3 · purchases 1
ITERATION 3 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 4 — policy v3 (working-cell expansion) · sim world-3 seed 304 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit: proof=benefit named_story=False demo=False | Lead: benefit words (cumulative signup leader + 3-era buying cell)
  outcome: proof=outcome named_story=False demo=False | Challenger: outcome words
  benefit-demo: proof=benefit named_story=False demo=True | Demo on the working words: benefit script over 60s product screen-demo
CAMPAIGN CELLS: pageviews($60,pageviews,broad,fixed), pageviews-2($30,pageviews,interest_biztools,fixed), leads($30,leads,broad,fixed), sales-broad($20,sales,broad,fixed), sales-interest($20,sales,interest_biztools,fixed), sales-auto~broad($10.95,sales,broad,auto), sales-auto~interest_biztools($29.05,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit               pageviews                  20.00  1.56   84.6        2   10.0      0     0  [trust]
outcome               pageviews                  20.00  1.24   64.5        1   20.0      0     0  [trust]
benefit-demo          pageviews                  20.00  1.60   87.5        6   3.33      1     0  [trust]
benefit               pageviews-2                10.00  1.96   78.6        2    5.0      1     0  [trust]
outcome               pageviews-2                10.00  0.84   83.3        1   10.0      1     0  [trust]
benefit-demo          pageviews-2                10.00  0.84   50.0        1   10.0      0     0  [trust]
benefit               leads                      10.00  0.56   71.4        0      —      0     0  [trust]
outcome               leads                      10.00  0.56   71.4        0      —      0     0  [trust]
benefit-demo          leads                      10.00  0.48  100.0        0      —      0     0  [trust]
benefit               sales-broad                 6.67  1.56   69.2        0      —      0     0  [trust]
outcome               sales-broad                 6.67  0.72   83.3        0      —      0     0  [trust]
benefit-demo          sales-broad                 6.67  0.24  100.0        0      —      0     0  [trust]
benefit               sales-interest              6.67  0.63   33.3        0      —      0     0  [trust]
outcome               sales-interest              6.67  1.26   83.3        1   6.67      1     0  [trust]
benefit-demo          sales-interest              6.67  1.26   83.3        0      —      0     0  [trust]
benefit               sales-auto~broad            3.65  2.19   70.0        3   1.22      2     0  [NO-TRUST auto-window]
outcome               sales-auto~broad            3.65  0.66    0.0        0      —      0     0  [NO-TRUST auto-window]
benefit-demo          sales-auto~broad            3.65  0.88  100.0        0      —      0     0  [NO-TRUST auto-window]
benefit               sales-auto~interest_biztools  9.68  0.14    0.0        0      —      0     0  [NO-TRUST auto-window]
outcome               sales-auto~interest_biztools  9.68  1.16   50.0        0      —      0     0  [NO-TRUST auto-window]
benefit-demo          sales-auto~interest_biztools  9.68  0.43  100.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 168 · sign-ups 17 · demos 6 · purchases 0
ITERATION 4 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 (auto cut, sales deepened) · sim world-3 seed 305 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit: proof=benefit named_story=False demo=False | Lead: benefit words
  outcome: proof=outcome named_story=False demo=False | Challenger: outcome words
  benefit-demo: proof=benefit named_story=False demo=True | Demo row: benefit script over product screen-demo (held)
CAMPAIGN CELLS: pageviews($60,pageviews,broad,fixed), pageviews-2($30,pageviews,interest_biztools,fixed), leads($20,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-interest($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit               pageviews                  20.00  1.60   92.5        1   20.0      0     0  [trust]
outcome               pageviews                  20.00  1.64   75.6        1   20.0      0     0  [trust]
benefit-demo          pageviews                  20.00  1.60   92.5        1   20.0      1     0  [trust]
benefit               pageviews-2                10.00  0.56  100.0        1   10.0      0     0  [trust]
outcome               pageviews-2                10.00  1.12   75.0        0      —      0     0  [trust]
benefit-demo          pageviews-2                10.00  1.12   87.5        2    5.0      0     0  [trust]
benefit               leads                       6.67  0.84  100.0        0      —      0     0  [trust]
outcome               leads                       6.67  1.20   80.0        1   6.67      0     0  [trust]
benefit-demo          leads                       6.67  0.24  100.0        0      —      0     0  [trust]
benefit               sales-broad                16.67  0.86   83.3        2   8.34      0     0  [trust]
outcome               sales-broad                16.67  1.44   63.3        2   8.34      0     0  [trust]
benefit-demo          sales-broad                16.67  0.82   94.1        0      —      0     0  [trust]
benefit               sales-interest             13.33  0.95  100.0        1  13.33      0     0  [trust]
outcome               sales-interest             13.33  0.84   62.5        0      —      0     0  [trust]
benefit-demo          sales-interest             13.33  1.26   83.3        3   4.44      1     0  [trust]
SITE FUNNEL: visits 213 · sign-ups 15 · demos 2 · purchases 0
ITERATION 5 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 (pageviews-primary) · sim world-3 seed 306 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit: proof=benefit named_story=False demo=False | Lead: benefit words
  outcome: proof=outcome named_story=False demo=False | Challenger: outcome words
  benefit-demo: proof=benefit named_story=False demo=True | Demo row (held)
CAMPAIGN CELLS: pageviews($80,pageviews,broad,fixed), pageviews-2($40,pageviews,interest_biztools,fixed), leads($20,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit               pageviews                  26.67  0.84   89.3        3   8.89      0     0  [trust]
outcome               pageviews                  26.67  1.05   71.4        4   6.67      0     0  [trust]
benefit-demo          pageviews                  26.67  1.86   88.7       10   2.67      1     0  [trust]
benefit               pageviews-2                13.33  0.84   75.0        1  13.33      0     0  [trust]
outcome               pageviews-2                13.33  1.36   84.6        0      —      0     0  [trust]
benefit-demo          pageviews-2                13.33  1.47   71.4        2   6.67      0     0  [trust]
benefit               leads                       6.67  0.60   60.0        0      —      0     0  [trust]
outcome               leads                       6.67  0.96   75.0        0      —      0     0  [trust]
benefit-demo          leads                       6.67  0.96   75.0        2   3.33      0     0  [trust]
benefit               sales-broad                10.00  0.88   81.8        2    5.0      0     0  [trust]
outcome               sales-broad                10.00  0.48   83.3        0      —      0     0  [trust]
benefit-demo          sales-broad                10.00  0.80   70.0        0      —      0     0  [trust]
benefit               sales-interest             10.00  1.40   70.0        3   3.33      0     0  [trust]
outcome               sales-interest             10.00  1.12   87.5        1   10.0      0     0  [trust]
benefit-demo          sales-interest             10.00  0.14    0.0        0      —      0     0  [trust]
SITE FUNNEL: visits 182 · sign-ups 28 · demos 1 · purchases 0
ITERATION 6 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 (demo-variant lead) · sim world-3 seed 307 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo: proof=benefit named_story=False demo=True | Lead: benefit script over product screen-demo
  benefit: proof=benefit named_story=False demo=False | No-demo control (same words)
  benefit-demo-prooffirst: proof=benefit named_story=False demo=True | Production variant: same demo, re-cut to open ON the product working (first 3s)
CAMPAIGN CELLS: pageviews($80,pageviews,broad,fixed), pageviews-2($40,pageviews,interest_biztools,fixed), leads($20,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo          pageviews                  26.67  0.51   76.5        1  26.67      0     0  [trust]
benefit               pageviews                  26.67  1.02   82.4        3   8.89      0     0  [trust]
benefit-demo-prooffirstpageviews                  26.67  0.72   83.3        1  26.67      1     0  [trust]
benefit-demo          pageviews-2                13.33  0.73   42.9        1  13.33      0     0  [trust]
benefit               pageviews-2                13.33  1.57   86.7        1  13.33      0     0  [trust]
benefit-demo-prooffirstpageviews-2                13.33  0.84   75.0        1  13.33      0     0  [trust]
benefit-demo          leads                       6.67  1.08   88.9        0      —      0     0  [trust]
benefit               leads                       6.67  0.72   66.7        1   6.67      0     0  [trust]
benefit-demo-prooffirstleads                       6.67  1.20   90.0        0      —      0     0  [trust]
benefit-demo          sales-broad                10.00  1.28   75.0        2    5.0      0     1  [trust]
benefit               sales-broad                10.00  0.24   66.7        0      —      0     0  [trust]
benefit-demo-prooffirstsales-broad                10.00  2.08   69.2        2    5.0      0     0  [trust]
benefit-demo          sales-interest             10.00  1.26   77.8        0      —      0     0  [trust]
benefit               sales-interest             10.00  1.54   81.8        1   10.0      0     0  [trust]
benefit-demo-prooffirstsales-interest             10.00  0.70   80.0        0      —      0     0  [trust]
SITE FUNNEL: visits 156 · sign-ups 14 · demos 1 · purchases 1
ITERATION 7 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 8 — policy v7 (final: mild rebalance) · sim world-3 seed 308 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo: proof=benefit named_story=False demo=True | Lead: benefit script over product screen-demo
  benefit: proof=benefit named_story=False demo=False | Declared no-demo control
  outcome-demo: proof=outcome named_story=False demo=True | Copy axis under demo: outcome words, same demo footage
CAMPAIGN CELLS: pageviews($70,pageviews,broad,fixed), pageviews-2($40,pageviews,interest_biztools,fixed), leads($20,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo          pageviews                  23.33  1.61   83.0        5   4.67      2     0  [trust]
benefit               pageviews                  23.33  1.20   74.3        3   7.78      0     0  [trust]
outcome-demo          pageviews                  23.33  0.51   60.0        0      —      0     0  [trust]
benefit-demo          pageviews-2                13.33  0.84   62.5        1  13.33      1     0  [trust]
benefit               pageviews-2                13.33  1.05   90.0        1  13.33      0     0  [trust]
outcome-demo          pageviews-2                13.33  0.42   75.0        0      —      0     0  [trust]
benefit-demo          leads                       6.67  0.24  100.0        0      —      0     0  [trust]
benefit               leads                       6.67  0.72   83.3        0      —      0     0  [trust]
outcome-demo          leads                       6.67  0.24   50.0        0      —      0     0  [trust]
benefit-demo          sales-broad                13.33  0.78   92.3        0      —      0     0  [trust]
benefit               sales-broad                13.33  1.32   77.3        0      —      0     0  [trust]
outcome-demo          sales-broad                13.33  1.32   68.2        1  13.33      0     0  [trust]
benefit-demo          sales-interest             10.00  1.68   83.3        0      —      0     0  [trust]
benefit               sales-interest             10.00  0.70  100.0        1   10.0      0     0  [trust]
outcome-demo          sales-interest             10.00  0.42   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 160 · sign-ups 12 · demos 3 · purchases 0
ITERATION 8 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 0, "roas": 0.0}
