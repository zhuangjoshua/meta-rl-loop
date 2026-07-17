
━━ ITERATION 1 — policy v0 · sim world-9 seed 901 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): leads with the core benefit - stop rebuilding the same int
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): leads with a concrete outcome - teams cut form-building ti
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya, an ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), auto-sales~broad($33.63,sales,broad,auto), auto-sales~interest_biztools($6.37,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.96   75.0        0      —      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.24   75.0        1  13.33      0     0  [trust]
it1-story             pv-broad                   13.33  0.66   54.5        2   6.67      1     0  [trust]
it1-benefit           leads-broad                13.33  0.30   80.0        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.60   80.0        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.48   75.0        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.30   60.0        0      —      0     0  [trust]
it1-story             sales-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.10  100.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.84   62.5        1  13.33      0     0  [trust]
it1-story             sales-biztools             13.33  1.26   75.0        3   4.44      2     0  [trust]
it1-benefit           auto-sales~broad           11.21  0.64   88.9        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           auto-sales~broad           11.21  0.29   75.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             auto-sales~broad           11.21  0.43   16.7        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           auto-sales~interest_biztools  2.12  0.66    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           auto-sales~interest_biztools  2.12  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             auto-sales~interest_biztools  2.12  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 75 · sign-ups 8 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-9 seed 902 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
  it2-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): adoption-count angle - 4,200 teams build their intake form
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), clicks-broad($40,clicks,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-story             pv-broad                   13.33  1.02   64.7        3   4.44      1     0  [trust]
it2-story-demo        pv-broad                   13.33  0.36   33.3        0      —      0     0  [trust]
it2-count             pv-broad                   13.33  0.42   57.1        0      —      0     0  [trust]
it2-story             leads-broad                13.33  0.48   50.0        0      —      0     0  [trust]
it2-story-demo        leads-broad                13.33  0.60   70.0        1  13.33      1     0  [trust]
it2-count             leads-broad                13.33  1.02   70.6        3   4.44      1     0  [trust]
it2-story             sales-broad                13.33  0.78   69.2        1  13.33      0     0  [trust]
it2-story-demo        sales-broad                13.33  2.52   54.8        0      —      0     0  [trust]
it2-count             sales-broad                13.33  0.00    0.0        0      —      0     0  [trust]
it2-story             sales-niche                13.33  0.72   87.5        1  13.33      0     0  [trust]
it2-story-demo        sales-niche                13.33  0.99   72.7        1  13.33      1     0  [trust]
it2-count             sales-niche                13.33  0.45   80.0        0      —      0     0  [trust]
it2-story             clicks-broad               13.33  0.36   33.3        0      —      0     0  [trust]
it2-story-demo        clicks-broad               13.33  0.54   66.7        0      —      0     0  [trust]
it2-count             clicks-broad               13.33  0.60   80.0        0      —      0     0  [trust]
SITE FUNNEL: visits 107 · sign-ups 10 · demos 4 · purchases 0
ITERATION 2 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-9 seed 903 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story-maya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya, ops lead at a 12-person agenc
  it3-story-dan: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
  it3-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): adoption-count angle - 4,200 teams build their intake form
