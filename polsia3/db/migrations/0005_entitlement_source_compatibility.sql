ALTER TABLE generated_app_entitlements
  ALTER COLUMN source SET DEFAULT 'manual';

UPDATE generated_app_entitlements
SET source = 'manual'
WHERE source = 'free';
