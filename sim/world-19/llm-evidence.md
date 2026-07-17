
━━ ITERATION 1 — policy v0 · sim world-19 seed 1901 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit — stop rebuilding the same inta
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome — teams cut form-building tim
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Maya, an ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($6.16,sales,broad,auto), sales-auto~interest_biztools($33.84,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.24  100.0        0      —      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.54   44.4        2   6.67      2     0  [trust]
it1-story             pv-broad                   13.33  0.78   76.9        2   6.67      0     0  [trust]
it1-benefit           leads-broad                13.33  0.30   80.0        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.60   50.0        0      —      0     0  [trust]
it1-story             leads-broad                13.33  0.30  100.0        1  13.33      1     0  [trust]
it1-benefit           sales-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.90   60.0        1  13.33      1     0  [trust]
it1-story             sales-broad                13.33  1.20   75.0        0      —      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.52  100.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.52  100.0        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  0.73   57.1        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            2.05  0.39  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            2.05  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            2.05  0.78  100.0        1   2.05      1     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools 11.28  0.62  100.0        1  11.28      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools 11.28  0.25   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools 11.28  0.50   50.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 84 · sign-ups 8 · demos 5 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-19 seed 1902 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): lead with adoption numbers — 4,200 teams build their intak
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): outcome-first opening — cut form-building from hours to mi
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Dan, operations manager at a home-s
CAMPAIGN CELLS: clicks-broad($30,clicks,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($35.49,sales,broad,auto), sales-auto~interest_biztools($4.51,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count             clicks-broad               10.00  0.32   75.0        0      —      0     0  [trust]
it2-outcome-demo      clicks-broad               10.00  0.80   50.0        0      —      0     0  [trust]
it2-story             clicks-broad               10.00  0.24   33.3        1   10.0      1     0  [trust]
it2-count             leads-broad                13.33  0.72   33.3        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                13.33  0.48   62.5        0      —      0     0  [trust]
it2-story             leads-broad                13.33  0.42   71.4        0      —      0     0  [trust]
it2-count             sales-broad                16.67  0.58   75.0        0      —      0     0  [trust]
it2-outcome-demo      sales-broad                16.67  0.48   80.0        0      —      0     0  [trust]
it2-story             sales-broad                16.67  0.34   57.1        0      —      0     0  [trust]
it2-count             sales-niche                13.33  0.54  100.0        0      —      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  0.27  100.0        0      —      0     0  [trust]
it2-story             sales-niche                13.33  0.63   85.7        0      —      0     0  [trust]
it2-count             sales-auto~broad           11.83  0.41   83.3        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad           11.83  0.34   60.0        1  11.83      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~broad           11.83  0.74   81.8        1  11.83      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  1.50  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  1.50  0.93    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  1.50  0.93  100.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 77 · sign-ups 3 · demos 1 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-19 seed 1903 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-first — teams cut intake form turnaround from two 
  it3-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Priya, clinic office manager, descr
  it3-benefit-retest: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led re-test — never rebuild the same intake form a
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($80,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-outcome           pv-broad                   13.33  0.60   60.0        0      —      0     0  [trust]
it3-story             pv-broad                   13.33  0.72   66.7        1  13.33      0     0  [trust]
it3-benefit-retest    pv-broad                   13.33  2.52   81.0        6   2.22      1     0  [trust]
it3-outcome           leads-broad                13.33  0.42   71.4        0      —      0     0  [trust]
it3-story             leads-broad                13.33  0.84   64.3        0      —      0     0  [trust]
it3-benefit-retest    leads-broad                13.33  0.36  100.0        0      —      0     0  [trust]
it3-outcome           sales-broad                26.67  1.35   62.2        3   8.89      0     1  [trust]
it3-story             sales-broad                26.67  0.51   70.6        1  26.67      0     0  [trust]
it3-benefit-retest    sales-broad                26.67  0.18   83.3        0      —      0     0  [trust]
it3-outcome           sales-biztools             13.33  1.47   64.3        0      —      0     0  [trust]
it3-story             sales-biztools             13.33  1.26  100.0        0      —      0     0  [trust]
it3-benefit-retest    sales-biztools             13.33  0.21  100.0        1  13.33      0     0  [trust]
SITE FUNNEL: visits 136 · sign-ups 12 · demos 1 · purchases 1
ITERATION 3 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 4 — policy v3 · sim world-19 seed 1904 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): benefit-led — never rebuild the same intake form again; Fo
  it4-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit-led — never rebuild the same intake form again — f
  it4-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-first challenger — teams cut intake form turnaroun
CAMPAIGN CELLS: pv-broad($50,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($90,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-benefit           pv-broad                   16.67  2.35   81.6        9   1.85      3     3  [trust]
it4-benefit-demo      pv-broad                   16.67  0.91   78.9        0      —      0     0  [trust]
it4-outcome           pv-broad                   16.67  0.19   75.0        0      —      0     0  [trust]
it4-benefit           leads-broad                10.00  1.28   81.2        0      —      0     0  [trust]
it4-benefit-demo      leads-broad                10.00  1.36   70.6        1   10.0      0     0  [trust]
it4-outcome           leads-broad                10.00  0.24   33.3        0      —      0     0  [trust]
it4-benefit           sales-broad                30.00  0.32   75.0        0      —      0     0  [trust]
it4-benefit-demo      sales-broad                30.00  0.32   66.7        1   30.0      0     0  [trust]
it4-outcome           sales-broad                30.00  0.91   70.6        0      —      0     0  [trust]
it4-benefit           sales-biztools             10.00  0.28   50.0        0      —      0     0  [trust]
it4-benefit-demo      sales-biztools             10.00  0.14  100.0        0      —      0     0  [trust]
it4-outcome           sales-biztools             10.00  0.84  100.0        1   10.0      1     0  [trust]
SITE FUNNEL: visits 133 · sign-ups 12 · demos 4 · purchases 3
ITERATION 4 TOTALS: spend $200.01 · settled revenue $87 · ROAS 0.43
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 87, "roas": 0.43}

━━ ITERATION 5 — policy v4 · sim world-19 seed 1905 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): plain benefit-led — never rebuild the same intake form aga
  it5-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-first challenger — teams cut intake form turnaroun
  it5-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story challenger — Marcus, gym owner, tells
CAMPAIGN CELLS: pv-broad($100,pageviews,broad,fixed), sales-broad($60,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-benefit           pv-broad                   33.33  1.27   77.4        6   5.55      0     0  [trust]
it5-outcome           pv-broad                   33.33  0.41   76.5        2  16.66      1     0  [trust]
it5-story             pv-broad                   33.33  0.26   72.7        1  33.33      1     0  [trust]
it5-benefit           sales-broad                20.00  0.40   80.0        0      —      0     0  [trust]
it5-outcome           sales-broad                20.00  1.20   70.0        6   3.33      2     0  [trust]
it5-story             sales-broad                20.00  1.36   44.1        1   20.0      0     0  [trust]
it5-benefit           sales-biztools             13.33  0.00    0.0        0      —      0     0  [trust]
it5-outcome           sales-biztools             13.33  0.21  100.0        0      —      0     0  [trust]
it5-story             sales-biztools             13.33  0.42   75.0        0      —      0     0  [trust]
SITE FUNNEL: visits 111 · sign-ups 16 · demos 4 · purchases 0
ITERATION 5 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 · sim world-19 seed 1906 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): plain benefit-led — never rebuild the same intake form aga
  it6-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led — teams cut intake form turnaround from two da
  it6-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): outcome-led challenger with demo — the two-days-to-twenty-
CAMPAIGN CELLS: pv-broad($70,pageviews,broad,fixed), sales-broad($70,sales,broad,fixed), leads-broad($30,leads,broad,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-benefit           pv-broad                   23.33  0.51   86.7        2  11.66      1     0  [trust]
it6-outcome           pv-broad                   23.33  0.27   75.0        0      —      0     0  [trust]
it6-outcome-demo      pv-broad                   23.33  0.72   81.0        3   7.78      1     0  [trust]
it6-benefit           sales-broad                23.33  0.31  100.0        1  23.33      0     1  [trust]
it6-outcome           sales-broad                23.33  0.51   46.7        1  23.33      0     0  [trust]
it6-outcome-demo      sales-broad                23.33  1.17   70.6        2  11.66      0     0  [trust]
it6-benefit           leads-broad                10.00  0.48   83.3        1   10.0      0     0  [trust]
it6-outcome           leads-broad                10.00  0.16  100.0        0      —      0     0  [trust]
it6-outcome-demo      leads-broad                10.00  0.72   77.8        1   10.0      0     0  [trust]
it6-benefit           sales-niche                10.00  0.24  100.0        0      —      0     0  [trust]
it6-outcome           sales-niche                10.00  0.24  100.0        0      —      0     0  [trust]
it6-outcome-demo      sales-niche                10.00  0.84   71.4        0      —      0     0  [trust]
SITE FUNNEL: visits 99 · sign-ups 11 · demos 2 · purchases 1
ITERATION 6 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 7 — policy v6 · sim world-19 seed 1907 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): plain benefit-led — never rebuild the same intake form aga
  it7-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): outcome-led with demo — teams cut intake form turnaround f
  it7-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story challenger — Elena, event planner, te
