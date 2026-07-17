
━━ ITERATION 1 — policy v0 · sim world-15 seed 1501 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit — build intake forms in minutes
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the outcome — teams cut form-handling time and c
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Maya R., ops lead' tells how her t
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($34.43,sales,broad,auto), sales-auto~interest_biztools($5.57,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  1.14   84.2        2   6.67      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.60   80.0        0      —      0     0  [trust]
it1-story             pv-broad                   13.33  0.66   54.5        0      —      0     0  [trust]
it1-benefit           leads-broad                13.33  0.48   87.5        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.54   55.6        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.96   81.2        1  13.33      0     0  [trust]
it1-benefit           sales-broad                13.33  0.54   66.7        1  13.33      0     0  [trust]
it1-outcome           sales-broad                13.33  0.42   57.1        0      —      0     0  [trust]
it1-story             sales-broad                13.33  1.92   59.4        1  13.33      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.84   87.5        1  13.33      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.32   33.3        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  1.47   71.4        1  13.33      0     0  [trust]
it1-benefit           sales-auto~broad           11.48  0.63   88.9        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad           11.48  0.49   57.1        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad           11.48  1.05   60.0        2   5.74      1     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  1.86  0.75  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools  1.86  0.75    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools  1.86  3.02   50.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 126 · sign-ups 10 · demos 1 · purchases 0
ITERATION 1 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-15 seed 1502 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): lead with adoption numbers — '4,200 teams route their inta
  it2-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead — build intake forms in minutes — over a live
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Dev P., support manager' explains 
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($35,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($7.01,sales,broad,auto), sales-auto~interest_biztools($27.99,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count             pv-broad                    8.33  0.77   62.5        0      —      0     0  [trust]
it2-benefit-demo      pv-broad                    8.33  0.67   71.4        1   8.33      0     0  [trust]
it2-story             pv-broad                    8.33  1.34   85.7        3   2.78      1     0  [trust]
it2-count             leads-broad                 8.33  1.06   63.6        1   8.33      1     0  [trust]
it2-benefit-demo      leads-broad                 8.33  0.86  100.0        2   4.17      0     1  [trust]
it2-story             leads-broad                 8.33  0.58   83.3        0      —      0     0  [trust]
it2-count             sales-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it2-benefit-demo      sales-broad                13.33  0.72   91.7        1  13.33      0     0  [trust]
it2-story             sales-broad                13.33  1.08   44.4        2   6.67      0     1  [trust]
it2-count             sales-biztools             11.67  1.20   80.0        0      —      0     0  [trust]
it2-benefit-demo      sales-biztools             11.67  2.04   88.2        1  11.67      0     0  [trust]
it2-story             sales-biztools             11.67  1.08   55.6        0      —      0     0  [trust]
it2-count             sales-niche                13.33  1.08   91.7        0      —      0     0  [trust]
it2-benefit-demo      sales-niche                13.33  1.08   83.3        4   3.33      2     1  [trust]
it2-story             sales-niche                13.33  1.17   69.2        2   6.67      1     0  [trust]
it2-count             sales-auto~broad            2.34  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-benefit-demo      sales-auto~broad            2.34  0.68  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~broad            2.34  1.37  100.0        1   2.34      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  9.33  1.35   88.9        0      —      0     0  [NO-TRUST auto-window]
it2-benefit-demo      sales-auto~interest_biztools  9.33  0.60   75.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  9.33  0.75   60.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 143 · sign-ups 18 · demos 5 · purchases 3
ITERATION 2 TOTALS: spend $199.98 · settled revenue $87 · ROAS 0.44
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 87, "roas": 0.44}

━━ ITERATION 3 — policy v2 · sim world-15 seed 1503 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit lead — build intake forms in minutes, not hours. C
  it3-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): identical benefit lead — build intake forms in minutes, no
  it3-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Lena K., agency founder' tells how
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($35,leads,broad,fixed), sales-broad($45,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-benefit           pv-broad                    8.33  0.19  100.0        0      —      0     0  [trust]
it3-benefit-demo      pv-broad                    8.33  1.15   91.7        4   2.08      2     0  [trust]
it3-story             pv-broad                    8.33  1.44   66.7        1   8.33      0     0  [trust]
it3-benefit           leads-broad                11.67  0.89   61.5        1  11.67      0     0  [trust]
it3-benefit-demo      leads-broad                11.67  0.00    0.0        0      —      0     0  [trust]
it3-story             leads-broad                11.67  0.96   64.3        1  11.67      1     0  [trust]
it3-benefit           sales-broad                15.00  0.64   75.0        2    7.5      2     0  [trust]
it3-benefit-demo      sales-broad                15.00  0.43  100.0        1   15.0      0     0  [trust]
it3-story             sales-broad                15.00  1.17   63.6        1   15.0      1     0  [trust]
it3-benefit           sales-biztools             13.33  0.52   80.0        0      —      0     0  [trust]
it3-benefit-demo      sales-biztools             13.33  1.05   70.0        2   6.67      0     0  [trust]
it3-story             sales-biztools             13.33  1.78   64.7        1  13.33      0     0  [trust]
it3-benefit           sales-niche                18.33  0.46   71.4        1  18.33      0     0  [trust]
it3-benefit-demo      sales-niche                18.33  0.52   75.0        1  18.33      0     0  [trust]
it3-story             sales-niche                18.33  1.18   72.2        2   9.16      1     0  [trust]
SITE FUNNEL: visits 117 · sign-ups 18 · demos 7 · purchases 0
ITERATION 3 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-15 seed 1504 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Omar T., clinic office manager' on
  it4-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead — build intake forms in minutes, not hours — 
  it4-outcome-challenger: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): refreshed outcome execution — 'requests answered same-day'
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($35,leads,broad,fixed), sales-broad($45,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story             pv-broad                    8.33  0.58   33.3        0      —      0     0  [trust]
it4-benefit-demo      pv-broad                    8.33  0.96  100.0        1   8.33      0     0  [trust]
it4-outcome-challengerpv-broad                    8.33  0.67   85.7        0      —      0     0  [trust]
it4-story             leads-broad                11.67  1.03   80.0        3   3.89      2     0  [trust]
it4-benefit-demo      leads-broad                11.67  0.69   80.0        0      —      0     0  [trust]
it4-outcome-challengerleads-broad                11.67  0.82  100.0        1  11.67      0     0  [trust]
it4-story             sales-broad                15.00  2.99   69.6        4   3.75      1     1  [trust]
it4-benefit-demo      sales-broad                15.00  0.69   76.9        0      —      0     0  [trust]
it4-outcome-challengersales-broad                15.00  1.01   63.2        2    7.5      1     0  [trust]
it4-story             sales-biztools             13.33  0.63   16.7        0      —      0     0  [trust]
it4-benefit-demo      sales-biztools             13.33  0.42   75.0        2   6.67      0     0  [trust]
it4-outcome-challengersales-biztools             13.33  0.42   50.0        0      —      0     0  [trust]
it4-story             sales-niche                18.33  1.11   64.7        0      —      0     0  [trust]
it4-benefit-demo      sales-niche                18.33  0.98  100.0        1  18.33      0     0  [trust]
it4-outcome-challengersales-niche                18.33  0.65   80.0        0      —      0     0  [trust]
SITE FUNNEL: visits 151 · sign-ups 14 · demos 4 · purchases 1
ITERATION 4 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 5 — policy v4 · sim world-15 seed 1505 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Priya S., HR coordinator' on how c
  it5-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead — build intake forms in minutes, not hours — 
  it5-count-challenger: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): refreshed count execution — '12,000 forms submitted throug
