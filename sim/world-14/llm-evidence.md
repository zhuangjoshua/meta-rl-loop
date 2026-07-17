
━━ ITERATION 1 — policy v0 · sim world-14 seed 1401 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it1-benefit: proof=benefit named_story=False demo=False | Video ad for Formflow ($29/mo): lead with the core benefit — stop rebuilding the same inta
  it1-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): outcome-led — 'Teams cut form-processing time from hours t
  it1-count: proof=count named_story=False demo=False | Video ad for Formflow ($29/mo): adoption-count-led — 'Over 4,000 teams already run their i
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($29.52,sales,broad,auto), sales-auto~interest_biztools($10.48,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it1-benefit           pv-broad                   13.33  0.48  100.0        1  13.33      0     0  [trust]
it1-outcome-demo      pv-broad                   13.33  0.84   85.7        2   6.67      1     0  [trust]
it1-count             pv-broad                   13.33  0.60   70.0        1  13.33      1     0  [trust]
it1-benefit           leads-broad                13.33  0.30  100.0        0      —      0     0  [trust]
it1-outcome-demo      leads-broad                13.33  0.66   63.6        1  13.33      1     0  [trust]
it1-count             leads-broad                13.33  0.48   75.0        0      —      0     0  [trust]
it1-benefit           sales-broad                13.33  0.18  100.0        0      —      0     0  [trust]
it1-outcome-demo      sales-broad                13.33  0.90   60.0        0      —      0     0  [trust]
it1-count             sales-broad                13.33  0.66   54.5        1  13.33      0     0  [trust]
it1-benefit           sales-biztools             13.33  0.32  100.0        1  13.33      0     0  [trust]
it1-outcome-demo      sales-biztools             13.33  0.10  100.0        0      —      0     0  [trust]
it1-count             sales-biztools             13.33  0.95   66.7        0      —      0     0  [trust]
it1-benefit           sales-auto~broad            9.84  0.73   77.8        1   9.84      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~broad            9.84  0.41  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-count             sales-auto~broad            9.84  0.33   75.0        0      —      0     0  [NO-TRUST auto-window]
it1-benefit           sales-auto~interest_biztools  3.49  0.40  100.0        0      —      0     0  [NO-TRUST auto-window]
it1-outcome-demo      sales-auto~interest_biztools  3.49  0.40    0.0        0      —      0     0  [NO-TRUST auto-window]
it1-count             sales-auto~interest_biztools  3.49  0.80    0.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 89 · sign-ups 8 · demos 3 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-14 seed 1402 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it2-outcome-demo: proof=outcome named_story=False demo=True | Video ad for Formflow ($29/mo): outcome-led — 'Teams cut form-processing time from hours t
  it2-outcome-nodemo: proof=outcome named_story=False demo=False | Video ad for Formflow ($29/mo): outcome-led — 'Teams cut form-processing time from hours t
  it2-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story — 'Maya Chen, ops lead at Harbor Clin
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed), sales-auto~broad($4.42,sales,broad,auto), sales-auto~interest_biztools($25.58,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it2-outcome-demo      pv-broad                   10.00  0.48   50.0        0      —      0     0  [trust]
it2-outcome-nodemo    pv-broad                   10.00  0.16  100.0        0      —      0     0  [trust]
it2-story-demo        pv-broad                   10.00  1.60   80.0        0      —      0     0  [trust]
it2-outcome-demo      leads-broad                10.00  1.12   42.9        0      —      0     0  [trust]
it2-outcome-nodemo    leads-broad                10.00  0.56   57.1        1   10.0      0     0  [trust]
it2-story-demo        leads-broad                10.00  1.76   54.5        1   10.0      0     0  [trust]
it2-outcome-demo      sales-broad                13.33  0.66   81.8        0      —      0     0  [trust]
it2-outcome-nodemo    sales-broad                13.33  0.54   66.7        1  13.33      0     0  [trust]
it2-story-demo        sales-broad                13.33  0.84   64.3        3   4.44      1     1  [trust]
it2-outcome-demo      sales-biztools             10.00  0.28   50.0        0      —      0     0  [trust]
it2-outcome-nodemo    sales-biztools             10.00  0.00    0.0        0      —      0     0  [trust]
it2-story-demo        sales-biztools             10.00  0.56  100.0        1   10.0      0     0  [trust]
it2-outcome-demo      sales-niche                13.33  1.26   78.6        2   6.67      0     0  [trust]
it2-outcome-nodemo    sales-niche                13.33  0.36   75.0        0      —      0     0  [trust]
it2-story-demo        sales-niche                13.33  0.99   63.6        1  13.33      0     0  [trust]
it2-outcome-demo      sales-auto~broad            1.47  1.09  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-nodemo    sales-auto~broad            1.47  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~broad            1.47  2.71  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-demo      sales-auto~interest_biztools  8.53  0.49    0.0        0      —      0     0  [NO-TRUST auto-window]
it2-outcome-nodemo    sales-auto~interest_biztools  8.53  0.33  100.0        0      —      0     0  [NO-TRUST auto-window]
it2-story-demo        sales-auto~interest_biztools  8.53  0.99   50.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 105 · sign-ups 10 · demos 1 · purchases 1
ITERATION 2 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 2, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 3 — policy v2 · sim world-14 seed 1403 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it3-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story — 'Maya Chen, ops lead at Harbor Clin
  it3-story-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Maya Chen, ops lead at Harbor Clin
  it3-benefit-demo: proof=benefit named_story=False demo=True | Video ad for Formflow ($29/mo): benefit-led — stop rebuilding the same intake forms; Formf
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($50,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it3-story-demo        pv-broad                   10.00  0.56   71.4        0      —      0     0  [trust]
it3-story-nodemo      pv-broad                   10.00  0.64   50.0        1   10.0      0     0  [trust]
it3-benefit-demo      pv-broad                   10.00  0.72  100.0        1   10.0      1     0  [trust]
it3-story-demo        leads-broad                10.00  1.36   52.9        1   10.0      0     0  [trust]
it3-story-nodemo      leads-broad                10.00  1.28   68.8        0      —      0     0  [trust]
it3-benefit-demo      leads-broad                10.00  0.32   75.0        0      —      0     0  [trust]
it3-story-demo        sales-broad                16.67  1.25   69.2        0      —      0     0  [trust]
it3-story-nodemo      sales-broad                16.67  0.53   54.5        1  16.67      0     0  [trust]
it3-benefit-demo      sales-broad                16.67  0.34   85.7        0      —      0     0  [trust]
it3-story-demo        sales-biztools             13.33  2.10   65.0        4   3.33      0     0  [trust]
it3-story-nodemo      sales-biztools             13.33  2.41   69.6        1  13.33      0     0  [trust]
it3-benefit-demo      sales-biztools             13.33  0.42   75.0        0      —      0     0  [trust]
it3-story-demo        sales-niche                16.67  1.51   42.9        1  16.67      0     0  [trust]
it3-story-nodemo      sales-niche                16.67  0.58   37.5        0      —      0     0  [trust]
it3-benefit-demo      sales-niche                16.67  0.50  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 122 · sign-ups 10 · demos 1 · purchases 0
ITERATION 3 TOTALS: spend $200.01 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 · sim world-14 seed 1404 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it4-story-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story — 'Diego Ramos, office manager at Kes
  it4-story-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Diego Ramos, office manager at Kes
  it4-count-demo: proof=count named_story=False demo=True | Video ad for Formflow ($29/mo): adoption-count-led — 'Over 4,000 teams already run their i
CAMPAIGN CELLS: pv-broad($20,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($50,sales,interest_biztools,fixed), sales-niche($50,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it4-story-demo        pv-broad                    6.67  0.96   75.0        1   6.67      0     1  [trust]
it4-story-nodemo      pv-broad                    6.67  2.04   52.9        0      —      0     0  [trust]
it4-count-demo        pv-broad                    6.67  0.12    0.0        0      —      0     0  [trust]
it4-story-demo        leads-broad                10.00  1.12   64.3        0      —      0     0  [trust]
it4-story-nodemo      leads-broad                10.00  0.56   85.7        1   10.0      0     0  [trust]
it4-count-demo        leads-broad                10.00  0.72   55.6        0      —      0     0  [trust]
it4-story-demo        sales-broad                16.67  0.96   45.0        0      —      0     0  [trust]
it4-story-nodemo      sales-broad                16.67  3.41   76.1        4   4.17      3     0  [trust]
it4-count-demo        sales-broad                16.67  1.06   63.6        0      —      0     0  [trust]
it4-story-demo        sales-biztools             16.67  1.01   66.7        0      —      0     0  [trust]
it4-story-nodemo      sales-biztools             16.67  1.51   61.1        0      —      0     0  [trust]
it4-count-demo        sales-biztools             16.67  0.42   80.0        0      —      0     0  [trust]
it4-story-demo        sales-niche                16.67  1.44   90.0        4   4.17      0     0  [trust]
it4-story-nodemo      sales-niche                16.67  0.94   92.3        2   8.34      0     0  [trust]
it4-count-demo        sales-niche                16.67  1.01   50.0        0      —      0     0  [trust]
SITE FUNNEL: visits 172 · sign-ups 12 · demos 3 · purchases 1
ITERATION 4 TOTALS: spend $200.04 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 4, "spend": 200.04, "revenue": 29, "roas": 0.14}

━━ ITERATION 5 — policy v4 · sim world-14 seed 1405 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it5-story-maya-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story — 'Maya Chen, ops lead at Harbor Clin
  it5-story-diego-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story — 'Diego Ramos, office manager at Kes
  it5-story-priya-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story — 'Priya Nair, founder of Brightside 
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it5-story-maya-demo   pv-broad                   10.00  0.64  100.0        2    5.0      0     0  [trust]
it5-story-diego-nodemopv-broad                   10.00  2.00   68.0        1   10.0      0     0  [trust]
it5-story-priya-demo  pv-broad                   10.00  2.08   69.2        5    2.0      1     2  [trust]
it5-story-maya-demo   leads-broad                 6.67  1.32   54.5        0      —      0     0  [trust]
it5-story-diego-nodemoleads-broad                 6.67  1.32   36.4        0      —      0     0  [trust]
it5-story-priya-demo  leads-broad                 6.67  1.56   76.9        2   3.33      0     0  [trust]
it5-story-maya-demo   sales-broad                16.67  1.10   69.6        2   8.34      1     0  [trust]
it5-story-diego-nodemosales-broad                16.67  0.67   92.9        1  16.67      0     0  [trust]
it5-story-priya-demo  sales-broad                16.67  1.20   60.0        2   8.34      0     0  [trust]
it5-story-maya-demo   sales-biztools             13.33  0.73   85.7        0      —      0     0  [trust]
it5-story-diego-nodemosales-biztools             13.33  1.36   61.5        2   6.67      0     0  [trust]
it5-story-priya-demo  sales-biztools             13.33  1.26   66.7        2   6.67      0     0  [trust]
it5-story-maya-demo   sales-niche                20.00  1.02   76.5        0      —      0     0  [trust]
it5-story-diego-nodemosales-niche                20.00  1.26   66.7        1   20.0      1     0  [trust]
it5-story-priya-demo  sales-niche                20.00  4.62   63.6        6   3.33      1     1  [trust]
SITE FUNNEL: visits 205 · sign-ups 26 · demos 4 · purchases 3
ITERATION 5 TOTALS: spend $200.01 · settled revenue $87 · ROAS 0.43
@@SUMMARY {"iteration": 5, "spend": 200.01, "revenue": 87, "roas": 0.43}

━━ ITERATION 6 — policy v5 · sim world-14 seed 1406 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it6-founder-priya-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named founder story — 'Priya Nair, founder of Brightside S
  it6-founder-sam-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named founder story — 'Sam Okafor, founder of Ledgerline C
  it6-smb-rosa-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story, other-SMB vertical — 'Rosa Delgado, 
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it6-founder-priya-demopv-broad                   13.33  1.08   61.1        0      —      0     0  [trust]
it6-founder-sam-nodemopv-broad                   13.33  1.50   60.0        1  13.33      0     0  [trust]
it6-smb-rosa-demo     pv-broad                   13.33  1.44   54.2        1  13.33      0     0  [trust]
it6-founder-priya-demoleads-broad                 6.67  1.80   60.0        0      —      0     0  [trust]
it6-founder-sam-nodemoleads-broad                 6.67  1.44   75.0        0      —      0     0  [trust]
it6-smb-rosa-demo     leads-broad                 6.67  1.20   80.0        1   6.67      1     0  [trust]
it6-founder-priya-demosales-broad                13.33  1.32   81.8        1  13.33      0     0  [trust]
it6-founder-sam-nodemosales-broad                13.33  1.26   61.9        1  13.33      1     0  [trust]
it6-smb-rosa-demo     sales-broad                13.33  1.08   77.8        1  13.33      0     0  [trust]
it6-founder-priya-demosales-biztools             13.33  0.73  100.0        1  13.33      1     0  [trust]
it6-founder-sam-nodemosales-biztools             13.33  0.52   80.0        2   6.67      1     0  [trust]
it6-smb-rosa-demo     sales-biztools             13.33  1.99   63.2        1  13.33      0     0  [trust]
it6-founder-priya-demosales-niche                20.00  1.56   61.5        3   6.67      1     0  [trust]
it6-founder-sam-nodemosales-niche                20.00  1.20   65.0        2   10.0      0     0  [trust]
it6-smb-rosa-demo     sales-niche                20.00  1.20   80.0        2   10.0      0     0  [trust]
SITE FUNNEL: visits 178 · sign-ups 17 · demos 5 · purchases 0
ITERATION 6 TOTALS: spend $199.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 199.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 · sim world-14 seed 1407 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it7-founder-lena-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named founder story — 'Lena Park, founder of Fieldnote Des
  it7-legal-omar-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named customer story, legal-office vertical — 'Omar Haddad
  it7-health-tasha-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named customer story, healthcare-ops vertical — 'Tasha Ngu
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($20,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it7-founder-lena-demo pv-broad                   13.33  0.72   75.0        2   6.67      1     0  [trust]
it7-legal-omar-demo   pv-broad                   13.33  0.54   55.6        2   6.67      1     0  [trust]
it7-health-tasha-nodemopv-broad                   13.33  0.60   60.0        1  13.33      1     0  [trust]
it7-founder-lena-demo leads-broad                 6.67  2.76   60.9        2   3.33      0     1  [trust]
it7-legal-omar-demo   leads-broad                 6.67  0.60   40.0        0      —      0     0  [trust]
it7-health-tasha-nodemoleads-broad                 6.67  0.48   75.0        0      —      0     0  [trust]
it7-founder-lena-demo sales-broad                13.33  0.66   45.5        3   4.44      0     0  [trust]
it7-legal-omar-demo   sales-broad                13.33  1.32   72.7        1  13.33      1     0  [trust]
it7-health-tasha-nodemosales-broad                13.33  1.32   40.9        1  13.33      1     0  [trust]
it7-founder-lena-demo sales-biztools             13.33  0.73   57.1        1  13.33      1     0  [trust]
it7-legal-omar-demo   sales-biztools             13.33  1.05   70.0        2   6.67      0     0  [trust]
it7-health-tasha-nodemosales-biztools             13.33  2.10   70.0        1  13.33      1     0  [trust]
it7-founder-lena-demo sales-niche                20.00  1.14   57.9        2   10.0      1     0  [trust]
it7-legal-omar-demo   sales-niche                20.00  1.02   52.9        0      —      0     0  [trust]
it7-health-tasha-nodemosales-niche                20.00  1.92   59.4        3   6.67      1     0  [trust]
SITE FUNNEL: visits 133 · sign-ups 21 · demos 9 · purchases 1
ITERATION 7 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 7, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 8 — policy v7 · sim world-14 seed 1408 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  it8-founder-june-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named founder story — 'June Alvarez, founder of Copperleaf
  it8-founder-marcus-nodemo: proof=story named_story=True demo=False | Video ad for Formflow ($29/mo): named founder story — 'Marcus Bell, founder of Bell & Reed
  it8-founder-aisha-demo: proof=story named_story=True demo=True | Video ad for Formflow ($29/mo): named founder story — 'Aisha Karim, founder of Northloop S
CAMPAIGN CELLS: pv-broad($50,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($50,sales,broad,fixed), sales-biztools($30,sales,interest_biztools,fixed), sales-niche($40,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
it8-founder-june-demo pv-broad                   16.67  1.25   80.8        2   8.34      0     0  [trust]
it8-founder-marcus-nodemopv-broad                   16.67  1.15   58.3        1  16.67      0     0  [trust]
it8-founder-aisha-demopv-broad                   16.67  0.62   53.8        0      —      0     0  [trust]
it8-founder-june-demo leads-broad                10.00  0.80   70.0        1   10.0      0     0  [trust]
it8-founder-marcus-nodemoleads-broad                10.00  1.20   66.7        0      —      0     0  [trust]
it8-founder-aisha-demoleads-broad                10.00  2.00   48.0        1   10.0      0     0  [trust]
it8-founder-june-demo sales-broad                16.67  1.06   68.2        1  16.67      0     0  [trust]
it8-founder-marcus-nodemosales-broad                16.67  0.19   75.0        0      —      0     0  [trust]
it8-founder-aisha-demosales-broad                16.67  1.49   64.5        3   5.56      1     2  [trust]
it8-founder-june-demo sales-biztools             10.00  1.26   88.9        0      —      0     0  [trust]
it8-founder-marcus-nodemosales-biztools             10.00  0.56   50.0        0      —      0     0  [trust]
it8-founder-aisha-demosales-biztools             10.00  0.98   42.9        0      —      0     0  [trust]
it8-founder-june-demo sales-niche                13.33  1.71   63.2        0      —      0     0  [trust]
it8-founder-marcus-nodemosales-niche                13.33  2.52   60.7        1  13.33      0     0  [trust]
it8-founder-aisha-demosales-niche                13.33  1.35   66.7        0      —      0     0  [trust]
SITE FUNNEL: visits 161 · sign-ups 10 · demos 1 · purchases 2
ITERATION 8 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 58, "roas": 0.29}
