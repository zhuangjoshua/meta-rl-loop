# Auth, Companies, Tasks, Events

## Auth

Keep Auth0 support from v2. Local auth bypass may exist for local development only.

## Companies

A company is the operator workspace. It has:
- owner
- members
- public title/pitch
- site slug
- generated app state
- documents/reports
- tasks
- events

## Tasks

Tasks must support:
- queued
- running
- completed
- blocked
- failed
- cancelled

`blocked` means missing input/config/policy/budget/approval.

`failed` means execution errored.

## Events

Every important transition writes an event.

The dashboard activity feed should be explainable from events.

