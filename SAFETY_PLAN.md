# Safety Plan

This file is the source of truth for the isolation rollout. Before changing runtime topology, auth, worker routing, or secret handling, check this file and make sure the change still matches the target here.

## Goal

We want all of the following at once:

1. Top-level users must not be able to read, write, or induce writes into other owners' businesses.
2. Sub-user apps must not gain general tool or filesystem authority.
3. Safebox must remain the only secret/funding authority and must not be embedded into each worker.
4. We must lose no current product capability:
   - top-level operator chat / CEO turns
   - wallet / billing / payouts
   - generated-app auth / checkout / usage / AI generate
   - long-running business jobs
   - business workspace durability

## Current Truth

### Rollout status as of 2026-06-02

- Existing operator droplet confirmed: `ubuntu-s-2vcpu-4gb-120gb-intel-nyc1` at `137.184.75.57` in `NYC1` on VPC `default-nyc1`.
- New Safebox droplet created: `takyon-safebox` (`1 GB / 1 vCPU / 25 GB`, droplet id `574885009`, public `67.205.158.170`, private `10.116.0.2`) in the same `NYC1` / `default-nyc1` network.
- New sub-user droplet created: `takyon-subuser` (`4 GB / 2 AMD vCPU / 80 GB`, droplet id `574885454`, public `134.209.123.8`, private `10.116.0.3`) in the same `NYC1` / `default-nyc1` network.
- Dedicated firewall objects created:
  - existing operator firewall: `argon-alpha`
  - new Safebox firewall: `takyon-safebox-fw`
  - new sub-user firewall: `takyon-subuser-fw`
- Firewall hardening now matches the intended public/private split:
  - `argon-alpha`: `SSH 22` from `73.63.144.229/32`, `HTTP 80` public, `HTTPS 443` public
  - `takyon-safebox-fw`: `SSH 22` from `73.63.144.229/32`, `TCP 8000` from private VPC CIDR `10.116.0.0/20`
  - `takyon-subuser-fw`: `SSH 22` from `73.63.144.229/32`, `HTTP 80` public, `HTTPS 443` public
- Code now contains the first split-plane enforcement pieces:
  - app-plane routes explicitly reject `tk_` owner tokens
  - Safebox has a dedicated service app (`plugins/takyon/safebox_app.py`)
  - Safebox callers can switch from in-process authority to remote HTTP authority through `TAKYON_SAFEBOX_URL`
  - `takyon_cli.web_server` now supports explicit host roles via `TAKYON_HOST_ROLE=combined|operator|subuser`
  - `subuser` role serves only product hosts plus app-runtime rails and rejects dashboard/operator chat surfaces
  - `operator` role rejects product hosts and public app-runtime routes
  - the tracked operator services now set `TERMINAL_ENV=docker` and `TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=true`
  - the existing tool Docker backend now uses the gateway session key as its sandbox key and prefers the session workspace root as cwd, so scoped operator shell/file tools stop sharing one global default container
  - tracked deploy/service assets now exist for all three planes:
    - `deploy/argon-alpha-14/*`
    - `deploy/takyon-subuser/*`
    - `deploy/takyon-safebox/*`
- `.github/workflows/deploy.yml` now has optional Safebox/sub-user deploy steps when their host secrets are configured
- `.github/workflows/deploy.yml` now probes SSH reachability first and skips unreachable remote deploy steps instead of failing the whole push when GitHub-hosted runners are outside the local-only firewall allowlist
- Honest current gap: the repo/runtime split is now wired, but live cutover is not finished yet. Safebox is still not confirmed running as its own live service, DNS/Caddy cutover for shared product hosts is still pending, and top-level mutation is only partially Docker-enforced: terminal/file sandboxing now routes through the existing Docker backend, but not every business mutation path has been reduced to “one explicit Docker job” yet.

### Business files

- Durable business workspace bytes are already S3-backed through `supabase_s3`.
- Runtime execution still materializes one business into local scratch while it runs.
- The seam for this already exists in `hermes-agent-main/plugins/takyon/storage.py` via `isolated_business_workspace(...)`.

### Top-level operator path

- The main top-level path is not `/v1`. It is dashboard chat into `tui_gateway`.
- `session.create` attaches `operator_user_id`.
- Business scope changes are owner-gated.
- Business-scoped CEO turns already run inside `isolated_business_workspace(...)`.
- Safebox is still in-process, not isolated as a separate service yet.

### Sub-user app path

- The sub-user app path is already a narrow backend surface:
  - `auth`
  - `checkout`
  - `usage`
  - `generate`
- `generate` goes through the app AI gateway and uses `tkg_` plus app session auth.
- There is no generic “call tool by name” app endpoint today.

### Token truth

- `tk_` = top-level owner authority.
- `tkg_` = business app gateway authority.
- `tk_` and `tkg_` must remain disjoint.
- If a malicious top-level owner pastes `tk_` into their app, that is an attack surface against that owner's own operator authority unless the sub-user plane refuses `tk_`.

## Target Architecture

We split the system into three planes.

### 1. Safebox Plane

