"""Database layer for the takyon control plane.

Holds the SQL migrations (``migrations/``) and the single idempotent runner (``runner.py``) that
applies them. The runner is the one production "bring the DB to current" path; the test fixtures
delegate to it so the schema tests run against and the schema production runs against come from one
definition. See mediationplan.md > Runtime Cutover.
"""
