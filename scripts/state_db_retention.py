#!/usr/bin/env python3
"""Build auditable, non-destructive Hermes state.db retention canaries.

This tool never deletes from the source database.  It can:

* emit a per-session JSONL manifest with conservative hot/cold/review policy;
* create a compact copy with only the rebuildable trigram FTS surface removed;
* verify canonical session/message equality with deterministic streaming digests.

The source should be a SQLite-native backup when reproducible evidence matters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

CANONICAL_TABLES = (
    "sessions",
    "messages",
    "system_prompts",
    "session_model_usage",
)
TRIGRAM_TRIGGERS = (
    "messages_fts_trigram_insert",
    "messages_fts_trigram_delete",
    "messages_fts_trigram_update",
)
SENSITIVE_TITLE_TERMS = (
    "legal",
    "finance",
    "payment",
    "incident",
    "security",
    "purchase",
    "production",
    "customer",
    "compliance",
    "договор",
    "юрид",
    "финанс",
    "платеж",
    "инцидент",
    "безопас",
    "покуп",
    "продакш",
    "клиент",
)


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _byte_len_sql(column: str) -> str:
    return f"length(CAST(COALESCE({column}, '') AS BLOB))"


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _last_active(row: dict[str, Any]) -> float:
    values = (
        row.get("last_activity_at"),
        row.get("ended_at"),
        row.get("started_at"),
    )
    return max(float(value or 0) for value in values)


def _lineage_roots(rows: list[dict[str, Any]]) -> tuple[dict[str, str], set[str]]:
    parents = {str(row["id"]): row.get("parent_session_id") for row in rows}
    roots: dict[str, str] = {}
    cycles: set[str] = set()

    for session_id in parents:
        trail: list[str] = []
        seen: set[str] = set()
        current = session_id
        while current in parents and parents.get(current):
            if current in seen:
                cycles.update(seen)
                break
            seen.add(current)
            trail.append(current)
            parent = str(parents[current])
            if parent not in parents:
                current = parent
                break
            current = parent
        root = current
        for item in trail:
            roots[item] = root
        roots.setdefault(session_id, root)
    return roots, cycles


def _runtime_references(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return tables that hold exact session-id references, grouped per id."""
    refs: dict[str, set[str]] = defaultdict(set)
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'messages_fts%'"
        )
    ]
    skip = {"sessions", "messages", "session_model_usage"}
    for table in tables:
        if table in skip:
            continue
        columns = _table_columns(conn, table)
        candidates = [
            col
            for col in columns
            if col == "session_id" or col.endswith("_session_id")
        ]
        for column in candidates:
            sql = (
                f'SELECT "{column}", COUNT(*) FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL GROUP BY "{column}"'
            )
            try:
                for value, _count in conn.execute(sql):
                    refs[str(value)].add(f"{table}.{column}")
            except sqlite3.Error:
                continue
    return {key: sorted(value) for key, value in refs.items()}


