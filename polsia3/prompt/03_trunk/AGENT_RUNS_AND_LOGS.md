# Agent Runs And Logs

Every workflow/agent run stores:

- run id
- company id
- task id if any
- workflow id
- add-on key if any
- agent key
- status
- input snapshot
- prompt id/version if used
- output
- error
- started/completed timestamps

Every step stores:

- run id
- step index
- tool/vendor name
- input
- output
- error
- receipt id if any

## Success Rule

An agent run cannot complete if its promised deliverable is missing.

Examples:
- research workflow must save a report
- build workflow must save build evidence
- vendor action must save receipt

