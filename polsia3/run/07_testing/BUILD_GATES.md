# Build Gates

Generated app cannot be marked ready unless:

- source exists
- install succeeds
- typecheck succeeds
- build succeeds
- smoke tests pass
- deploy succeeds
- deployed URL health check passes

Platform cannot be considered ready unless:

- migrations run
- typecheck passes
- build passes
- core route smoke tests pass
- cron secret configured
- required secrets documented