def build_manifest(
    source: Path,
    output: Path,
    *,
    recent_days: int,
    archive_sha256: str | None,
) -> dict[str, Any]:
    if source.resolve() == output.resolve():
        raise ValueError("manifest output must differ from the source database")
    if output.exists():
        raise FileExistsError("manifest output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect_ro(source)
    try:
        session_rows = [dict(row) for row in conn.execute("SELECT * FROM sessions")]
        roots, cycles = _lineage_roots(session_rows)
        refs = _runtime_references(conn)
        message_columns = _table_columns(conn, "messages")
        payload_columns = [
            name
            for name in (
                "content",
                "tool_calls",
                "reasoning",
                "reasoning_content",
                "reasoning_details",
                "codex_reasoning_items",
                "codex_message_items",
                "api_content",
            )
            if name in message_columns
        ]
        payload_sql = " + ".join(_byte_len_sql(name) for name in payload_columns) or "0"
        aggregates = {
            str(row["session_id"]): dict(row)
            for row in conn.execute(
                "SELECT session_id, COUNT(*) AS message_rows, "
                f"COALESCE(SUM({payload_sql}), 0) AS payload_bytes, "
                "SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) AS active_rows, "
                "SUM(CASE WHEN compacted=1 THEN 1 ELSE 0 END) AS compacted_rows "
                "FROM messages GROUP BY session_id"
            )
        }
    finally:
        conn.close()

    now = time.time()
    recent_cutoff = now - recent_days * 86400
    decisions: dict[str, str] = {}
    reasons: dict[str, list[str]] = {}
    for row in session_rows:
        session_id = str(row["id"])
        why: list[str] = []
        title = str(row.get("title") or "").lower()
        last_active = _last_active(row)
        runtime_refs = refs.get(session_id, [])
        preserve = False
        if int(row.get("pinned") or 0):
            why.append("pinned")
            preserve = True
        if row.get("ended_at") is None:
            why.append("open_session")
            preserve = True
        if last_active >= recent_cutoff:
            why.append(f"recent_{recent_days}d")
            preserve = True
        if runtime_refs:
            why.append("runtime_reference")
            preserve = True
        if any(term in title for term in SENSITIVE_TITLE_TERMS):
            why.append("sensitive_title")
            preserve = True
        if session_id in cycles:
            why.append("lineage_cycle")
            preserve = True
        if preserve:
            decisions[session_id] = "preserve_hot"
        elif int(row.get("archived") or 0) or row.get("source") in {"cron", "subagent"}:
            decisions[session_id] = "preserve_cold"
            why.append("cold_candidate_only_after_restore")
        else:
            decisions[session_id] = "review"
            why.append("manual_importance_review_required")
        reasons[session_id] = why

    # A lineage is indivisible: one hot member promotes the complete chain.
    hot_roots = {
        roots.get(session_id, session_id)
        for session_id, decision in decisions.items()
        if decision == "preserve_hot"
    }
    for session_id in decisions:
        if roots.get(session_id, session_id) in hot_roots:
            if decisions[session_id] != "preserve_hot":
                reasons[session_id].append("hot_lineage_member")
            decisions[session_id] = "preserve_hot"

    summary = {
        "format": "hermes-state-retention-manifest-v1",
        "source": str(source.resolve()),
        "source_bytes": source.stat().st_size,
        "source_sha256": archive_sha256,
        "generated_at": time.time(),
        "recent_days": recent_days,
        "sessions": len(session_rows),
        "messages": sum(int(v.get("message_rows") or 0) for v in aggregates.values()),
        "lineage_cycles": sorted(cycles),
        "decisions": {
            decision: sum(1 for value in decisions.values() if value == decision)
            for decision in ("preserve_hot", "preserve_cold", "review")
        },
        "deletion_authority": False,
    }

    with tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.candidate-",
        suffix=".jsonl",
        dir=output.parent,
        delete=False,
        mode="w",
        encoding="utf-8",
    ) as handle:
        tmp = Path(handle.name)
        try:
            handle.write(
                json.dumps({"type": "header", **summary}, ensure_ascii=False) + "\n"
            )
            for row in sorted(session_rows, key=lambda item: str(item["id"])):
                session_id = str(row["id"])
                aggregate = aggregates.get(session_id, {})
                record = {
                "type": "session",
                "session_id": session_id,
                "parent_session_id": row.get("parent_session_id"),
                "lineage_root": roots.get(session_id, session_id),
                "source": row.get("source"),
                "user_id": row.get("user_id"),
                "chat_id": row.get("chat_id"),
                "thread_id": row.get("thread_id"),
                "title": row.get("title"),
                "started_at": row.get("started_at"),
                "ended_at": row.get("ended_at"),
                "last_activity_at": row.get("last_activity_at"),
                "archived": int(row.get("archived") or 0),
                "pinned": int(row.get("pinned") or 0),
                "message_rows": int(aggregate.get("message_rows") or 0),
                "payload_bytes": int(aggregate.get("payload_bytes") or 0),
                "active_rows": int(aggregate.get("active_rows") or 0),
                "compacted_rows": int(aggregate.get("compacted_rows") or 0),
                "runtime_references": refs.get(session_id, []),
                "decision": decisions[session_id],
                "reasons": reasons[session_id],
                "eligible_for_hot_deletion": False,
                "restore_verified": False,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, output)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    summary["manifest"] = str(output.resolve())
    summary["manifest_sha256"] = _file_sha256(output)
    return summary


