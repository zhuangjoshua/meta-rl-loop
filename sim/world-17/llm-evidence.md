
━━ ITERATION 1 — policy v0 · sim world-17 seed 1701 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit - stop rebuilding the same inta
  it1-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-handling tim
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., an ops manager, explains h
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($8.84,sales,broad,auto), sales-auto~interest_biztools($31.16,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  1.68   78.6        2   6.67      1     0  [trust]
it1-outcome-demo      pv-broad                   13.33  2.34   74.4        2   6.67      0     0  [trust]
it1-story             pv-broad                   13.33  1.08   50.0        1  13.33      0     0  [trust]
it1-benefit           leads-broad                13.33  3.24   81.5        5   2.67      0     0  [trust]
it1-outcome-demo      leads-broad                13.33  1.50   68.0        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.24  100.0        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.66   90.9        1  13.33      0     0  [trust]
it1-outcome-demo      sales-broad                13.33  0.48   75.0        1  13.33      0     0  [trust]
it1-story             sales-broad                13.33  1.32   72.7        1  13.33      1     0  [trust]
it1-benefit           sales-biztools             13.33  1.57   80.0        3   4.44      1     0  [trust]
it1-outcome-demo      sales-biztools             13.33  0.95   88.9        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  1.26   58.3        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            2.95  0.27  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~broad            2.95  2.17   62.5        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            2.95  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools 10.39  0.40   66.7        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~interest_biztools 10.39  0.27   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools 10.39  0.81   66.7        1  10.39      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 197 · sign-ups 18 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-17 seed 1702 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led control - stop rebuilding the same intake form
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): count-led challenger - 12,000 teams route their intake thr
  it2-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): same benefit-led script as the control, but shown as a scr