CAMPAIGN CELLS: pv-broad($80,pageviews,broad,fixed), sales-broad($80,sales,broad,fixed), leads-broad($20,leads,broad,fixed), sales-biztools($20,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-benefit           pv-broad                   26.67  0.63   76.2        1  26.67      1     0  [trust]
it7-outcome-demo      pv-broad                   26.67  0.42   71.4        1  26.67      0     0  [trust]
it7-story             pv-broad                   26.67  0.96   78.1        1  26.67      0     0  [trust]
it7-benefit           sales-broad                26.67  0.54   88.9        1  26.67      0     0  [trust]
it7-outcome-demo      sales-broad                26.67  0.72   58.3        1  26.67      1     0  [trust]
it7-story             sales-broad                26.67  0.63   57.1        0      —      0     0  [trust]
it7-benefit           leads-broad                 6.67  1.20   80.0        0      —      0     0  [trust]
it7-outcome-demo      leads-broad                 6.67  0.48   50.0        0      —      0     0  [trust]
it7-story             leads-broad                 6.67  0.72   66.7        1   6.67      0     0  [trust]
it7-benefit           sales-biztools              6.67  0.42   50.0        0      —      0     0  [trust]
it7-outcome-demo      sales-biztools              6.67  0.63  100.0        0      —      0     0  [trust]
it7-story             sales-biztools              6.67  0.84   75.0        1   6.67      1     0  [trust]
SITE FUNNEL: visits 114 · sign-ups 7 · demos 3 · purchases 0
ITERATION 7 TOTALS: spend $200.04 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 200.04, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-19 seed 1908 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-benefit-fresh: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): fresh plain benefit-led — one link, every form: send a sin
  it8-benefit-fresh2: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): fresh plain benefit-led — your forms build themselves: dro
  it8-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): fresh outcome-led with demo — a 40-hour admin month cut to
