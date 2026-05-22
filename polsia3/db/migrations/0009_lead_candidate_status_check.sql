ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_check;
ALTER TABLE leads
  ADD CONSTRAINT leads_status_check
  CHECK (status IN ('new', 'candidate', 'pending', 'contacted', 'qualified', 'converted', 'archived', 'blocked', 'failed'));
