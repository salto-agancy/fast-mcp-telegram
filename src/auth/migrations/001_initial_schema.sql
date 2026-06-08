-- OIDC identity mapping
CREATE TABLE IF NOT EXISTS oidc_identity (
    oidc_key TEXT PRIMARY KEY,
    oidc_sub TEXT NOT NULL,
    oidc_issuer TEXT NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    telegram_phone TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Links OIDC identity to Telethon session file
CREATE TABLE IF NOT EXISTS telegram_session (
    oidc_key TEXT PRIMARY KEY REFERENCES oidc_identity(oidc_key),
    session_filename TEXT NOT NULL,
    dc_id INTEGER NOT NULL,
    server_address TEXT NOT NULL,
    port INTEGER NOT NULL,
    auth_key BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Elicitation state machine
CREATE TABLE IF NOT EXISTS setup_state (
    oidc_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('WAITING_PHONE','WAITING_CODE','WAITING_PASS','COMPLETED','FAILED')),
    phone_number TEXT,
    tg_code_hash TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