- One logical Safebox service per environment.
- Not one Safebox per worker.
- Not one Safebox per user.
- Operator and sub-user planes call it over a narrow service interface.
- Workers never receive raw platform secrets.

### 2. Operator Plane

- Host: `app.fourmanifold.com`
- Purpose: top-level dashboard, `tui_gateway`, CEO/operator chat, operator billing surfaces.
- Rule: business-scoped mutation and long-running work must go through isolated Docker workers.
- Shared operator process may orchestrate, inspect, read, and enqueue; it should not be the steady-state place where business filesystem mutation happens.

### 3. Sub-user Plane

- Host: product/business hosts such as `slug.fourmanifold.com` plus product app API surfaces.
- Purpose: app auth/session/checkout/usage/AI generate plus typed job enqueue/status.
- Rule: no general tools here.
- Rule: no `tui_gateway`, no `/api/ws`, no `/api/tui/rpc`, no generic shell, no generic business tool surface, no `/v1` owner API.

## Hard Rules

### No-tools rule for the `tkg_` plane

The sub-user plane must expose only narrow app/runtime actions:

- `auth.request`
- `auth.verify`
- `session.read`
- `account.read`
- `checkout.create`
- `usage.read`
- `usage.record`
- `ai.generate`
- `jobs.create`
- `jobs.status`

It must not expose:

- `tui_gateway`
- generic `business_*` tools
- shell/terminal
- generic file read/write APIs
- Docker control
- raw provider gateways intended for operators
- `/v1` owner endpoints

### Docker-only mutation rule for the top-level plane

Top-level users can still use the operator runtime normally, but:

- file-mutating business work
- builds
- publish/deploy actions
- long-running product/business jobs
- campaign work
- heavy agent tasks

must execute in an isolated worker container, not in the shared operator process.

### Token audience rule

- `tk_` is accepted only by the operator plane.
- `tkg_` is accepted only by the sub-user app plane.
- internal dashboard session tokens are accepted only by internal operator-only gateways.
- workers get a short-lived run token, not `tk_`, not `tkg_`, and not raw provider secrets.

## Worker Model

One job = one fresh container from a prebuilt image.

The image contains:

- worker code
- runtime dependencies

The job injects only:

- `business_slug`
- `job_id`
- short-lived run token
- one scratch Takyon home for exactly one business

The worker must not receive:

- raw `.env`
- Docker socket
- shared `.takyon`
- repo-root write access
- another business mount
- a public port

## VPS Topology

Minimum rollout target:

1. Existing operator VPS stays the operator plane.
2. Add one Safebox VPS.
3. Add one sub-user VPS.

Later scale-out:

- add more operator worker VPSes if needed
- add more sub-user worker VPSes if needed
- keep one logical Safebox service per environment

Do not run a separate full Safebox on every worker host.

## Firewall / Network Rules

### Safebox VPS

- Current enforced rule:
  - `SSH 22` from `73.63.144.229/32`
  - `TCP 8000` from `10.116.0.0/20`
- Public app ingress: none.
- Service traffic stays on the private network.

### Operator VPS

- Current enforced rule:
  - `SSH 22` from `73.63.144.229/32`
  - `HTTP 80` from `All IPv4` and `All IPv6`
  - `HTTPS 443` from `All IPv4` and `All IPv6`
- No public Docker API.

### Sub-user VPS

- Current enforced rule:
  - `SSH 22` from `73.63.144.229/32`
  - `HTTP 80` from `All IPv4` and `All IPv6`
  - `HTTPS 443` from `All IPv4` and `All IPv6`
- No public Docker API.

### All worker hosts

- no public Docker socket
- no worker control port exposed to the internet
- no direct secret file exposure

## Implementation Phases

### Phase 0. Freeze the contract

- Keep this file updated and use it as the rollout checklist.
- Do not add new broad tool surfaces to the sub-user plane.

### Phase 1. Provision network and VPS classes

- Keep the existing operator VPS.
- Provision:
  - one Safebox VPS
  - one sub-user VPS
- Place them on the same private network/VPC.
- Apply the firewall rules above before moving traffic.

### Phase 2. Extract Safebox

- Move Safebox from in-process module usage to a real service boundary.
- Keep one logical Safebox authority per environment.
- Operator plane and sub-user plane call Safebox over private network.
- Workers call operator/sub-user backends or Safebox narrow endpoints, not raw `.env`.

Current repo status:

- implemented in code and deploy assets
- not yet verified live on the dedicated Safebox VPS

### Phase 3. Lock the sub-user plane

- Serve only:
  - auth/session/account
  - checkout/usage
  - AI generate
  - typed jobs API
- Reject `tk_` on the sub-user plane.
- Accept only `tkg_` plus app session semantics there.
- Keep no general tools on this host.

Current repo status:

- implemented in `web_server.py` through `TAKYON_HOST_ROLE=subuser`
- tracked sub-user service and Caddy config exist
- live traffic cutover still pending

### Phase 4. Move long/mutating sub-user tasks to isolated workers

- Add typed `jobs.create` actions for app-originated long/mutating work.
- Run each job in a one-business container.
- Sync one business from S3 to scratch, run, then sync back.

