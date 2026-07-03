#!/usr/bin/env bash
# Shared source of truth for named operator profiles, used by BOTH rails:
#   scripts/takyon-operator-prod.sh   (against the prod control plane)
#   scripts/takyon-operator-dev.sh    (against the dev twin)
# so `... sai` / `... josh` mean the same person in dev and prod.
#
# Add a teammate with ONE line in the case below, or in scripts/operator-users.conf
# ("<name> <uuid>" per line, '#' comments ok). Raw UUIDs always pass through unchanged.

# Resolve a short name -> Takyon operator user-id. Unknown input passes through as-is.
resolve_operator_alias() {
  local name="$1"
  case "$name" in
    sai)  printf '%s' 'c351355a-34f4-4885-8c60-429bf79bd7a5' ;;
    josh) printf '%s' '150e4213-4006-4dc1-9cf3-ca7ab3b4696f' ;;
    *)
      local conf="${OPERATOR_USERS_CONF:-$ROOT/scripts/operator-users.conf}" n u
      if [[ -f "$conf" ]]; then
        while read -r n u _; do
          [[ -z "$n" || "${n#\#}" != "$n" ]] && continue
          [[ "$n" == "$name" ]] && { printf '%s' "$u"; return 0; }
        done < "$conf"
      fi
      printf '%s' "$name"
      ;;
  esac
}

# True only when the argument is a KNOWN profile name (resolves to a different value).
is_operator_alias() {
  local resolved; resolved="$(resolve_operator_alias "$1")"
  [[ -n "$1" && "$resolved" != "$1" ]]
}
