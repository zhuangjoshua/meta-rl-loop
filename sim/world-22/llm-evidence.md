
━━ ITERATION 1 — policy v0 · sim world-22 seed 2201 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad leading with the core benefit: Formflow turns messy intake forms into clean, auto
  it1-outcome: proof=outcome named_story=False demo=False | Video ad leading with a concrete outcome: teams cut form-processing time 70% after switchi
  it1-story: proof=story named_story=True demo=False | Video ad telling the named story of Dana Reyes, an ops lead who replaced three tools with 
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($30.22,sales,broad,auto), sales-auto~interest_biztools($9.78,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.66   63.6        1  13.33      0     0  [trust]
it1-outcome           pv-broad                   13.33  1.56   69.2        0      —      0     0  [trust]
it1-story             pv-broad                   13.33  0.66   45.5        1  13.33      0     0  [trust]
it1-benefit           leads-broad                13.33  0.36   83.3        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.66   54.5        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.90   80.0        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.54   66.7        0      —      0     0  [trust]
it1-story             sales-broad                13.33  0.60   50.0        0      —      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.52   80.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  1.05   50.0        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  0.52   40.0        0      —      0     0  [trust]
it1-benefit           sales-auto~broad           10.07  0.08    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad           10.07  1.03   61.5        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad           10.07  0.64   62.5        1  10.07      1     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  3.26  0.43    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools  3.26  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools  3.26  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 91 · sign-ups 4 · demos 1 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-22 seed 2202 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count: proof=count named_story=False demo=False | Video ad leading with adoption numbers: 12,400 teams now run their intake on Formflow. Cou
  it2-outcome: proof=outcome named_story=False demo=False | Video ad leading with a concrete outcome: teams cut form-processing time 70% after switchi
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad with the same outcome-first hook (teams cut form-processing time 70%) but the bod
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($20,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($6.36,sales,broad,auto), sales-auto~interest_biztools($33.64,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count             pv-broad                   10.00  0.64   87.5        0      —      0     0  [trust]
it2-outcome           pv-broad                   10.00  0.24  100.0        0      —      0     0  [trust]
it2-outcome-demo      pv-broad                   10.00  0.32   50.0        0      —      0     0  [trust]
it2-count             leads-broad                10.00  0.56   42.9        0      —      0     0  [trust]
it2-outcome           leads-broad                10.00  1.28   87.5        2    5.0      0     0  [trust]
it2-outcome-demo      leads-broad                10.00  0.48   33.3        0      —      0     0  [trust]
it2-count             sales-broad                13.33  0.60   40.0        1  13.33      1     0  [trust]
it2-outcome           sales-broad                13.33  0.78   61.5        1  13.33      0     0  [trust]
it2-outcome-demo      sales-broad                13.33  0.84   71.4        1  13.33      0     0  [trust]
it2-count             sales-biztools              6.67  0.42  100.0        0      —      0     0  [trust]
it2-outcome           sales-biztools              6.67  0.63   66.7        0      —      0     0  [trust]
it2-outcome-demo      sales-biztools              6.67  1.47   85.7        0      —      0     0  [trust]
it2-count             sales-niche                13.33  0.54   50.0        1  13.33      1     0  [trust]
it2-outcome           sales-niche                13.33  0.45  100.0        0      —      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  0.36   25.0        0      —      0     0  [trust]
it2-count             sales-auto~broad            2.12  0.38  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome           sales-auto~broad            2.12  0.38    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad            2.12  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools 11.21  1.25   70.0        1  11.21      0     0  [NO-TRUST auto-window]
it2-outcome           sales-auto~interest_biztools 11.21  0.25  100.0        1  11.21      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools 11.21  0.37   66.7        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 84 · sign-ups 8 · demos 2 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-22 seed 2203 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-count: proof=count named_story=False demo=False | Video ad leading with adoption numbers: 12,400 teams now run their intake on Formflow. Cou
  it3-outcome: proof=outcome named_story=False demo=False | Video ad leading with a concrete outcome: teams cut form-processing time 70% after switchi
  it3-story: proof=story named_story=True demo=False | Video ad telling the named story of Dana Reyes, an ops lead who replaced three tools with 
CAMPAIGN CELLS: clicks-broad($25,clicks,broad,fixed), pv-broad($25,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($45,sales,broad,fixed), sales-biztools($35,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-count             clicks-broad                8.33  1.44   60.0        0      —      0     0  [trust]
it3-outcome           clicks-broad                8.33  0.86   66.7        0      —      0     0  [trust]
it3-story             clicks-broad                8.33  1.06   81.8        1   8.33      1     0  [trust]
it3-count             pv-broad                    8.33  0.29   33.3        0      —      0     0  [trust]
it3-outcome           pv-broad                    8.33  0.38   75.0        0      —      0     0  [trust]
it3-story             pv-broad                    8.33  0.77   62.5        0      —      0     0  [trust]
it3-count             leads-broad                10.00  0.40   80.0        1   10.0      0     0  [trust]
it3-outcome           leads-broad                10.00  0.72   44.4        1   10.0      1     0  [trust]
it3-story             leads-broad                10.00  1.20   73.3        2    5.0      0     0  [trust]
it3-count             sales-broad                15.00  1.23   56.5        1   15.0      1     0  [trust]
it3-outcome           sales-broad                15.00  0.27   40.0        1   15.0      1     0  [trust]
it3-story             sales-broad                15.00  0.16  100.0        0      —      0     0  [trust]
it3-count             sales-biztools             11.67  0.48   75.0        0      —      0     0  [trust]
it3-outcome           sales-biztools             11.67  1.20   50.0        0      —      0     0  [trust]
it3-story             sales-biztools             11.67  0.24  100.0        0      —      0     0  [trust]
it3-count             sales-niche                13.33  0.54   50.0        0      —      0     0  [trust]
it3-outcome           sales-niche                13.33  0.36   75.0        0      —      0     0  [trust]
it3-story             sales-niche                13.33  0.09    0.0        0      —      0     0  [trust]
SITE FUNNEL: visits 86 · sign-ups 7 · demos 4 · purchases 0
ITERATION 3 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-22 seed 2204 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-count: proof=count named_story=False demo=False | Video ad leading with adoption numbers: 12,400 teams now run their intake on Formflow. Cou
  it4-outcome: proof=outcome named_story=False demo=False | Video ad leading with a concrete outcome: teams cut form-processing time 70% after switchi
  it4-story: proof=story named_story=True demo=False | Video ad telling the named story of Dana Reyes, an ops lead who replaced three tools with 
CAMPAIGN CELLS: sales-broad($67,sales,broad,fixed), sales-niche($67,sales,interest_niche,fixed), leads-broad($66,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-count             sales-broad                22.33  0.36   80.0        1  22.33      0     0  [trust]
it4-outcome           sales-broad                22.33  0.43   58.3        0      —      0     0  [trust]
it4-story             sales-broad                22.33  0.29   75.0        1  22.33      0     0  [trust]
it4-count             sales-niche                22.33  1.24   87.0        3   7.44      1     0  [trust]
it4-outcome           sales-niche                22.33  1.29   50.0        0      —      0     0  [trust]
it4-story             sales-niche                22.33  0.32   66.7        0      —      0     0  [trust]
it4-count             leads-broad                22.00  0.51   50.0        1   22.0      1     0  [trust]
it4-outcome           leads-broad                22.00  0.51   57.1        0      —      0     0  [trust]
it4-story             leads-broad                22.00  0.47   69.2        0      —      0     0  [trust]
SITE FUNNEL: visits 81 · sign-ups 6 · demos 2 · purchases 0
ITERATION 4 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-22 seed 2205 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-count-teams: proof=count named_story=False demo=False | Video ad leading with adoption numbers: 12,400 teams now run their intake on Formflow. Cou
  it5-count-forms: proof=count named_story=False demo=False | Video ad leading with a different count surface: over 3 million forms processed through Fo
  it5-benefit: proof=benefit named_story=False demo=False | Video ad leading with the core benefit: Formflow turns messy intake forms into clean, auto
CAMPAIGN CELLS: sales-broad($67,sales,broad,fixed), sales-niche($67,sales,interest_niche,fixed), leads-broad($66,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-count-teams       sales-broad                22.33  1.29   69.4        1  22.33      0     0  [trust]
it5-count-forms       sales-broad                22.33  0.25   42.9        0      —      0     0  [trust]
it5-benefit           sales-broad                22.33  0.68   78.9        0      —      0     0  [trust]
it5-count-teams       sales-niche                22.33  0.64   41.7        0      —      0     0  [trust]
it5-count-forms       sales-niche                22.33  1.13   52.4        1  22.33      1     0  [trust]
it5-benefit           sales-niche                22.33  0.21  100.0        0      —      0     0  [trust]
it5-count-teams       leads-broad                22.00  0.80   77.3        4    5.5      2     0  [trust]
it5-count-forms       leads-broad                22.00  0.22   83.3        0      —      0     0  [trust]
it5-benefit           leads-broad                22.00  0.36   90.0        2   11.0      0     0  [trust]
SITE FUNNEL: visits 94 · sign-ups 8 · demos 3 · purchases 0
ITERATION 5 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 · sim world-22 seed 2206 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-count-teams: proof=count named_story=False demo=False | Video ad leading with adoption numbers: 12,400 teams now run their intake on Formflow. Cou
  it6-count-reviews: proof=count named_story=False demo=False | Video ad leading with a distinct count surface: 4,800 five-star reviews from ops teams usi
  it6-outcome: proof=outcome named_story=False demo=False | Video ad leading with a concrete outcome: teams cut form-processing time 70% after switchi
CAMPAIGN CELLS: sales-broad($67,sales,broad,fixed), sales-niche($67,sales,interest_niche,fixed), leads-broad($66,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-count-teams       sales-broad                22.33  0.43   50.0        0      —      0     0  [trust]
it6-count-reviews     sales-broad                22.33  0.61   70.6        0      —      0     0  [trust]
it6-outcome           sales-broad                22.33  0.07  100.0        0      —      0     0  [trust]
it6-count-teams       sales-niche                22.33  0.70   69.2        0      —      0     0  [trust]
it6-count-reviews     sales-niche                22.33  1.40   69.2        2  11.16      2     0  [trust]
it6-outcome           sales-niche                22.33  0.75   71.4        0      —      0     0  [trust]
it6-count-teams       leads-broad                22.00  1.09   66.7        1   22.0      0     0  [trust]
it6-count-reviews     leads-broad                22.00  1.38   68.4        2   11.0      0     0  [trust]
it6-outcome           leads-broad                22.00  0.15   75.0        1   22.0      0     0  [trust]
SITE FUNNEL: visits 106 · sign-ups 6 · demos 2 · purchases 0
ITERATION 6 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-22 seed 2207 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-count-reviews: proof=count named_story=False demo=False | Video ad leading with a count surface: 4,800 five-star reviews from ops teams using Formfl
  it7-count-hours: proof=count named_story=False demo=False | Video ad leading with a fresh count surface: 1.2 million hours of manual form work elimina
  it7-story: proof=story named_story=True demo=False | Video ad telling the named story of Dana Reyes, an ops lead who replaced three tools with 
CAMPAIGN CELLS: sales-niche($90,sales,interest_niche,fixed), sales-broad($44,sales,broad,fixed), leads-broad($66,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-count-reviews     sales-niche                30.00  0.20   40.0        0      —      0     0  [trust]
it7-count-hours       sales-niche                30.00  1.00   68.0        0      —      0     0  [trust]
it7-story             sales-niche                30.00  1.20   63.3        4    7.5      2     0  [trust]
it7-count-reviews     sales-broad                14.67  0.65   33.3        0      —      0     0  [trust]
it7-count-hours       sales-broad                14.67  2.29   69.0        2   7.33      1     0  [trust]
it7-story             sales-broad                14.67  0.44   75.0        1  14.67      1     0  [trust]
it7-count-reviews     leads-broad                22.00  0.44   75.0        0      —      0     0  [trust]
it7-count-hours       leads-broad                22.00  0.40   81.8        0      —      0     0  [trust]
it7-story             leads-broad                22.00  0.58   62.5        1   22.0      0     0  [trust]
SITE FUNNEL: visits 105 · sign-ups 8 · demos 4 · purchases 0
ITERATION 7 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-22 seed 2208 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-count-hours: proof=count named_story=False demo=False | Video ad leading with a count surface: 1.2 million hours of manual form work eliminated by
  it8-count-joined: proof=count named_story=False demo=False | Video ad leading with a brand-new count surface: 214 ops teams joined Formflow last week a
  it8-story: proof=story named_story=True demo=False | Video ad telling the named story of Dana Reyes, an ops lead who replaced three tools with 
CAMPAIGN CELLS: sales-niche($90,sales,interest_niche,fixed), sales-broad($44,sales,broad,fixed), leads-broad($66,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-count-hours       sales-niche                30.00  1.24   41.9        1   30.0      1     0  [trust]
it8-count-joined      sales-niche                30.00  0.64   50.0        0      —      0     0  [trust]
it8-story             sales-niche                30.00  1.76   61.4        5    6.0      1     0  [trust]
it8-count-hours       sales-broad                14.67  1.04   73.7        2   7.33      0     0  [trust]
it8-count-joined      sales-broad                14.67  0.82   60.0        0      —      0     0  [trust]
it8-story             sales-broad                14.67  0.49   88.9        0      —      0     0  [trust]
it8-count-hours       leads-broad                22.00  0.25   57.1        1   22.0      0     0  [trust]
it8-count-joined      leads-broad                22.00  0.36   50.0        0      —      0     0  [trust]
it8-story             leads-broad                22.00  0.55   60.0        0      —      0     0  [trust]
SITE FUNNEL: visits 97 · sign-ups 9 · demos 2 · purchases 0
ITERATION 8 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 0, "roas": 0.0}
