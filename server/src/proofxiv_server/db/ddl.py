"""DDL that SQLAlchemy metadata does not express: extensions and search indexes."""

EXTRA_DDL = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_problems_title_trgm ON problems USING gin (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_problems_variant_trgm ON problems USING gin (variant gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_problems_fts ON problems USING gin "
    "(to_tsvector('simple', title || ' ' || coalesce(variant, '') || ' ' || statement_md))",
    "CREATE INDEX IF NOT EXISTS ix_papers_title_trgm ON papers USING gin (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_papers_fts ON papers USING gin "
    "(to_tsvector('simple', title || ' ' || abstract))",
]