CAMPAIGN CELLS: sales-broad($80,sales,broad,fixed), sales-biztools($80,sales,interest_biztools,fixed), leads-broad($40,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story-maya        sales-broad                26.67  0.12   50.0        0      —      0     0  [trust]
it3-story-dan         sales-broad                26.67  0.87   58.6        2  13.34      0     0  [trust]
it3-count             sales-broad                26.67  0.27   88.9        0      —      0     0  [trust]
it3-story-maya        sales-biztools             26.67  0.68   69.2        0      —      0     0  [trust]
it3-story-dan         sales-biztools             26.67  0.73   57.1        2  13.34      0     0  [trust]
it3-count             sales-biztools             26.67  0.37   71.4        0      —      0     0  [trust]
it3-story-maya        leads-broad                13.33  0.66   72.7        1  13.33      0     0  [trust]
it3-story-dan         leads-broad                13.33  1.38   87.0        5   2.67      2     0  [trust]
it3-count             leads-broad                13.33  0.60   60.0        0      —      0     0  [trust]
SITE FUNNEL: visits 83 · sign-ups 10 · demos 2 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-9 seed 904 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-dan: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
  it4-story-lena: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Lena, paralegal at a 6-attorney law
  it4-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome angle grounded in a concrete workplace - a propert
CAMPAIGN CELLS: sales-broad($80,sales,broad,fixed), sales-biztools($80,sales,interest_biztools,fixed), leads-broad($40,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-dan         sales-broad                26.67  0.96   62.5        1  26.67      1     0  [trust]
it4-story-lena        sales-broad                26.67  0.63   61.9        0      —      0     0  [trust]
it4-outcome           sales-broad                26.67  0.51   70.6        1  26.67      1     0  [trust]
it4-story-dan         sales-biztools             26.67  0.68   69.2        0      —      0     0  [trust]
it4-story-lena        sales-biztools             26.67  0.73   64.3        0      —      0     0  [trust]
it4-outcome           sales-biztools             26.67  0.21  100.0        0      —      0     0  [trust]
it4-story-dan         leads-broad                13.33  0.78   61.5        0      —      0     0  [trust]
it4-story-lena        leads-broad                13.33  0.60   50.0        2   6.67      2     0  [trust]
it4-outcome           leads-broad                13.33  0.30   80.0        0      —      0     0  [trust]
SITE FUNNEL: visits 84 · sign-ups 4 · demos 4 · purchases 0
ITERATION 4 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-9 seed 905 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-dan: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
  it5-story-priya: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Priya, owner of a 2-studio fitness 
  it5-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome angle grounded in a concrete workplace - a propert
CAMPAIGN CELLS: sales-biztools($200,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-dan         sales-biztools             66.67  0.65   58.1        3  22.22      2     1  [trust]
it5-story-priya       sales-biztools             66.67  0.95   55.6        0      —      0     0  [trust]
it5-outcome           sales-biztools             66.67  0.46   68.2        1  66.67      0     1  [trust]
SITE FUNNEL: visits 58 · sign-ups 4 · demos 2 · purchases 2
ITERATION 5 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 58, "roas": 0.29}

━━ ITERATION 6 — policy v5 · sim world-9 seed 906 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-story-dan: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
  it6-story-rosa: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Rosa, front-desk lead at a physical
  it6-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome angle grounded in a concrete workplace - a propert
CAMPAIGN CELLS: sales-broad($200,sales,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-story-dan         sales-broad                66.67  0.52   67.4        2  33.34      0     0  [trust]
it6-story-rosa        sales-broad                66.67  0.42   68.6        1  66.67      0     1  [trust]
it6-outcome           sales-broad                66.67  0.74   71.0        6  11.11      3     0  [trust]
SITE FUNNEL: visits 97 · sign-ups 9 · demos 3 · purchases 1
ITERATION 6 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 7 — policy v6 · sim world-9 seed 907 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-story-rosa: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Rosa, front-desk lead at a physical
  it7-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome angle grounded in a concrete workplace - a propert
  it7-story-dan: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dan, office manager at a dental gro
CAMPAIGN CELLS: sales-niche($200,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-story-rosa        sales-niche                66.67  0.45   60.0        1  66.67      1     0  [trust]
it7-outcome           sales-niche                66.67  0.72   72.5        2  33.34      1     0  [trust]
it7-story-dan         sales-niche                66.67  0.77   51.2        0      —      0     0  [trust]
SITE FUNNEL: visits 66 · sign-ups 3 · demos 2 · purchases 0
ITERATION 7 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-9 seed 908 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome angle grounded in a concrete workplace - a propert
  it8-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): outcome angle grounded in a concrete workplace - a propert
  it8-story-rosa: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Rosa, front-desk lead at a physical
CAMPAIGN CELLS: sales-biztools($200,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-outcome           sales-biztools             66.67  1.05   58.0        3  22.22      0     0  [trust]
it8-outcome-demo      sales-biztools             66.67  0.27   69.2        2  33.34      1     1  [trust]
it8-story-rosa        sales-biztools             66.67  0.36   76.5        0      —      0     0  [trust]
SITE FUNNEL: visits 51 · sign-ups 5 · demos 1 · purchases 1
ITERATION 8 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 29, "roas": 0.14}
