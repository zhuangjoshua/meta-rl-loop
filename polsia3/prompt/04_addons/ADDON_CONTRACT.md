# Add-On Contract

Each add-on owns its vendor/workflow details.

Manifest fields:

```ts
type AddonManifest = {
  key: string;
  title: string;
  version: string;
  capabilities: string[];
  requiredSecrets: string[];
  optionalSecrets: string[];
  workflows: string[];
  cronJobs: string[];
  promptIds: string[];
  uiPanels: string[];
}
```

Company add-on states:

```text
installed -> configured -> enabled
          -> blocked
          -> paused
          -> removed
```

Add-ons must expose:
- health check
- missing secrets/config
- workflow handlers
- optional cron handlers
- safe dry run where useful

