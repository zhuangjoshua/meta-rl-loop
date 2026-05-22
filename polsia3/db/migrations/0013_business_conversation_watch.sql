CREATE TABLE IF NOT EXISTS business_conversation_threads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL,
  source text NOT NULL,
  external_id text NOT NULL DEFAULT '',
  url text,
  title text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  last_checked_at timestamptz,
  last_message_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE business_conversation_threads DROP CONSTRAINT IF EXISTS business_conversation_threads_status_check;
ALTER TABLE business_conversation_threads
  ADD CONSTRAINT business_conversation_threads_status_check
  CHECK (status IN ('active', 'paused', 'archived'));

CREATE TABLE IF NOT EXISTS business_conversation_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  thread_id uuid NOT NULL REFERENCES business_conversation_threads(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL,
  source text NOT NULL,
  external_id text NOT NULL DEFAULT '',
  direction text NOT NULL,
  author_label text NOT NULL DEFAULT '',
  body text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'needs_response',
  received_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE business_conversation_messages DROP CONSTRAINT IF EXISTS business_conversation_messages_direction_check;
ALTER TABLE business_conversation_messages
  ADD CONSTRAINT business_conversation_messages_direction_check
  CHECK (direction IN ('inbound', 'outbound', 'internal'));

ALTER TABLE business_conversation_messages DROP CONSTRAINT IF EXISTS business_conversation_messages_status_check;
ALTER TABLE business_conversation_messages
  ADD CONSTRAINT business_conversation_messages_status_check
  CHECK (status IN ('needs_response', 'responded', 'ignored', 'archived'));

CREATE UNIQUE INDEX IF NOT EXISTS business_conversation_threads_external_uidx
  ON business_conversation_threads(business_id, source, external_id)
  WHERE external_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS business_conversation_messages_external_uidx
  ON business_conversation_messages(business_id, source, external_id)
  WHERE external_id <> '';

CREATE INDEX IF NOT EXISTS business_conversation_threads_business_status_idx
  ON business_conversation_threads(business_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS business_conversation_messages_business_status_idx
  ON business_conversation_messages(business_id, status, received_at DESC);

DROP TRIGGER IF EXISTS business_conversation_threads_set_updated_at ON business_conversation_threads;
CREATE TRIGGER business_conversation_threads_set_updated_at BEFORE UPDATE ON business_conversation_threads FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_conversation_messages_set_updated_at ON business_conversation_messages;
CREATE TRIGGER business_conversation_messages_set_updated_at BEFORE UPDATE ON business_conversation_messages FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO addons (addon_key, title, status)
VALUES ('conversation_watch', 'Conversation Watch', 'available')
ON CONFLICT (addon_key) DO NOTHING;

INSERT INTO action_policies (policy_key, title, default_requires_approval, metadata)
VALUES (
  'distribution.response_check',
  'Handle Replies Before Distribution',
  true,
  '{"blocks_new_distribution_when_unresolved":true,"scope":"per_business"}'::jsonb
)
ON CONFLICT (policy_key) DO NOTHING;
