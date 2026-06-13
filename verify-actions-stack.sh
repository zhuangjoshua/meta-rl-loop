#!/usr/bin/env bash
# Verification script for the actions-stack changes (plan §14–§16 + createActionRunner).
# Run from the workspace root: bash verify-actions-stack.sh
set -u -o pipefail
cd "$(dirname "$0")/hermes-agent-main" || exit 1

pass=0; fail=0
check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "PASS  $name"; pass=$((pass+1));
  else echo "FAIL  $name"; fail=$((fail+1)); fi
}

KIT=plugins/takyon/subuser_app_kit/runtime-client.js

echo "== automated checks =="
check "kit syntax (node --check)" node --check "$KIT"
check "kit defines createActionRunner" grep -q "createActionRunner(name)" "$KIT"
check "kit budget errors attach upgrade checkoutUrl" grep -q 'kind === "budget" && checkoutCallable' "$KIT"
check "kit replays idempotency key only on network failures" grep -q 'classified.kind === "network" ? idempotencyKey' "$KIT"
check "registry worker contract mandates createActionRunner" grep -q "createActionRunner(name): disable the trigger" plugins/takyon/core.py
check "registry worker contract carries schedule-results convention" grep -q "what happened since they left" plugins/takyon/core.py
check "app-runtime skill carries frontend conventions" grep -q "createActionRunner" skills/takyon/takyon-app-runtime/SKILL.md
check "forbidden-scan blockers wired into refresh" grep -q "_format_forbidden_product_source_blockers" plugins/takyon/core.py
check "invocation evidence wired into surface" grep -q "summarize_action_invocations" plugins/takyon/core.py
check "deno cmd has no allow-run/allow-env/allow-ffi" bash -c '! grep -E -- "--allow-(run|env|ffi)" plugins/takyon/app_actions.py'
check "deno cmd forbids remote imports" grep -q -- "--no-remote" plugins/takyon/app_actions.py

echo
echo "== frontend lane (§17) checks =="
SCAFFOLD=plugins/takyon/subuser_app_kit/scaffold
check "frontend_stack choices constant exists" grep -q "SUBUSER_FRONTEND_STACK_CHOICES" plugins/takyon/core.py
check "frontend_stack exposed in contract tool schema" bash -c 'grep -q "\"frontend_stack\": {\"type\": \"string\"" plugins/takyon/core.py && grep -q "vite_react_ts" plugins/takyon/core.py'
check "static-only scanner exists and is stack-gated" bash -c 'grep -q "_scan_for_pinned_stack_server_entrypoints" plugins/takyon/core.py && grep -q "frontend_stack.*== .vite_react_ts." plugins/takyon/core.py'
check "placeholder-token advisory exists (advisory, not blocker)" grep -q "_scaffold_placeholder_tokens_marker" plugins/takyon/core.py
check "scaffold present with pinned lockfile" bash -c "test -f $SCAFFOLD/package.json && test -f $SCAFFOLD/package-lock.json"
check "scaffold _takyon re-exports the real kit" grep -q 'export \* from "../../runtime-client.js"' "$SCAFFOLD/_takyon/runtime-client.js"
check "scaffold ships placeholder tokens with marker" grep -q "SCAFFOLD-PLACEHOLDER" "$SCAFFOLD/src/tokens.css"
check "scaffold has no server code" bash -c "! find $SCAFFOLD/src -name 'route.*' -o -path '*pages/api*' | grep -q ."
check "build-product skill teaches the lane" grep -q "vite_react_ts" skills/takyon/takyon-build-product/SKILL.md
check "product-workflow skill is lane-aware" grep -q "Gated source root by lane" skills/takyon/takyon-product-workflow/SKILL.md
check "app-runtime skill notes scaffold hooks" grep -q "src/lib/hooks.ts" skills/takyon/takyon-app-runtime/SKILL.md

echo
echo "== email rail (§20A) checks =="
check "email registry entry with worker contract" grep -q '"email": {' plugins/takyon/core.py
check "email deps declared" grep -q '"email": ("auth", "account")' plugins/takyon/core.py
check "email in runtime feature order" bash -c 'grep -A12 "_RUNTIME_FEATURE_ORDER" plugins/takyon/core.py | grep -q "\"email\","'
check "business_send_app_email handler + registration" bash -c 'grep -q "def handle_business_send_app_email" plugins/takyon/core.py && grep -q "\"name\": \"business_send_app_email\"" plugins/takyon/core.py'
check "email/send rail route + status mapping" bash -c 'grep -q "\"email/send\"" takyon_cli/web_server.py && grep -q "_takyon_email_status_payload" takyon_cli/web_server.py'
check "app-runtime skill teaches business_send_app_email" grep -q "business_send_app_email" skills/takyon/takyon-app-runtime/SKILL.md
check "product-workflow routes email rail decl" grep -q "declare the \`email\` rail" skills/takyon/takyon-product-workflow/SKILL.md
check "email leaf exists (agent-built)" test -f plugins/takyon/app_email.py

