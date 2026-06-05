-- 0016_disable_test_mode.sql
-- Turn off business test mode globally: normalize stored businesses to live,
-- make live the default, and align the app-surface contract default wording.

BEGIN;

UPDATE businesses
SET mode = 'live'
WHERE mode IS NULL OR mode <> 'live';

ALTER TABLE businesses
    ALTER COLUMN mode SET DEFAULT 'live';

UPDATE app_surface_contracts
SET mode_behavior = 'live_only_hard_fail_missing_gates'
WHERE mode_behavior IS NULL
   OR mode_behavior = ''
   OR mode_behavior = 'test_mode_publishes_product_surface';

ALTER TABLE app_surface_contracts
    ALTER COLUMN mode_behavior SET DEFAULT 'live_only_hard_fail_missing_gates';

COMMIT;
