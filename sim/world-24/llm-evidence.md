
━━ ITERATION 1 — policy v0 · sim world-24 seed 2401 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo form builder). Lead with the benefit: 'Stop wrestling with c
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo form builder). Lead with the outcome: 'Teams that switch to 
  it1-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo form builder). Lead with adoption numbers: 'Over 12,000 team
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biz($50,sales,interest_biztools,fixed), sales-auto~broad($6.84,sales,broad,auto), sales-auto~interest_biztools($33.16,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   10.00  0.80   80.0        1   10.0      0     0  [trust]
it1-outcome           pv-broad                   10.00  2.24   60.7        1   10.0      0     0  [trust]
it1-count             pv-broad                   10.00  0.48   66.7        1   10.0      1     0  [trust]
it1-benefit           leads-broad                10.00  0.08  100.0        0      —      0     0  [trust]
it1-outcome           leads-broad                10.00  1.28   50.0        3   3.33      0     0  [trust]
it1-count             leads-broad                10.00  1.60   60.0        0      —      0     0  [trust]
it1-benefit           sales-broad                16.67  0.19   75.0        1  16.67      0     0  [trust]
it1-outcome           sales-broad                16.67  0.62   53.8        0      —      0     0  [trust]
it1-count             sales-broad                16.67  1.01   61.9        4   4.17      1     0  [trust]
it1-benefit           sales-biz                  16.67  0.59   85.7        0      —      0     0  [trust]
it1-outcome           sales-biz                  16.67  0.50  100.0        1  16.67      1     0  [trust]
it1-count             sales-biz                  16.67  0.17   50.0        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            2.28  0.35  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            2.28  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-count             sales-auto~broad            2.28  0.70  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools 11.05  0.89   42.9        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools 11.05  0.76   83.3        0      —      0     0  [NO-TRUST auto-window]
it1-count             sales-auto~interest_biztools 11.05  0.51   75.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 100 · sign-ups 12 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-24 seed 2402 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo form builder). Lead with the outcome: 'Teams that switch to 
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo form builder). Same outcome lead — 'cut form build time from
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-niche($30,sales,interest_niche,fixed), sales-biz($30,sales,interest_biztools,fixed), sales-auto~broad($6.7,sales,broad,auto), sales-auto~interest_biztools($23.3,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-outcome           pv-broad                   10.00  0.80   80.0        0      —      0     0  [trust]
it2-outcome-demo      pv-broad                   10.00  0.32  100.0        0      —      0     0  [trust]
it2-story             pv-broad                   10.00  0.88   63.6        2    5.0      1     0  [trust]
it2-outcome           leads-broad                10.00  0.64   87.5        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                10.00  0.48   66.7        1   10.0      0     0  [trust]
it2-story             leads-broad                10.00  0.64   87.5        1   10.0      1     0  [trust]
it2-outcome           sales-broad                16.67  0.53   54.5        1  16.67      0     0  [trust]
it2-outcome-demo      sales-broad                16.67  0.53   45.5        1  16.67      0     0  [trust]
it2-story             sales-broad                16.67  0.77   68.8        2   8.34      1     0  [trust]
it2-outcome           sales-niche                10.00  0.60  100.0        0      —      0     0  [trust]
it2-outcome-demo      sales-niche                10.00  1.80   73.3        3   3.33      2     0  [trust]
it2-story             sales-niche                10.00  1.20   80.0        0      —      0     0  [trust]
it2-outcome           sales-biz                  10.00  1.40   60.0        0      —      0     0  [trust]
it2-outcome-demo      sales-biz                  10.00  0.42   66.7        0      —      0     0  [trust]
it2-story             sales-biz                  10.00  1.82   46.2        1   10.0      0     0  [trust]
it2-outcome           sales-auto~broad            2.23  1.43   50.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad            2.23  1.07  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~broad            2.23  1.07    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome           sales-auto~interest_biztools  7.77  1.44   37.5        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  7.77  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  7.77  1.62   33.3        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 108 · sign-ups 12 · demos 5 · purchases 0
ITERATION 2 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-24 seed 2403 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
  it3-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo form builder). Same named story — Maya Chen of Brightloom Co
  it3-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo form builder). Lead with adoption numbers: 'Over 12,000 team
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biz($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story             pv-broad                   10.00  1.04   69.2        2    5.0      1     0  [trust]
it3-story-demo        pv-broad                   10.00  0.48   66.7        0      —      0     0  [trust]
it3-count             pv-broad                   10.00  1.04   61.5        0      —      0     0  [trust]
it3-story             leads-broad                10.00  0.88   81.8        1   10.0      1     0  [trust]
it3-story-demo        leads-broad                10.00  0.48   66.7        0      —      0     0  [trust]
it3-count             leads-broad                10.00  0.88   63.6        0      —      0     0  [trust]
it3-story             sales-broad                16.67  0.48   90.0        4   4.17      2     1  [trust]
it3-story-demo        sales-broad                16.67  0.53   63.6        2   8.34      1     0  [trust]
it3-count             sales-broad                16.67  0.48   80.0        0      —      0     0  [trust]
it3-story             sales-biz                  15.00  0.65   57.1        1   15.0      0     0  [trust]
it3-story-demo        sales-biz                  15.00  1.31   50.0        0      —      0     0  [trust]
it3-count             sales-biz                  15.00  0.56  100.0        1   15.0      0     0  [trust]
it3-story             sales-niche                15.00  0.96   75.0        0      —      0     0  [trust]
it3-story-demo        sales-niche                15.00  0.96   66.7        0      —      0     0  [trust]
it3-count             sales-niche                15.00  0.48   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 103 · sign-ups 11 · demos 5 · purchases 1
ITERATION 3 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 4 — policy v3 · sim world-24 seed 2404 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-maya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
  it4-story-diego: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Diego Ramos, who runs 
  it4-story-priya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Priya Nair, events dir
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($80,sales,broad,fixed), sales-biz($30,sales,interest_biztools,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-maya        pv-broad                   10.00  0.80   70.0        1   10.0      0     0  [trust]
it4-story-diego       pv-broad                   10.00  0.88   63.6        0      —      0     0  [trust]
it4-story-priya       pv-broad                   10.00  0.40   60.0        0      —      0     0  [trust]
it4-story-maya        leads-broad                10.00  1.36   47.1        2    5.0      0     0  [trust]
it4-story-diego       leads-broad                10.00  0.80   70.0        0      —      0     0  [trust]
it4-story-priya       leads-broad                10.00  0.80   90.0        0      —      0     0  [trust]
it4-story-maya        sales-broad                26.67  1.05   57.1        5   5.33      1     0  [trust]
it4-story-diego       sales-broad                26.67  1.23   75.6        1  26.67      0     0  [trust]
it4-story-priya       sales-broad                26.67  1.38   65.2        5   5.33      2     2  [trust]
it4-story-maya        sales-biz                  10.00  0.56   75.0        1   10.0      0     0  [trust]
it4-story-diego       sales-biz                  10.00  0.28    0.0        0      —      0     0  [trust]
it4-story-priya       sales-biz                  10.00  0.98   42.9        0      —      0     0  [trust]
it4-story-maya        sales-niche                10.00  1.56   38.5        0      —      0     0  [trust]
it4-story-diego       sales-niche                10.00  0.48   50.0        1   10.0      1     0  [trust]
it4-story-priya       sales-niche                10.00  1.20   70.0        0      —      0     0  [trust]
SITE FUNNEL: visits 142 · sign-ups 16 · demos 4 · purchases 2
ITERATION 4 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 58, "roas": 0.29}

━━ ITERATION 5 — policy v4 · sim world-24 seed 2405 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-maya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
  it5-story-priya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Priya Nair, events dir
  it5-story-sam: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Sam Okafor, clinic man
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($110,sales,broad,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-maya        pv-broad                   10.00  0.96   58.3        1   10.0      0     0  [trust]
it5-story-priya       pv-broad                   10.00  0.72  100.0        2    5.0      0     0  [trust]
it5-story-sam         pv-broad                   10.00  0.72   77.8        0      —      0     0  [trust]
it5-story-maya        leads-broad                10.00  0.40   20.0        0      —      0     0  [trust]
it5-story-priya       leads-broad                10.00  1.20   73.3        2    5.0      1     0  [trust]
it5-story-sam         leads-broad                10.00  0.24   66.7        0      —      0     0  [trust]
it5-story-maya        sales-broad                36.67  1.13   71.2        8   4.58      6     0  [trust]
it5-story-priya       sales-broad                36.67  0.70   62.5        0      —      0     0  [trust]
it5-story-sam         sales-broad                36.67  0.83   71.1        1  36.67      0     1  [trust]
it5-story-maya        sales-niche                10.00  1.80   60.0        0      —      0     0  [trust]
it5-story-priya       sales-niche                10.00  0.48   75.0        1   10.0      1     0  [trust]
it5-story-sam         sales-niche                10.00  0.60   20.0        0      —      0     0  [trust]
SITE FUNNEL: visits 134 · sign-ups 15 · demos 8 · purchases 1
ITERATION 5 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 6 — policy v5 · sim world-24 seed 2406 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-story-maya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
  it6-story-priya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Priya Nair, events dir
  it6-story-sam: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Sam Okafor, clinic man
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($100,sales,broad,fixed), sales-biz($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-story-maya        pv-broad                   10.00  0.88   81.8        2    5.0      0     0  [trust]
it6-story-priya       pv-broad                   10.00  1.84   78.3        2    5.0      1     0  [trust]
it6-story-sam         pv-broad                   10.00  0.72   77.8        1   10.0      0     0  [trust]
it6-story-maya        leads-broad                10.00  1.44   77.8        2    5.0      1     0  [trust]
it6-story-priya       leads-broad                10.00  1.28   68.8        3   3.33      3     0  [trust]
it6-story-sam         leads-broad                10.00  0.88   63.6        0      —      0     0  [trust]
it6-story-maya        sales-broad                33.33  1.15   62.5        0      —      0     0  [trust]
it6-story-priya       sales-broad                33.33  0.53   72.7        3  11.11      1     0  [trust]
it6-story-sam         sales-broad                33.33  1.08   57.8        2  16.66      1     0  [trust]
it6-story-maya        sales-biz                  13.33  0.21   50.0        0      —      0     0  [trust]
it6-story-priya       sales-biz                  13.33  0.84   62.5        0      —      0     0  [trust]
it6-story-sam         sales-biz                  13.33  1.16   63.6        0      —      0     0  [trust]
SITE FUNNEL: visits 151 · sign-ups 15 · demos 7 · purchases 0
ITERATION 6 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-24 seed 2407 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-story-maya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
  it7-story-priya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Priya Nair, events dir
  it7-story-sam: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Sam Okafor, clinic man
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($100,sales,broad,fixed), sales-biz($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-story-maya        pv-broad                   10.00  0.24   66.7        0      —      0     0  [trust]
it7-story-priya       pv-broad                   10.00  0.32   75.0        0      —      0     0  [trust]
it7-story-sam         pv-broad                   10.00  0.80   60.0        0      —      0     0  [trust]
it7-story-maya        leads-broad                10.00  0.64   37.5        0      —      0     0  [trust]
it7-story-priya       leads-broad                10.00  0.32   75.0        0      —      0     0  [trust]
it7-story-sam         leads-broad                10.00  0.16  100.0        0      —      0     0  [trust]
it7-story-maya        sales-broad                33.33  0.89   64.9        0      —      0     0  [trust]
it7-story-priya       sales-broad                33.33  0.77   65.6        4   8.33      0     0  [trust]
it7-story-sam         sales-broad                33.33  1.15   77.1        4   8.33      2     0  [trust]
it7-story-maya        sales-biz                  13.33  0.73   71.4        1  13.33      1     0  [trust]
it7-story-priya       sales-biz                  13.33  0.52   80.0        0      —      0     0  [trust]
it7-story-sam         sales-biz                  13.33  0.73   71.4        0      —      0     0  [trust]
SITE FUNNEL: visits 115 · sign-ups 9 · demos 3 · purchases 0
ITERATION 7 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-24 seed 2408 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-maya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Maya Chen, ops lead at
  it8-story-priya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Priya Nair, events dir
  it8-story-sam: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo form builder). Named customer story: 'Sam Okafor, clinic man
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($100,sales,broad,fixed), sales-biz($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-maya        pv-broad                   10.00  0.80   80.0        0      —      0     0  [trust]
it8-story-priya       pv-broad                   10.00  0.32  100.0        0      —      0     0  [trust]
it8-story-sam         pv-broad                   10.00  0.56   42.9        0      —      0     0  [trust]
it8-story-maya        leads-broad                10.00  0.80   70.0        2    5.0      1     0  [trust]
it8-story-priya       leads-broad                10.00  0.64   62.5        0      —      0     0  [trust]
it8-story-sam         leads-broad                10.00  1.76   68.2        4    2.5      1     0  [trust]
it8-story-maya        sales-broad                33.33  1.22   70.6        3  11.11      0     0  [trust]
it8-story-priya       sales-broad                33.33  1.03   65.1        6   5.55      2     0  [trust]
it8-story-sam         sales-broad                33.33  1.25   57.7        1  33.33      1     1  [trust]
it8-story-maya        sales-biz                  13.33  1.36   61.5        1  13.33      1     0  [trust]
it8-story-priya       sales-biz                  13.33  0.95   88.9        3   4.44      1     0  [trust]
it8-story-sam         sales-biz                  13.33  0.21   50.0        1  13.33      0     0  [trust]
SITE FUNNEL: visits 153 · sign-ups 21 · demos 7 · purchases 1
ITERATION 8 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 29, "roas": 0.15}