echo
echo "== records-v2 (§18) + media rail (§19) checks =="
check "records-v2 query compiler exists" grep -q "def compile_record_query" plugins/takyon/app_records.py
check "records-v2 query_records leaf (PG)" grep -q "def query_records" plugins/takyon/app_records.py
check "records-v2 field names whitelist-only (no raw interpolation)" grep -q "_resolve_query_field" plugins/takyon/app_records.py
check "records/query route wired" grep -q '\[\"records\", \"query\"\]' takyon_cli/web_server.py
check "kit listRecords routes filters to records/query" grep -q "records/query" plugins/takyon/subuser_app_kit/runtime-client.js
check "media leaf exists" test -f plugins/takyon/app_media.py
check "media registry entry + deps + order" bash -c 'grep -q "\"media\": {" plugins/takyon/core.py && grep -q "\"media\": (\"auth\", \"account\")" plugins/takyon/core.py'
check "app_media SQLite table" grep -q "CREATE TABLE IF NOT EXISTS app_media" plugins/takyon/core.py
check "app_media PG migration with shape guard" bash -c 'test -f plugins/takyon/db/migrations/0024_app_media.sql && grep -q "is not the takyon shape" plugins/takyon/db/migrations/0024_app_media.sql'
check "media routes (POST multipart / GET bytes / DELETE)" bash -c 'grep -q "parts == \[\"media\"\]" takyon_cli/web_server.py && grep -q "app_media_get_bytes" takyon_cli/web_server.py'
check "kit uploadMedia/mediaUrl/deleteMedia" bash -c 'grep -q "async uploadMedia" plugins/takyon/subuser_app_kit/runtime-client.js && grep -q "mediaUrl(id)" plugins/takyon/subuser_app_kit/runtime-client.js'
check "app-runtime skill teaches media + records-v2" bash -c 'grep -q "business_list_app_media" skills/takyon/takyon-app-runtime/SKILL.md && grep -q "listRecords({filters" skills/takyon/takyon-app-runtime/SKILL.md'
check "media excluded from bootstrap defaults" bash -c '! grep -A3 "DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES = " plugins/takyon/core.py | grep -q "media"'

echo
echo "== scaffold build (npm ci && build && tsc) =="
scaffold_log="$(mktemp)"
if (cd "$SCAFFOLD" && npm ci --silent && npm run build --silent && npx tsc --noEmit) >"$scaffold_log" 2>&1; then
  echo "PASS  scaffold builds clean"; pass=$((pass+1))
else
  tail -15 "$scaffold_log"
  echo "FAIL  scaffold builds clean"; fail=$((fail+1))
fi
rm -f "$scaffold_log"

echo
echo "== hermetic test suites =="
# web_server runs as its own invocation: test_skill_lab_routes_redirect_to_chat... is
# ordering-sensitive under some combined xdist schedules (pre-existing isolation bug,
# fails only in certain multi-suite combinations; passes alone and in every pair).
suite_log="$(mktemp)"
if scripts/run_tests.sh \
  tests/plugins/test_takyon_subuser_app_kit_action_runner.py \
  tests/plugins/test_takyon_app_actions.py \
  tests/plugins/test_takyon_app_email.py \
  tests/plugins/test_takyon_app_media.py \
  tests/plugins/test_takyon_app_records_query.py \
  tests/plugins/test_takyon_product_enforcement.py \
  tests/plugins/test_takyon_customer_experience_shape.py -q >"$suite_log" 2>&1 \
  && scripts/run_tests.sh tests/takyon_cli/test_web_server.py -q >>"$suite_log" 2>&1; then
  grep -E "passed" "$suite_log" | tail -2
  echo "PASS  hermetic test suites"; pass=$((pass+1))
else
  tail -25 "$suite_log"
  echo "FAIL  hermetic test suites"; fail=$((fail+1))
fi
rm -f "$suite_log"

echo
echo "== results: $pass passed, $fail failed =="
echo
cat <<'MANUAL'
== manual checks (report done / attempted / blocked for each) ==
1. Deno-gated sandbox suite: run
   scripts/run_tests.sh tests/plugins/test_takyon_app_actions.py -q -rs and confirm
   the deno-marked tests RAN, not skipped (deno is on PATH here; if a machine lacks
   it, skipped = blocked, not done). Each sandbox denial (fs write, shell, env read,
   non-allowlisted fetch, deadline kill) must fail with its pinned truthful error.
2. Skill sync: relaunch ./takyon. If "user-modified, skipping" appears for
   takyon-app-runtime, run `takyon skills reset takyon-app-runtime --restore`,
   relaunch again, then verify $TAKYON_HOME/skills/takyon/takyon-app-runtime/
   SKILL.md contains "createActionRunner" and the skill shows in the index.
3. §15D-2 acceptance rerun (latexflow lab profile): business_refresh_product_surface
   must BLOCK the existing provider-proxy route.js with the exact forbidden-pattern
   and reserved-namespace blockers; the worker repair retry must remove the route
   and wire the runtime client; the rerun then passes rails-only. Do NOT hand-edit
   the lab artifact — the gates must force the fix.
4. §14C E2E: temp TAKYON_HOME, ./takyon shell, /create a test business, declare one
   http + one schedule action, walk the §14A recipe end to end (capability gate →
   test customer → magic link → invoke → receipt read-back → /cron tick → service-
   principal receipt), then confirm refresh evidence shows no declared action with
   "never" invocation status.
5. Kit delivery: after a surface refresh, confirm the refreshed business's
   product/site/_takyon/runtime-client.js contains createActionRunner.
6. Frontend lane E2E (needs the container runtime installed): /create a fresh test
   business, declare frontend_stack vite_react_ts before business_create_workspace,
   confirm the platform auto-seeds product/site from
   plugins/takyon/subuser_app_kit/scaffold/, theme tokens from a real brief, build
   green, refresh shows NO server-entrypoint blockers and NO
   scaffold_placeholder_tokens advisory.
7. NOT yet implemented (named §17 follow-ups — do not assume them): machinery
   landing prerender/SEO parity, legacy-product migration, and
   task_b388902b (ordering-sensitive web_server test isolation).
MANUAL
exit $(( fail > 0 ))
