
━━ ITERATION 1 — policy v0 (seed; cold start now sweeps per coverage rule) · sim world-5 seed 501 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit: proof=benefit named_story=False demo=False | Taste lead: benefit words, single register
  outcome: proof=outcome named_story=False demo=False | Sweep: outcome words
  story: proof=story named_story=True demo=False | Sweep: named-customer story words (Rivera-style, concrete numbers)
CAMPAIGN CELLS: clicks($30,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($25,sales,broad,fixed), sales-interest($25,sales,interest_biztools,fixed), sales-auto~broad($28.86,sales,broad,auto), sales-auto~interest_biztools($11.14,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit               clicks                     10.00  0.48   66.7        0      —      0     0  [trust]
outcome               clicks                     10.00  0.24   33.3        0      —      0     0  [trust]
story                 clicks                     10.00  0.56   71.4        1   10.0      0     0  [trust]
benefit               pageviews                  13.33  1.14   84.2        3   4.44      2     0  [trust]
outcome               pageviews                  13.33  0.78   76.9        1  13.33      0     0  [trust]
story                 pageviews                  13.33  1.68   71.4        0      —      0     0  [trust]
benefit               leads                      13.33  1.02   64.7        1  13.33      1     0  [trust]
outcome               leads                      13.33  0.54   77.8        0      —      0     0  [trust]
story                 leads                      13.33  1.02   76.5        1  13.33      0     0  [trust]
benefit               sales-broad                 8.33  0.38   50.0        0      —      0     0  [trust]
outcome               sales-broad                 8.33  1.15   50.0        1   8.33      1     0  [trust]
story                 sales-broad                 8.33  0.48   80.0        0      —      0     0  [trust]
benefit               sales-interest              8.33  0.00    0.0        0      —      0     0  [trust]
outcome               sales-interest              8.33  0.84   40.0        0      —      0     0  [trust]
story                 sales-interest              8.33  0.84   40.0        0      —      0     0  [trust]
benefit               sales-auto~broad            9.62  0.42   40.0        0      —      0     0  [NO-TRUST auto-window]
outcome               sales-auto~broad            9.62  0.42   60.0        0      —      0     0  [NO-TRUST auto-window]
story                 sales-auto~broad            9.62  1.25   33.3        0      —      0     0  [NO-TRUST auto-window]
benefit               sales-auto~interest_biztools  3.71  0.75  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome               sales-auto~interest_biztools  3.71  0.75  100.0        1   3.71      1     0  [NO-TRUST auto-window]
story                 sales-auto~interest_biztools  3.71  1.89   60.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 120 · sign-ups 9 · demos 5 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 (sweep completion) · sim world-5 seed 502 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit: proof=benefit named_story=False demo=False | Comparator: benefit words (mild it1 lean)
  benefit-demo: proof=benefit named_story=False demo=True | Sweep DEMO: same benefit words over 60s product screen-demo
  count: proof=count named_story=False demo=False | Sweep COUNT: adoption-count words
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($35.45,sales,broad,auto), sales-auto~interest_biztools($4.55,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit               pageviews                  13.33  0.48   87.5        0      —      0     0  [trust]
benefit-demo          pageviews                  13.33  1.02   88.2        0      —      0     0  [trust]
count                 pageviews                  13.33  0.90   93.3        2   6.67      1     0  [trust]
benefit               leads                      13.33  0.72   91.7        0      —      0     0  [trust]
benefit-demo          leads                      13.33  0.78   92.3        2   6.67      0     0  [trust]
count                 leads                      13.33  1.20   45.0        0      —      0     0  [trust]
benefit               sales-broad                13.33  1.14   89.5        2   6.67      1     0  [trust]
benefit-demo          sales-broad                13.33  0.72   50.0        0      —      0     0  [trust]
count                 sales-broad                13.33  1.44   66.7        3   4.44      3     0  [trust]
benefit               sales-niche                13.33  0.45  100.0        0      —      0     0  [trust]
benefit-demo          sales-niche                13.33  0.36   75.0        0      —      0     0  [trust]
count                 sales-niche                13.33  1.08   83.3        2   6.67      2     0  [trust]
benefit               sales-auto~broad           11.82  1.02   93.3        0      —      0     0  [NO-TRUST auto-window]
benefit-demo          sales-auto~broad           11.82  0.54   62.5        0      —      0     0  [NO-TRUST auto-window]
count                 sales-auto~broad           11.82  0.81   66.7        0      —      0     0  [NO-TRUST auto-window]
benefit               sales-auto~interest_biztools  1.52  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
benefit-demo          sales-auto~interest_biztools  1.52  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
count                 sales-auto~interest_biztools  1.52  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 152 · sign-ups 11 · demos 7 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 (auto cut; count co-lead) · sim world-5 seed 503 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Co-lead: count words (proxy leader)
  benefit: proof=benefit named_story=False demo=False | Co-lead: benefit words
  count-demo: proof=count named_story=False demo=True | Demo row on count words (held axis)
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($30,leads,broad,fixed), sales-broad($60,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  13.33  0.84   64.3        1  13.33      0     0  [trust]
benefit               pageviews                  13.33  0.60   80.0        1  13.33      0     0  [trust]
count-demo            pageviews                  13.33  0.48   62.5        0      —      0     0  [trust]
count                 leads                      10.00  0.72   77.8        0      —      0     0  [trust]
benefit               leads                      10.00  0.72   88.9        1   10.0      0     0  [trust]
count-demo            leads                      10.00  0.72   33.3        0      —      0     0  [trust]
count                 sales-broad                20.00  1.04   73.1        1   20.0      0     0  [trust]
benefit               sales-broad                20.00  0.80   75.0        1   20.0      0     0  [trust]
count-demo            sales-broad                20.00  0.84   71.4        2   10.0      0     0  [trust]
count                 sales-niche                13.33  1.35   73.3        2   6.67      1     1  [trust]
benefit               sales-niche                13.33  1.35   73.3        1  13.33      0     0  [trust]
count-demo            sales-niche                13.33  0.81   88.9        1  13.33      1     0  [trust]
count                 sales-interest             10.00  0.56  100.0        0      —      0     0  [trust]
benefit               sales-interest             10.00  0.84  100.0        0      —      0     0  [trust]
count-demo            sales-interest             10.00  0.98   71.4        0      —      0     0  [trust]
SITE FUNNEL: visits 134 · sign-ups 11 · demos 2 · purchases 1
ITERATION 3 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 4 — policy v3 (count lead; niche serviced) · sim world-5 seed 504 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Lead: count words
  benefit: proof=benefit named_story=False demo=False | Challenger: benefit words
  count-demo: proof=count named_story=False demo=True | Demo row (held)
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($20,leads,broad,fixed), sales-broad($60,sales,broad,fixed), sales-niche($50,sales,interest_niche,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  13.33  0.84   78.6        0      —      0     0  [trust]
benefit               pageviews                  13.33  0.72   58.3        1  13.33      0     0  [trust]
count-demo            pageviews                  13.33  1.26   66.7        3   4.44      3     0  [trust]
count                 leads                       6.67  0.84   57.1        1   6.67      0     0  [trust]
benefit               leads                       6.67  1.08   66.7        1   6.67      0     0  [trust]
count-demo            leads                       6.67  1.20   70.0        0      —      0     0  [trust]
count                 sales-broad                20.00  0.72   83.3        1   20.0      0     0  [trust]
benefit               sales-broad                20.00  0.92   73.9        3   6.67      0     0  [trust]
count-demo            sales-broad                20.00  0.52   61.5        1   20.0      0     0  [trust]
count                 sales-niche                16.67  0.50   71.4        1  16.67      1     0  [trust]
benefit               sales-niche                16.67  0.36   60.0        1  16.67      0     0  [trust]
count-demo            sales-niche                16.67  1.51   71.4        0      —      0     0  [trust]
count                 sales-interest             10.00  0.28   50.0        0      —      0     0  [trust]
benefit               sales-interest             10.00  0.28  100.0        0      —      0     0  [trust]
count-demo            sales-interest             10.00  2.80   50.0        2    5.0      0     0  [trust]
SITE FUNNEL: visits 125 · sign-ups 15 · demos 4 · purchases 0
ITERATION 4 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 (demo rotated onto benefit) · sim world-5 seed 505 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Lead: count words
  benefit: proof=benefit named_story=False demo=False | Challenger: benefit words
  benefit-demo: proof=benefit named_story=False demo=True | Demo row rotated: benefit words over product screen-demo
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($20,leads,broad,fixed), sales-broad($60,sales,broad,fixed), sales-niche($50,sales,interest_niche,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  13.33  0.36   83.3        1  13.33      0     0  [trust]
benefit               pageviews                  13.33  0.30   80.0        0      —      0     0  [trust]
benefit-demo          pageviews                  13.33  1.08   83.3        1  13.33      0     0  [trust]
count                 leads                       6.67  0.96   62.5        1   6.67      0     0  [trust]
benefit               leads                       6.67  0.60  100.0        0      —      0     0  [trust]
benefit-demo          leads                       6.67  0.60   80.0        0      —      0     0  [trust]
count                 sales-broad                20.00  1.60   65.0        2   10.0      1     0  [trust]
benefit               sales-broad                20.00  0.44   63.6        0      —      0     0  [trust]
benefit-demo          sales-broad                20.00  0.40   90.0        1   20.0      0     0  [trust]
count                 sales-niche                16.67  0.79   81.8        0      —      0     0  [trust]
benefit               sales-niche                16.67  0.79   72.7        1  16.67      0     0  [trust]
benefit-demo          sales-niche                16.67  0.86   75.0        0      —      0     0  [trust]
count                 sales-interest             10.00  0.14    0.0        0      —      0     0  [trust]
benefit               sales-interest             10.00  0.42   33.3        1   10.0      0     0  [trust]
benefit-demo          sales-interest             10.00  0.56   50.0        0      —      0     0  [trust]
SITE FUNNEL: visits 109 · sign-ups 8 · demos 1 · purchases 0
ITERATION 5 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 (lead-economics mode; sales listeners) · sim world-5 seed 506 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Lead: count words (cumulative proxy leader)
  count-demo: proof=count named_story=False demo=True | Demo row (accruing toward floor)
  benefit: proof=benefit named_story=False demo=False | Challenger: benefit words
CAMPAIGN CELLS: pageviews($50,pageviews,broad,fixed), leads($50,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($30,sales,interest_niche,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  16.67  1.20   72.0        3   5.56      1     0  [trust]
count-demo            pageviews                  16.67  0.38   87.5        2   8.34      1     0  [trust]
benefit               pageviews                  16.67  0.34   85.7        2   8.34      0     0  [trust]
count                 leads                      16.67  0.53   81.8        1  16.67      0     0  [trust]
count-demo            leads                      16.67  0.53   63.6        0      —      0     0  [trust]
benefit               leads                      16.67  0.34   85.7        0      —      0     0  [trust]
count                 sales-broad                13.33  0.42   71.4        0      —      0     0  [trust]
count-demo            sales-broad                13.33  0.60   90.0        0      —      0     0  [trust]
benefit               sales-broad                13.33  0.48   87.5        0      —      0     0  [trust]
count                 sales-niche                10.00  0.84   57.1        0      —      0     0  [trust]
count-demo            sales-niche                10.00  0.84   42.9        0      —      0     0  [trust]
benefit               sales-niche                10.00  0.72   50.0        0      —      0     0  [trust]
count                 sales-interest             10.00  0.56   75.0        0      —      0     0  [trust]
count-demo            sales-interest             10.00  0.14    0.0        0      —      0     0  [trust]
benefit               sales-interest             10.00  0.98   85.7        0      —      0     0  [trust]
SITE FUNNEL: visits 93 · sign-ups 8 · demos 2 · purchases 0
ITERATION 6 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 (count lead formalized) · sim world-5 seed 507 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Lead: count words (cumulative proxy leader)
  count-demo: proof=count named_story=False demo=True | Demo row (accruing toward floor)
  benefit: proof=benefit named_story=False demo=False | Challenger: benefit words
CAMPAIGN CELLS: pageviews($50,pageviews,broad,fixed), leads($50,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($30,sales,interest_niche,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  16.67  0.19   50.0        0      —      0     0  [trust]
count-demo            pageviews                  16.67  0.62   76.9        1  16.67      1     0  [trust]
benefit               pageviews                  16.67  1.39   89.7        2   8.34      0     0  [trust]
count                 leads                      16.67  1.01   66.7        6   2.78      2     0  [trust]
count-demo            leads                      16.67  2.64   67.3        5   3.33      1     0  [trust]
benefit               leads                      16.67  0.53   72.7        1  16.67      1     0  [trust]
count                 sales-broad                13.33  0.54   66.7        0      —      0     0  [trust]
count-demo            sales-broad                13.33  0.60   90.0        2   6.67      1     1  [trust]
benefit               sales-broad                13.33  0.48   75.0        1  13.33      0     0  [trust]
count                 sales-niche                10.00  0.36   33.3        0      —      0     0  [trust]
count-demo            sales-niche                10.00  0.24   50.0        0      —      0     0  [trust]
benefit               sales-niche                10.00  0.24   50.0        0      —      0     0  [trust]
count                 sales-interest             10.00  0.84   66.7        0      —      0     0  [trust]
count-demo            sales-interest             10.00  1.82   84.6        1   10.0      0     0  [trust]
benefit               sales-interest             10.00  0.14    0.0        0      —      0     0  [trust]
SITE FUNNEL: visits 136 · sign-ups 19 · demos 6 · purchases 1
ITERATION 7 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 8 — policy v7 (final) · sim world-5 seed 508 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Lead: count words (cumulative proxy leader)
  count-demo: proof=count named_story=False demo=True | Demo row (accruing toward floor)
  benefit: proof=benefit named_story=False demo=False | Challenger: benefit words
CAMPAIGN CELLS: pageviews($50,pageviews,broad,fixed), leads($50,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($30,sales,interest_niche,fixed), sales-interest($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  16.67  0.53   54.5        0      —      0     0  [trust]
count-demo            pageviews                  16.67  0.82   76.5        0      —      0     0  [trust]
benefit               pageviews                  16.67  0.77   75.0        0      —      0     0  [trust]
count                 leads                      16.67  1.01   76.2        2   8.34      0     0  [trust]
count-demo            leads                      16.67  0.77   37.5        0      —      0     0  [trust]
benefit               leads                      16.67  0.19   75.0        0      —      0     0  [trust]
count                 sales-broad                13.33  0.72   66.7        0      —      0     0  [trust]
count-demo            sales-broad                13.33  0.90   93.3        1  13.33      0     0  [trust]
benefit               sales-broad                13.33  0.30  100.0        1  13.33      1     0  [trust]
count                 sales-niche                10.00  0.96   87.5        0      —      0     0  [trust]
count-demo            sales-niche                10.00  0.60  100.0        0      —      0     0  [trust]
benefit               sales-niche                10.00  0.24   50.0        0      —      0     0  [trust]
count                 sales-interest             10.00  0.84  100.0        0      —      0     0  [trust]
count-demo            sales-interest             10.00  0.84   50.0        1   10.0      0     0  [trust]
benefit               sales-interest             10.00  0.28  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 107 · sign-ups 5 · demos 1 · purchases 0
ITERATION 8 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 0, "roas": 0.0}
