
━━ ITERATION 1 — policy v0 · sim world-6 seed 601 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): leads with the core benefit — build forms in minutes witho
  it1-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow: outcome-first — 'teams cut form-building time 80%'; screen-recordin
  it1-story-named: proof=story named_story=True demo=False | Video ad for Formflow: named customer story — 'How Maya at Brightpath Consulting replaced 
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($36.37,sales,broad,auto), sales-auto~interest_biztools($3.63,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.66   72.7        0      —      0     0  [trust]
it1-outcome-demo      pv-broad                   13.33  0.78   53.8        0      —      0     0  [trust]
it1-story-named       pv-broad                   13.33  0.60   50.0        1  13.33      0     0  [trust]
it1-benefit           leads-broad                13.33  0.30  100.0        0      —      0     0  [trust]
it1-outcome-demo      leads-broad                13.33  0.12  100.0        0      —      0     0  [trust]
it1-story-named       leads-broad                13.33  1.26   52.4        1  13.33      0     0  [trust]
it1-benefit           sales-broad                13.33  0.24  100.0        1  13.33      0     0  [trust]
it1-outcome-demo      sales-broad                13.33  1.02   64.7        1  13.33      0     0  [trust]
it1-story-named       sales-broad                13.33  1.74   75.9        1  13.33      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.42  100.0        0      —      0     0  [trust]
it1-outcome-demo      sales-biztools             13.33  1.47   71.4        0      —      0     0  [trust]
it1-story-named       sales-biztools             13.33  1.78   76.5        1  13.33      1     0  [trust]
it1-benefit           sales-auto~broad           12.12  0.40   83.3        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~broad           12.12  0.59   66.7        0      —      0     0  [NO-TRUST auto-window]
it1-story-named       sales-auto~broad           12.12  1.39   38.1        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  1.21  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~interest_biztools  1.21  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story-named       sales-auto~interest_biztools  1.21  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 121 · sign-ups 6 · demos 1 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-6 seed 602 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Dev at Larkin Legal ditched paper 
  it2-story-demo: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Priya at Northbeam Studio shows e
  it2-count: proof=count named_story=False demo=False | Coverage test, count family: 'Over 12,000 teams build their forms on Formflow' — adoption-
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($35,sales,broad,fixed), sales-biztools($35,sales,interest_biztools,fixed), sales-niche($35,sales,interest_niche,fixed), sales-auto~broad($25.79,sales,broad,auto), sales-auto~interest_biztools($9.21,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-story             pv-broad                   10.00  0.48   50.0        0      —      0     0  [trust]
it2-story-demo        pv-broad                   10.00  0.72   88.9        1   10.0      0     1  [trust]
it2-count             pv-broad                   10.00  0.56   42.9        0      —      0     0  [trust]
it2-story             leads-broad                10.00  1.04   69.2        2    5.0      1     0  [trust]
it2-story-demo        leads-broad                10.00  0.40   60.0        0      —      0     0  [trust]
it2-count             leads-broad                10.00  1.04   69.2        0      —      0     0  [trust]
it2-story             sales-broad                11.67  0.75   72.7        2   5.83      1     0  [trust]
it2-story-demo        sales-broad                11.67  0.14   50.0        0      —      0     0  [trust]
it2-count             sales-broad                11.67  1.44   81.0        0      —      0     0  [trust]
it2-story             sales-biztools             11.67  0.96   62.5        0      —      0     0  [trust]
it2-story-demo        sales-biztools             11.67  1.08   66.7        1  11.67      1     0  [trust]
it2-count             sales-biztools             11.67  0.12  100.0        0      —      0     0  [trust]
it2-story             sales-niche                11.67  0.10  100.0        0      —      0     0  [trust]
it2-story-demo        sales-niche                11.67  0.41   50.0        1  11.67      1     0  [trust]
it2-count             sales-niche                11.67  1.54   46.7        1  11.67      0     0  [trust]
it2-story             sales-auto~broad            8.60  1.21   53.8        1    8.6      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~broad            8.60  0.84   55.6        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~broad            8.60  1.21   46.2        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  3.07  1.37   66.7        0      —      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~interest_biztools  3.07  0.91   50.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  3.07  0.46    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 104 · sign-ups 9 · demos 4 · purchases 1
ITERATION 2 TOTALS: spend $200.04 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 2, "spend": 200.04, "revenue": 29, "roas": 0.14}

━━ ITERATION 3 — policy v2 · sim world-6 seed 603 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story-plain: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Ana at Fieldstone Surveys stopped 
  it3-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Marcus at Helio Tutoring shows ho
  it3-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Ines at Corner Bakery Collective 
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story-plain       pv-broad                   10.00  0.72   55.6        0      —      0     0  [trust]
it3-story-demo-a      pv-broad                   10.00  1.28   81.2        2    5.0      2     0  [trust]
it3-story-demo-b      pv-broad                   10.00  0.72   33.3        0      —      0     0  [trust]
it3-story-plain       leads-broad                10.00  0.48   83.3        0      —      0     0  [trust]
it3-story-demo-a      leads-broad                10.00  1.12   78.6        2    5.0      1     0  [trust]
it3-story-demo-b      leads-broad                10.00  0.88   72.7        0      —      0     0  [trust]
it3-story-plain       sales-broad                16.67  0.48  100.0        1  16.67      1     0  [trust]
it3-story-demo-a      sales-broad                16.67  1.06   68.2        0      —      0     0  [trust]
it3-story-demo-b      sales-broad                16.67  1.68   60.0        2   8.34      1     0  [trust]
it3-story-plain       sales-biztools             15.00  0.47   80.0        0      —      0     0  [trust]
it3-story-demo-a      sales-biztools             15.00  1.03   63.6        0      —      0     0  [trust]
it3-story-demo-b      sales-biztools             15.00  0.47   40.0        0      —      0     0  [trust]
it3-story-plain       sales-niche                15.00  0.40   80.0        0      —      0     0  [trust]
it3-story-demo-a      sales-niche                15.00  0.48   66.7        0      —      0     0  [trust]
it3-story-demo-b      sales-niche                15.00  0.48   83.3        0      —      0     0  [trust]
SITE FUNNEL: visits 117 · sign-ups 7 · demos 5 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-6 seed 604 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-plain: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Tomas at Redline Cycles replaced h
  it4-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Leah at Summit HR shows how she b
  it4-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Omar at Casita Property Group wal
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($115,sales,broad,fixed), probe-biztools($12,sales,interest_biztools,fixed), probe-niche($13,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-plain       pv-broad                   10.00  0.64   50.0        1   10.0      1     0  [trust]
it4-story-demo-a      pv-broad                   10.00  0.32   50.0        0      —      0     0  [trust]
it4-story-demo-b      pv-broad                   10.00  0.88  100.0        1   10.0      0     0  [trust]
it4-story-plain       leads-broad                10.00  0.80   70.0        1   10.0      0     0  [trust]
it4-story-demo-a      leads-broad                10.00  0.40   80.0        1   10.0      1     0  [trust]
it4-story-demo-b      leads-broad                10.00  0.64   75.0        0      —      0     0  [trust]
it4-story-plain       sales-broad                38.33  0.31   60.0        0      —      0     0  [trust]
it4-story-demo-a      sales-broad                38.33  1.42   55.9        3  12.78      1     0  [trust]
it4-story-demo-b      sales-broad                38.33  0.46   59.1        2  19.16      1     1  [trust]
it4-story-plain       probe-biztools              4.00  0.35    0.0        0      —      0     0  [trust]
it4-story-demo-a      probe-biztools              4.00  1.75   20.0        0      —      0     0  [trust]
it4-story-demo-b      probe-biztools              4.00  1.40   25.0        0      —      0     0  [trust]
it4-story-plain       probe-niche                 4.33  0.55   50.0        0      —      0     0  [trust]
it4-story-demo-a      probe-niche                 4.33  1.11   50.0        0      —      0     0  [trust]
it4-story-demo-b      probe-niche                 4.33  0.00    0.0        0      —      0     0  [trust]
SITE FUNNEL: visits 99 · sign-ups 9 · demos 4 · purchases 1
ITERATION 4 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 5 — policy v4 · sim world-6 seed 605 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with demo — 'Grace at Alder Bookkeepi
  it5-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Sam at Duskline Photography walks
  it5-story-demo-c: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Nadia at Crescent Cleaning builds
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($115,sales,broad,fixed), probe-biztools($12,sales,interest_biztools,fixed), probe-niche($13,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-demo-a      pv-broad                   10.00  1.20   66.7        0      —      0     0  [trust]
it5-story-demo-b      pv-broad                   10.00  0.64   75.0        1   10.0      0     1  [trust]
it5-story-demo-c      pv-broad                   10.00  0.08  100.0        0      —      0     0  [trust]
it5-story-demo-a      leads-broad                10.00  0.24   66.7        0      —      0     0  [trust]
it5-story-demo-b      leads-broad                10.00  0.24  100.0        1   10.0      1     0  [trust]
it5-story-demo-c      leads-broad                10.00  1.20   66.7        2    5.0      0     0  [trust]
it5-story-demo-a      sales-broad                38.33  0.92   70.5        1  38.33      0     0  [trust]
it5-story-demo-b      sales-broad                38.33  1.04   66.0        3  12.78      2     1  [trust]
it5-story-demo-c      sales-broad                38.33  0.27   69.2        2  19.16      2     1  [trust]
it5-story-demo-a      probe-biztools              4.00  0.70   50.0        0      —      0     0  [trust]
it5-story-demo-b      probe-biztools              4.00  0.00    0.0        0      —      0     0  [trust]
it5-story-demo-c      probe-biztools              4.00  2.45   85.7        1    4.0      0     0  [trust]
it5-story-demo-a      probe-niche                 4.33  1.11   50.0        0      —      0     0  [trust]
it5-story-demo-b      probe-niche                 4.33  1.38  100.0        0      —      0     0  [trust]
it5-story-demo-c      probe-niche                 4.33  0.83  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 122 · sign-ups 11 · demos 5 · purchases 3
ITERATION 5 TOTALS: spend $199.98 · settled revenue $87 · ROAS 0.44
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 87, "roas": 0.44}

━━ ITERATION 6 — policy v5 · sim world-6 seed 606 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with demo — 'Ruth at Harborline Event
  it6-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Kofi at Brightgate Coaching build
  it6-story-plain-control: proof=story named_story=True demo=False | Video ad for Formflow: named customer story, no demo footage — 'Elena at Windrose Travel t
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($15,leads,broad,fixed), sales-broad($140,sales,broad,fixed), probe-biztools($10,sales,interest_biztools,fixed), probe-niche($10,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-story-demo-a      pv-broad                    8.33  0.29   33.3        0      —      0     0  [trust]
it6-story-demo-b      pv-broad                    8.33  1.25   76.9        1   8.33      0     0  [trust]
it6-story-plain-controlpv-broad                    8.33  0.48   60.0        0      —      0     0  [trust]
it6-story-demo-a      leads-broad                 5.00  0.80   60.0        1    5.0      1     0  [trust]
it6-story-demo-b      leads-broad                 5.00  0.64   50.0        1    5.0      0     1  [trust]
it6-story-plain-controlleads-broad                 5.00  0.64   75.0        0      —      0     0  [trust]
it6-story-demo-a      sales-broad                46.67  0.86   58.0        3  15.56      1     0  [trust]
it6-story-demo-b      sales-broad                46.67  0.86   74.0        4  11.67      1     2  [trust]
it6-story-plain-controlsales-broad                46.67  0.53   71.0        4  11.67      1     1  [trust]
it6-story-demo-a      probe-biztools              3.33  1.26  100.0        1   3.33      1     1  [trust]
it6-story-demo-b      probe-biztools              3.33  0.84   50.0        0      —      0     0  [trust]
it6-story-plain-controlprobe-biztools              3.33  1.26  100.0        0      —      0     0  [trust]
it6-story-demo-a      probe-niche                 3.33  0.00    0.0        0      —      0     0  [trust]
it6-story-demo-b      probe-niche                 3.33  0.00    0.0        0      —      0     0  [trust]
it6-story-plain-controlprobe-niche                 3.33  1.08  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 120 · sign-ups 15 · demos 5 · purchases 5
ITERATION 6 TOTALS: spend $199.98 · settled revenue $145 · ROAS 0.73
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 145, "roas": 0.73}

━━ ITERATION 7 — policy v6 · sim world-6 seed 607 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with demo — 'Jonah at Pinewood Woodwo
  it7-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Aisha at Lantern Therapy builds h
  it7-story-demo-c: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Piet at Cobble Lane Catering walk
CAMPAIGN CELLS: sales-broad($150,sales,broad,fixed), sales-biztools($15,sales,interest_biztools,fixed), leads-broad($15,leads,broad,fixed), pv-broad($10,pageviews,broad,fixed), probe-niche($10,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-story-demo-a      sales-broad                50.00  1.25   62.8        2   25.0      0     0  [trust]
it7-story-demo-b      sales-broad                50.00  0.77   64.6        2   25.0      1     1  [trust]
it7-story-demo-c      sales-broad                50.00  0.98   67.2        4   12.5      1     2  [trust]
it7-story-demo-a      sales-biztools              5.00  1.68   50.0        0      —      0     0  [trust]
it7-story-demo-b      sales-biztools              5.00  0.00    0.0        0      —      0     0  [trust]
it7-story-demo-c      sales-biztools              5.00  1.12   50.0        0      —      0     0  [trust]
it7-story-demo-a      leads-broad                 5.00  0.48   33.3        0      —      0     0  [trust]
it7-story-demo-b      leads-broad                 5.00  0.96   50.0        0      —      0     0  [trust]
it7-story-demo-c      leads-broad                 5.00  0.32  100.0        0      —      0     0  [trust]
it7-story-demo-a      pv-broad                    3.33  1.44   66.7        0      —      0     0  [trust]
it7-story-demo-b      pv-broad                    3.33  0.24    0.0        0      —      0     0  [trust]
it7-story-demo-c      pv-broad                    3.33  1.44   66.7        0      —      0     0  [trust]
it7-story-demo-a      probe-niche                 3.33  2.16   50.0        0      —      0     0  [trust]
it7-story-demo-b      probe-niche                 3.33  1.44   25.0        0      —      0     0  [trust]
it7-story-demo-c      probe-niche                 3.33  0.00    0.0        0      —      0     0  [trust]
SITE FUNNEL: visits 144 · sign-ups 8 · demos 2 · purchases 3
ITERATION 7 TOTALS: spend $199.98 · settled revenue $87 · ROAS 0.44
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 87, "roas": 0.44}

━━ ITERATION 8 — policy v7 · sim world-6 seed 608 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-demo-a: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with demo — 'Mara at Stillwater Yoga 
  it8-story-demo-b: proof=story named_story=True demo=True | Video ad for Formflow: named customer story with demo — 'Theo at Granite Peak Accounting b
  it8-story-plain-control: proof=story named_story=True demo=False | Video ad for Formflow: named customer story, no demo footage — 'Yusuf at Beacon Rowing Clu
CAMPAIGN CELLS: sales-broad($165,sales,broad,fixed), sales-biztools($10,sales,interest_biztools,fixed), leads-broad($10,leads,broad,fixed), pv-broad($8,pageviews,broad,fixed), probe-niche($7,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-demo-a      sales-broad                55.00  0.81   71.4        7   7.86      1     0  [trust]
it8-story-demo-b      sales-broad                55.00  0.57   66.7        2   27.5      1     1  [trust]
it8-story-plain-controlsales-broad                55.00  0.49   52.9        0      —      0     0  [trust]
it8-story-demo-a      sales-biztools              3.33  0.42  100.0        0      —      0     0  [trust]
it8-story-demo-b      sales-biztools              3.33  0.42    0.0        0      —      0     0  [trust]
it8-story-plain-controlsales-biztools              3.33  0.00    0.0        0      —      0     0  [trust]
it8-story-demo-a      leads-broad                 3.33  0.24  100.0        1   3.33      0     0  [trust]
it8-story-demo-b      leads-broad                 3.33  0.72  100.0        1   3.33      0     0  [trust]
it8-story-plain-controlleads-broad                 3.33  0.00    0.0        0      —      0     0  [trust]
it8-story-demo-a      pv-broad                    2.67  0.60   50.0        0      —      0     0  [trust]
it8-story-demo-b      pv-broad                    2.67  1.50  100.0        0      —      0     0  [trust]
it8-story-plain-controlpv-broad                    2.67  1.50   40.0        0      —      0     0  [trust]
it8-story-demo-a      probe-niche                 2.33  0.51  100.0        0      —      0     0  [trust]
it8-story-demo-b      probe-niche                 2.33  0.00    0.0        0      —      0     0  [trust]
it8-story-plain-controlprobe-niche                 2.33  0.51  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 99 · sign-ups 11 · demos 2 · purchases 1
ITERATION 8 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 29, "roas": 0.15}
