# Production Fixes

This document captures the minimum-delta production fixes that materially improve Takyon's VPS backend without changing the overall three-plane topology (`operator`, `sub-user`, `safebox`).

It focuses on four questions:

1. What is most fragile today?
2. What are the smallest high-value security fixes?
3. What are the smallest high-value scalability fixes?
4. What are the smallest high-value efficiency fixes?

## Minimum-Delta Security / Fragility Fixes

These should be treated as the first production hardening pass.

### 1. Make Safebox fail closed on auth

Current risk:

- `hermes-agent-main/plugins/takyon/safebox_app.py` currently disables auth entirely if `TAKYON_SAFEBOX_TOKEN` is unset.
- That means Safebox safety depends too heavily on firewall/VPC correctness.

Minimum fix:

- Change Safebox startup so a missing `TAKYON_SAFEBOX_TOKEN` is a hard error, or make every protected request return `401` when the token is missing.
- Keep the existing bearer-token shape; do not redesign the protocol yet.

Why this is the minimum delta:

- It closes the sharpest security edge without changing host topology, client callsites, or the VPC routing model.

### 2. Run services as a dedicated non-root user

Current risk:

- The tracked systemd units currently run as `root` by default because they do not set `User=...` and they use `HOME=/root`.
- Any app compromise gets a larger blast radius than necessary.

Minimum fix:

- Create a dedicated system user such as `takyon`.
- Update the tracked service units to run as that user:
  - `deploy/argon-alpha-14/takyon-dashboard.service`
  - `deploy/argon-alpha-14/takyon-worker.service`
  - `deploy/takyon-subuser/takyon-subuser.service`
  - `deploy/takyon-safebox/takyon-safebox.service`
- Update directory ownership for `/opt/takyon/hermes-agent-main` and `/opt/takyon/.takyon` as needed.

Why this is the minimum delta:

- It preserves the exact deployment model and command lines while materially reducing impact from a runtime escape or dependency bug.

### 3. Add basic systemd hardening flags

Current risk:

- The services run with broad default permissions and little containment.

Minimum fix:

- Add conservative hardening flags to all tracked units, starting with:
  - `NoNewPrivileges=true`
  - `PrivateTmp=true`
  - `ProtectHome=true`
  - `ProtectSystem=full` or `strict`
  - `ReadWritePaths=` for the specific writable Takyon paths
- Roll these in carefully to avoid blocking required writes.

Why this is the minimum delta:

- It improves containment without changing the app architecture, reverse proxying, or deploy rails.

### 4. Stop mutating Python dependencies during deploy

Current risk:

- The operator deploy script may install missing dependencies on the host during deploy.
- That means production behavior depends partly on accumulated mutable host state.

Minimum fix:

- Remove ad hoc `pip install` behavior from deploy scripts.
- Move required Python dependencies into the tracked runtime/bootstrap path so the host is prepared intentionally rather than repaired implicitly.
- If a dependency is required for production, fail deploy loudly when it is absent.

Why this is the minimum delta:

- It makes production reproducible without requiring a full containerized rewrite.

### 5. Replace `curl | bash` bootstrap installs with pinned artifacts or tracked install steps

Current risk:

- The operator bootstrap script currently installs `xurl` using a remote shell script.
- That is a supply-chain and reproducibility weak point.

Minimum fix:

- Pin the exact install source and version, or move to a checked-in install path with explicit verification.
- Keep the same tool if needed; just make acquisition deterministic.

Why this is the minimum delta:

- It hardens bootstrap without changing the deploy workflow shape.

### 6. Tighten SSH host verification for deploys

Current risk:

- The tracked deploy scripts use `StrictHostKeyChecking=accept-new`.
- This is convenient but weaker than pinned host verification.

Minimum fix:

- Maintain pinned host keys in the deploy environment or a tracked known-hosts file.
- Switch deploy scripts to strict verification once the keys are in place.

Why this is the minimum delta:

- It improves deploy-path integrity without replacing SSH-based deployment.

## Minimum-Delta Scalability Fixes

These are the smallest changes that improve scale posture without changing the overall architecture.

### 1. Allow more than one worker by configuration, not by redesign

Current posture:

- The worker model is already directionally good because it is Postgres-backed and is documented as safe with `FOR UPDATE SKIP LOCKED`.
- In practice, the tracked deployment appears to run one worker instance.

