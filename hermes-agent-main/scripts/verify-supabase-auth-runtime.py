from __future__ import annotations

import os
import sys


def _resolve_public_value(safebox, *names: str) -> str:
    resolved_names: list[str] = []
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        resolved_names.append(name)
        direct = str(os.getenv(name) or "").strip()
        if direct:
            return direct
    if not resolved_names:
        return ""
    try:
        value = str(safebox.first_env_backed_value(*resolved_names) or "").strip()
    except Exception:
        value = ""
    if value:
        return value
    for name in resolved_names:
        try:
            value = str(safebox.read_env_backed_value(name) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def main() -> int:
    from plugins.takyon.core import load_takyon_env
    from plugins.takyon import safebox

    load_takyon_env()
    project_url = _resolve_public_value(
        safebox,
        "SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_URL",
        "TAKYON_SUPABASE_URL",
    )
    publishable = _resolve_public_value(
        safebox,
        "SUPABASE_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    )
    anon = _resolve_public_value(
        safebox,
        "SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    )
    legacy_secret = str(safebox.read_env_backed_value("SUPABASE_JWT_SECRET") or "").strip()
    missing: list[str] = []
    if not project_url:
        missing.append("SUPABASE_URL")
    if not publishable and not anon:
        missing.append("SUPABASE_PUBLISHABLE_KEY|SUPABASE_ANON_KEY")
    if missing:
        raise SystemExit(
            "Supabase auth config is not readable through Safebox: "
            + ", ".join(missing)
        )
    key_name = "SUPABASE_PUBLISHABLE_KEY" if publishable else "SUPABASE_ANON_KEY"
    sys.stdout.write(
        "Validated Supabase auth config via Safebox "
        f"(SUPABASE_URL, {key_name}; legacy SUPABASE_JWT_SECRET={'present' if legacy_secret else 'absent'})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
