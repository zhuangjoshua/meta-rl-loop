
━━ ITERATION 1 — policy v0 · sim world-8 seed 801 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit - stop rebuilding the same inta
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-building tim
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($29.96,sales,broad,auto), sales-auto~interest_biztools($10.04,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.24   75.0        0      —      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.72   75.0        1  13.33      1     0  [trust]
it1-story             pv-broad                   13.33  1.50   72.0        1  13.33      0     0  [trust]
it1-benefit           leads-broad                13.33  0.24   50.0        1  13.33      1     0  [trust]
it1-outcome           leads-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it1-story             leads-broad                13.33  0.66   45.5        1  13.33      0     0  [trust]
it1-benefit           sales-broad                13.33  0.42   57.1        0      —      0     0  [trust]
it1-outcome           sales-broad                13.33  0.96   56.2        0      —      0     0  [trust]
it1-story             sales-broad                13.33  0.90   53.3        1  13.33      0     0  [trust]
it1-benefit           sales-biztools             13.33  1.05   90.0        0      —      0     0  [trust]
it1-outcome           sales-biztools             13.33  0.73   85.7        0      —      0     0  [trust]
it1-story             sales-biztools             13.33  1.05   40.0        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            9.99  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            9.99  0.80  100.0        1   9.99      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            9.99  0.48   33.3        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  3.35  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools  3.35  0.42  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools  3.35  0.84  100.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 95 · sign-ups 6 · demos 2 · purchases 0
ITERATION 1 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-8 seed 802 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): lead with adoption numbers - 4,200 teams build their intak
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): screen-recording demo of the product turning a messy doc i
  it2-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: clicks-broad($30,clicks,broad,fixed), sales-niche($35,sales,interest_niche,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($35,sales,interest_biztools,fixed), leads-broad($30,leads,broad,fixed), sales-auto~broad($8.24,sales,broad,auto), sales-auto~interest_biztools($21.76,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count             clicks-broad               10.00  0.96   66.7        2    5.0      1     0  [trust]
it2-outcome-demo      clicks-broad               10.00  1.60   70.0        3   3.33      1     0  [trust]
it2-story             clicks-broad               10.00  0.80   80.0        0      —      0     0  [trust]
it2-count             sales-niche                11.67  0.62   83.3        0      —      0     0  [trust]
it2-outcome-demo      sales-niche                11.67  1.03   60.0        2   5.83      1     0  [trust]
it2-story             sales-niche                11.67  0.41  100.0        2   5.83      1     0  [trust]
it2-count             sales-broad                13.33  1.02   70.6        2   6.67      0     0  [trust]
it2-outcome-demo      sales-broad                13.33  0.96   68.8        0      —      0     0  [trust]
it2-story             sales-broad                13.33  1.08   55.6        1  13.33      1     0  [trust]
it2-count             sales-biztools             11.67  0.84  100.0        1  11.67      0     0  [trust]
it2-outcome-demo      sales-biztools             11.67  0.48  100.0        1  11.67      1     0  [trust]
it2-story             sales-biztools             11.67  1.32   72.7        1  11.67      1     0  [trust]
it2-count             leads-broad                10.00  0.40   80.0        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                10.00  1.44   66.7        0      —      0     0  [trust]
it2-story             leads-broad                10.00  2.56   68.8        1   10.0      0     0  [trust]
it2-count             sales-auto~broad            2.75  0.29  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~broad            2.75  0.58  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~broad            2.75  0.87  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-count             sales-auto~interest_biztools  7.25  0.39  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  7.25  1.74   44.4        0      —      0     0  [NO-TRUST auto-window]
it2-story             sales-auto~interest_biztools  7.25  0.19  100.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 148 · sign-ups 16 · demos 7 · purchases 0
ITERATION 2 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-8 seed 803 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-building tim
  it3-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): screen-recording demo of the product turning a messy doc i
  it3-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-outcome           pv-broad                   10.00  1.12   42.9        0      —      0     0  [trust]
it3-outcome-demo      pv-broad                   10.00  0.80   40.0        1   10.0      0     0  [trust]
it3-story             pv-broad                   10.00  0.72   77.8        2    5.0      1     0  [trust]
it3-outcome           leads-broad                10.00  0.88   54.5        0      —      0     0  [trust]
it3-outcome-demo      leads-broad                10.00  0.80   60.0        1   10.0      0     0  [trust]
it3-story             leads-broad                10.00  0.32  100.0        0      —      0     0  [trust]
it3-outcome           sales-broad                16.67  0.91   78.9        1  16.67      1     1  [trust]
it3-outcome-demo      sales-broad                16.67  1.10   56.5        0      —      0     0  [trust]
it3-story             sales-broad                16.67  0.91   63.2        0      —      0     0  [trust]
it3-outcome           sales-biztools             15.00  0.47   80.0        0      —      0     0  [trust]
it3-outcome-demo      sales-biztools             15.00  0.75   62.5        0      —      0     0  [trust]
it3-story             sales-biztools             15.00  0.37   50.0        0      —      0     0  [trust]
it3-outcome           sales-niche                15.00  0.32   50.0        0      —      0     0  [trust]
it3-outcome-demo      sales-niche                15.00  0.72   66.7        0      —      0     0  [trust]
it3-story             sales-niche                15.00  0.96   83.3        1   15.0      0     0  [trust]
SITE FUNNEL: visits 102 · sign-ups 6 · demos 2 · purchases 1
ITERATION 3 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 4 — policy v3 · sim world-8 seed 804 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-building tim
  it4-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): screen-recording demo of the product turning a messy doc i
  it4-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($60,sales,broad,fixed), sales-biztools($45,sales,interest_biztools,fixed), sales-niche($45,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-outcome           pv-broad                    8.33  0.67   57.1        0      —      0     0  [trust]
it4-outcome-demo      pv-broad                    8.33  1.63   52.9        0      —      0     0  [trust]
it4-story             pv-broad                    8.33  0.58  100.0        0      —      0     0  [trust]
it4-outcome           leads-broad                 8.33  0.67   57.1        1   8.33      0     0  [trust]
it4-outcome-demo      leads-broad                 8.33  1.06   54.5        0      —      0     0  [trust]
it4-story             leads-broad                 8.33  0.77   50.0        0      —      0     0  [trust]
it4-outcome           sales-broad                20.00  0.56   71.4        1   20.0      0     0  [trust]
it4-outcome-demo      sales-broad                20.00  0.88   59.1        0      —      0     0  [trust]
it4-story             sales-broad                20.00  0.76   52.6        1   20.0      0     0  [trust]
it4-outcome           sales-biztools             15.00  0.75   87.5        1   15.0      1     0  [trust]
it4-outcome-demo      sales-biztools             15.00  1.12   66.7        2    7.5      0     0  [trust]
it4-story             sales-biztools             15.00  0.47   60.0        1   15.0      0     0  [trust]
it4-outcome           sales-niche                15.00  0.16  100.0        0      —      0     0  [trust]
it4-outcome-demo      sales-niche                15.00  0.80   60.0        0      —      0     0  [trust]
it4-story             sales-niche                15.00  0.32   75.0        0      —      0     0  [trust]
SITE FUNNEL: visits 95 · sign-ups 7 · demos 1 · purchases 0
ITERATION 4 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 · sim world-8 seed 805 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-building tim
  it5-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($75,sales,broad,fixed), sales-biztools($75,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-outcome           pv-broad                   12.50  0.64   50.0        0      —      0     0  [trust]
it5-story             pv-broad                   12.50  0.77   83.3        2   6.25      1     0  [trust]
it5-outcome           leads-broad                12.50  0.64   70.0        0      —      0     0  [trust]
it5-story             leads-broad                12.50  1.41   68.2        0      —      0     0  [trust]
it5-outcome           sales-broad                37.50  0.94   70.5        1   37.5      0     0  [trust]
it5-story             sales-broad                37.50  0.64   63.3        1   37.5      0     0  [trust]
it5-outcome           sales-biztools             37.50  0.49   69.2        1   37.5      0     0  [trust]
it5-story             sales-biztools             37.50  0.71   47.4        0      —      0     0  [trust]
SITE FUNNEL: visits 105 · sign-ups 5 · demos 1 · purchases 0
ITERATION 5 TOTALS: spend $200.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 200.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 · sim world-8 seed 806 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): concrete outcome first - a team ships its client intake fo
  it6-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($75,sales,broad,fixed), sales-biztools($75,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-outcome           pv-broad                   12.50  0.70   81.8        0      —      0     0  [trust]
it6-story             pv-broad                   12.50  0.26   75.0        0      —      0     0  [trust]
it6-outcome           leads-broad                12.50  0.64   50.0        0      —      0     0  [trust]
it6-story             leads-broad                12.50  1.28   65.0        0      —      0     0  [trust]
it6-outcome           sales-broad                37.50  1.17   81.8        5    7.5      1     2  [trust]
it6-story             sales-broad                37.50  0.43   75.0        1   37.5      0     0  [trust]
it6-outcome           sales-biztools             37.50  1.27   76.5        0      —      0     0  [trust]
it6-story             sales-biztools             37.50  2.65   63.4        5    7.5      1     1  [trust]
SITE FUNNEL: visits 161 · sign-ups 11 · demos 2 · purchases 3
ITERATION 6 TOTALS: spend $200.0 · settled revenue $87 · ROAS 0.43
@@SUMMARY {"iteration": 6, "spend": 200.0, "revenue": 87, "roas": 0.43}

━━ ITERATION 7 — policy v6 · sim world-8 seed 807 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo), written for a broad audience: concrete outcome first - you
  it7-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo), written for business-tools buyers: named customer story - 
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($75,sales,broad,fixed), sales-biztools($75,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-outcome           pv-broad                   12.50  0.70   72.7        0      —      0     0  [trust]
it7-story             pv-broad                   12.50  0.58   66.7        1   12.5      1     0  [trust]
it7-outcome           leads-broad                12.50  0.19   66.7        0      —      0     0  [trust]
it7-story             leads-broad                12.50  0.64   50.0        0      —      0     0  [trust]
it7-outcome           sales-broad                37.50  1.30   55.7        0      —      0     0  [trust]
it7-story             sales-broad                37.50  0.41   63.2        1   37.5      1     0  [trust]
it7-outcome           sales-biztools             37.50  0.63   88.2        1   37.5      0     0  [trust]
it7-story             sales-biztools             37.50  0.67   61.1        1   37.5      1     0  [trust]
SITE FUNNEL: visits 93 · sign-ups 4 · demos 3 · purchases 0
ITERATION 7 TOTALS: spend $200.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 200.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-8 seed 808 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): concrete outcome first - a team ships its client intake fo
  it8-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya R., ops lead at a 12-person ag
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($75,sales,broad,fixed), sales-biztools($75,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-outcome           pv-broad                   12.50  0.45   85.7        0      —      0     0  [trust]
it8-story             pv-broad                   12.50  1.34   81.0        0      —      0     0  [trust]
it8-outcome           leads-broad                12.50  1.09   58.8        1   12.5      1     0  [trust]
it8-story             leads-broad                12.50  0.90   85.7        2   6.25      1     0  [trust]
it8-outcome           sales-broad                37.50  1.88   63.6        8   4.69      2     1  [trust]
it8-story             sales-broad                37.50  0.32   93.3        0      —      0     0  [trust]
it8-outcome           sales-biztools             37.50  0.71   63.2        1   37.5      0     0  [trust]
it8-story             sales-biztools             37.50  1.72   52.2        4   9.38      0     0  [trust]
SITE FUNNEL: visits 151 · sign-ups 16 · demos 4 · purchases 1
ITERATION 8 TOTALS: spend $200.0 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 8, "spend": 200.0, "revenue": 29, "roas": 0.14}
