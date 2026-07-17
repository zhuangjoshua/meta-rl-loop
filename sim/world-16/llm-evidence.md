# World-16 LLM evidence (receipts only)


━━ ITERATION 1 — policy v0 · sim world-16 seed 1601 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-plain: proof=benefit named_story=False demo=False | Video ad: Formflow saves your team hours every week by turning messy intake forms into cle
  outcome-demo: proof=outcome named_story=False demo=True | Video ad: screen-recording demo of Formflow in action — a raw form submission arrives and 
  story-maya: proof=story named_story=True demo=False | Video ad: named customer story. Maya Chen, ops lead at a 12-person agency, tells how Formf
CAMPAIGN CELLS: pv-broad($40,pageviews,broad,fixed), leads-broad($40,leads,broad,fixed), sales-broad($40,sales,broad,fixed), sales-biztools($40,sales,interest_biztools,fixed), sales-auto~broad($3.84,sales,broad,auto), sales-auto~interest_biztools($36.16,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-plain         pv-broad                   13.33  1.92   84.4        2   6.67      0     0  [trust]
outcome-demo          pv-broad                   13.33  0.96   68.8        0      —      0     0  [trust]
story-maya            pv-broad                   13.33  0.96   68.8        1  13.33      0     0  [trust]
benefit-plain         leads-broad                13.33  0.72   83.3        1  13.33      0     0  [trust]
outcome-demo          leads-broad                13.33  0.24   75.0        1  13.33      1     0  [trust]
story-maya            leads-broad                13.33  0.84   35.7        0      —      0     0  [trust]
benefit-plain         sales-broad                13.33  0.54   77.8        0      —      0     0  [trust]
outcome-demo          sales-broad                13.33  0.90   66.7        1  13.33      1     0  [trust]
story-maya            sales-broad                13.33  1.50   76.0        3   4.44      2     0  [trust]
benefit-plain         sales-biztools             13.33  0.52   60.0        1  13.33      0     0  [trust]
outcome-demo          sales-biztools             13.33  0.42   50.0        0      —      0     0  [trust]
story-maya            sales-biztools             13.33  2.10   80.0        4   3.33      2     0  [trust]
benefit-plain         sales-auto~broad            1.28  1.88   66.7        1   1.28      0     0  [NO-TRUST auto-window]
outcome-demo          sales-auto~broad            1.28  0.00    0.0        0      —      0     0  [NO-TRUST auto-window]
story-maya            sales-auto~broad            1.28  0.62  100.0        0      —      0     0  [NO-TRUST auto-window]
benefit-plain         sales-auto~interest_biztools 12.05  0.35   33.3        1  12.05      0     0  [NO-TRUST auto-window]
outcome-demo          sales-auto~interest_biztools 12.05  0.46   75.0        0      —      0     0  [NO-TRUST auto-window]
story-maya            sales-auto~interest_biztools 12.05  1.28   63.6        1  12.05      1     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 138 · sign-ups 17 · demos 7 · purchases 0
ITERATION 1 TOTALS: spend $199.95 · settled revenue $0 · ROAS 0.0
@@SUMMARY {"iteration": 1, "spend": 199.95, "revenue": 0, "roas": 0.0}

━━ ITERATION 2 — policy v1 · sim world-16 seed 1602 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count-plain: proof=count named_story=False demo=False | Video ad: adoption-numbers angle. '4,200 teams route their intake forms through Formflow.'
  benefit-demo: proof=benefit named_story=False demo=True | Video ad: screen-recording demo of Formflow while voiceover leads with the benefit: 'Stop 
  outcome-plain: proof=outcome named_story=False demo=False | Video ad: outcome-led, no demo footage. Bold text-forward cut: 'Form-handling time down 80
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($35,sales,broad,fixed), sales-biztools($35,sales,interest_biztools,fixed), sales-niche($35,sales,interest_niche,fixed), sales-auto~broad($29.2,sales,broad,auto), sales-auto~interest_biztools($5.8,sales,interest_biztools,auto)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count-plain           pv-broad                   10.00  0.24   66.7        0      —      0     0  [trust]
benefit-demo          pv-broad                   10.00  0.72   77.8        0      —      0     0  [trust]
outcome-plain         pv-broad                   10.00  1.12   50.0        0      —      0     0  [trust]
count-plain           leads-broad                10.00  1.84   82.6        2    5.0      0     0  [trust]
benefit-demo          leads-broad                10.00  1.04   84.6        1   10.0      0     0  [trust]
outcome-plain         leads-broad                10.00  0.24  100.0        0      —      0     0  [trust]
count-plain           sales-broad                11.67  0.48   57.1        0      —      0     0  [trust]
benefit-demo          sales-broad                11.67  0.82   75.0        2   5.83      1     0  [trust]
outcome-plain         sales-broad                11.67  0.48   57.1        0      —      0     0  [trust]
count-plain           sales-biztools             11.67  0.60   40.0        0      —      0     0  [trust]
benefit-demo          sales-biztools             11.67  2.28   63.2        1  11.67      0     0  [trust]
outcome-plain         sales-biztools             11.67  0.84   71.4        0      —      0     0  [trust]
count-plain           sales-niche                11.67  0.72   57.1        0      —      0     0  [trust]
benefit-demo          sales-niche                11.67  1.03  100.0        2   5.83      0     1  [trust]
outcome-plain         sales-niche                11.67  0.31   66.7        0      —      0     0  [trust]
count-plain           sales-auto~broad            9.73  1.23   80.0        1   9.73      0     0  [NO-TRUST auto-window]
benefit-demo          sales-auto~broad            9.73  0.58  100.0        1   9.73      0     0  [NO-TRUST auto-window]
outcome-plain         sales-auto~broad            9.73  1.07   84.6        0      —      0     0  [NO-TRUST auto-window]
count-plain           sales-auto~interest_biztools  1.93  0.72  100.0        0      —      0     0  [NO-TRUST auto-window]
benefit-demo          sales-auto~interest_biztools  1.93  2.17   66.7        0      —      0     0  [NO-TRUST auto-window]
outcome-plain         sales-auto~interest_biztools  1.93  2.17   33.3        0      —      0     0  [NO-TRUST auto-window]
SITE FUNNEL: visits 135 · sign-ups 10 · demos 1 · purchases 1
ITERATION 2 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 2, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 3 — policy v2 · sim world-16 seed 1603 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  count-demo: proof=count named_story=False demo=True | Video ad: adoption-numbers angle over live screen-recording demo. '4,200 teams route intak
  story-demo: proof=story named_story=True demo=True | Video ad: named customer story with demo footage. Maya Chen, ops lead at a 12-person agenc
  benefit-demo: proof=benefit named_story=False demo=True | Video ad: screen-recording demo of Formflow while voiceover leads with the benefit: 'Stop 
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($47,sales,broad,fixed), sales-biztools($47,sales,interest_biztools,fixed), sales-niche($46,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
count-demo            pv-broad                   10.00  1.04   53.8        0      —      0     0  [trust]
story-demo            pv-broad                   10.00  0.48  100.0        1   10.0      1     0  [trust]
benefit-demo          pv-broad                   10.00  0.56  100.0        1   10.0      0     0  [trust]
count-demo            leads-broad                10.00  0.80   90.0        1   10.0      0     0  [trust]
story-demo            leads-broad                10.00  1.20   73.3        2    5.0      1     0  [trust]
benefit-demo          leads-broad                10.00  1.04   76.9        4    2.5      1     1  [trust]
count-demo            sales-broad                15.67  0.77   60.0        1  15.67      0     1  [trust]
story-demo            sales-broad                15.67  0.77   86.7        1  15.67      0     0  [trust]
benefit-demo          sales-broad                15.67  1.79   68.6        2   7.83      1     1  [trust]
count-demo            sales-biztools             15.67  0.63  100.0        0      —      0     0  [trust]
story-demo            sales-biztools             15.67  0.80  100.0        0      —      0     0  [trust]
benefit-demo          sales-biztools             15.67  1.16   92.3        2   7.83      0     0  [trust]
count-demo            sales-niche                15.33  0.70   77.8        0      —      0     0  [trust]
story-demo            sales-niche                15.33  0.00    0.0        0      —      0     0  [trust]
benefit-demo          sales-niche                15.33  0.63   87.5        3   5.11      2     0  [trust]
SITE FUNNEL: visits 138 · sign-ups 18 · demos 6 · purchases 3
ITERATION 3 TOTALS: spend $200.01 · settled revenue $87 · ROAS 0.43
@@SUMMARY {"iteration": 3, "spend": 200.01, "revenue": 87, "roas": 0.43}

━━ ITERATION 4 — policy v3 · sim world-16 seed 1604 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo-inbox: proof=benefit named_story=False demo=True | Video ad: screen-recording demo of Formflow while voiceover leads with the benefit: 'Stop 
  benefit-demo-hours: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Get your Tuesdays back.' Screen-recording shows
  benefit-demo-quiet: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'The quietest inbox on your team.' Screen-record
CAMPAIGN CELLS: pv-broad($30,pageviews,broad,fixed), leads-broad($30,leads,broad,fixed), sales-broad($47,sales,broad,fixed), sales-biztools($47,sales,interest_biztools,fixed), sales-niche($46,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo-inbox    pv-broad                   10.00  1.04   38.5        0      —      0     0  [trust]
benefit-demo-hours    pv-broad                   10.00  1.20   93.3        2    5.0      0     0  [trust]
benefit-demo-quiet    pv-broad                   10.00  0.80   90.0        1   10.0      1     0  [trust]
benefit-demo-inbox    leads-broad                10.00  1.60   85.0        1   10.0      0     0  [trust]
benefit-demo-hours    leads-broad                10.00  1.68   71.4        2    5.0      1     1  [trust]
benefit-demo-quiet    leads-broad                10.00  0.96   83.3        1   10.0      1     0  [trust]
benefit-demo-inbox    sales-broad                15.67  0.36  100.0        2   7.83      0     0  [trust]
benefit-demo-hours    sales-broad                15.67  1.02   85.0        3   5.22      2     0  [trust]
benefit-demo-quiet    sales-broad                15.67  0.61   75.0        0      —      0     0  [trust]
benefit-demo-inbox    sales-biztools             15.67  0.27  100.0        0      —      0     0  [trust]
benefit-demo-hours    sales-biztools             15.67  0.63   57.1        0      —      0     0  [trust]
benefit-demo-quiet    sales-biztools             15.67  0.27  100.0        0      —      0     0  [trust]
benefit-demo-inbox    sales-niche                15.33  2.19   85.7        5   3.07      1     0  [trust]
benefit-demo-hours    sales-niche                15.33  1.02   84.6        1  15.33      0     0  [trust]
benefit-demo-quiet    sales-niche                15.33  0.86   81.8        4   3.83      1     0  [trust]
SITE FUNNEL: visits 157 · sign-ups 22 · demos 7 · purchases 1
ITERATION 4 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 4, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 5 — policy v4 · sim world-16 seed 1605 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo-hours: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Get your Tuesdays back.' Screen-recording shows
  benefit-demo-inbox: proof=benefit named_story=False demo=True | Video ad: screen-recording demo of Formflow while voiceover leads with the benefit: 'Stop 
  benefit-demo-handsfree: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Your intake runs itself.' Screen-recording of a
CAMPAIGN CELLS: pv-broad($25,pageviews,broad,fixed), leads-broad($45,leads,broad,fixed), sales-broad($45,sales,broad,fixed), sales-biztools($25,sales,interest_biztools,fixed), sales-niche($60,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo-hours    pv-broad                    8.33  0.67   57.1        0      —      0     0  [trust]
benefit-demo-inbox    pv-broad                    8.33  1.54   68.8        2   4.17      0     0  [trust]
benefit-demo-handsfreepv-broad                    8.33  0.67   57.1        0      —      0     0  [trust]
benefit-demo-hours    leads-broad                15.00  0.64   91.7        1   15.0      0     0  [trust]
benefit-demo-inbox    leads-broad                15.00  1.81   70.6        2    7.5      0     0  [trust]
benefit-demo-handsfreeleads-broad                15.00  0.80   80.0        1   15.0      0     0  [trust]
benefit-demo-hours    sales-broad                15.00  1.44   92.6        3    5.0      2     0  [trust]
benefit-demo-inbox    sales-broad                15.00  1.65   83.9        5    3.0      0     0  [trust]
benefit-demo-handsfreesales-broad                15.00  1.39   73.1        5    3.0      1     1  [trust]
benefit-demo-hours    sales-biztools              8.33  1.18   85.7        0      —      0     0  [trust]
benefit-demo-inbox    sales-biztools              8.33  1.18  100.0        1   8.33      1     0  [trust]
benefit-demo-handsfreesales-biztools              8.33  0.67  100.0        1   8.33      0     0  [trust]
benefit-demo-hours    sales-niche                20.00  0.60   80.0        1   20.0      1     0  [trust]
benefit-demo-inbox    sales-niche                20.00  0.48  100.0        0      —      0     0  [trust]
benefit-demo-handsfreesales-niche                20.00  0.78   92.3        3   6.67      0     0  [trust]
SITE FUNNEL: visits 181 · sign-ups 25 · demos 5 · purchases 1
ITERATION 5 TOTALS: spend $199.98 · settled revenue $29 · ROAS 0.15
@@SUMMARY {"iteration": 5, "spend": 199.98, "revenue": 29, "roas": 0.15}

━━ ITERATION 6 — policy v5 · sim world-16 seed 1606 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo-hours: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Get your Tuesdays back.' Screen-recording shows
  benefit-demo-inbox: proof=benefit named_story=False demo=True | Video ad: screen-recording demo of Formflow while voiceover leads with the benefit: 'Stop 
  benefit-demo-handsfree: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Your intake runs itself.' Screen-recording of a
CAMPAIGN CELLS: sales-broad($100,sales,broad,fixed), leads-broad($40,leads,broad,fixed), pv-broad($20,pageviews,broad,fixed), sales-biztools($20,sales,interest_biztools,fixed), sales-niche($20,sales,interest_niche,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo-hours    sales-broad                33.33  0.46   73.7        2  16.66      0     1  [trust]
benefit-demo-inbox    sales-broad                33.33  2.06   82.6       22   1.51      6     0  [trust]
benefit-demo-handsfreesales-broad                33.33  0.50   81.0        0      —      0     0  [trust]
benefit-demo-hours    leads-broad                13.33  0.78   92.3        3   4.44      0     0  [trust]
benefit-demo-inbox    leads-broad                13.33  0.30   60.0        1  13.33      0     0  [trust]
benefit-demo-handsfreeleads-broad                13.33  1.86   80.6        2   6.67      0     0  [trust]
benefit-demo-hours    pv-broad                    6.67  0.12  100.0        0      —      0     0  [trust]
benefit-demo-inbox    pv-broad                    6.67  1.08   55.6        0      —      0     0  [trust]
benefit-demo-handsfreepv-broad                    6.67  2.28   89.5        2   3.33      0     0  [trust]
benefit-demo-hours    sales-biztools              6.67  0.84  100.0        0      —      0     0  [trust]
benefit-demo-inbox    sales-biztools              6.67  1.05   60.0        0      —      0     0  [trust]
benefit-demo-handsfreesales-biztools              6.67  0.63  100.0        0      —      0     0  [trust]
benefit-demo-hours    sales-niche                 6.67  1.98   90.9        2   3.33      0     0  [trust]
benefit-demo-inbox    sales-niche                 6.67  1.44   62.5        0      —      0     0  [trust]
benefit-demo-handsfreesales-niche                 6.67  0.90  100.0        1   6.67      1     0  [trust]
SITE FUNNEL: visits 195 · sign-ups 35 · demos 7 · purchases 1
ITERATION 6 TOTALS: spend $200.01 · settled revenue $29 · ROAS 0.14
@@SUMMARY {"iteration": 6, "spend": 200.01, "revenue": 29, "roas": 0.14}

━━ ITERATION 7 — policy v6 · sim world-16 seed 1607 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo-hours: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Get your Tuesdays back.' Screen-recording shows
  benefit-demo-inbox: proof=benefit named_story=False demo=True | Video ad: screen-recording demo of Formflow while voiceover leads with the benefit: 'Stop 
  benefit-demo-handsfree: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Your intake runs itself.' Screen-recording of a
CAMPAIGN CELLS: sales-broad($105,sales,broad,fixed), leads-broad($30,leads,broad,fixed), sales-niche($25,sales,interest_niche,fixed), pv-broad($20,pageviews,broad,fixed), sales-biztools($20,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo-hours    sales-broad                35.00  1.19   76.9        7    5.0      3     0  [trust]
benefit-demo-inbox    sales-broad                35.00  1.10   77.1        7    5.0      2     0  [trust]
benefit-demo-handsfreesales-broad                35.00  0.46   65.0        4   8.75      0     1  [trust]
benefit-demo-hours    leads-broad                10.00  0.88   81.8        3   3.33      0     1  [trust]
benefit-demo-inbox    leads-broad                10.00  0.32   75.0        0      —      0     0  [trust]
benefit-demo-handsfreeleads-broad                10.00  2.16   88.9        4    2.5      1     1  [trust]
benefit-demo-hours    sales-niche                 8.33  1.44   90.0        3   2.78      0     0  [trust]
benefit-demo-inbox    sales-niche                 8.33  0.43   66.7        0      —      0     0  [trust]
benefit-demo-handsfreesales-niche                 8.33  0.72   80.0        0      —      0     0  [trust]
benefit-demo-hours    pv-broad                    6.67  0.72   50.0        1   6.67      0     0  [trust]
benefit-demo-inbox    pv-broad                    6.67  0.36  100.0        0      —      0     0  [trust]
benefit-demo-handsfreepv-broad                    6.67  1.32   90.9        0      —      0     0  [trust]
benefit-demo-hours    sales-biztools              6.67  0.00    0.0        0      —      0     0  [trust]
benefit-demo-inbox    sales-biztools              6.67  0.63  100.0        0      —      0     0  [trust]
benefit-demo-handsfreesales-biztools              6.67  0.84  100.0        0      —      0     0  [trust]
SITE FUNNEL: visits 164 · sign-ups 29 · demos 6 · purchases 3
ITERATION 7 TOTALS: spend $200.01 · settled revenue $87 · ROAS 0.43
@@SUMMARY {"iteration": 7, "spend": 200.01, "revenue": 87, "roas": 0.43}

━━ ITERATION 8 — policy v7 · sim world-16 seed 1608 ━━
ADS (features as tagged in the batch spec; full prompts in the spec file):
  benefit-demo-handsfree: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Your intake runs itself.' Screen-recording of a
  benefit-demo-hours: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Get your Tuesdays back.' Screen-recording shows
  benefit-demo-firstweek: proof=benefit named_story=False demo=True | Video ad: benefit-led demo variant. Hook: 'Set it up before lunch, forget it by Friday.' S
CAMPAIGN CELLS: leads-broad($85,leads,broad,fixed), sales-broad($62,sales,broad,fixed), sales-niche($23,sales,interest_niche,fixed), pv-broad($15,pageviews,broad,fixed), sales-biztools($15,sales,interest_biztools,fixed)
ad                    cell                       spend  CTR%  load%  signups    CPL  demos  buys  trust
benefit-demo-handsfreeleads-broad                28.33  1.07   86.8        5   5.67      2     0  [trust]
benefit-demo-hours    leads-broad                28.33  0.65   82.6        4   7.08      0     0  [trust]
benefit-demo-firstweekleads-broad                28.33  0.79   89.3        3   9.44      0     1  [trust]
benefit-demo-handsfreesales-broad                20.67  0.35   88.9        0      —      0     0  [trust]
benefit-demo-hours    sales-broad                20.67  0.70   83.3        3   6.89      1     0  [trust]
benefit-demo-firstweeksales-broad                20.67  0.35   88.9        2  10.34      2     0  [trust]
benefit-demo-handsfreesales-niche                 7.67  1.88   91.7        1   7.67      1     0  [trust]
benefit-demo-hours    sales-niche                 7.67  1.57  100.0        3   2.56      0     1  [trust]
benefit-demo-firstweeksales-niche                 7.67  0.47   66.7        0      —      0     0  [trust]
benefit-demo-handsfreepv-broad                    5.00  2.72   76.5        0      —      0     0  [trust]
benefit-demo-hours    pv-broad                    5.00  0.96  100.0        1    5.0      0     0  [trust]
benefit-demo-firstweekpv-broad                    5.00  0.80  100.0        1    5.0      0     0  [trust]
benefit-demo-handsfreesales-biztools              5.00  0.00    0.0        0      —      0     0  [trust]
benefit-demo-hours    sales-biztools              5.00  2.80  100.0        0      —      0     0  [trust]
benefit-demo-firstweeksales-biztools              5.00  1.12  100.0        1    5.0      1     0  [trust]
SITE FUNNEL: visits 169 · sign-ups 24 · demos 7 · purchases 2
ITERATION 8 TOTALS: spend $200.01 · settled revenue $58 · ROAS 0.29
@@SUMMARY {"iteration": 8, "spend": 200.01, "revenue": 58, "roas": 0.29}
