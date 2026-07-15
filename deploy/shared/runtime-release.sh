#!/usr/bin/env bash
# Shared revision-staged runtime activation primitives.
# Caller owns service quiescence; these functions never stop or restart a service themselves.

takyon_runtime_release_init() {
  local live_runtime="$1"
  local revision="$2"
  if ! [[ "$revision" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid deploy source revision: $revision" >&2
    return 1
  fi
  TAKYON_REMOTE_LIVE_RUNTIME="$live_runtime"
  TAKYON_REMOTE_STAGED_RUNTIME="${live_runtime}.staged-${revision}"
  TAKYON_REMOTE_BACKUP_RUNTIME="${live_runtime}.backup-${revision}"
  TAKYON_REMOTE_ACTIVATION_MARKER="${live_runtime}.activation-${revision}"
  TAKYON_REMOTE_RELEASE_META="${live_runtime}.release-${revision}"
  export \
    TAKYON_REMOTE_LIVE_RUNTIME \
    TAKYON_REMOTE_STAGED_RUNTIME \
    TAKYON_REMOTE_BACKUP_RUNTIME \
    TAKYON_REMOTE_ACTIVATION_MARKER \
    TAKYON_REMOTE_RELEASE_META
}

takyon_stage_runtime_release() {
  local artifact_runtime="$1"
  local target_host="$2"
  local target_key="$3"
  local revision="$4"
  local release_profile="${5:-full}"
  local ssh_command=(ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  # Keep the array non-empty: production deploys run under macOS Bash 3.2 with `set -u`, where
  # expanding an empty array is an unbound-variable error.
  local profile_rsync_filters=(--exclude '/.takyon-release-profile-noop')

  case "$release_profile" in
    full) ;;
    safebox|subuser)
      # Non-operator planes receive the shared backend runtime, never the operator-agent
      # implementation or its approved skill material. These paths are absent from the staged tree,
      # so activation's --delete also removes any legacy live copies.
      profile_rsync_filters+=(
        --exclude '/.claude/'
        --exclude '/skills/'
        --exclude '/plugins/takyon/bootstrap_phases.py'
        --exclude '/plugins/takyon/claude_sdk_runtime.py'
        --exclude '/plugins/takyon/claude_sdk_sessions.py'
        --exclude '/scripts/build_approved_skills_manifest.py'
        --exclude '/scripts/takyon-claude-agent-task.mjs'
        --exclude '/scripts/takyon-claude-primary-entrypoint.mjs'
        --exclude '/scripts/takyon-claude-primary-runtime.mjs'
      )
      ;;
    *)
      echo "unsupported runtime release profile: $release_profile" >&2
      return 1
      ;;
  esac

  "${ssh_command[@]}" "$target_host" \
    "set -euo pipefail
    if compgen -G '${TAKYON_REMOTE_LIVE_RUNTIME}.activation-*' >/dev/null; then
      echo 'refusing to stage over a pending runtime activation' >&2
      exit 1
    fi
    rm -rf '$TAKYON_REMOTE_STAGED_RUNTIME' '$TAKYON_REMOTE_BACKUP_RUNTIME' '$TAKYON_REMOTE_RELEASE_META'
    install -d '$TAKYON_REMOTE_STAGED_RUNTIME' '$TAKYON_REMOTE_RELEASE_META/candidates' '$TAKYON_REMOTE_RELEASE_META/backups'"

  # The sealed artifact is assembled on the operator's Mac. Preserve its deliberate 0444/0555
  # modes, but never transplant that Mac user's uid/gid onto a root-owned VPS runtime tree.
  rsync -az --delete --no-owner --no-group \
    --exclude '.git/' \
    --exclude '.pytest_cache/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'node_modules/' \
    --exclude 'web/node_modules/' \
    --exclude '/.venv' \
    "${profile_rsync_filters[@]}" \
    -e "ssh -i $target_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    "$artifact_runtime/" \
    "$target_host:$TAKYON_REMOTE_STAGED_RUNTIME/"

  "${ssh_command[@]}" "$target_host" \
    "set -euo pipefail
    if [[ -x '$TAKYON_REMOTE_LIVE_RUNTIME/.venv/bin/python' ]]; then
      ln -s '$TAKYON_REMOTE_LIVE_RUNTIME/.venv' '$TAKYON_REMOTE_STAGED_RUNTIME/.venv'
    fi
    python3 - '$TAKYON_REMOTE_STAGED_RUNTIME/.takyon-deploy-artifact.json' '$revision' <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
if manifest.get('source_revision') != sys.argv[2]:
    raise SystemExit('staged runtime source revision does not match the locked deploy revision')
PY"
}

