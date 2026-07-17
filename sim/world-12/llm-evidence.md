
━━ ITERATION 1 — policy v0 · sim world-12 seed 1201 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo). Lead with the core benefit: stop rebuilding the same forms
  it1-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Outcome-first: teams cut form-admin time from hours to min
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo). Named customer story: Maya R., ops lead at a 12-person age
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($50,sales,interest_biztools,fixed), sales-auto~broad($7.95,sales,broad,auto), sales-auto~interest_biztools($32.05,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   10.00  0.40   80.0        0      —      0     0  [trust]
it1-outcome-demo      pv-broad                   10.00  0.48   83.3        0      —      0     0  [trust]
it1-story             pv-broad                   10.00  0.48   66.7        0      —      0     0  [trust]
it1-benefit           leads-broad                10.00  0.32  100.0        0      —      0     0  [trust]
it1-outcome-demo      leads-broad                10.00  0.32   75.0        0      —      0     0  [trust]
it1-story             leads-broad                10.00  0.48   33.3        1   10.0      0     0  [trust]
it1-benefit           sales-broad                16.67  0.62  100.0        2   8.34      0     0  [trust]
it1-outcome-demo      sales-broad                16.67  1.06   63.6        2   8.34      1     0  [trust]
it1-story             sales-broad                16.67  1.78   73.0        1  16.67      0     0  [trust]
it1-benefit           sales-biztools             16.67  0.50   83.3        0      —      0     0  [trust]
it1-outcome-demo      sales-biztools             16.67  1.85   68.2        1  16.67      1     0  [trust]
it1-story             sales-biztools             16.67  1.18   92.9        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            2.65  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~broad            2.65  0.30    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            2.65  0.91   66.7        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools 10.68  0.26  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~interest_biztools 10.68  0.79   83.3        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools 10.68  0.26    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 118 · sign-ups 7 · demos 2 · purchases 0
ITERATION 1 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-12 seed 1202 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Count-led: 4,800 teams already build their forms in Formfl
  it2-count-nodemo: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo). Count-led: 4,800 teams already build their forms in Formfl
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo). Outcome-first: teams cut form-admin time from hours to min
CAMPAIGN CELLS: pv-broad($20,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), clicks-broad($20,clicks,broad,fixed), sales-broad($40,sales,broad,fixed), sales-niche($40,sales,interest_niche,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-auto~broad($5.48,sales,broad,auto), sales-auto~interest_biztools($24.52,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count-demo        pv-broad                    6.67  0.36   33.3        1   6.67      1     0  [trust]
it2-count-nodemo      pv-broad                    6.67  0.72   83.3        1   6.67      0     0  [trust]
it2-outcome-demo      pv-broad                    6.67  1.08   44.4        0      —      0     0  [trust]
it2-count-demo        leads-broad                 6.67  1.32   72.7        0      —      0     0  [trust]
it2-count-nodemo      leads-broad                 6.67  0.24   50.0        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                 6.67  0.48   50.0        0      —      0     0  [trust]
it2-count-demo        clicks-broad                6.67  0.60   80.0        0      —      0     0  [trust]
it2-count-nodemo      clicks-broad                6.67  3.00   72.0        0      —      0     0  [trust]
it2-outcome-demo      clicks-broad                6.67  0.60  100.0        0      —      0     0  [trust]
it2-count-demo        sales-broad                13.33  2.70   75.6        2   6.67      0     0  [trust]
it2-count-nodemo      sales-broad                13.33  0.78   53.8        1  13.33      0     0  [trust]
it2-outcome-demo      sales-broad                13.33  2.16   66.7        0      —      0     0  [trust]
it2-count-demo        sales-niche                13.33  0.81   66.7        0      —      0     0  [trust]
it2-count-nodemo      sales-niche                13.33  0.72   75.0        1  13.33      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  1.44   68.8        0      —      0     0  [trust]
it2-count-demo        sales-biztools             10.00  0.56   50.0        0      —      0     0  [trust]
it2-count-nodemo      sales-biztools             10.00  1.40   60.0        0      —      0     0  [trust]
it2-outcome-demo      sales-biztools             10.00  0.70   60.0        0      —      0     0  [trust]
it2-count-demo        sales-auto~broad            1.83  0.44  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-count-nodemo      sales-auto~broad            1.83  0.44  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad            1.83  1.31   66.7        0      —      0     0  [NO-TRUST auto-window]
it2-count-demo        sales-auto~interest_biztools  8.17  1.03   83.3        0      —      0     0  [NO-TRUST auto-window]
it2-count-nodemo      sales-auto~interest_biztools  8.17  1.03   50.0        1   8.17      1     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  8.17  0.51   33.3        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 160 · sign-ups 7 · demos 2 · purchases 0
ITERATION 2 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-12 seed 1203 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo). Named customer story: Maya R., ops lead at a 12-person age
  it3-story-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo). Named customer story: Maya R., ops lead at a 12-person age
  it3-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo). Count-led: 4,800 teams already build their forms in Formfl