CAMPAIGN CELLS: pv-broad($10,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($65,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story             pv-broad                    3.33  2.16   77.8        2   1.67      0     1  [trust]
it5-benefit-demo      pv-broad                    3.33  0.24  100.0        0      —      0     0  [trust]
it5-count-challenger  pv-broad                    3.33  1.68   71.4        0      —      0     0  [trust]
it5-story             leads-broad                10.00  1.12   78.6        2    5.0      1     0  [trust]
it5-benefit-demo      leads-broad                10.00  0.24  100.0        0      —      0     0  [trust]
it5-count-challenger  leads-broad                10.00  1.04   84.6        0      —      0     0  [trust]
it5-story             sales-broad                21.67  1.66   51.1        6   3.61      3     1  [trust]
it5-benefit-demo      sales-broad                21.67  0.48   92.3        2  10.84      0     1  [trust]
it5-count-challenger  sales-broad                21.67  1.03   71.4        4   5.42      2     0  [trust]
it5-story             sales-biztools             13.33  0.32   66.7        2   6.67      0     1  [trust]
it5-benefit-demo      sales-biztools             13.33  0.32  100.0        1  13.33      0     0  [trust]
it5-count-challenger  sales-biztools             13.33  0.84   50.0        0      —      0     0  [trust]
it5-story             sales-niche                18.33  1.44   63.6        1  18.33      1     0  [trust]
it5-benefit-demo      sales-niche                18.33  0.46   42.9        2   9.16      0     0  [trust]
it5-count-challenger  sales-niche                18.33  0.72   81.8        0      —      0     0  [trust]
SITE FUNNEL: visits 128 · sign-ups 22 · demos 7 · purchases 4
ITERATION 5 TOTALS: spend $199.98 · settled revenue $116 · ROAS 0.58
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 116, "roas": 0.58}

━━ ITERATION 6 — policy v5 · sim world-15 seed 1506 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-story-a: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Marcus D., property manager' on ho
  it6-story-b: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Elena V., nonprofit program direct
  it6-count-decision: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): count decision test — '4,200 teams, 12,000 forms a week' —
