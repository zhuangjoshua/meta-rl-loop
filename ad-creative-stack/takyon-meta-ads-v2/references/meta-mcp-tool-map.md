# Meta MCP tool map (single source of truth)

Capability → official `mcp.facebook.com/ads` tool. The authority runtime calls these via the MCP
client; the CEO uses the guarded `business_meta_*` wrappers, never these directly.

## Discovery / read
| Capability | Tool |
|---|---|
| List ad accounts (status, queryable, mcp-enabled) | `ads_get_ad_accounts` |
| List pages for an account (incl. `leadgen_tos_accepted`) | `ads_get_ad_account_pages` |
| List user pages (account-agnostic) | `ads_get_user_pages` |
| Read entities + metrics (level, filters, sort, breakdowns, date range) | `ads_get_ad_entities` |
| Verify valid field/enum values before a call | `ads_get_field_context` |
| Existing images / creatives (reuse by hash/id) | `ads_get_ad_images`, `ads_get_creatives` |
| Render an ad/creative preview | `ads_get_ad_preview` |

## Create / build
| Capability | Tool | Notes |
|---|---|---|
| Campaign | `ads_create_campaign` | objective, buying_type, special_ad_categories; CBO via `campaign_daily_budget`/`campaign_lifetime_budget`; `campaign_spend_cap`. Created PAUSED. |
| Ad set | `ads_create_ad_set` | billing_event, optimization_goal, targeting, destination_type, schedule; ABO budget via `daily_budget`/`lifetime_budget` (only if parent is not CBO). Created PAUSED. |
| Creative (image) | `ads_create_creative` | `page_id` + `link_url` + `image_url` (public) or `image_hash`; `message`/`headline`/`description`/`call_to_action_type`. |
| Creative (video) | `ads_create_creative` | `page_id` + `video_id` + thumbnail `image_url`. Video upload is **not** an MCP capability → `runtime-spec/advideos-shim.md`. |
| Ad | `ads_create_ad` | binds a creative to an ad set. Created PAUSED. |

## Manage / control
| Capability | Tool | Notes |
|---|---|---|
| Activate / publish | `ads_activate_entity` | campaign→adset→ad; all three must be ACTIVE to deliver. |
| Edit name/budget/targeting/schedule/status | `ads_update_entity` | budgets in minor units (cents); CBO budget on campaign, ABO on ad set. |
| Pause / stop | `ads_update_entity` | `{"status":"PAUSED"}`. |
| **Delete / archive** | — | **NOT SUPPORTED**: `DELETED`/`ARCHIVED` are forced to PAUSED (response `status_forced_to_paused:true`). Delete in Ads Manager UI. |

## Insights / evaluation
| Capability | Tool |
|---|---|
| Metrics with breakdowns (date range required) | `ads_get_ad_entities` |
| Performance trend / anomaly | `ads_insights_performance_trend`, `ads_insights_anomaly_signal` |
| Benchmarks (auction rank, industry) | `ads_insights_auction_ranking_benchmarks`, `ads_insights_industry_benchmark` |
| Meta's own recommendations | `ads_get_opportunity_score` |
| Account activity / errors | `ads_account_get_activity_logs`, `ads_get_errors` |

## Optional surface (kept — real capability, wrap when needed)
| Area | Tools |
|---|---|
| Custom audiences (note: audiences **can** be deleted) | `ads_create_custom_audience`, `ads_get_custom_audience`, `ads_update_custom_audience`, `ads_update_custom_audience_users`, `ads_delete_custom_audience` |
| Catalog / commerce (dynamic ads) | `ads_catalog_*` (catalogs, product sets, feeds, diagnostics) |
| Experiments (A/B, lift) | `ads_experiment_abtest_*`, `ads_experiment_lift_*`, `ads_experiment_list_tests` |
| Pixel / datasets / conversions | `ads_pixel_event_*`, `ads_pixel_parameter_*`, `ads_get_datasets`, `ads_get_customconversions` |
| Public ads research | `ads_library_search` |

## Targeting note
Interest targeting requires **verified numeric IDs** (never invented). **Default to geo-only broad
targeting.** To fetch interest IDs, use Meta's targeting search referenced by the `ads_create_ad_set`
schema — **confirm the exact MCP tool name against the live toolset before relying on it** (it was not
seen in the surfaced set; geo-only broad until confirmed).
