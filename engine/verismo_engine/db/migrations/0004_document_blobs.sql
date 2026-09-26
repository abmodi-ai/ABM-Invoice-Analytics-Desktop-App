-- 0004_document_blobs: original PDF/image bytes for the extraction correction view.
-- Stored inside the encrypted database so no plaintext document copies exist on disk.
CREATE TABLE document_blobs (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    data        BLOB NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