CAMPAIGN CELLS: pv-broad($20,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($70,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story-demo        pv-broad                    6.67  0.48   75.0        0      —      0     0  [trust]
it3-story-nodemo      pv-broad                    6.67  1.20   50.0        1   6.67      0     0  [trust]
it3-count-demo        pv-broad                    6.67  0.72   66.7        0      —      0     0  [trust]
it3-story-demo        leads-broad                 6.67  0.24  100.0        0      —      0     0  [trust]
it3-story-nodemo      leads-broad                 6.67  0.36   66.7        0      —      0     0  [trust]
it3-count-demo        leads-broad                 6.67  1.32   81.8        1   6.67      0     0  [trust]
it3-story-demo        sales-broad                23.33  1.06   77.4        4   5.83      2     0  [trust]
it3-story-nodemo      sales-broad                23.33  1.13   57.6        0      —      0     0  [trust]
it3-count-demo        sales-broad                23.33  0.48   71.4        1  23.33      0     0  [trust]
it3-story-demo        sales-biztools             15.00  1.68   50.0        0      —      0     0  [trust]
it3-story-nodemo      sales-biztools             15.00  0.93   40.0        1   15.0      0     0  [trust]
it3-count-demo        sales-biztools             15.00  0.65   57.1        1   15.0      1     0  [trust]
it3-story-demo        sales-niche                15.00  0.48   33.3        0      —      0     0  [trust]
it3-story-nodemo      sales-niche                15.00  1.28   56.2        2    7.5      1     0  [trust]
it3-count-demo        sales-niche                15.00  1.60   60.0        1   15.0      0     0  [trust]
SITE FUNNEL: visits 118 · sign-ups 12 · demos 4 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-12 seed 1204 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-demo-q: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it4-story-nodemo-q: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it4-outcome-demo-q: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
CAMPAIGN CELLS: pv-broad($20,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($70,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-demo-q      pv-broad                    6.67  1.20   80.0        0      —      0     0  [trust]
it4-story-nodemo-q    pv-broad                    6.67  0.72   83.3        1   6.67      0     0  [trust]
it4-outcome-demo-q    pv-broad                    6.67  0.48  100.0        1   6.67      1     0  [trust]
it4-story-demo-q      leads-broad                 6.67  1.20   90.0        0      —      0     0  [trust]
it4-story-nodemo-q    leads-broad                 6.67  0.60   20.0        1   6.67      0     0  [trust]
it4-outcome-demo-q    leads-broad                 6.67  0.48   75.0        1   6.67      0     0  [trust]
it4-story-demo-q      sales-broad                23.33  1.30   65.8        1  23.33      0     0  [trust]
it4-story-nodemo-q    sales-broad                23.33  1.13   63.6        4   5.83      2     0  [trust]
it4-outcome-demo-q    sales-broad                23.33  0.75   63.6        0      —      0     0  [trust]
it4-story-demo-q      sales-biztools             15.00  1.49   68.8        0      —      0     0  [trust]
it4-story-nodemo-q    sales-biztools             15.00  1.31   78.6        0      —      0     0  [trust]
it4-outcome-demo-q    sales-biztools             15.00  0.65   57.1        2    7.5      0     0  [trust]
it4-story-demo-q      sales-niche                15.00  0.64   75.0        0      —      0     0  [trust]
it4-story-nodemo-q    sales-niche                15.00  0.80   70.0        0      —      0     0  [trust]
it4-outcome-demo-q    sales-niche                15.00  0.32  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 133 · sign-ups 11 · demos 3 · purchases 0
ITERATION 4 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-12 seed 1205 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-nodemo-q: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it5-outcome-nodemo-q: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it5-outcome-demo-q: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
CAMPAIGN CELLS: pv-broad($20,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($70,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-nodemo-q    pv-broad                    6.67  1.08   33.3        0      —      0     0  [trust]
it5-outcome-nodemo-q  pv-broad                    6.67  2.52   66.7        0      —      0     0  [trust]
it5-outcome-demo-q    pv-broad                    6.67  0.24  100.0        0      —      0     0  [trust]
it5-story-nodemo-q    leads-broad                 6.67  0.60   60.0        1   6.67      0     0  [trust]
it5-outcome-nodemo-q  leads-broad                 6.67  1.08   77.8        0      —      0     0  [trust]
it5-outcome-demo-q    leads-broad                 6.67  0.36   66.7        0      —      0     0  [trust]
it5-story-nodemo-q    sales-broad                23.33  0.62   83.3        0      —      0     0  [trust]
it5-outcome-nodemo-q  sales-broad                23.33  0.62   50.0        0      —      0     0  [trust]
it5-outcome-demo-q    sales-broad                23.33  0.27   75.0        0      —      0     0  [trust]
it5-story-nodemo-q    sales-biztools             15.00  2.15   60.9        2    7.5      1     0  [trust]
it5-outcome-nodemo-q  sales-biztools             15.00  1.03   54.5        1   15.0      0     1  [trust]
it5-outcome-demo-q    sales-biztools             15.00  0.47  100.0        0      —      0     0  [trust]
it5-story-nodemo-q    sales-niche                15.00  0.48   33.3        0      —      0     0  [trust]
it5-outcome-nodemo-q  sales-niche                15.00  0.16   50.0        0      —      0     0  [trust]
it5-outcome-demo-q    sales-niche                15.00  0.40   60.0        0      —      0     0  [trust]
SITE FUNNEL: visits 92 · sign-ups 4 · demos 1 · purchases 1
ITERATION 5 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 6 — policy v5 · sim world-12 seed 1206 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-outcome-q: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it6-story-q: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it6-count-q: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
CAMPAIGN CELLS: pv-broad($20,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($55,sales,broad,fixed), sales-biztools($60,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-outcome-q         pv-broad                    6.67  0.72  100.0        1   6.67      0     0  [trust]
it6-story-q           pv-broad                    6.67  0.48   75.0        0      —      0     0  [trust]
it6-count-q           pv-broad                    6.67  0.84   85.7        0      —      0     0  [trust]
it6-outcome-q         leads-broad                 6.67  1.20   40.0        2   3.33      0     0  [trust]
it6-story-q           leads-broad                 6.67  0.72   83.3        0      —      0     0  [trust]
it6-count-q           leads-broad                 6.67  1.32   72.7        3   2.22      0     0  [trust]
it6-outcome-q         sales-broad                18.33  0.52   75.0        2   9.16      0     0  [trust]
it6-story-q           sales-broad                18.33  1.57   66.7        2   9.16      1     1  [trust]
it6-count-q           sales-broad                18.33  0.83   57.9        1  18.33      0     0  [trust]
it6-outcome-q         sales-biztools             20.00  0.63   66.7        0      —      0     0  [trust]
it6-story-q           sales-biztools             20.00  0.42   66.7        0      —      0     0  [trust]
it6-count-q           sales-biztools             20.00  0.28   50.0        0      —      0     0  [trust]
it6-outcome-q         sales-niche                15.00  0.64   75.0        0      —      0     0  [trust]
it6-story-q           sales-niche                15.00  0.48   50.0        0      —      0     0  [trust]
it6-count-q           sales-niche                15.00  1.12   64.3        0      —      0     0  [trust]
SITE FUNNEL: visits 106 · sign-ups 11 · demos 1 · purchases 1
ITERATION 6 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 7 — policy v6 · sim world-12 seed 1207 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-outcome-q: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it7-story-q: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it7-benefit-q: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
CAMPAIGN CELLS: probe-pv-broad($15,pageviews,broad,fixed), sales-broad($85,sales,broad,fixed), sales-biztools($60,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-outcome-q         probe-pv-broad              5.00  0.64   75.0        0      —      0     0  [trust]
it7-story-q           probe-pv-broad              5.00  0.48   66.7        0      —      0     0  [trust]
it7-benefit-q         probe-pv-broad              5.00  0.32  100.0        0      —      0     0  [trust]
it7-outcome-q         sales-broad                28.33  0.31   63.6        2  14.16      1     0  [trust]
it7-story-q           sales-broad                28.33  0.56   55.0        1  28.33      0     0  [trust]
it7-benefit-q         sales-broad                28.33  0.25   88.9        0      —      0     0  [trust]
it7-outcome-q         sales-biztools             20.00  0.63   66.7        0      —      0     0  [trust]
it7-story-q           sales-biztools             20.00  1.26   61.1        1   20.0      0     1  [trust]
it7-benefit-q         sales-biztools             20.00  0.21  100.0        0      —      0     0  [trust]
it7-outcome-q         sales-niche                13.33  0.99   81.8        0      —      0     0  [trust]
it7-story-q           sales-niche                13.33  0.36   75.0        1  13.33      0     0  [trust]
it7-benefit-q         sales-niche                13.33  0.54   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 69 · sign-ups 5 · demos 1 · purchases 1
ITERATION 7 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 8 — policy v7 · sim world-12 seed 1208 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-q: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it8-story2-q: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
  it8-outcome-q: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo, card-required trial — stated plainly on screen and in voice
CAMPAIGN CELLS: probe-leads-broad($15,leads,broad,fixed), sales-broad($70,sales,broad,fixed), sales-biztools($75,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-q           probe-leads-broad           5.00  1.60   80.0        1    5.0      0     0  [trust]
it8-story2-q          probe-leads-broad           5.00  1.76   72.7        0      —      0     0  [trust]
it8-outcome-q         probe-leads-broad           5.00  1.12   42.9        0      —      0     0  [trust]
it8-story-q           sales-broad                23.33  0.99   48.3        2  11.66      2     1  [trust]
it8-story2-q          sales-broad                23.33  0.62   61.1        0      —      0     0  [trust]
it8-outcome-q         sales-broad                23.33  0.38   63.6        3   7.78      1     0  [trust]
it8-story-q           sales-biztools             25.00  0.90   75.0        0      —      0     0  [trust]
it8-story2-q          sales-biztools             25.00  0.90   62.5        3   8.33      1     0  [trust]
it8-outcome-q         sales-biztools             25.00  1.18   71.4        0      —      0     0  [trust]
it8-story-q           sales-niche                13.33  1.26   71.4        1  13.33      1     0  [trust]
it8-story2-q          sales-niche                13.33  0.99   54.5        0      —      0     0  [trust]
it8-outcome-q         sales-niche                13.33  0.18  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 106 · sign-ups 10 · demos 5 · purchases 1
ITERATION 8 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 29, "roas": 0.15}
