-- =============================================================================
-- Asset Core Schema v1.1 → v1.2: Performance indexes
-- Date: 2026-05-21
-- Target DB: PostgreSQL 14+ (coremasterkb)
-- Idempotent: yes (IF NOT EXISTS / IF EXISTS throughout)
-- One-time migration: run once, then this file is the schema of record
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Entity refs GIN index
--
--    Purpose: accelerate JSONB @> containment queries in searchByEntityExact.
--
--    Before: query used jsonb_array_elements() to expand the array and check
--            each element's "name" field with an IN-list. This is a full table
--            scan — no index can help.
--
--    After:  query uses entity_refs_json @> '[{"name":"SMF"}]'::jsonb
--            This GIN index makes each containment check an index lookup.
--
--    Column type: JSONB (native, not TEXT cast) — see 002_asset_core_postgresql.sql line 196
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_asset_ru_entity_refs_gin
    ON asset_retrieval_units USING GIN (entity_refs_json);
