# coscale.app Google Search Console domain property

Date applied: 2026-06-21

Domain: `coscale.app`
Search Console property: `sc-domain:coscale.app`
Service account owner: `gcs-service-account@gcs-api-499216.iam.gserviceaccount.com`

This file records the one-time Google Search Console domain-property ownership step for the
current Takyon product domain. It mirrors the inherited ownership model that already covered
legacy `*.fourmanifold.com` sites.

## DNS verification

Cloudflare zone: `coscale.app`

TXT record added at the zone apex:

```text
coscale.app TXT "google-site-verification=kH9_pL4cysCDGrfnQZjXtEX6UicgLLBYVsZ5b9ceJoc"
```

The record is DNS-only with automatic TTL. It is intentionally alongside the pre-existing
Google verification TXT record for a different owner.

## Google verification result

The token was generated through the Google Site Verification API using the Safebox-held
`TAKYON_GSC_SERVICE_ACCOUNT_KEY` service account.

Verification result:

```text
site_verification_insert= {'id': 'dns%3A%2F%2Fcoscale.app', 'site': {'type': 'INET_DOMAIN', 'identifier': 'coscale.app'}, 'owners': ['gcs-service-account@gcs-api-499216.iam.gserviceaccount.com', 'tejas@fourmanifold.com']}
search_console_add=sc-domain:coscale.app
coscale_entries= [{'siteUrl': 'sc-domain:coscale.app', 'permissionLevel': 'siteOwner'}]
```

This means new `*.coscale.app` business sites are covered by inherited Search Console domain
ownership, and `business_seo_add_property` can add URL-prefix child properties under the verified
parent.

## Verification commands

DNS:

```bash
dig +short TXT coscale.app @1.1.1.1
dig +short TXT coscale.app @8.8.8.8
```

GSC from the operator VPS:

```bash
ssh -i ~/.ssh/takyon_argon_alpha14 root@137.184.75.57

env TAKYON_HOME=/opt/takyon/.takyon HOME=/root PYTHONUNBUFFERED=1 \
  TAKYON_DB_BACKEND=postgres TAKYON_HOST_ROLE=operator \
  TAKYON_SAFEBOX_URL=http://10.116.0.2:8000 \
  /opt/takyon/hermes-agent-main/.venv/bin/python - <<'PY'
from plugins.takyon.core import load_takyon_env, _resolve_gsc_service_account_json, _GSC_OAUTH_SCOPES
from google.oauth2 import service_account
from googleapiclient import discovery
import json

load_takyon_env()
info = json.loads(_resolve_gsc_service_account_json())
creds = service_account.Credentials.from_service_account_info(info, scopes=list(_GSC_OAUTH_SCOPES))
sc = discovery.build("searchconsole", "v1", credentials=creds, cache_discovery=False)
entries = sc.sites().list().execute().get("siteEntry", [])
print([entry for entry in entries if "coscale.app" in entry.get("siteUrl", "")])
PY
```

Expected GSC output includes:

```text
{'siteUrl': 'sc-domain:coscale.app', 'permissionLevel': 'siteOwner'}
```
