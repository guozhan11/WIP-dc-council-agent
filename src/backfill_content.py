import argparse
import os
from datetime import datetime, timedelta, timezone

import yaml

from collect import ARTICLE_FETCH_SKIP_SOURCES, fetch_article_content
from db import connect, init_db


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fallback_content(row: dict) -> str:
    source = str(row.get("source") or "")
    title = str(row.get("title") or "").strip()
    summary = str(row.get("summary") or "").strip()

    if source in ARTICLE_FETCH_SKIP_SOURCES:
        if source.startswith("dcregs"):
            return " | ".join(part for part in [title, summary] if part)
        return summary

    return ""


def candidate_rows(conn, *, since: str | None, limit: int | None) -> list[dict]:
    params: list[object] = []
    where = ["(content IS NULL OR trim(content) = '')"]
    if since:
        where.append("COALESCE(published_at, created_at) >= ?")
        params.append(since)

    sql = f"""
        SELECT id, source, title, url, published_at, summary, created_at
        FROM items
        WHERE {" AND ".join(where)}
        ORDER BY COALESCE(published_at, created_at) DESC
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def backfill_content(
    conn,
    *,
    days: int | None,
    limit: int | None,
    dry_run: bool,
) -> tuple[int, int, int]:
    since = None
    if days is not None:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = candidate_rows(conn, since=since, limit=limit)
    updated = 0
    would_update = 0
    skipped = 0

    for row in rows:
        source = str(row.get("source") or "")
        content = fallback_content(row)
        if not content:
            content = fetch_article_content(str(row.get("url") or ""), source)

        if not content:
            skipped += 1
            print(f"skip id={row['id']} source={source} title={row.get('title')}")
            continue

        if dry_run:
            would_update += 1
            print(
                f"would update id={row['id']} source={source} "
                f"chars={len(content)} title={row.get('title')}"
            )
        else:
            conn.execute("UPDATE items SET content = ? WHERE id = ?", (content, row["id"]))
            updated += 1
            print(
                f"updated id={row['id']} source={source} "
                f"chars={len(content)} title={row.get('title')}"
            )

    if not dry_run:
        conn.commit()

    return len(rows), (would_update if dry_run else updated), skipped


def main() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    parser = argparse.ArgumentParser(
        description="Backfill missing item.content values in the digest SQLite database."
    )
    parser.add_argument(
        "--config",
        default=os.path.join(repo_root, "config.yaml"),
        help="Path to config.yaml.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path. Defaults to storage.db_path from config.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only backfill rows from the last N days. Use 0 to scan all rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of missing-content rows to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing to the database.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    db_path = args.db or cfg["storage"]["db_path"]
    if not os.path.isabs(db_path):
        db_path = os.path.join(repo_root, db_path)

    conn = connect(db_path)
    init_db(conn)

    days = None if args.days == 0 else args.days
    considered, updated, skipped = backfill_content(
        conn,
        days=days,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    action = "would_update" if args.dry_run else "updated"
    print(f"Backfill complete. considered={considered}, {action}={updated}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