def _update_digest(digest: Any, value: Any) -> None:
    if value is None:
        digest.update(b"N")
        return
    if isinstance(value, bytes):
        payload = value
        tag = b"B"
    elif isinstance(value, int):
        payload = str(value).encode("ascii")
        tag = b"I"
    elif isinstance(value, float):
        payload = struct.pack(">d", value)
        tag = b"F"
    else:
        payload = str(value).encode("utf-8", "surrogatepass")
        tag = b"T"
    digest.update(tag)
    digest.update(struct.pack(">Q", len(payload)))
    digest.update(payload)


def canonical_digest(path: Path) -> dict[str, Any]:
    conn = _connect_ro(path)
    overall = hashlib.sha256()
    table_results: dict[str, Any] = {}
    existing = _existing_tables(conn)
    try:
        for table in CANONICAL_TABLES:
            if table not in existing:
                continue
            columns = _table_columns(conn, table)
            table_info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            primary_key = [
                str(row[1])
                for row in sorted(table_info, key=lambda item: int(item[5] or 0))
                if int(row[5] or 0) > 0
            ]
            order_columns = primary_key or (["id"] if "id" in columns else columns)
            quoted = ", ".join(f'"{name}"' for name in columns)
            order_sql = ", ".join(f'"{name}"' for name in order_columns)
            digest = hashlib.sha256()
            count = 0
            for row in conn.execute(
                f'SELECT {quoted} FROM "{table}" ORDER BY {order_sql}'
            ):
                for value in row:
                    _update_digest(digest, value)
                digest.update(b"R")
                count += 1
            value = digest.hexdigest()
            table_results[table] = {"rows": count, "sha256": value}
            overall.update(table.encode("utf-8") + b":" + value.encode("ascii"))
    finally:
        conn.close()
    return {"tables": table_results, "sha256": overall.hexdigest()}


def _sqlite_backup(source: Path, destination: Path) -> None:
    src = _connect_ro(source)
    dst = sqlite3.connect(destination, timeout=30)
    try:
        src.backup(dst, pages=8192, sleep=0.02)
        dst.commit()
    finally:
        dst.close()
        src.close()


