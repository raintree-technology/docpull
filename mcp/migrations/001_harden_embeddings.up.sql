-- Embeddings are required for pgvector similarity operators. Refuse to
-- silently harden a table that already contains unusable cache rows.
DO $$
BEGIN
	IF EXISTS (
		SELECT 1
		FROM doc_embeddings
		WHERE embedding IS NULL
	) THEN
		RAISE EXCEPTION 'doc_embeddings contains rows with NULL embeddings; re-run ingestion for affected libraries before applying this migration';
	END IF;
END $$;

-- Existing rows predate the NOT NULL/default requirement.
UPDATE doc_embeddings
SET created_at = NOW()
WHERE created_at IS NULL;

ALTER TABLE doc_embeddings
	ALTER COLUMN embedding SET NOT NULL,
	ALTER COLUMN created_at SET DEFAULT NOW(),
	ALTER COLUMN created_at SET NOT NULL;