### Phase 5. Enforce Docker-only mutation on the operator plane

- Audit operator-facing mutating paths.
- Route all business-scoped writes and long-running work through isolated workers.
- Current repo status:
  - partially implemented through the existing terminal/file Docker backend on the tracked operator services
  - business-scoped CEO turns already run inside `isolated_business_workspace(...)`
  - remaining gap is to finish reducing non-terminal mutation paths to explicit isolated-worker semantics, not to invent a brand-new Docker backend
- Leave shared operator process with orchestration/read/enqueue duties.

### Phase 6. Add more compute without changing trust boundaries

- More operator worker VPSes:
  - drain operator job queue
  - call shared Safebox
- More sub-user worker VPSes:
  - drain sub-user job queue
  - call shared Safebox
- Do not duplicate secret authority onto each worker host.

Deploy note under the current firewall:

- GitHub-hosted runners can no longer SSH into the VPSes while `22` is restricted to `73.63.144.229/32`.
- The tracked workflow now skips unreachable remote deploy steps cleanly and still reports build/compile status.
- Full push-to-all-hosts automation therefore still depends on either a reachable deploy runner or a firewall change.

## Capability Preservation

We must preserve all of these:

- top-level CEO turns
- wallet / top-up / payouts / creative-credit purchase
- generated-app auth and app sessions
- generated-app checkout
- generated-app usage recording
- generated-app AI generate
- business-scoped long-running work
- S3-backed business workspace durability

## Acceptance Criteria

We are done only when all of these are true:

1. A sub-user app cannot call any generic tool or filesystem endpoint because none exist on that plane.
2. A `tkg_` token cannot authenticate to operator APIs.
3. A `tk_` token is rejected on the sub-user plane.
4. A worker container can only see one business workspace at a time.
5. Workers have no raw `.env`.
6. Safebox is not embedded into each worker host as a second mutable authority copy.
7. Top-level business mutations and long jobs execute through isolated workers, not the shared operator process.
8. Cross-owner business access is blocked both at scope resolution and at job/workspace execution time.

## Baseline Graph: Current Top-Level Path

```mermaid
flowchart TD
    TU["Top-level user"] --> WS["Dashboard /api/ws or /api/tui/rpc"]
    WS --> TG["tui_gateway session"]
    TG --> OID["operator_user_id attached"]
    OID --> SCOPE["scope.set business:slug"]
    SCOPE --> OWNER["owner gate"]
    OWNER --> ISO["isolated_business_workspace(...)"]
    ISO --> S3["S3-backed business workspace"]
    ISO --> CEO["CEO turn runs in scratch home"]
    CEO --> S3
    CEO -. "Safebox still in-process today" .-> SB["Safebox module"]
```

## Target Graph: Operator Plane

```mermaid
flowchart TD
    TU["Top-level user"] --> OWEB["Operator dashboard / chat"]
    OWEB --> OVPS["Operator VPS"]
    OVPS --> TG["tui_gateway / CEO runtime"]
    OVPS --> OAPI["Operator-only API"]
    OVPS --> JQ["Operator job queue"]
    JQ --> OWORK["Isolated operator worker container"]
    OWORK --> S3["S3 business workspace"]
    OWORK --> PG["Postgres"]
    OVPS --> SB["Safebox VPS"]
    OWORK -. "no raw .env" .-> SB
    OWORK -. "one business only" .-> S3
    OVPS -. "owner-gated business scope" .-> PG
```

## Baseline Graph: Current Sub-user Path

```mermaid
flowchart TD
    SU["Sub-user"] --> FE["Frontend"]
    FE --> API["Current app API"]
    API --> AUTH["app session"]
    AUTH --> ROUTES["auth / checkout / usage / generate"]
    ROUTES --> AIG["AI gateway with tkg + app session"]
    AIG --> PG["Postgres app budget / usage"]
    AIG -. "no generic tool endpoint today" .-> X["no business tool runner"]
```

## Target Graph: Sub-user Plane

```mermaid
flowchart TD
    SU["Sub-user"] --> FE["App frontend"]
    FE --> SVPS["Sub-user VPS"]
    SVPS --> AAPI["Public app API"]
    AAPI --> AUTH["app session + tkg only"]
    AUTH --> SAFE["allowlisted inline actions only"]
    AUTH --> JOB["typed jobs.create only"]
    SAFE --> AIG["AI generate gateway"]
    JOB --> AWORK["Isolated app worker container"]
    AWORK --> S3["S3 business workspace"]
    AWORK --> PG["Postgres"]
    SVPS --> SB["Safebox VPS"]
    AWORK -. "no raw .env" .-> SB
    AUTH -. "cannot use tk" .-> OAPI["Operator-only API"]
    AUTH -. "cannot control workers directly" .-> DC["Docker / worker control"]
    AWORK -. "one business only" .-> S3
```

## Proof Graph Requirement

After each major phase, regenerate a proof graph from the live implementation and compare it to the target graphs above. Do not claim the architecture exists until the proof graph matches the deployed state.