CAMPAIGN CELLS: clicks-broad($35,clicks,broad,fixed), leads-broad($40,leads,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-auto~broad($10.3,sales,broad,auto), sales-auto~interest_biztools($29.7,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-benefit           clicks-broad               11.67  1.85   81.5        0      —      0     0  [trust]
it2-count             clicks-broad               11.67  0.34   40.0        0      —      0     0  [trust]
it2-benefit-demo      clicks-broad               11.67  0.82   83.3        2   5.83      0     0  [trust]
it2-benefit           leads-broad                13.33  0.48  100.0        0      —      0     0  [trust]
it2-count             leads-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it2-benefit-demo      leads-broad                13.33  0.66  100.0        2   6.67      0     0  [trust]
it2-benefit           sales-niche                13.33  0.63   71.4        3   4.44      1     0  [trust]
it2-count             sales-niche                13.33  0.63   71.4        1  13.33      1     0  [trust]
it2-benefit-demo      sales-niche                13.33  0.81   77.8        0      —      0     0  [trust]
it2-benefit           sales-biztools             15.00  1.12   75.0        0      —      0     0  [trust]
it2-count             sales-biztools             15.00  0.28  100.0        0      —      0     0  [trust]
it2-benefit-demo      sales-biztools             15.00  0.65  100.0        1   15.0      0     0  [trust]
it2-benefit           sales-auto~broad            3.43  0.47  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~broad            3.43  0.47  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-benefit-demo      sales-auto~broad            3.43  0.70   33.3        0      —      0     0  [NO-TRUST auto-window]
it2-benefit           sales-auto~interest_biztools  9.90  0.28   50.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  9.90  0.14    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-benefit-demo      sales-auto~interest_biztools  9.90  0.85   83.3        2   4.95      1     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 103 · sign-ups 11 · demos 3 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-17 seed 1703 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led control - stop rebuilding the same intake form
  it3-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): identical benefit-led script to the control, rendered as a
  it3-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led challenger - teams cut form-handling time from
CAMPAIGN CELLS: leads-broad($40,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($55,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-benefit           leads-broad                13.33  0.78   69.2        2   6.67      0     0  [trust]
it3-benefit-demo      leads-broad                13.33  0.78   76.9        0      —      0     0  [trust]
it3-outcome           leads-broad                13.33  1.02   88.2        2   6.67      0     1  [trust]
it3-benefit           sales-broad                16.67  0.67   92.9        3   5.56      0     0  [trust]
it3-benefit-demo      sales-broad                16.67  0.67   78.6        0      —      0     0  [trust]
it3-outcome           sales-broad                16.67  1.44   56.7        3   5.56      0     0  [trust]
it3-benefit           sales-biztools             18.33  0.38   80.0        0      —      0     0  [trust]
it3-benefit-demo      sales-biztools             18.33  0.08  100.0        0      —      0     0  [trust]
it3-outcome           sales-biztools             18.33  0.38  100.0        1  18.33      0     0  [trust]
it3-benefit           sales-niche                18.33  0.92   85.7        2   9.16      1     0  [trust]
it3-benefit-demo      sales-niche                18.33  0.98   73.3        1  18.33      0     0  [trust]
it3-outcome           sales-niche                18.33  0.79   66.7        2   9.16      1     0  [trust]
SITE FUNNEL: visits 116 · sign-ups 16 · demos 2 · purchases 1
ITERATION 3 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 4 — policy v3 · sim world-17 seed 1704 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led control - teams cut form-handling time from ho
  it4-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led co-control - stop rebuilding the same intake f
  it4-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): identical outcome-led script to the control, rendered as a
CAMPAIGN CELLS: leads-broad($40,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($55,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-outcome           leads-broad                13.33  1.38   91.3        2   6.67      0     0  [trust]
it4-benefit           leads-broad                13.33  0.78   61.5        0      —      0     0  [trust]
it4-outcome-demo      leads-broad                13.33  1.86   90.3        4   3.33      0     0  [trust]
it4-outcome           sales-broad                16.67  0.72   80.0        2   8.34      1     0  [trust]
it4-benefit           sales-broad                16.67  1.15   83.3        1  16.67      0     0  [trust]
it4-outcome-demo      sales-broad                16.67  1.06   72.7        0      —      0     0  [trust]
it4-outcome           sales-biztools             18.33  1.60   85.7        2   9.16      0     0  [trust]
it4-benefit           sales-biztools             18.33  0.38   80.0        0      —      0     0  [trust]
it4-outcome-demo      sales-biztools             18.33  0.46   33.3        1  18.33      1     0  [trust]
it4-outcome           sales-niche                18.33  1.83   64.3        3   6.11      1     0  [trust]
it4-benefit           sales-niche                18.33  0.39   83.3        0      —      0     0  [trust]
it4-outcome-demo      sales-niche                18.33  0.72   63.6        1  18.33      0     0  [trust]
SITE FUNNEL: visits 159 · sign-ups 16 · demos 3 · purchases 0
ITERATION 4 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-17 seed 1705 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led house ad - teams cut form-handling time from h
  it5-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): identical outcome-led script to the house ad, rendered as 
  it5-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led periodic challenger - stop rebuilding the same
CAMPAIGN CELLS: leads-broad($40,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($55,sales,interest_biztools,fixed), sales-niche($55,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-outcome           leads-broad                13.33  1.20   75.0        3   4.44      0     0  [trust]
it5-outcome-demo      leads-broad                13.33  1.20   60.0        0      —      0     0  [trust]
it5-benefit           leads-broad                13.33  1.32   68.2        3   4.44      1     1  [trust]
it5-outcome           sales-broad                16.67  0.53   72.7        2   8.34      1     0  [trust]
it5-outcome-demo      sales-broad                16.67  2.06   79.1        4   4.17      1     0  [trust]
it5-benefit           sales-broad                16.67  0.58   91.7        1  16.67      0     0  [trust]
it5-outcome           sales-biztools             18.33  0.69   44.4        1  18.33      0     0  [trust]
it5-outcome-demo      sales-biztools             18.33  1.22   43.8        1  18.33      0     0  [trust]
it5-benefit           sales-biztools             18.33  0.69   77.8        2   9.16      1     0  [trust]
it5-outcome           sales-niche                18.33  0.92   71.4        1  18.33      0     0  [trust]
it5-outcome-demo      sales-niche                18.33  1.24   57.9        1  18.33      0     0  [trust]
it5-benefit           sales-niche                18.33  0.72   72.7        1  18.33      1     0  [trust]
SITE FUNNEL: visits 142 · sign-ups 20 · demos 5 · purchases 1
ITERATION 5 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 6 — policy v5 · sim world-17 seed 1706 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led house ad - teams cut form-handling time from h
  it6-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): identical outcome-led script to the house ad, rendered as 
  it6-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led periodic challenger - stop rebuilding the same
CAMPAIGN CELLS: leads-broad($120,leads,broad,fixed), sales-broad($27,sales,broad,fixed), sales-biztools($27,sales,interest_biztools,fixed), sales-niche($26,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-outcome           leads-broad                40.00  2.24   66.1        9   4.44      1     0  [trust]
it6-outcome-demo      leads-broad                40.00  1.26   74.6        9   4.44      2     0  [trust]
it6-benefit           leads-broad                40.00  0.62   77.4        5    8.0      3     0  [trust]
it6-outcome           sales-broad                 9.00  1.51   82.4        2    4.5      0     0  [trust]
it6-outcome-demo      sales-broad                 9.00  1.60   61.1        2    4.5      0     0  [trust]
it6-benefit           sales-broad                 9.00  0.36  100.0        1    9.0      1     0  [trust]
it6-outcome           sales-biztools              9.00  2.64   88.2        2    4.5      0     0  [trust]
it6-outcome-demo      sales-biztools              9.00  0.93   66.7        0      —      0     0  [trust]
it6-benefit           sales-biztools              9.00  0.16  100.0        0      —      0     0  [trust]
it6-outcome           sales-niche                 8.67  0.97  100.0        1   8.67      0     0  [trust]
it6-outcome-demo      sales-niche                 8.67  0.42   33.3        1   8.67      0     0  [trust]
it6-benefit           sales-niche                 8.67  0.55   50.0        0      —      0     0  [trust]
SITE FUNNEL: visits 204 · sign-ups 32 · demos 7 · purchases 0
ITERATION 6 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-17 seed 1707 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led house ad - teams cut form-handling time from h
  it7-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): rotating challenger, named customer story - Maya R., an op
CAMPAIGN CELLS: leads-broad($120,leads,broad,fixed), sales-broad($27,sales,broad,fixed), sales-biztools($27,sales,interest_biztools,fixed), sales-niche($26,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-outcome           leads-broad                60.00  2.32   77.6       13   4.62      4     1  [trust]
it7-story             leads-broad                60.00  1.88   74.5        8    7.5      3     0  [trust]
it7-outcome           sales-broad                13.50  1.24   57.1        0      —      0     0  [trust]
it7-story             sales-broad                13.50  1.96   66.7        3    4.5      1     0  [trust]
it7-outcome           sales-biztools             13.50  0.62   50.0        2   6.75      1     0  [trust]
it7-story             sales-biztools             13.50  1.35   76.9        2   6.75      2     0  [trust]
it7-outcome           sales-niche                13.00  1.02   90.9        0      —      0     0  [trust]
it7-story             sales-niche                13.00  1.29   78.6        3   4.33      3     0  [trust]
SITE FUNNEL: visits 308 · sign-ups 31 · demos 14 · purchases 1
ITERATION 7 TOTALS: spend $200.0 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 7, "spend": 200.0, "revenue": 29, "roas": 0.14}

━━ ITERATION 8 — policy v7 · sim world-17 seed 1708 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led house ad - teams cut form-handling time from h
  it8-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): rotating challenger, count-led - 12,000 teams route their 
CAMPAIGN CELLS: leads-broad($150,leads,broad,fixed), sales-biztools($25,sales,interest_biztools,fixed), sales-niche($25,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-outcome           leads-broad                75.00  1.48   80.6       13   5.77      4     1  [trust]
it8-count             leads-broad                75.00  0.97   65.9        4  18.75      1     0  [trust]
it8-outcome           sales-biztools             12.50  0.90   87.5        1   12.5      0     0  [trust]
it8-count             sales-biztools             12.50  0.00    0.0        0      —      0     0  [trust]
it8-outcome           sales-niche                12.50  1.54   75.0        1   12.5      0     0  [trust]
it8-count             sales-niche                12.50  1.25   76.9        0      —      0     0  [trust]
SITE FUNNEL: visits 201 · sign-ups 19 · demos 5 · purchases 1
ITERATION 8 TOTALS: spend $200.0 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 8, "spend": 200.0, "revenue": 29, "roas": 0.14}
