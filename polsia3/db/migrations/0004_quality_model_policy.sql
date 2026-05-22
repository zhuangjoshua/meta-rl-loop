UPDATE project_ai_model_policies
SET provider = 'anthropic',
  model = 'claude-opus-4-7',
  quality_tier = 'frontier',
  max_input_tokens = GREATEST(max_input_tokens, 12000),
  max_output_tokens = GREATEST(max_output_tokens, 2200),
  max_estimated_cost_microusd = GREATEST(max_estimated_cost_microusd, 3000000),
  metadata = metadata || '{"source":"v3_frontier_policy_migration","tier":"frontier_default","replaces":"gpt-4.1-mini"}'::jsonb,
  updated_at = now()
WHERE purpose = 'product'
  AND (model IN ('gpt-4.1-mini', 'claude-sonnet-4-6') OR quality_tier IN ('fast', 'quality'));
