-- =============================================================================
-- Phase 1: Performance indexes — apply to production database
-- Target DB: coremasterkb @ 121.89.90.178:5432
-- =============================================================================

-- 1. Fix FTS index: remove COALESCE wrapper (search_text is NOT NULL)
--    Old index cannot be used for expression matches because the expression
--    in the query (to_tsvector('simple', search_text)) doesn't match
--    the indexed expression (to_tsvector('simple', COALESCE(search_text, ''))).
DROP INDEX IF EXISTS idx_asset_ru_fts;
CREATE INDEX idx_asset_ru_fts
    ON asset_retrieval_units
    USING GIN (to_tsvector('simple', search_text));

-- 2. Add entity refs GIN index for JSONB @> containment queries
--    Enables: entity_refs_json::jsonb @> '[{"name":"SMF"}]'
CREATE INDEX IF NOT EXISTS idx_asset_ru_entity_refs_gin
    ON asset_retrieval_units
    USING GIN (entity_refs_json::jsonb);
