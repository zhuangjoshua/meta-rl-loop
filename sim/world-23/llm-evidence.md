
━━ ITERATION 1 — policy v0 · sim world-23 seed 2301 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit - stop wrestling with clunky fo
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form build time f
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya, ops lead at a 12-person agenc
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($10.67,sales,broad,auto), sales-auto~interest_biztools($29.33,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.72   58.3        1  13.33      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.48   37.5        1  13.33      0     0  [trust]
it1-story             pv-broad                   13.33  0.54   55.6        1  13.33      1     0  [trust]
it1-benefit           leads-broad                13.33  0.48   87.5        1  13.33      0     0  [trust]
it1-outcome           leads-broad                13.33  0.66   63.6        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.60   80.0        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.54   88.9        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.96   50.0        0      —      0     0  [trust]
it1-story             sales-broad                13.33  0.90   73.3        1  13.33      1     0  [trust]
it1-benefit           sales-biztools             13.33  1.05  100.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.52   40.0        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  0.84   50.0        1  13.33      1     0  [trust]
it1-benefit           sales-auto~broad            3.56  0.45  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            3.56  1.35   83.3        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            3.56  0.67  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  9.78  0.43   66.7        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools  9.78  0.72   60.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools  9.78  1.00   57.1        1   9.78      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 99 · sign-ups 8 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-23 seed 2302 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Dario, founder of a 6-person bookke
  it2-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with live demo - Priya, operations ma
  it2-count: proof=count named_story=False demo=False | Coverage-test slot. Video ad for Formflow ($29/mo): lead with adoption numbers - 12,000 te
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($4.11,sales,broad,auto), sales-auto~interest_biztools($35.89,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-story             pv-broad                   13.33  0.78   76.9        0      —      0     0  [trust]
it2-story-demo        pv-broad                   13.33  0.90   73.3        2   6.67      0     0  [trust]
it2-count             pv-broad                   13.33  0.30   60.0        0      —      0     0  [trust]
it2-story             leads-broad                13.33  1.08   61.1        1  13.33      0     0  [trust]
it2-story-demo        leads-broad                13.33  1.44   70.8        1  13.33      0     0  [trust]
it2-count             leads-broad                13.33  0.66   63.6        1  13.33      1     0  [trust]
it2-story             sales-broad                13.33  0.60   50.0        1  13.33      0     0  [trust]
it2-story-demo        sales-broad                13.33  1.08   50.0        1  13.33      0     0  [trust]
it2-count             sales-broad                13.33  0.66   54.5        1  13.33      1     0  [trust]
it2-story             sales-niche                13.33  0.99   27.3        1  13.33      0     0  [trust]
it2-story-demo        sales-niche                13.33  0.18   50.0        0      —      0     0  [trust]
it2-count             sales-niche                13.33  0.81   66.7        1  13.33      0     0  [trust]
it2-story             sales-auto~broad            1.37  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~broad            1.37  1.17   50.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~broad            1.37  1.75   66.7        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools 11.96  0.12  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~interest_biztools 11.96  0.23  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools 11.96  0.94   62.5        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 100 · sign-ups 10 · demos 2 · purchases 0
ITERATION 2 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-23 seed 2303 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story-lena: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Lena, HR lead at a 40-person logist
  it3-story-lena-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): the same named customer story with live demo - Lena, HR le
  it3-story-marc: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Marc, who runs a two-person photogr
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story-lena        pv-broad                   13.33  1.08   55.6        1  13.33      0     0  [trust]
it3-story-lena-demo   pv-broad                   13.33  1.38   56.5        1  13.33      1     0  [trust]
it3-story-marc        pv-broad                   13.33  0.90   73.3        1  13.33      1     0  [trust]
it3-story-lena        leads-broad                13.33  0.72   83.3        5   2.67      1     0  [trust]
it3-story-lena-demo   leads-broad                13.33  0.78   92.3        1  13.33      1     0  [trust]
it3-story-marc        leads-broad                13.33  1.08   50.0        0      —      0     0  [trust]
it3-story-lena        sales-broad                13.33  0.96   43.8        0      —      0     0  [trust]
it3-story-lena-demo   sales-broad                13.33  0.30   40.0        0      —      0     0  [trust]
it3-story-marc        sales-broad                13.33  0.84   50.0        1  13.33      0     0  [trust]
it3-story-lena        sales-biztools             13.33  1.89   66.7        1  13.33      1     0  [trust]
it3-story-lena-demo   sales-biztools             13.33  0.95   55.6        0      —      0     0  [trust]
it3-story-marc        sales-biztools             13.33  1.68   62.5        0      —      0     0  [trust]
it3-story-lena        sales-niche                13.33  0.36   25.0        0      —      0     0  [trust]
it3-story-lena-demo   sales-niche                13.33  0.09    0.0        0      —      0     0  [trust]
it3-story-marc        sales-niche                13.33  0.81   44.4        0      —      0     0  [trust]
SITE FUNNEL: visits 113 · sign-ups 11 · demos 5 · purchases 0
ITERATION 3 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-23 seed 2304 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-ana: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Ana, clinic manager at a physiother
  it4-story-tom: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Tom, events director at a nonprofit
  it4-story-rosa: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Rosa, co-founder of an online cours
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-ana         pv-broad                   13.33  0.36   50.0        0      —      0     0  [trust]
it4-story-tom         pv-broad                   13.33  0.72   66.7        1  13.33      1     0  [trust]
it4-story-rosa        pv-broad                   13.33  0.96   56.2        1  13.33      0     0  [trust]
it4-story-ana         leads-broad                13.33  1.98   69.7        0      —      0     0  [trust]
it4-story-tom         leads-broad                13.33  1.44   54.2        1  13.33      1     0  [trust]
it4-story-rosa        leads-broad                13.33  1.98   60.6        1  13.33      0     0  [trust]
it4-story-ana         sales-broad                13.33  0.48   62.5        2   6.67      1     0  [trust]
it4-story-tom         sales-broad                13.33  1.62   55.6        1  13.33      1     0  [trust]
it4-story-rosa        sales-broad                13.33  0.72   50.0        0      —      0     0  [trust]
it4-story-ana         sales-biztools             13.33  0.32   33.3        0      —      0     0  [trust]
it4-story-tom         sales-biztools             13.33  1.36   53.8        1  13.33      0     0  [trust]
it4-story-rosa        sales-biztools             13.33  0.84   50.0        0      —      0     0  [trust]
it4-story-ana         sales-niche                13.33  0.90   80.0        1  13.33      0     0  [trust]
it4-story-tom         sales-niche                13.33  0.81   22.2        0      —      0     0  [trust]
it4-story-rosa        sales-niche                13.33  0.99   36.4        0      —      0     0  [trust]
SITE FUNNEL: visits 128 · sign-ups 9 · demos 4 · purchases 0
ITERATION 4 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-23 seed 2305 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-june: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - June, office manager at a dental gr
  it5-story-omar: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Omar, who runs a 15-person landscap
  it5-story-kate: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Kate, program coordinator at a codi
CAMPAIGN CELLS: pv-broad($10,pageviews,broad,fixed), leads-broad($10,leads,broad,fixed), sales-broad($60,sales,broad,fixed), sales-biztools($60,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-june        pv-broad                    3.33  0.24    0.0        0      —      0     0  [trust]
it5-story-omar        pv-broad                    3.33  0.00    0.0        0      —      0     0  [trust]
it5-story-kate        pv-broad                    3.33  0.96   25.0        0      —      0     0  [trust]
it5-story-june        leads-broad                 3.33  1.20   60.0        0      —      0     0  [trust]
it5-story-omar        leads-broad                 3.33  0.96  100.0        0      —      0     0  [trust]
it5-story-kate        leads-broad                 3.33  1.44   66.7        0      —      0     0  [trust]
it5-story-june        sales-broad                20.00  0.88   50.0        4    5.0      0     0  [trust]
it5-story-omar        sales-broad                20.00  0.60   66.7        1   20.0      1     0  [trust]
it5-story-kate        sales-broad                20.00  0.92   65.2        1   20.0      1     0  [trust]
it5-story-june        sales-biztools             20.00  0.98   64.3        0      —      0     0  [trust]
it5-story-omar        sales-biztools             20.00  1.05   73.3        2   10.0      0     1  [trust]
it5-story-kate        sales-biztools             20.00  0.49   42.9        0      —      0     0  [trust]
it5-story-june        sales-niche                20.00  1.08   72.2        0      —      0     0  [trust]
it5-story-omar        sales-niche                20.00  1.02   82.4        0      —      0     0  [trust]
it5-story-kate        sales-niche                20.00  1.68   64.3        0      —      0     0  [trust]
SITE FUNNEL: visits 116 · sign-ups 8 · demos 2 · purchases 1
ITERATION 5 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 6 — policy v5 · sim world-23 seed 2306 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-story-priyanka: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Priyanka, operations lead at a 30-p
  it6-story-diego: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Diego, gym owner with two locations
  it6-story-helen: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Helen, property manager for 80 rent
CAMPAIGN CELLS: pv-broad($5,pageviews,broad,fixed), leads-broad($5,leads,broad,fixed), sales-broad($63,sales,broad,fixed), sales-biztools($64,sales,interest_biztools,fixed), sales-niche($63,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-story-priyanka    pv-broad                    1.67  1.44  100.0        0      —      0     0  [trust]
it6-story-diego       pv-broad                    1.67  0.48    0.0        0      —      0     0  [trust]
it6-story-helen       pv-broad                    1.67  0.00    0.0        0      —      0     0  [trust]
it6-story-priyanka    leads-broad                 1.67  1.44   66.7        1   1.67      1     0  [trust]
it6-story-diego       leads-broad                 1.67  0.96    0.0        0      —      0     0  [trust]
it6-story-helen       leads-broad                 1.67  1.92   50.0        0      —      0     0  [trust]
it6-story-priyanka    sales-broad                21.00  0.53   85.7        0      —      0     0  [trust]
it6-story-diego       sales-broad                21.00  1.10   48.3        0      —      0     0  [trust]
it6-story-helen       sales-broad                21.00  1.07   71.4        1   21.0      1     0  [trust]
it6-story-priyanka    sales-biztools             21.33  0.53   37.5        1  21.33      0     0  [trust]
it6-story-diego       sales-biztools             21.33  0.72   45.5        0      —      0     0  [trust]
it6-story-helen       sales-biztools             21.33  0.85   61.5        0      —      0     0  [trust]
it6-story-priyanka    sales-niche                21.00  0.40   57.1        0      —      0     0  [trust]
it6-story-diego       sales-niche                21.00  0.91   62.5        2   10.5      0     0  [trust]
it6-story-helen       sales-niche                21.00  1.09   73.7        2   10.5      0     0  [trust]
SITE FUNNEL: visits 97 · sign-ups 7 · demos 2 · purchases 0
ITERATION 6 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-23 seed 2307 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-story-buy-sam: proof=story named_story=True demo=False | Video ad for Formflow, purchase-intent framing: named customer story - Sam, managing partn
  it7-story-try-ines: proof=story named_story=True demo=False | Trial-intent control. Video ad for Formflow ($29/mo): named customer story - Ines, studio 
  it7-story-buy-ray: proof=story named_story=True demo=False | Video ad for Formflow, purchase-intent framing: named customer story - Ray, service manage
CAMPAIGN CELLS: pv-broad($5,pageviews,broad,fixed), leads-broad($5,leads,broad,fixed), sales-broad($63,sales,broad,fixed), sales-biztools($64,sales,interest_biztools,fixed), sales-niche($63,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-story-buy-sam     pv-broad                    1.67  0.48  100.0        0      —      0     0  [trust]
it7-story-try-ines    pv-broad                    1.67  0.48    0.0        0      —      0     0  [trust]
it7-story-buy-ray     pv-broad                    1.67  0.96   50.0        0      —      0     0  [trust]
it7-story-buy-sam     leads-broad                 1.67  0.48    0.0        0      —      0     0  [trust]
it7-story-try-ines    leads-broad                 1.67  0.96   50.0        0      —      0     0  [trust]
it7-story-buy-ray     leads-broad                 1.67  0.96    0.0        0      —      0     0  [trust]
it7-story-buy-sam     sales-broad                21.00  0.50   61.5        0      —      0     0  [trust]
it7-story-try-ines    sales-broad                21.00  0.50   84.6        1   21.0      0     0  [trust]
it7-story-buy-ray     sales-broad                21.00  0.88   56.5        1   21.0      0     0  [trust]
it7-story-buy-sam     sales-biztools             21.33  0.39   66.7        0      —      0     0  [trust]
it7-story-try-ines    sales-biztools             21.33  1.71   61.5        0      —      0     0  [trust]
it7-story-buy-ray     sales-biztools             21.33  0.59   44.4        0      —      0     0  [trust]
it7-story-buy-sam     sales-niche                21.00  1.09   42.1        1   21.0      0     0  [trust]
it7-story-try-ines    sales-niche                21.00  0.63   63.6        0      —      0     0  [trust]
it7-story-buy-ray     sales-niche                21.00  0.51   55.6        1   21.0      0     0  [trust]
SITE FUNNEL: visits 79 · sign-ups 4 · demos 0 · purchases 0
ITERATION 7 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-23 seed 2308 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-buy-nadia: proof=story named_story=True demo=False | Video ad for Formflow, purchase-intent framing: named customer story - Nadia, clinic direc
  it8-story-try-wes: proof=story named_story=True demo=False | Trial-intent control. Video ad for Formflow ($29/mo): named customer story - Wes, operatio
  it8-story-try-mira: proof=story named_story=True demo=False | Alternating slot, trial-intent this batch. Video ad for Formflow ($29/mo): named customer 
CAMPAIGN CELLS: pv-broad($5,pageviews,broad,fixed), leads-broad($5,leads,broad,fixed), sales-broad($63,sales,broad,fixed), sales-biztools($64,sales,interest_biztools,fixed), sales-niche($63,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-buy-nadia   pv-broad                    1.67  0.00    0.0        0      —      0     0  [trust]
it8-story-try-wes     pv-broad                    1.67  0.48  100.0        0      —      0     0  [trust]
it8-story-try-mira    pv-broad                    1.67  0.96   50.0        0      —      0     0  [trust]
it8-story-buy-nadia   leads-broad                 1.67  0.96  100.0        0      —      0     0  [trust]
it8-story-try-wes     leads-broad                 1.67  0.00    0.0        0      —      0     0  [trust]
it8-story-try-mira    leads-broad                 1.67  0.48    0.0        0      —      0     0  [trust]
it8-story-buy-nadia   sales-broad                21.00  0.95   64.0        1   21.0      0     0  [trust]
it8-story-try-wes     sales-broad                21.00  1.07   67.9        0      —      0     0  [trust]
it8-story-try-mira    sales-broad                21.00  1.56   58.5        1   21.0      0     0  [trust]
it8-story-buy-nadia   sales-biztools             21.33  1.05   50.0        0      —      0     0  [trust]
it8-story-try-wes     sales-biztools             21.33  0.53   37.5        1  21.33      1     0  [trust]
it8-story-try-mira    sales-biztools             21.33  0.59   77.8        0      —      0     0  [trust]
it8-story-buy-nadia   sales-niche                21.00  0.80   78.6        1   21.0      0     0  [trust]
it8-story-try-wes     sales-niche                21.00  0.80   78.6        3    7.0      2     0  [trust]
it8-story-try-mira    sales-niche                21.00  1.60   75.0        2   10.5      0     0  [trust]
SITE FUNNEL: visits 124 · sign-ups 9 · demos 3 · purchases 0
ITERATION 8 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 0, "roas": 0.0}
