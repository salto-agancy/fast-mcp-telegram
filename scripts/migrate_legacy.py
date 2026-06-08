#!/usr/bin/env python3
"""Migrate legacy bearer tokens to OIDC identity placeholders.

Usage:
    python scripts/migrate_legacy.py --bearer-map legacy_tokens.yaml --db ./data/auth.db

Input YAML format:
    tokens:
      - bearer_prefix: abc123
        telegram_user_id: 999
        telegram_username: legacyuser
      - bearer_prefix: def456
        telegram_user_id: 888
        telegram_phone: "1555000111"

For each entry, inserts an oidc_identity row with:
  - oidc_key = sha256(bearer_prefix)[:32]
  - oidc_sub = "LEGACY_PLACEHOLDER"
  - oidc_issuer = "legacy-bearer-migration"
"""

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

import yaml

# Import run_migrations to ensure schema exists before inserting
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.auth.db import run_migrations  # noqa: E402


def migrate(bearer_map_path: str, db_path: str) -> int:
    """Read YAML and insert placeholder rows. Returns count of inserted rows."""
    data = yaml.safe_load(Path(bearer_map_path).read_text())
    if data is None:
        data = {}
    tokens = data.get("tokens") or []

    # Ensure schema exists before inserting
    run_migrations(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    inserted = 0

    for entry in tokens:
        if not isinstance(entry, dict):
            print(f"Skipping malformed entry: {entry!r}", file=sys.stderr)
            continue
        prefix = entry.get("bearer_prefix")
        if not prefix:
            print(f"Skipping entry without bearer_prefix: {entry!r}", file=sys.stderr)
            continue
        oidc_key = hashlib.sha256(prefix.encode()).hexdigest()[:32]
        user_id = entry.get("telegram_user_id")
        if user_id is None:
            print(f"Skipping entry without telegram_user_id: {entry!r}", file=sys.stderr)
            continue
        username = entry.get("telegram_username")
        phone = entry.get("telegram_phone")

        try:
            conn.execute(
                """
                INSERT INTO oidc_identity
                    (oidc_key, oidc_sub, oidc_issuer, telegram_user_id,
                     telegram_username, telegram_phone)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    oidc_key,
                    "LEGACY_PLACEHOLDER",
                    "legacy-bearer-migration",
                    user_id,
                    username,
                    phone,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            print(f"Skipping duplicate key for bearer prefix: {prefix}", file=sys.stderr)

    conn.commit()
    conn.close()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy bearer tokens to OIDC placeholders")
    parser.add_argument("--bearer-map", required=True, help="Path to legacy_tokens.yaml")
    parser.add_argument("--db", required=True, help="Path to auth.db")
    args = parser.parse_args()

    count = migrate(args.bearer_map, args.db)
    print(f"Migrated {count} legacy bearer token(s) to OIDC placeholders.")


if __name__ == "__main__":
    main()
