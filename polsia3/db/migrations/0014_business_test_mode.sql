ALTER TABLE businesses
  ADD COLUMN IF NOT EXISTS "mode" text NOT NULL DEFAULT 'live';

ALTER TABLE businesses DROP CONSTRAINT IF EXISTS businesses_mode_check;
ALTER TABLE businesses
  ADD CONSTRAINT businesses_mode_check
  CHECK ("mode" IN ('live', 'test'));

CREATE INDEX IF NOT EXISTS businesses_mode_idx
  ON businesses("mode", updated_at DESC);
