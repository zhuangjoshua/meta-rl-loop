
━━ ITERATION 1 — policy v0 (seed; cold start now sweeps per coverage rule) · sim world-4 seed 401 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit: proof=benefit named_story=False demo=False | Taste lead: benefit words, single register
  outcome: proof=outcome named_story=False demo=False | Sweep: outcome words
  story: proof=story named_story=True demo=False | Sweep: named-customer story words (Rivera-style, concrete numbers)
CAMPAIGN CELLS: clicks($30,clicks,broad,fixed), pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($25,sales,broad,fixed), sales-interest($25,sales,interest_biztools,fixed), sales-auto~broad($5.96,sales,broad,auto), sales-auto~interest_biztools($34.04,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit               clicks                     10.00  0.64   62.5        1   10.0      0     0  [trust]
outcome               clicks                     10.00  0.32   75.0        0      —      0     0  [trust]
story                 clicks                     10.00  0.24   66.7        0      —      0     0  [trust]
benefit               pageviews                  13.33  0.90   93.3        0      —      0     0  [trust]
outcome               pageviews                  13.33  0.66   45.5        0      —      0     0  [trust]
story                 pageviews                  13.33  0.78   84.6        0      —      0     0  [trust]
benefit               leads                      13.33  0.96   81.2        0      —      0     0  [trust]
outcome               leads                      13.33  0.48   50.0        1  13.33      1     0  [trust]
story                 leads                      13.33  0.30  100.0        1  13.33      0     0  [trust]
benefit               sales-broad                 8.33  0.58   83.3        0      —      0     0  [trust]
outcome               sales-broad                 8.33  0.48   80.0        1   8.33      0     0  [trust]
story                 sales-broad                 8.33  0.58   50.0        2   4.17      1     0  [trust]
benefit               sales-interest              8.33  0.84   40.0        0      —      0     0  [trust]
outcome               sales-interest              8.33  2.02   66.7        0      —      0     0  [trust]
story                 sales-interest              8.33  0.34   50.0        0      —      0     0  [trust]
benefit               sales-auto~broad            1.99  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
outcome               sales-auto~broad            1.99  1.61   50.0        0      —      0     0  [NO-TRUST auto-window]
story                 sales-auto~broad            1.99  0.40  100.0        0      —      0     0  [NO-TRUST auto-window]
benefit               sales-auto~interest_biztools 11.35  0.86   71.4        0      —      0     0  [NO-TRUST auto-window]
outcome               sales-auto~interest_biztools 11.35  1.11   55.6        1  11.35      0     0  [NO-TRUST auto-window]
story                 sales-auto~interest_biztools 11.35  0.99   87.5        1  11.35      1     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 105 · sign-ups 8 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 (sweep completion) · sim world-4 seed 402 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  story: proof=story named_story=True demo=False | Comparator: named-story words (for the demo pair)
  story-demo: proof=story named_story=True demo=True | Sweep DEMO: same story words over 60s product screen-demo
  count: proof=count named_story=False demo=False | Sweep COUNT: adoption-count words (2,300 firms)
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($9.32,sales,broad,auto), sales-auto~interest_biztools($30.68,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
story                 pageviews                  13.33  0.90   80.0        2   6.67      1     0  [trust]
story-demo            pageviews                  13.33  0.60   80.0        1  13.33      1     0  [trust]
count                 pageviews                  13.33  0.78   84.6        2   6.67      1     0  [trust]
story                 leads                      13.33  0.84   71.4        0      —      0     0  [trust]
story-demo            leads                      13.33  0.12  100.0        0      —      0     0  [trust]
count                 leads                      13.33  1.14   78.9        0      —      0     0  [trust]
story                 sales-broad                13.33  0.72   83.3        1  13.33      0     0  [trust]
story-demo            sales-broad                13.33  0.54   77.8        2   6.67      0     0  [trust]
count                 sales-broad                13.33  0.72   75.0        1  13.33      0     0  [trust]
story                 sales-niche                13.33  0.45   60.0        0      —      0     0  [trust]
story-demo            sales-niche                13.33  0.90   80.0        0      —      0     0  [trust]
count                 sales-niche                13.33  2.34   65.4        3   4.44      2     0  [trust]
story                 sales-auto~broad            3.11  1.03   75.0        0      —      0     0  [NO-TRUST auto-window]
story-demo            sales-auto~broad            3.11  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
count                 sales-auto~broad            3.11  1.29   60.0        0      —      0     0  [NO-TRUST auto-window]
story                 sales-auto~interest_biztools 10.23  1.78   61.5        0      —      0     0  [NO-TRUST auto-window]
story-demo            sales-auto~interest_biztools 10.23  0.27  100.0        0      —      0     0  [NO-TRUST auto-window]
count                 sales-auto~interest_biztools 10.23  0.68   40.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 130 · sign-ups 12 · demos 5 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 (auto cut; count lean serviced) · sim world-4 seed 403 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count: proof=count named_story=False demo=False | Co-lead: adoption-count words (niche lean)
  story: proof=story named_story=True demo=False | Co-lead comparator: named-story words
  count-demo: proof=count named_story=False demo=True | Demo riding the promising words: count script over product screen-demo
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($30,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count                 pageviews                  13.33  0.78   69.2        0      —      0     0  [trust]
story                 pageviews                  13.33  0.72   75.0        0      —      0     0  [trust]
count-demo            pageviews                  13.33  1.20   60.0        2   6.67      1     0  [trust]
count                 leads                      10.00  0.88   63.6        0      —      0     0  [trust]
story                 leads                      10.00  0.88   36.4        1   10.0      1     0  [trust]
count-demo            leads                      10.00  0.72   44.4        0      —      0     0  [trust]
count                 sales-broad                13.33  1.02   47.1        0      —      0     0  [trust]
story                 sales-broad                13.33  1.86   67.7        1  13.33      0     0  [trust]
count-demo            sales-broad                13.33  0.60   60.0        1  13.33      0     0  [trust]
count                 sales-interest             10.00  0.56   50.0        0      —      0     0  [trust]
story                 sales-interest             10.00  1.54   81.8        1   10.0      0     1  [trust]
count-demo            sales-interest             10.00  1.12   50.0        2    5.0      0     0  [trust]
count                 sales-niche                20.00  2.52   50.0        1   20.0      1     0  [trust]
story                 sales-niche                20.00  0.30   80.0        1   20.0      0     1  [trust]
count-demo            sales-niche                20.00  0.96   81.2        0      —      0     0  [trust]
SITE FUNNEL: visits 133 · sign-ups 10 · demos 3 · purchases 2
ITERATION 3 TOTALS: spend $199.98 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 58, "roas": 0.29}

━━ ITERATION 4 — policy v3 (story lead) · sim world-4 seed 404 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  story: proof=story named_story=True demo=False | Lead: named-story words
  count: proof=count named_story=False demo=False | Challenger: count words
  story-demo: proof=story named_story=True demo=True | Demo on lead words: story script over product screen-demo
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($30,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
story                 pageviews                  13.33  0.90   53.3        1  13.33      1     0  [trust]
count                 pageviews                  13.33  1.32   72.7        0      —      0     0  [trust]
story-demo            pageviews                  13.33  0.54   77.8        0      —      0     0  [trust]
story                 leads                      10.00  0.64  100.0        0      —      0     0  [trust]
count                 leads                      10.00  0.56   71.4        1   10.0      1     0  [trust]
story-demo            leads                      10.00  0.80   80.0        1   10.0      0     0  [trust]
story                 sales-broad                13.33  0.78   53.8        0      —      0     0  [trust]
count                 sales-broad                13.33  0.72   58.3        1  13.33      1     0  [trust]
story-demo            sales-broad                13.33  0.48   37.5        0      —      0     0  [trust]
story                 sales-interest             10.00  0.56   50.0        0      —      0     0  [trust]
count                 sales-interest             10.00  1.26   55.6        0      —      0     0  [trust]
story-demo            sales-interest             10.00  0.56  100.0        1   10.0      1     0  [trust]
story                 sales-niche                20.00  0.48   62.5        0      —      0     0  [trust]
count                 sales-niche                20.00  1.44   75.0        1   20.0      1     0  [trust]
story-demo            sales-niche                20.00  0.72   83.3        1   20.0      0     0  [trust]
SITE FUNNEL: visits 113 · sign-ups 7 · demos 5 · purchases 0
ITERATION 4 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v3+tidy (confirmation batch) · sim world-4 seed 405 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  story: proof=story named_story=True demo=False | Lead: named-story words
  count: proof=count named_story=False demo=False | Challenger: count words
  story-demo: proof=story named_story=True demo=True | Demo on lead words: story script over product screen-demo
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($20,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
story                 pageviews                  13.33  1.38   60.9        1  13.33      0     0  [trust]
count                 pageviews                  13.33  0.54   55.6        1  13.33      0     0  [trust]
story-demo            pageviews                  13.33  0.90   73.3        0      —      0     0  [trust]
story                 leads                       6.67  1.68   71.4        0      —      0     0  [trust]
count                 leads                       6.67  0.84   85.7        1   6.67      0     0  [trust]
story-demo            leads                       6.67  0.72   83.3        0      —      0     0  [trust]
story                 sales-broad                16.67  1.01   81.0        1  16.67      0     0  [trust]
count                 sales-broad                16.67  1.97   65.9        3   5.56      1     0  [trust]
story-demo            sales-broad                16.67  0.34  100.0        1  16.67      0     0  [trust]
story                 sales-interest             10.00  0.70   80.0        0      —      0     0  [trust]
count                 sales-interest             10.00  1.26   66.7        1   10.0      1     1  [trust]
story-demo            sales-interest             10.00  1.12   87.5        0      —      0     0  [trust]
story                 sales-niche                20.00  1.20   80.0        3   6.67      1     0  [trust]
count                 sales-niche                20.00  0.18   33.3        0      —      0     0  [trust]
story-demo            sales-niche                20.00  0.72   66.7        1   20.0      0     0  [trust]
SITE FUNNEL: visits 144 · sign-ups 13 · demos 3 · purchases 1
ITERATION 5 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 6 — policy v4 (demo demoted; story robustness) · sim world-4 seed 406 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  story-rivera: proof=story named_story=True demo=False | Lead: Rivera consulting story (incumbent words)
  count: proof=count named_story=False demo=False | Challenger: count words
  story-okafor: proof=story named_story=True demo=False | Story variant B: Okafor agency story (different customer, same family)
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($20,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
story-rivera          pageviews                  13.33  0.48   87.5        1  13.33      1     0  [trust]
count                 pageviews                  13.33  0.96   50.0        1  13.33      0     0  [trust]
story-okafor          pageviews                  13.33  0.72   66.7        0      —      0     0  [trust]
story-rivera          leads                       6.67  1.32   54.5        0      —      0     0  [trust]
count                 leads                       6.67  0.12  100.0        0      —      0     0  [trust]
story-okafor          leads                       6.67  0.84   71.4        0      —      0     0  [trust]
story-rivera          sales-broad                16.67  1.63   55.9        1  16.67      0     0  [trust]
count                 sales-broad                16.67  1.15   75.0        3   5.56      1     0  [trust]
story-okafor          sales-broad                16.67  0.62   84.6        0      —      0     0  [trust]
story-rivera          sales-interest             10.00  0.84   66.7        0      —      0     0  [trust]
count                 sales-interest             10.00  1.82   84.6        1   10.0      1     0  [trust]
story-okafor          sales-interest             10.00  1.40   70.0        0      —      0     0  [trust]
story-rivera          sales-niche                20.00  0.66   63.6        1   20.0      0     0  [trust]
count                 sales-niche                20.00  1.38   65.2        1   20.0      0     0  [trust]
story-okafor          sales-niche                20.00  0.30   80.0        0      —      0     0  [trust]
SITE FUNNEL: visits 131 · sign-ups 9 · demos 3 · purchases 0
ITERATION 6 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v5 (co-leads; outcome re-check) · sim world-4 seed 407 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  story-rivera: proof=story named_story=True demo=False | Co-lead: Rivera story
  count: proof=count named_story=False demo=False | Co-lead: count words
  outcome: proof=outcome named_story=False demo=False | Re-price: outcome words (stale since it1)
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($20,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
story-rivera          pageviews                  13.33  0.96   62.5        0      —      0     0  [trust]
count                 pageviews                  13.33  1.02   64.7        3   4.44      2     0  [trust]
outcome               pageviews                  13.33  1.14   36.8        1  13.33      1     1  [trust]
story-rivera          leads                       6.67  0.84   71.4        0      —      0     0  [trust]
count                 leads                       6.67  0.36   66.7        0      —      0     0  [trust]
outcome               leads                       6.67  0.60   80.0        0      —      0     0  [trust]
story-rivera          sales-broad                16.67  0.48   70.0        1  16.67      1     0  [trust]
count                 sales-broad                16.67  0.82   64.7        1  16.67      0     0  [trust]
outcome               sales-broad                16.67  1.10   78.3        0      —      0     0  [trust]
story-rivera          sales-interest             10.00  0.42   66.7        0      —      0     0  [trust]
count                 sales-interest             10.00  0.70   60.0        0      —      0     0  [trust]
outcome               sales-interest             10.00  0.56   50.0        0      —      0     0  [trust]
story-rivera          sales-niche                20.00  1.92   65.6        1   20.0      1     0  [trust]
count                 sales-niche                20.00  0.90   40.0        1   20.0      1     0  [trust]
outcome               sales-niche                20.00  0.54   66.7        1   20.0      1     0  [trust]
SITE FUNNEL: visits 115 · sign-ups 9 · demos 7 · purchases 1
ITERATION 7 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 8 — policy v6 (final: rotation held) · sim world-4 seed 408 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  story-rivera: proof=story named_story=True demo=False | Co-lead: Rivera story
  count: proof=count named_story=False demo=False | Co-lead: count words
  outcome: proof=outcome named_story=False demo=False | Re-price: outcome words (stale since it1)
CAMPAIGN CELLS: pageviews($40,pageviews,broad,fixed), leads($20,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-interest($30,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
story-rivera          pageviews                  13.33  0.60   50.0        0      —      0     0  [trust]
count                 pageviews                  13.33  0.60   50.0        2   6.67      0     1  [trust]
outcome               pageviews                  13.33  1.08   77.8        0      —      0     0  [trust]
story-rivera          leads                       6.67  0.84   85.7        0      —      0     0  [trust]
count                 leads                       6.67  0.72   66.7        0      —      0     0  [trust]
outcome               leads                       6.67  0.84   42.9        0      —      0     0  [trust]
story-rivera          sales-broad                16.67  0.77   68.8        1  16.67      0     0  [trust]
count                 sales-broad                16.67  3.36   78.6        8   2.08      2     1  [trust]
outcome               sales-broad                16.67  1.20   68.0        0      —      0     0  [trust]
story-rivera          sales-interest             10.00  0.70   80.0        0      —      0     0  [trust]
count                 sales-interest             10.00  0.70   80.0        0      —      0     0  [trust]
outcome               sales-interest             10.00  1.54   81.8        2    5.0      1     0  [trust]
story-rivera          sales-niche                20.00  1.02   47.1        0      —      0     0  [trust]
count                 sales-niche                20.00  0.60   60.0        2   10.0      1     0  [trust]
outcome               sales-niche                20.00  0.36   33.3        0      —      0     0  [trust]
SITE FUNNEL: visits 153 · sign-ups 15 · demos 4 · purchases 2
ITERATION 8 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 58, "roas": 0.29}