CAMPAIGN CELLS: pv-broad($90,pageviews,broad,fixed), sales-broad($90,sales,broad,fixed), leads-broad($20,leads,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-benefit-fresh     pv-broad                   30.00  1.47   81.8        3   10.0      0     1  [trust]
it8-benefit-fresh2    pv-broad                   30.00  0.59   77.3        2   15.0      0     0  [trust]
it8-outcome-demo      pv-broad                   30.00  0.88   81.8        1   30.0      0     0  [trust]
it8-benefit-fresh     sales-broad                30.00  0.27   90.0        1   30.0      0     0  [trust]
it8-benefit-fresh2    sales-broad                30.00  0.43   68.8        2   15.0      2     0  [trust]
it8-outcome-demo      sales-broad                30.00  0.48   83.3        2   15.0      1     0  [trust]
it8-benefit-fresh     leads-broad                 6.67  0.36  100.0        0      —      0     0  [trust]
it8-benefit-fresh2    leads-broad                 6.67  1.08   88.9        1   6.67      0     0  [trust]
it8-outcome-demo      leads-broad                 6.67  1.68   57.1        0      —      0     0  [trust]
SITE FUNNEL: visits 143 · sign-ups 12 · demos 3 · purchases 1
ITERATION 8 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 29, "roas": 0.14}
