ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS url text;

ALTER TABLE leads
  ALTER COLUMN email DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS leads_business_url_uidx
  ON leads(business_id, url)
  WHERE url IS NOT NULL;

CREATE INDEX IF NOT EXISTS leads_business_status_idx
  ON leads(business_id, status, created_at DESC);

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_check;
ALTER TABLE leads
  ADD CONSTRAINT leads_status_check
  CHECK (status IN ('new', 'candidate', 'pending', 'contacted', 'qualified', 'converted', 'archived', 'blocked', 'failed'));