Minimum fix:

- Add a documented, supported path for scaling worker count on the operator plane.
- Keep the same job-claiming model.
- Start by validating that two workers can run safely under systemd on the operator host.

Why this matters:

- This is the cheapest path to higher throughput for queued work and wake processing.

### 2. Reduce operator-edge dependence for product traffic

Current posture:

- The operator edge currently proxies shared product-host traffic to the sub-user plane.
- That makes the operator host a routing chokepoint for part of the product surface.

Minimum fix:

- Keep the topology, but make the sub-user plane the clearer owner of product-host ingress over time.
- At minimum, document operator-edge dependency as a capacity and failure-domain limit, and avoid adding more product routing responsibility to the operator plane.

Why this matters:

- It reduces cross-plane coupling and makes future scaling cleaner.

### 3. Make sub-user deploy independent of operator-host file copying

Current posture:

- Sub-user deploy still copies `product-sites` from the operator host.

Minimum fix:

- Move `product-sites` sync toward a canonical shared source rather than tar-streaming from the operator host.
- If that cannot happen immediately, at least treat the current sync as a tracked temporary dependency and keep it visible.

Why this matters:

- It reduces deployment coupling and improves recoverability of the sub-user plane.

### 4. Add lightweight operational visibility before adding more infrastructure

Current posture:

- The current system can run, but diagnosing saturation or queue lag will be harder than it should be.

Minimum fix:

- Add a small, tracked set of production metrics/log checks:
  - worker queue depth
  - stale/running job count
  - Safebox latency/error count
  - dashboard and sub-user health check latency
- Prefer small visibility additions before introducing more moving parts.

Why this matters:

- Scaling decisions become evidence-based instead of guess-based.

## Minimum-Delta Efficiency Fixes

These focus on better throughput and lower operational waste with minimal architecture churn.

### 1. Avoid unnecessary rebuild/rebootstrap work during deploy

Current posture:

- The workflow already avoids some repeated work, but the VPS scripts still do several verification and repair steps on every deploy.

Minimum fix:

- Keep the current safety checks, but separate true bootstrap-only steps from ordinary runtime deploy steps.
- Ensure one-time host preparation stays in bootstrap, and recurring deploy only validates expected invariants.

Why this matters:

- Faster deploys reduce downtime risk and operator friction.

### 2. Keep Docker image availability warm on the operator host

Current posture:

- Operator deploy validates Docker and may pull the worker image during deployment.

Minimum fix:

- Keep the tracked image pre-pulled on the operator host as part of bootstrap or a periodic maintenance path.
- Fail deploy if the image is missing only when the host has drifted unexpectedly.

Why this matters:

- It reduces deploy latency and avoids surprise cold-start delays during production changes.

### 3. Scale throughput with extra workers before introducing new services

Current posture:

- The biggest likely throughput ceiling is queued background work, not reverse proxy overhead.

Minimum fix:

- If throughput becomes a problem, add another worker process before introducing a more complex queue stack.
- Measure queue lag first, then scale the worker count.

Why this matters:

- It is the cheapest efficiency gain available in the current architecture.

### 4. Keep the app servers simple and loopback-bound

Current posture:

- Operator and sub-user runtimes are already bound to `127.0.0.1` behind Caddy, which is a good operational baseline.

Minimum fix:

- Preserve this posture.
- Do not add direct public exposure of the app servers just to avoid proxy configuration work.

Why this matters:

- It keeps the public surface small and avoids introducing performance/security regressions in the name of convenience.

## Suggested Priority Order

If only a few changes happen immediately, do them in this order:

1. Safebox fail-closed auth
2. Non-root service users
3. Remove deploy-time Python dependency mutation
4. Basic systemd hardening flags
5. Pinned bootstrap/install sources
6. Pinned SSH host verification

For scale/efficiency, the best minimum-delta sequence is:

1. Add basic queue/health visibility
2. Validate multi-worker operator scaling
3. Reduce operator-plane routing dependence for product traffic
4. Decouple sub-user product-site sync from the operator host

## Summary

The current setup is viable for a small production system, but the smallest meaningful improvements are about hardening boundaries and reducing hidden host-state dependency, not replacing the architecture.

The main theme of the minimum-delta plan is:

- fail closed
- run with less privilege
- make deploys reproducible
- scale the existing worker model before adding new infrastructure
