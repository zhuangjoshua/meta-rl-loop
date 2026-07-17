
━━ ITERATION 1 — policy v0 (original, no learned rules) · sim world-1 seed 101 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-selfie: proof=benefit named_story=False demo=False | UGC selfie, female consultant, energetic: 'Beautiful forms in minutes - no code. Pick a te
  outcome-selfie: proof=outcome named_story=False demo=False | IDENTICAL spec; only words differ: 'Intake auto-syncs to your CRM - no copy-paste. The rec
  benefit-calm: proof=benefit named_story=False demo=False | Same words as benefit-selfie; CALM seated delivery. Everything else identical.
CAMPAIGN CELLS: clicks($4.0,clicks,broad,fixed), pageviews($4.0,pageviews,broad,fixed), leads($4.0,leads,broad,fixed), sales-broad($2.0,sales,broad,fixed), sales-interest($2.0,sales,interest_biztools,fixed), sales-auto~broad($0.99,sales,broad,auto), sales-auto~interest_biztools($3.01,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-selfie        clicks                      1.33  0.60    0.0        0      —      0     0  [trust]
outcome-selfie        clicks                      1.33  0.00    0.0        0      —      0     0  [trust]
benefit-calm          clicks                      1.33  1.80  100.0        0      —      0     0  [trust]
benefit-selfie        pageviews                   1.33  1.20  100.0        1   1.33      1     0  [trust]
outcome-selfie        pageviews                   1.33  0.00    0.0        0      —      0     0  [trust]
benefit-calm          pageviews                   1.33  1.20  100.0        0      —      0     0  [trust]
benefit-selfie        leads                       1.33  1.20   50.0        0      —      0     0  [trust]
outcome-selfie        leads                       1.33  1.80   66.7        0      —      0     0  [trust]
benefit-calm          leads                       1.33  1.20  100.0        0      —      0     0  [trust]
benefit-selfie        sales-broad                 0.67  1.20    0.0        0      —      0     0  [trust]
outcome-selfie        sales-broad                 0.67  3.60   33.3        0      —      0     0  [trust]
benefit-calm          sales-broad                 0.67  0.00    0.0        0      —      0     0  [trust]
benefit-selfie        sales-interest              0.67  0.00    0.0        0      —      0     0  [trust]
outcome-selfie        sales-interest              0.67  0.00    0.0        0      —      0     0  [trust]
benefit-calm          sales-interest              0.67  0.00    0.0        0      —      0     0  [trust]
benefit-selfie        sales-auto~broad            0.33  0.00    0.0        0      —      0     0  [NO-TRUST starved]
outcome-selfie        sales-auto~broad            0.33  0.00    0.0        0      —      0     0  [NO-TRUST starved]
benefit-calm          sales-auto~broad            0.33  2.42  100.0        0      —      0     0  [NO-TRUST starved]
benefit-selfie        sales-auto~interest_biztools  1.00  1.40  100.0        0      —      0     0  [NO-TRUST auto-window]
outcome-selfie        sales-auto~interest_biztools  1.00  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
benefit-calm          sales-auto~interest_biztools  1.00  1.40  100.0        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 16 · sign-ups 1 · demos 1 · purchases 0
ITERATION 1 TOTALS: spend $19.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 19.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 (consolidated power) · sim world-1 seed 102 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-selfie: proof=benefit named_story=False demo=False | UGC selfie, female consultant, energetic: 'Beautiful forms in minutes - no code.' Template
  outcome-selfie: proof=outcome named_story=False demo=False | IDENTICAL spec; only words: 'Intake auto-syncs to your CRM - no copy-paste.'
  benefit-calm: proof=benefit named_story=False demo=False | Same words as benefit-selfie; CALM delivery; else identical.
CAMPAIGN CELLS: pageviews($6.66,pageviews,broad,fixed), leads($6.66,leads,broad,fixed), sales-broad($6.66,sales,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-selfie        pageviews                   2.22  0.00    0.0        0      —      0     0  [trust]
outcome-selfie        pageviews                   2.22  1.44  100.0        1   2.22      1     0  [trust]
benefit-calm          pageviews                   2.22  0.00    0.0        0      —      0     0  [trust]
benefit-selfie        leads                       2.22  0.36    0.0        0      —      0     0  [trust]
outcome-selfie        leads                       2.22  0.72  100.0        0      —      0     0  [trust]
benefit-calm          leads                       2.22  0.36  100.0        0      —      0     0  [trust]
benefit-selfie        sales-broad                 2.22  0.00    0.0        0      —      0     0  [trust]
outcome-selfie        sales-broad                 2.22  0.72   50.0        0      —      0     0  [trust]
benefit-calm          sales-broad                 2.22  0.36  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 9 · sign-ups 1 · demos 1 · purchases 0
ITERATION 2 TOTALS: spend $19.98 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 2, "spend": 19.98, "revenue": 0, "roas": 0.0}

━━ ITERATION 3 — policy v2 (4 cells, pooled evidence) · sim world-1 seed 103 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-selfie: proof=benefit named_story=False demo=False | UGC selfie, energetic: 'Beautiful forms in minutes - no code.'
  outcome-selfie: proof=outcome named_story=False demo=False | IDENTICAL spec; only words: 'Intake auto-syncs to your CRM - no copy-paste.'
CAMPAIGN CELLS: leads($10.0,leads,broad,fixed), sales-broad($10.0,sales,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-selfie        leads                       5.00  0.80   60.0        0      —      0     0  [trust]
outcome-selfie        leads                       5.00  3.36   57.1        0      —      0     0  [trust]
benefit-selfie        sales-broad                 5.00  0.80  100.0        0      —      0     0  [trust]
outcome-selfie        sales-broad                 5.00  0.96  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 26 · sign-ups 0 · demos 0 · purchases 0
ITERATION 3 TOTALS: spend $20.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 3, "spend": 20.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 4 — policy v3 (outcome leads; story challenger) · sim world-1 seed 104 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-selfie: proof=outcome named_story=False demo=False | UGC selfie, energetic: 'Intake auto-syncs to your CRM - no copy-paste.'
  story-selfie: proof=story named_story=True demo=False | IDENTICAL spec; only words: 'How Rivera Consulting cut intake time 80% - four hours a week
CAMPAIGN CELLS: leads($10.0,leads,broad,fixed), sales-broad($10.0,sales,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-selfie        leads                       5.00  1.44   88.9        2    2.5      1     0  [trust]
story-selfie          leads                       5.00  2.08   30.8        0      —      0     0  [trust]
outcome-selfie        sales-broad                 5.00  1.60   30.0        0      —      0     0  [trust]
story-selfie          sales-broad                 5.00  0.80   60.0        0      —      0     0  [trust]
SITE FUNNEL: visits 18 · sign-ups 2 · demos 1 · purchases 0
ITERATION 4 TOTALS: spend $20.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 4, "spend": 20.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 5 — policy v4 (demo axis test) · sim world-1 seed 105 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-selfie: proof=outcome named_story=False demo=False | UGC selfie, energetic: 'Intake auto-syncs to your CRM - no copy-paste.'
  outcome-demo: proof=outcome named_story=False demo=True | SAME words voiced over a SCREEN RECORDING: a form submits and the CRM record appears field
CAMPAIGN CELLS: leads($10.0,leads,broad,fixed), sales-broad($10.0,sales,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-selfie        leads                       5.00  1.44   77.8        1    5.0      1     0  [trust]
outcome-demo          leads                       5.00  0.16  100.0        0      —      0     0  [trust]
outcome-selfie        sales-broad                 5.00  1.28   75.0        1    5.0      1     0  [trust]
outcome-demo          sales-broad                 5.00  1.60   70.0        1    5.0      1     0  [trust]
SITE FUNNEL: visits 21 · sign-ups 3 · demos 3 · purchases 0
ITERATION 5 TOTALS: spend $20.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 5, "spend": 20.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 6 — policy v5 (audience axis) · sim world-1 seed 106 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-selfie: proof=outcome named_story=False demo=False | UGC selfie, energetic: 'Intake auto-syncs to your CRM - no copy-paste.'
CAMPAIGN CELLS: leads-broad($5.0,leads,broad,fixed), sales-broad($5.0,sales,broad,fixed), sales-biztools($5.0,sales,interest_biztools,fixed), sales-niche($5.0,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-selfie        leads-broad                 5.00  1.44   66.7        0      —      0     0  [trust]
outcome-selfie        sales-broad                 5.00  3.36   71.4        0      —      0     0  [trust]
outcome-selfie        sales-biztools              5.00  1.40   80.0        1    5.0      1     0  [trust]
outcome-selfie        sales-niche                 5.00  0.96   75.0        0      —      0     0  [trust]
SITE FUNNEL: visits 28 · sign-ups 1 · demos 1 · purchases 0
ITERATION 6 TOTALS: spend $20.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 6, "spend": 20.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 7 — policy v6 (concentration for purchase readability) · sim world-1 seed 107 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-selfie: proof=outcome named_story=False demo=False | UGC selfie, energetic: 'Intake auto-syncs to your CRM - no copy-paste.'
CAMPAIGN CELLS: leads-broad($10.0,leads,broad,fixed), sales-broad($10.0,sales,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-selfie        leads-broad                10.00  0.96   75.0        0      —      0     0  [trust]
outcome-selfie        sales-broad                10.00  0.56   71.4        0      —      0     0  [trust]
SITE FUNNEL: visits 14 · sign-ups 0 · demos 0 · purchases 0
ITERATION 7 TOTALS: spend $20.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 7, "spend": 20.0, "revenue": 0, "roas": 0.0}

━━ ITERATION 8 — policy v7 (signup-economics operating mode) · sim world-1 seed 108 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  outcome-selfie: proof=outcome named_story=False demo=False | UGC selfie, energetic: 'Intake auto-syncs to your CRM - no copy-paste.'
  outcome-count-support: proof=count named_story=False demo=False | IDENTICAL spec; words lead with adoption count: '2,300 consultancies run intake on Formflo
CAMPAIGN CELLS: leads-broad($14.0,leads,broad,fixed), pageviews-broad($6.0,pageviews,broad,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
outcome-selfie        leads-broad                 7.00  2.40   71.4        2    3.5      1     0  [trust]
outcome-count-support leads-broad                 7.00  0.69   66.7        0      —      0     0  [trust]
outcome-selfie        pageviews-broad             3.00  0.53  100.0        0      —      0     0  [trust]
outcome-count-support pageviews-broad             3.00  0.53  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 23 · sign-ups 2 · demos 1 · purchases 0
ITERATION 8 TOTALS: spend $20.0 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 8, "spend": 20.0, "revenue": 0, "roas": 0.0}