def _drop_trigram_surface(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        for trigger in TRIGRAM_TRIGGERS:
            conn.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
        conn.execute("DROP TABLE IF EXISTS messages_fts_trigram")
        conn.execute("DROP VIEW IF EXISTS messages_fts_trigram_src")
        if "state_meta" in _existing_tables(conn):
            conn.execute(
                "INSERT INTO state_meta(key, value) VALUES('trigram_fts_disabled', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def build_lean_copy(
    source: Path,
    output: Path,
    *,
    receipt: Path,
    full_digest: bool,
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    receipt = receipt.resolve()
    if len({source, output, receipt}) != 3:
        raise ValueError("source, lean output, and receipt must be distinct paths")
    if output.exists() or receipt.exists():
        raise FileExistsError("output or receipt already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    free = os.statvfs(output.parent)
    free_bytes = free.f_bavail * free.f_frsize
    # Working backup + compact candidate can coexist transiently.
    if free_bytes < source.stat().st_size * 2:
        raise OSError("not enough free space for working backup plus compact output")

    started = time.time()
    with tempfile.NamedTemporaryFile(
        prefix="state-lean-work-", suffix=".db", dir=output.parent, delete=False
    ) as handle:
        work = Path(handle.name)
    work.unlink()
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.candidate-",
        suffix=".db",
        dir=output.parent,
        delete=False,
    ) as handle:
        candidate = Path(handle.name)
    candidate.unlink()
    receipt_tmp: Path | None = None
    output_published = False
    try:
        _sqlite_backup(source, work)
        conn = sqlite3.connect(work, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            _drop_trigram_surface(conn)
            quoted = str(candidate).replace("'", "''")
            conn.execute(f"VACUUM INTO '{quoted}'")
        finally:
            conn.close()
        os.chmod(candidate, 0o600)

        src_conn = _connect_ro(source)
        dst_conn = _connect_ro(candidate)
        try:
            source_counts = {
                "sessions": src_conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "messages": src_conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            }
            output_counts = {
                "sessions": dst_conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "messages": dst_conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            }
            quick_check = dst_conn.execute("PRAGMA quick_check").fetchone()[0]
            trigram_objects = [
                row[0]
                for row in dst_conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name LIKE 'messages_fts_trigram%'"
                )
            ]
            base_fts_rows = dst_conn.execute(
                "SELECT COUNT(*) FROM messages_fts_docsize"
            ).fetchone()[0]
        finally:
            src_conn.close()
            dst_conn.close()

        if source_counts != output_counts:
            raise RuntimeError(
                f"canonical count mismatch: {source_counts} != {output_counts}"
            )
        if quick_check != "ok":
            raise RuntimeError(f"lean copy quick_check failed: {quick_check}")
        if trigram_objects:
            raise RuntimeError(f"trigram objects remain: {trigram_objects}")

        source_digest = canonical_digest(source) if full_digest else None
        output_digest = canonical_digest(candidate) if full_digest else None
        if full_digest and source_digest != output_digest:
            raise RuntimeError("canonical digest mismatch")

        source_bytes = source.stat().st_size
        output_bytes = candidate.stat().st_size
        result = {
            "format": "hermes-state-lean-fts-receipt-v1",
            "source": str(source),
            "output": str(output),
            "started_at": started,
            "completed_at": time.time(),
            "source_bytes": source_bytes,
            "output_bytes": output_bytes,
            "saved_bytes": source_bytes - output_bytes,
            "saved_fraction": 1 - output_bytes / source_bytes,
            "counts": output_counts,
            "quick_check": quick_check,
            "base_fts_rows": base_fts_rows,
            "trigram_objects": trigram_objects,
            "canonical_digest": output_digest,
            "output_sha256": _file_sha256(candidate),
            "source_mutated": False,
            "conversation_rows_deleted": 0,
        }

        with tempfile.NamedTemporaryFile(
            prefix=f".{receipt.name}.candidate-",
            suffix=".json",
            dir=receipt.parent,
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as handle:
            receipt_tmp = Path(handle.name)
            handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        # Publish only after every verification has passed. If receipt publish
        # fails, remove the newly published DB so callers never mistake an
        # unreceipted artifact for a completed run.
        os.replace(candidate, output)
        output_published = True
        try:
            os.replace(receipt_tmp, receipt)
            receipt_tmp = None
        except BaseException:
            output.unlink(missing_ok=True)
            output_published = False
            raise

        result["receipt"] = str(receipt)
        result["receipt_sha256"] = _file_sha256(receipt)
        return result
    except BaseException:
        candidate.unlink(missing_ok=True)
        if receipt_tmp is not None:
            receipt_tmp.unlink(missing_ok=True)
        if output_published:
            output.unlink(missing_ok=True)
        raise
    finally:
        work.unlink(missing_ok=True)
        Path(str(work) + "-wal").unlink(missing_ok=True)
        Path(str(work) + "-shm").unlink(missing_ok=True)
        candidate.unlink(missing_ok=True)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    manifest = commands.add_parser("manifest", help="write a conservative session manifest")
    manifest.add_argument("--source", type=_path, required=True)
    manifest.add_argument("--output", type=_path, required=True)
    manifest.add_argument("--recent-days", type=int, default=30)
    manifest.add_argument("--archive-sha256")

    lean = commands.add_parser("lean-copy", help="create a compact copy without trigram FTS")
    lean.add_argument("--source", type=_path, required=True)
    lean.add_argument("--output", type=_path, required=True)
    lean.add_argument("--receipt", type=_path, required=True)
    lean.add_argument("--full-digest", action="store_true")

    digest = commands.add_parser("digest", help="stream a canonical table digest")
    digest.add_argument("--source", type=_path, required=True)
    return root


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "manifest":
        result = build_manifest(
            args.source,
            args.output,
            recent_days=args.recent_days,
            archive_sha256=args.archive_sha256,
        )
    elif args.command == "lean-copy":
        result = build_lean_copy(
            args.source,
            args.output,
            receipt=args.receipt,
            full_digest=args.full_digest,
        )
    else:
        result = canonical_digest(args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
