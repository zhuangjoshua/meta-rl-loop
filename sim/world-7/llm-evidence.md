
━━ ITERATION 1 — policy v0 · sim world-7 seed 701 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit - stop rebuilding the same inta
  it1-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-handling tim
  it1-story: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story - Maya Chen, ops lead at a 12-person 
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($5.83,sales,broad,auto), sales-auto~interest_biztools($34.17,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.42   85.7        0      —      0     0  [trust]
it1-outcome           pv-broad                   13.33  0.48   50.0        0      —      0     0  [trust]
it1-story             pv-broad                   13.33  1.44   58.3        0      —      0     0  [trust]
it1-benefit           leads-broad                13.33  0.78   76.9        0      —      0     0  [trust]
it1-outcome           leads-broad                13.33  0.72   83.3        1  13.33      0     0  [trust]
it1-story             leads-broad                13.33  0.66   72.7        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.78   84.6        1  13.33      0     0  [trust]
it1-outcome           sales-broad                13.33  1.56   53.8        1  13.33      1     0  [trust]
it1-story             sales-broad                13.33  0.60   40.0        1  13.33      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.42  100.0        1  13.33      0     0  [trust]
it1-outcome           sales-biztools             13.33  1.99   63.2        1  13.33      1     0  [trust]
it1-story             sales-biztools             13.33  0.52   80.0        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            1.94  0.41  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~broad            1.94  0.82   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~broad            1.94  1.65   50.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools 11.39  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome           sales-auto~interest_biztools 11.39  1.23   60.0        0      —      0     0  [NO-TRUST auto-window]
it1-story             sales-auto~interest_biztools 11.39  0.74   16.7        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 112 · sign-ups 6 · demos 2 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-7 seed 702 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): lead with adoption numbers - 4,200 teams now route their i
  it2-outcome: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): lead with a concrete outcome - teams cut form-handling tim
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): same concrete outcome lead - teams cut form-handling time 
CAMPAIGN CELLS: pv-broad($28,pageviews,broad,fixed), leads-broad($28,leads,broad,fixed), clicks-broad($24,clicks,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-count             pv-broad                    9.33  0.51   50.0        0      —      0     0  [trust]
it2-outcome           pv-broad                    9.33  0.77   55.6        0      —      0     0  [trust]
it2-outcome-demo      pv-broad                    9.33  1.29   66.7        1   9.33      1     0  [trust]
it2-count             leads-broad                 9.33  1.11   61.5        0      —      0     0  [trust]
it2-outcome           leads-broad                 9.33  0.34   75.0        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                 9.33  0.51   66.7        1   9.33      0     0  [trust]
it2-count             clicks-broad                8.00  0.40   75.0        0      —      0     0  [trust]
it2-outcome           clicks-broad                8.00  0.90   66.7        0      —      0     0  [trust]
it2-outcome-demo      clicks-broad                8.00  0.80   50.0        0      —      0     0  [trust]
it2-count             sales-broad                13.33  0.30   80.0        0      —      0     0  [trust]
it2-outcome           sales-broad                13.33  0.66   54.5        0      —      0     0  [trust]
it2-outcome-demo      sales-broad                13.33  0.54   88.9        0      —      0     0  [trust]
it2-count             sales-biztools             13.33  0.42   50.0        1  13.33      1     0  [trust]
it2-outcome           sales-biztools             13.33  0.73   71.4        1  13.33      1     0  [trust]
it2-outcome-demo      sales-biztools             13.33  0.95   55.6        0      —      0     0  [trust]
it2-count             sales-niche                13.33  0.18  100.0        0      —      0     0  [trust]
it2-outcome           sales-niche                13.33  0.81   55.6        1  13.33      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  0.99   81.8        2   6.67      1     0  [trust]
SITE FUNNEL: visits 92 · sign-ups 7 · demos 4 · purchases 0
ITERATION 2 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 · sim world-7 seed 703 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): concrete outcome lead - teams cut form-handling time from 
  it3-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story - Maya Chen, ops lead at a 12-person 
  it3-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead - stop rebuilding the same intake forms; ever
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($30,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($50,sales,interest_niche,fixed), sales-auto~broad($33.9,sales,broad,auto), sales-auto~interest_biztools($6.1,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-outcome-demo      pv-broad                    8.33  1.15   58.3        1   8.33      1     0  [trust]
it3-story-demo        pv-broad                    8.33  0.86   66.7        0      —      0     0  [trust]
it3-benefit-demo      pv-broad                    8.33  0.10  100.0        0      —      0     0  [trust]
it3-outcome-demo      leads-broad                 8.33  0.00    0.0        0      —      0     0  [trust]
it3-story-demo        leads-broad                 8.33  0.67   57.1        0      —      0     0  [trust]
it3-benefit-demo      leads-broad                 8.33  0.48   80.0        0      —      0     0  [trust]
it3-outcome-demo      sales-broad                10.00  0.32   75.0        0      —      0     0  [trust]
it3-story-demo        sales-broad                10.00  0.48   83.3        0      —      0     0  [trust]
it3-benefit-demo      sales-broad                10.00  0.16  100.0        0      —      0     0  [trust]
it3-outcome-demo      sales-biztools             10.00  0.42   33.3        0      —      0     0  [trust]
it3-story-demo        sales-biztools             10.00  2.24   68.8        0      —      0     0  [trust]
it3-benefit-demo      sales-biztools             10.00  0.42   66.7        0      —      0     0  [trust]
it3-outcome-demo      sales-niche                16.67  0.79   36.4        1  16.67      1     1  [trust]
it3-story-demo        sales-niche                16.67  0.50   71.4        0      —      0     0  [trust]
it3-benefit-demo      sales-niche                16.67  0.29   75.0        1  16.67      0     0  [trust]
it3-outcome-demo      sales-auto~broad           11.30  0.35   80.0        0      —      0     0  [NO-TRUST auto-window]
it3-story-demo        sales-auto~broad           11.30  1.27   66.7        2   5.65      1     0  [NO-TRUST auto-window]
it3-benefit-demo      sales-auto~broad           11.30  0.14  100.0        0      —      0     0  [NO-TRUST auto-window]
it3-outcome-demo      sales-auto~interest_biztools  2.03  0.69    0.0        0      —      0     0  [NO-TRUST auto-window]
it3-story-demo        sales-auto~interest_biztools  2.03  1.38   50.0        0      —      0     0  [NO-TRUST auto-window]
it3-benefit-demo      sales-auto~interest_biztools  2.03  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 77 · sign-ups 5 · demos 3 · purchases 1
ITERATION 3 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 3, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 4 — policy v3 · sim world-7 seed 704 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): concrete outcome lead - teams cut form-handling time from 
  it4-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story - Maya Chen, ops lead at a 12-person 
  it4-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead - stop rebuilding the same intake forms; ever
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($70,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-outcome-demo      pv-broad                    8.33  1.44   60.0        1   8.33      0     0  [trust]
it4-story-demo        pv-broad                    8.33  1.15   75.0        1   8.33      0     0  [trust]
it4-benefit-demo      pv-broad                    8.33  0.19  100.0        0      —      0     0  [trust]
it4-outcome-demo      leads-broad                 8.33  0.77   75.0        0      —      0     0  [trust]
it4-story-demo        leads-broad                 8.33  0.58   66.7        1   8.33      0     0  [trust]
it4-benefit-demo      leads-broad                 8.33  0.77   87.5        0      —      0     0  [trust]
it4-outcome-demo      sales-broad                13.33  0.18  100.0        0      —      0     0  [trust]
it4-story-demo        sales-broad                13.33  0.84   71.4        2   6.67      0     0  [trust]
it4-benefit-demo      sales-broad                13.33  0.24   75.0        0      —      0     0  [trust]
it4-outcome-demo      sales-biztools             13.33  0.42   75.0        2   6.67      0     0  [trust]
it4-story-demo        sales-biztools             13.33  2.94   64.3        2   6.67      0     1  [trust]
it4-benefit-demo      sales-biztools             13.33  0.10  100.0        0      —      0     0  [trust]
it4-outcome-demo      sales-niche                23.33  0.87   70.6        3   7.78      3     0  [trust]
it4-story-demo        sales-niche                23.33  0.98   47.4        2  11.66      1     0  [trust]
it4-benefit-demo      sales-niche                23.33  0.51   70.0        1  23.33      1     0  [trust]
SITE FUNNEL: visits 103 · sign-ups 15 · demos 5 · purchases 1
ITERATION 4 TOTALS: spend $199.95 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 4, "spend": 199.95, "revenue": 29, "roas": 0.15}

━━ ITERATION 5 — policy v4 · sim world-7 seed 705 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): concrete outcome lead - teams cut form-handling time from 
  it5-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story - Maya Chen, ops lead at a 12-person 
  it5-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo): adoption-numbers lead - 4,200 teams route their intake for
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($25,sales,broad,fixed), sales-biztools($60,sales,interest_biztools,fixed), sales-niche($65,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-outcome-demo      pv-broad                    8.33  0.29   33.3        0      —      0     0  [trust]
it5-story-demo        pv-broad                    8.33  0.86   55.6        1   8.33      0     0  [trust]
it5-count-demo        pv-broad                    8.33  1.06   54.5        2   4.17      0     1  [trust]
it5-outcome-demo      leads-broad                 8.33  0.48   80.0        0      —      0     0  [trust]
it5-story-demo        leads-broad                 8.33  0.58   83.3        0      —      0     0  [trust]
it5-count-demo        leads-broad                 8.33  0.58   50.0        1   8.33      0     0  [trust]
it5-outcome-demo      sales-broad                 8.33  0.86   44.4        0      —      0     0  [trust]
it5-story-demo        sales-broad                 8.33  1.06   63.6        0      —      0     0  [trust]
it5-count-demo        sales-broad                 8.33  0.58   66.7        0      —      0     0  [trust]
it5-outcome-demo      sales-biztools             20.00  0.98   85.7        1   20.0      0     0  [trust]
it5-story-demo        sales-biztools             20.00  1.05   60.0        1   20.0      0     0  [trust]
it5-count-demo        sales-biztools             20.00  1.19   64.7        1   20.0      0     0  [trust]
it5-outcome-demo      sales-niche                21.67  0.28   60.0        0      —      0     0  [trust]
it5-story-demo        sales-niche                21.67  0.78   57.1        0      —      0     0  [trust]
it5-count-demo        sales-niche                21.67  0.44   75.0        0      —      0     0  [trust]
SITE FUNNEL: visits 88 · sign-ups 7 · demos 0 · purchases 1
ITERATION 5 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 6 — policy v5 · sim world-7 seed 706 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead - stop rebuilding the same intake forms; ever
  it6-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo): adoption-numbers lead - 4,200 teams route their intake for
  it6-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story - Maya Chen, ops lead at a 12-person 
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($25,sales,broad,fixed), sales-biztools($65,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-benefit-demo      pv-broad                    8.33  0.38   50.0        0      —      0     0  [trust]
it6-count-demo        pv-broad                    8.33  0.19  100.0        0      —      0     0  [trust]
it6-story-demo        pv-broad                    8.33  1.25   69.2        0      —      0     0  [trust]
it6-benefit-demo      leads-broad                 8.33  0.29   66.7        0      —      0     0  [trust]
it6-count-demo        leads-broad                 8.33  0.29   66.7        1   8.33      0     0  [trust]
it6-story-demo        leads-broad                 8.33  0.77   50.0        0      —      0     0  [trust]
it6-benefit-demo      sales-broad                 8.33  0.38   75.0        0      —      0     0  [trust]
it6-count-demo        sales-broad                 8.33  1.44   60.0        1   8.33      0     0  [trust]
it6-story-demo        sales-broad                 8.33  0.48  100.0        0      —      0     0  [trust]
it6-benefit-demo      sales-biztools             21.67  0.13  100.0        0      —      0     0  [trust]
it6-count-demo        sales-biztools             21.67  0.45   28.6        0      —      0     0  [trust]
it6-story-demo        sales-biztools             21.67  0.39   50.0        0      —      0     0  [trust]
it6-benefit-demo      sales-niche                20.00  0.42   71.4        1   20.0      0     0  [trust]
it6-count-demo        sales-niche                20.00  0.96   68.8        0      —      0     0  [trust]
it6-story-demo        sales-niche                20.00  0.48   75.0        2   10.0      2     0  [trust]
SITE FUNNEL: visits 67 · sign-ups 5 · demos 2 · purchases 0
ITERATION 6 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-7 seed 707 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): concrete outcome lead - teams cut form-handling time from 
  it7-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo): adoption-numbers lead - 4,200 teams route their intake for
  it7-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit lead - stop rebuilding the same intake forms; ever
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($25,sales,broad,fixed), sales-biztools($55,sales,interest_biztools,fixed), sales-niche($70,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-outcome-demo      pv-broad                    8.33  0.86   66.7        0      —      0     0  [trust]
it7-count-demo        pv-broad                    8.33  0.00    0.0        0      —      0     0  [trust]
it7-benefit-demo      pv-broad                    8.33  1.82   78.9        1   8.33      0     0  [trust]
it7-outcome-demo      leads-broad                 8.33  0.19  100.0        0      —      0     0  [trust]
it7-count-demo        leads-broad                 8.33  0.67  100.0        0      —      0     0  [trust]
it7-benefit-demo      leads-broad                 8.33  0.38   50.0        0      —      0     0  [trust]
it7-outcome-demo      sales-broad                 8.33  1.25   61.5        0      —      0     0  [trust]
it7-count-demo        sales-broad                 8.33  0.29   66.7        0      —      0     0  [trust]
it7-benefit-demo      sales-broad                 8.33  0.38   50.0        0      —      0     0  [trust]
it7-outcome-demo      sales-biztools             18.33  0.69   88.9        2   9.16      0     0  [trust]
it7-count-demo        sales-biztools             18.33  0.38  100.0        0      —      0     0  [trust]
it7-benefit-demo      sales-biztools             18.33  0.53  100.0        2   9.16      1     0  [trust]
it7-outcome-demo      sales-niche                23.33  0.87   70.6        1  23.33      1     0  [trust]
it7-count-demo        sales-niche                23.33  1.13   50.0        0      —      0     0  [trust]
it7-benefit-demo      sales-niche                23.33  0.26  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 92 · sign-ups 6 · demos 2 · purchases 0
ITERATION 7 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 · sim world-7 seed 708 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story - Maya Chen, ops lead at a 12-person 
  it8-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): concrete outcome lead - teams cut form-handling time from 
  it8-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo): adoption-numbers lead - 4,200 teams route their intake for
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($25,leads,broad,fixed), sales-broad($25,sales,broad,fixed), sales-biztools($50,sales,interest_biztools,fixed), sales-niche($75,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-story-demo        pv-broad                    8.33  0.10  100.0        1   8.33      0     0  [trust]
it8-outcome-demo      pv-broad                    8.33  0.58   50.0        0      —      0     0  [trust]
it8-count-demo        pv-broad                    8.33  0.29   66.7        0      —      0     0  [trust]
it8-story-demo        leads-broad                 8.33  0.19   50.0        0      —      0     0  [trust]
it8-outcome-demo      leads-broad                 8.33  0.67   71.4        0      —      0     0  [trust]
it8-count-demo        leads-broad                 8.33  0.19  100.0        0      —      0     0  [trust]
it8-story-demo        sales-broad                 8.33  0.67   42.9        0      —      0     0  [trust]
it8-outcome-demo      sales-broad                 8.33  0.67   57.1        0      —      0     0  [trust]
it8-count-demo        sales-broad                 8.33  1.34   64.3        0      —      0     0  [trust]
it8-story-demo        sales-biztools             16.67  0.67   87.5        1  16.67      0     0  [trust]
it8-outcome-demo      sales-biztools             16.67  2.18   46.2        3   5.56      0     0  [trust]
it8-count-demo        sales-biztools             16.67  1.43   58.8        0      —      0     0  [trust]
it8-story-demo        sales-niche                25.00  0.38   37.5        0      —      0     0  [trust]
it8-outcome-demo      sales-niche                25.00  1.20   72.0        4   6.25      1     0  [trust]
it8-count-demo        sales-niche                25.00  0.48   70.0        1   25.0      0     0  [trust]
SITE FUNNEL: visits 87 · sign-ups 10 · demos 1 · purchases 0
ITERATION 8 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 199.98, "revenue": 0, "roas": 0.0}
