ALTER TABLE generated_app_magic_links
  ADD COLUMN IF NOT EXISTS email text,
  ADD COLUMN IF NOT EXISTS used_at timestamptz;

CREATE TABLE IF NOT EXISTS leads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  email text NOT NULL,
  name text,
  source text NOT NULL DEFAULT 'website',
  status text NOT NULL DEFAULT 'new',
  outbound_vendor text,
  campaign_url_or_vendor_id text,
  last_event text,
  last_contacted_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, email)
);

CREATE TABLE IF NOT EXISTS business_email_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  direction text NOT NULL CHECK (direction IN ('outbound', 'inbound')),
  from_email text NOT NULL,
  to_email text NOT NULL,
  subject text NOT NULL,
  body_text text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'proposed',
  action_id uuid,
  provider_message_id text,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  created_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  sent_at timestamptz,
  lead_id uuid REFERENCES leads(id) ON DELETE SET NULL,
  provider text,
  audience_type text NOT NULL DEFAULT 'cold',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE cold_outreach_events
  ALTER COLUMN recipient DROP NOT NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'cold_outreach_events'
      AND column_name = 'lead_id'
  ) AND NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'cold_outreach_events_lead_id_fkey'
  ) THEN
    ALTER TABLE cold_outreach_events
      ADD CONSTRAINT cold_outreach_events_lead_id_fkey
      FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS leads_business_created_idx ON leads(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS business_email_messages_business_created_idx ON business_email_messages(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS business_email_messages_business_status_idx ON business_email_messages(business_id, status, created_at DESC);

DROP TRIGGER IF EXISTS leads_set_updated_at ON leads;
CREATE TRIGGER leads_set_updated_at BEFORE UPDATE ON leads FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_email_messages_set_updated_at ON business_email_messages;
CREATE TRIGGER business_email_messages_set_updated_at BEFORE UPDATE ON business_email_messages FOR EACH ROW EXECUTE FUNCTION set_updated_at();
