
━━ ITERATION 1 — policy v0 · sim world-10 seed 1001 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit — stop rebuilding the same form
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome — teams cut form build time f
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Maya, an ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($4.8,sales,broad,auto), sales-auto~interest_biztools($35.2,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.42   42.9        0      —      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.30   60.0        0      —      0     0  [trust]
it1-story             pv-broad                   13.33  0.96   56.2        0      —      0     0  [trust]
it1-benefit           leads-broad                13.33  0.60  100.0        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.66   90.9        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.36   66.7        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.06  100.0        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.78   69.2        0      —      0     0  [trust]
it1-story             sales-broad                13.33  0.42   85.7        3   4.44      1     0  [trust]
it1-benefit           sales-biztools             13.33  0.52   80.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.73   57.1        1  13.33      1     0  [trust]
it1-story             sales-biztools             13.33  0.32   33.3        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            1.60  1.00  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            1.60  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            1.60  0.50    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools 11.73  0.72   83.3        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools 11.73  0.72   33.3        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools 11.73  0.72   66.7        1  11.73      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 77 · sign-ups 6 · demos 2 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-10 seed 1002 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): lead with adoption numbers — 4,800 teams build their forms
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): screen-recording demo of the product working — build a bra
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Dev, founder of a bookkeeping start
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($35,sales,broad,fixed), sales-biztools($35,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($25.66,sales,broad,auto), sales-auto~interest_biztools($9.34,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count             pv-broad                    8.33  0.96   80.0        0      —      0     0  [trust]
it2-outcome-demo      pv-broad                    8.33  0.86   66.7        1   8.33      0     0  [trust]
it2-story             pv-broad                    8.33  1.25   69.2        1   8.33      1     0  [trust]
it2-count             leads-broad                10.00  0.40   60.0        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                10.00  0.80   90.0        0      —      0     0  [trust]
it2-story             leads-broad                10.00  0.48   50.0        0      —      0     0  [trust]
it2-count             sales-broad                11.67  0.48   85.7        0      —      0     0  [trust]
it2-outcome-demo      sales-broad                11.67  1.71   60.0        1  11.67      0     0  [trust]
it2-story             sales-broad                11.67  1.10   62.5        0      —      0     0  [trust]
it2-count             sales-biztools             11.67  0.48   75.0        0      —      0     0  [trust]
it2-outcome-demo      sales-biztools             11.67  0.60   60.0        0      —      0     0  [trust]
it2-story             sales-biztools             11.67  0.84   57.1        0      —      0     0  [trust]
it2-count             sales-niche                13.33  0.45   60.0        0      —      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  0.09  100.0        0      —      0     0  [trust]
it2-story             sales-niche                13.33  0.99   45.5        1  13.33      0     0  [trust]
it2-count             sales-auto~broad            8.55  1.40   53.3        1   8.55      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad            8.55  0.56   33.3        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~broad            8.55  1.22   69.2        1   8.55      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  3.11  0.45    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  3.11  1.80   75.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  3.11  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 110 · sign-ups 6 · demos 1 · purchases 0
ITERATION 2 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-10 seed 1003 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Priya, operations manager at a 30-p
  it3-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with live screen-recording demo — Pri
  it3-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): screen-recording demo of the product working — build a bra
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story             pv-broad                   10.00  0.48   83.3        0      —      0     0  [trust]
it3-story-demo        pv-broad                   10.00  0.96   41.7        0      —      0     0  [trust]
it3-outcome-demo      pv-broad                   10.00  1.20   53.3        0      —      0     0  [trust]
it3-story             leads-broad                10.00  0.56   71.4        0      —      0     0  [trust]
it3-story-demo        leads-broad                10.00  0.56  100.0        1   10.0      0     0  [trust]
it3-outcome-demo      leads-broad                10.00  1.20   66.7        1   10.0      0     0  [trust]
it3-story             sales-broad                16.67  0.62   69.2        2   8.34      2     0  [trust]
it3-story-demo        sales-broad                16.67  1.10   69.6        3   5.56      0     0  [trust]
it3-outcome-demo      sales-broad                16.67  0.48   40.0        0      —      0     0  [trust]
it3-story             sales-biztools             15.00  1.12   83.3        0      —      0     0  [trust]
it3-story-demo        sales-biztools             15.00  0.37   75.0        1   15.0      0     0  [trust]
it3-outcome-demo      sales-biztools             15.00  0.47   80.0        0      —      0     0  [trust]
it3-story             sales-niche                15.00  1.04   69.2        1   15.0      1     0  [trust]
it3-story-demo        sales-niche                15.00  0.48   66.7        0      —      0     0  [trust]
it3-outcome-demo      sales-niche                15.00  0.48   33.3        0      —      0     0  [trust]
SITE FUNNEL: visits 101 · sign-ups 9 · demos 3 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-10 seed 1004 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-plain: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Marcus, office manager at a dental 
  it4-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Marcus, 
  it4-story2-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Lena, ev
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-plain       pv-broad                   10.00  0.72   44.4        2    5.0      0     0  [trust]
it4-story-demo        pv-broad                   10.00  0.64   62.5        0      —      0     0  [trust]
it4-story2-demo       pv-broad                   10.00  0.80   50.0        0      —      0     0  [trust]
it4-story-plain       leads-broad                10.00  0.96   75.0        0      —      0     0  [trust]
it4-story-demo        leads-broad                10.00  1.12   78.6        2    5.0      0     1  [trust]
it4-story2-demo       leads-broad                10.00  0.32   25.0        0      —      0     0  [trust]
it4-story-plain       sales-broad                16.67  1.49   80.6        2   8.34      1     0  [trust]
it4-story-demo        sales-broad                16.67  0.38   87.5        0      —      0     0  [trust]
it4-story2-demo       sales-broad                16.67  0.53   63.6        0      —      0     0  [trust]
it4-story-plain       sales-biztools             15.00  1.03   63.6        0      —      0     0  [trust]
it4-story-demo        sales-biztools             15.00  1.40   80.0        0      —      0     0  [trust]
it4-story2-demo       sales-biztools             15.00  0.75   50.0        0      —      0     0  [trust]
it4-story-plain       sales-niche                15.00  0.64   62.5        0      —      0     0  [trust]
it4-story-demo        sales-niche                15.00  0.48   66.7        0      —      0     0  [trust]
it4-story2-demo       sales-niche                15.00  0.56   71.4        1   15.0      1     0  [trust]
SITE FUNNEL: visits 111 · sign-ups 7 · demos 2 · purchases 1
ITERATION 4 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 5 — policy v4 · sim world-10 seed 1005 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-marcus-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Marcus, 
  it5-ana-plain: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — Ana, who runs client services at a 
  it5-ana-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Ana, who
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($45,leads,broad,fixed), sales-broad($65,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-marcus-demo       pv-broad                   10.00  0.56   71.4        1   10.0      0     0  [trust]
it5-ana-plain         pv-broad                   10.00  1.52   73.7        0      —      0     0  [trust]
it5-ana-demo          pv-broad                   10.00  0.64   75.0        0      —      0     0  [trust]
it5-marcus-demo       leads-broad                15.00  1.33   72.0        1   15.0      0     0  [trust]
it5-ana-plain         leads-broad                15.00  0.37   85.7        0      —      0     0  [trust]
it5-ana-demo          leads-broad                15.00  1.01   63.2        1   15.0      0     0  [trust]
it5-marcus-demo       sales-broad                21.67  0.89   62.5        3   7.22      3     3  [trust]
it5-ana-plain         sales-broad                21.67  1.44   53.8        1  21.67      0     0  [trust]
it5-ana-demo          sales-broad                21.67  1.11   70.0        2  10.84      0     1  [trust]
it5-marcus-demo       sales-biztools             10.00  0.56   50.0        0      —      0     0  [trust]
it5-ana-plain         sales-biztools             10.00  0.70   40.0        0      —      0     0  [trust]
it5-ana-demo          sales-biztools             10.00  0.42   66.7        0      —      0     0  [trust]
it5-marcus-demo       sales-niche                10.00  0.48   25.0        0      —      0     0  [trust]
it5-ana-plain         sales-niche                10.00  0.48  100.0        1   10.0      1     0  [trust]
it5-ana-demo          sales-niche                10.00  0.60   80.0        1   10.0      1     0  [trust]
SITE FUNNEL: visits 133 · sign-ups 11 · demos 5 · purchases 4
ITERATION 5 TOTALS: spend $200.01 · settled revenue $116 · ROAS 0.58
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 116, "roas": 0.58}

━━ ITERATION 6 — policy v5 · sim world-10 seed 1006 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-marcus-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Marcus, 
  it6-ana-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Ana, who
  it6-tom-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Tom, HR 
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($45,leads,broad,fixed), sales-broad($65,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-marcus-demo       pv-broad                   10.00  0.72   66.7        0      —      0     0  [trust]
it6-ana-demo          pv-broad                   10.00  0.40   60.0        0      —      0     0  [trust]
it6-tom-demo          pv-broad                   10.00  1.04   69.2        1   10.0      0     0  [trust]
it6-marcus-demo       leads-broad                15.00  0.91   58.8        2    7.5      0     0  [trust]
it6-ana-demo          leads-broad                15.00  1.07   60.0        1   15.0      0     1  [trust]
it6-tom-demo          leads-broad                15.00  0.32   66.7        0      —      0     0  [trust]
it6-marcus-demo       sales-broad                21.67  0.81   59.1        1  21.67      0     0  [trust]
it6-ana-demo          sales-broad                21.67  1.00   77.8        1  21.67      0     0  [trust]
it6-tom-demo          sales-broad                21.67  2.07   64.3        2  10.84      1     1  [trust]
it6-marcus-demo       sales-biztools             10.00  0.84   66.7        0      —      0     0  [trust]
it6-ana-demo          sales-biztools             10.00  1.26  100.0        1   10.0      1     0  [trust]
it6-tom-demo          sales-biztools             10.00  0.84   66.7        0      —      0     0  [trust]
it6-marcus-demo       sales-niche                10.00  1.20   80.0        1   10.0      0     0  [trust]
it6-ana-demo          sales-niche                10.00  0.60   40.0        0      —      0     0  [trust]
it6-tom-demo          sales-niche                10.00  0.36   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 143 · sign-ups 10 · demos 2 · purchases 2
ITERATION 6 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 58, "roas": 0.29}

━━ ITERATION 7 — policy v6 · sim world-10 seed 1007 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-ana-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Ana, who
  it7-tom-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Tom, HR 
  it7-rosa-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Rosa, wh
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($70,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-ana-demo          pv-broad                   10.00  1.04   76.9        0      —      0     0  [trust]
it7-tom-demo          pv-broad                   10.00  0.48   83.3        1   10.0      1     0  [trust]
it7-rosa-demo         pv-broad                   10.00  0.80   60.0        2    5.0      0     0  [trust]
it7-ana-demo          leads-broad                13.33  0.72   50.0        0      —      0     0  [trust]
it7-tom-demo          leads-broad                13.33  0.42   42.9        0      —      0     0  [trust]
it7-rosa-demo         leads-broad                13.33  1.50   52.0        1  13.33      1     1  [trust]
it7-ana-demo          sales-broad                23.33  0.72   76.2        2  11.66      1     1  [trust]
it7-tom-demo          sales-broad                23.33  0.65   84.2        0      —      0     0  [trust]
it7-rosa-demo         sales-broad                23.33  0.62   72.2        0      —      0     0  [trust]
it7-ana-demo          sales-biztools             10.00  0.70   80.0        0      —      0     0  [trust]
it7-tom-demo          sales-biztools             10.00  2.52   77.8        1   10.0      0     0  [trust]
it7-rosa-demo         sales-biztools             10.00  1.54   72.7        0      —      0     0  [trust]
it7-ana-demo          sales-niche                10.00  0.84   42.9        1   10.0      0     0  [trust]
it7-tom-demo          sales-niche                10.00  0.96  100.0        0      —      0     0  [trust]
it7-rosa-demo         sales-niche                10.00  1.56   84.6        0      —      0     0  [trust]
SITE FUNNEL: visits 136 · sign-ups 8 · demos 3 · purchases 2
ITERATION 7 TOTALS: spend $199.98 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 58, "roas": 0.29}

━━ ITERATION 8 — policy v7 · sim world-10 seed 1008 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-ana-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Ana, who
  it8-rosa-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Rosa, wh
  it8-ben-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story with screen-recording demo — Ben, own
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($70,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($30,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-ana-demo          pv-broad                   10.00  0.64   87.5        1   10.0      0     0  [trust]
it8-rosa-demo         pv-broad                   10.00  0.80   80.0        1   10.0      0     0  [trust]
it8-ben-demo          pv-broad                   10.00  0.72   66.7        0      —      0     0  [trust]
it8-ana-demo          leads-broad                13.33  0.48   75.0        0      —      0     0  [trust]
it8-rosa-demo         leads-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it8-ben-demo          leads-broad                13.33  0.42  100.0        0      —      0     0  [trust]
it8-ana-demo          sales-broad                23.33  0.65   73.7        0      —      0     0  [trust]
it8-rosa-demo         sales-broad                23.33  0.55   81.2        2  11.66      1     0  [trust]
it8-ben-demo          sales-broad                23.33  0.99   62.1        2  11.66      1     0  [trust]
it8-ana-demo          sales-biztools             10.00  0.42   33.3        0      —      0     0  [trust]
it8-rosa-demo         sales-biztools             10.00  0.70   40.0        0      —      0     0  [trust]
it8-ben-demo          sales-biztools             10.00  0.70   40.0        1   10.0      0     0  [trust]
it8-ana-demo          sales-niche                10.00  0.24   50.0        0      —      0     0  [trust]
it8-rosa-demo         sales-niche                10.00  1.80   60.0        0      —      0     0  [trust]
it8-ben-demo          sales-niche                10.00  0.96   62.5        1   10.0      1     0  [trust]
SITE FUNNEL: visits 102 · sign-ups 8 · demos 3 · purchases 0
ITERATION 8 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 0, "roas": 0.0}
