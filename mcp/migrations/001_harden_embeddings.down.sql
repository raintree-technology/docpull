-- Restore the pre-hardening schema shape. This intentionally does not erase
-- created_at values that were backfilled by the up migration.
ALTER TABLE doc_embeddings
	ALTER COLUMN embedding DROP NOT NULL,
	ALTER COLUMN created_at DROP DEFAULT,
	ALTER COLUMN created_at DROP NOT NULL;