CAMPAIGN CELLS: pv-broad($10,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($65,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-story-a           pv-broad                    3.33  0.72   66.7        1   3.33      0     0  [trust]
it6-story-b           pv-broad                    3.33  0.96   75.0        0      —      0     0  [trust]
it6-count-decision    pv-broad                    3.33  1.20   20.0        1   3.33      0     0  [trust]
it6-story-a           leads-broad                10.00  0.88   81.8        1   10.0      0     0  [trust]
it6-story-b           leads-broad                10.00  0.64   75.0        0      —      0     0  [trust]
it6-count-decision    leads-broad                10.00  1.44   77.8        1   10.0      1     0  [trust]
it6-story-a           sales-broad                21.67  1.22   81.8        1  21.67      0     0  [trust]
it6-story-b           sales-broad                21.67  0.89   62.5        2  10.84      1     0  [trust]
it6-count-decision    sales-broad                21.67  0.30   75.0        0      —      0     0  [trust]
it6-story-a           sales-biztools             13.33  0.73   57.1        0      —      0     0  [trust]
it6-story-b           sales-biztools             13.33  1.05   50.0        0      —      0     0  [trust]
it6-count-decision    sales-biztools             13.33  0.73   85.7        1  13.33      1     0  [trust]
it6-story-a           sales-niche                18.33  0.59   66.7        0      —      0     0  [trust]
it6-story-b           sales-niche                18.33  1.11   76.5        2   9.16      2     0  [trust]
it6-count-decision    sales-niche                18.33  0.20   66.7        1  18.33      0     0  [trust]
SITE FUNNEL: visits 119 · sign-ups 11 · demos 5 · purchases 0
ITERATION 6 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-15 seed 1507 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-story-a: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Ruth A., dental practice manager' 
  it7-story-b: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Jonah B., IT helpdesk lead' on cut
  it7-outcome-decision: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome decision test — 'zero dropped requests in 30 days'
CAMPAIGN CELLS: pv-broad($10,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($65,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-story-a           pv-broad                    3.33  1.20   40.0        0      —      0     0  [trust]
it7-story-b           pv-broad                    3.33  1.20   80.0        1   3.33      1     0  [trust]
it7-outcome-decision  pv-broad                    3.33  1.44   66.7        1   3.33      0     0  [trust]
it7-story-a           leads-broad                10.00  1.28   62.5        0      —      0     0  [trust]
it7-story-b           leads-broad                10.00  1.28   56.2        0      —      0     0  [trust]
it7-outcome-decision  leads-broad                10.00  0.48   66.7        0      —      0     0  [trust]
it7-story-a           sales-broad                21.67  1.03   57.1        1  21.67      0     0  [trust]
it7-story-b           sales-broad                21.67  1.22   75.8        4   5.42      3     1  [trust]
it7-outcome-decision  sales-broad                21.67  0.92   68.0        2  10.84      0     0  [trust]
it7-story-a           sales-biztools             13.33  0.95   88.9        0      —      0     0  [trust]
it7-story-b           sales-biztools             13.33  1.05   70.0        0      —      0     0  [trust]
it7-outcome-decision  sales-biztools             13.33  1.16   81.8        0      —      0     0  [trust]
it7-story-a           sales-niche                18.33  0.33  100.0        0      —      0     0  [trust]
it7-story-b           sales-niche                18.33  1.51   65.2        0      —      0     0  [trust]
it7-outcome-decision  sales-niche                18.33  0.39   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 139 · sign-ups 9 · demos 4 · purchases 1
ITERATION 7 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 8 — policy v7 · sim world-15 seed 1508 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-a: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Sofia M., accounting firm operatio
  it8-story-b: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Caleb N., gym franchise owner' on 
  it8-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead — build intake forms in minutes, not hours — 
CAMPAIGN CELLS: pv-broad($10,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($75,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-a           pv-broad                    3.33  1.68   57.1        0      —      0     0  [trust]
it8-story-b           pv-broad                    3.33  0.72   66.7        0      —      0     0  [trust]
it8-benefit-demo      pv-broad                    3.33  0.72  100.0        0      —      0     0  [trust]
it8-story-a           leads-broad                10.00  1.68   66.7        2    5.0      0     0  [trust]
it8-story-b           leads-broad                10.00  0.88   63.6        1   10.0      0     0  [trust]
it8-benefit-demo      leads-broad                10.00  0.80   60.0        0      —      0     0  [trust]
it8-story-a           sales-broad                25.00  1.06   60.6        3   8.33      0     0  [trust]
it8-story-b           sales-broad                25.00  1.50   78.7        3   8.33      1     0  [trust]
it8-benefit-demo      sales-broad                25.00  0.74   60.9        0      —      0     0  [trust]
it8-story-a           sales-biztools             13.33  2.10   70.0        3   4.44      1     0  [trust]
it8-story-b           sales-biztools             13.33  0.73   57.1        0      —      0     0  [trust]
it8-benefit-demo      sales-biztools             13.33  0.95  100.0        3   4.44      0     0  [trust]
it8-story-a           sales-niche                15.00  1.84   73.9        3    5.0      1     0  [trust]
it8-story-b           sales-niche                15.00  1.20   86.7        1   15.0      0     0  [trust]
it8-benefit-demo      sales-niche                15.00  0.24   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 166 · sign-ups 19 · demos 3 · purchases 0
ITERATION 8 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 0, "roas": 0.0}