takyon_prepare_runtime_rollback() {
  local target_host="$1"
  local target_key="$2"
  ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$target_host" \
    "set -euo pipefail
    if compgen -G '${TAKYON_REMOTE_LIVE_RUNTIME}.activation-*' >/dev/null; then
      echo 'refusing to replace a pending runtime rollback' >&2
      exit 1
    fi
    rm -rf '$TAKYON_REMOTE_BACKUP_RUNTIME'
    cp -al '$TAKYON_REMOTE_LIVE_RUNTIME' '$TAKYON_REMOTE_BACKUP_RUNTIME'"
}

takyon_begin_runtime_activation() {
  local target_host="$1"
  local target_key="$2"
  ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$target_host" \
    "set -euo pipefail
    test -d '$TAKYON_REMOTE_BACKUP_RUNTIME'
    test -d '$TAKYON_REMOTE_STAGED_RUNTIME'
    touch '$TAKYON_REMOTE_ACTIVATION_MARKER'"
}

takyon_activate_staged_runtime() {
  local target_host="$1"
  local target_key="$2"
  local revision="$3"
  ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$target_host" \
    "set -euo pipefail
    test -d '$TAKYON_REMOTE_BACKUP_RUNTIME'
    test -d '$TAKYON_REMOTE_STAGED_RUNTIME'
    test -f '$TAKYON_REMOTE_ACTIVATION_MARKER'
    rsync -a --delete \
      --filter='protect /.venv' \
      --exclude '.git/' \
      --exclude '.pytest_cache/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude 'node_modules/' \
      --exclude 'web/node_modules/' \
      --exclude '/.venv' \
      '$TAKYON_REMOTE_STAGED_RUNTIME/' '$TAKYON_REMOTE_LIVE_RUNTIME/'
    # rsync protects generated bytecode, so explicitly remove retired source
    # trees that could otherwise survive only because they contain __pycache__.
    rm -rf \
      '$TAKYON_REMOTE_LIVE_RUNTIME/optional-skills' \
      '$TAKYON_REMOTE_LIVE_RUNTIME/plugins/takyon/references/polsia3-skills'
    python3 - '$TAKYON_REMOTE_LIVE_RUNTIME/.takyon-deploy-artifact.json' '$revision' <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
if manifest.get('source_revision') != sys.argv[2]:
    raise SystemExit('activated runtime source revision does not match the locked deploy revision')
PY"
}

takyon_rollback_runtime_if_pending() {
  local target_host="$1"
  local target_key="$2"
  ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$target_host" \
    "set -euo pipefail
    if [[ -f '$TAKYON_REMOTE_ACTIVATION_MARKER' ]]; then
      if [[ ! -d '$TAKYON_REMOTE_BACKUP_RUNTIME' ]]; then
        echo 'runtime activation marker exists without its rollback tree' >&2
        exit 1
      fi
      rsync -a --delete \
        --filter='protect /.venv' \
        --exclude '.git/' \
        --exclude '.pytest_cache/' \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude 'node_modules/' \
        --exclude 'web/node_modules/' \
        --exclude '/.venv' \
        '$TAKYON_REMOTE_BACKUP_RUNTIME/' '$TAKYON_REMOTE_LIVE_RUNTIME/'
      rm -f '$TAKYON_REMOTE_ACTIVATION_MARKER'
    fi"
}

takyon_finalize_runtime_release() {
  local target_host="$1"
  local target_key="$2"
  ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$target_host" \
    "set -euo pipefail
    # Removing the marker commits the already-proven release. Cleanup after this point may leave
    # harmless residue, but it can no longer trigger an impossible partial rollback.
    rm -f '$TAKYON_REMOTE_ACTIVATION_MARKER'
    rm -rf '$TAKYON_REMOTE_STAGED_RUNTIME' '$TAKYON_REMOTE_BACKUP_RUNTIME' '$TAKYON_REMOTE_RELEASE_META'"
}

takyon_discard_staged_runtime_release() {
  local target_host="$1"
  local target_key="$2"
  ssh -i "$target_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$target_host" \
    "set -euo pipefail
    if [[ -f '$TAKYON_REMOTE_ACTIVATION_MARKER' ]]; then
      echo 'refusing to discard a release with a pending rollback' >&2
      exit 1
    fi
    rm -rf '$TAKYON_REMOTE_STAGED_RUNTIME' '$TAKYON_REMOTE_BACKUP_RUNTIME' '$TAKYON_REMOTE_RELEASE_META'"
}
