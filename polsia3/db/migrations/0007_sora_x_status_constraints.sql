ALTER TABLE business_social_posts DROP CONSTRAINT IF EXISTS business_social_posts_status_check;
ALTER TABLE business_social_posts
  ADD CONSTRAINT business_social_posts_status_check
  CHECK (status IN ('proposed', 'ready', 'published', 'failed'));

ALTER TABLE media_generation_jobs DROP CONSTRAINT IF EXISTS media_generation_jobs_provider_check;
ALTER TABLE media_generation_jobs
  ADD CONSTRAINT media_generation_jobs_provider_check
  CHECK (provider IN ('atlas', 'openai'));

ALTER TABLE media_generation_jobs DROP CONSTRAINT IF EXISTS media_generation_jobs_storage_provider_check;
ALTER TABLE media_generation_jobs
  ADD CONSTRAINT media_generation_jobs_storage_provider_check
  CHECK (storage_provider IN ('atlas_url', 'vercel_blob', 'openai_proxy'));
