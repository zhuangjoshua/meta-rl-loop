"""Print a worker job's status/error/attempts from the Postgres jobs table."""
import sys

sys.path.insert(0, "/opt/takyon/hermes-agent-main")
import psycopg  # noqa: E402
from plugins.takyon.runtime_app import resolve_database_url  # noqa: E402

job_id = sys.argv[1] if len(sys.argv) > 1 else ""
conn = psycopg.connect(resolve_database_url(), autocommit=True)
try:
    if job_id:
        row = conn.execute(
            "select id, kind, status, attempts, error, updated_at "
            "from jobs where id=%s",
            (job_id,),
        ).fetchone()
        print("JOB:", row)
    # also show the most recent x.publish jobs
    rows = conn.execute(
        "select id, status, attempts, error, updated_at from jobs "
        "where kind='x.publish_outreach' order by updated_at desc limit 5"
    ).fetchall()
    print("RECENT x.publish_outreach:")
    for r in rows:
        print(" ", r)
finally:
    conn.close()
