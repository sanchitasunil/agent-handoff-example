"""
escalations/store.py — SQLite-backed persistence for escalation requests.

This is the source of truth for escalations: every request is written here
via create() (or merged into an existing row via update()) BEFORE anything
is sent anywhere else. escalations/notify.py only delivers a *notification
about* a row that already exists here — if notify.send() fails, the record
still exists and nothing is lost; a human can always find it by querying
this store directly, even if no alert ever reached Slack/Discord/etc.

Storage is a single SQLite file (path from ESCALATIONS_DB_PATH, defaults to
escalations.db in the working directory — already covered by .gitignore).
This is intentionally simple: enough for a demo or a single-worker
deployment. Swap in a real database by reimplementing the functions below
with the same signatures — nothing else in the codebase should need to
change.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import uuid
from typing import Optional

from escalations.summary import redact

DB_PATH = os.getenv("ESCALATIONS_DB_PATH", "escalations.db")

# Used to decide whether a duplicate-guard update should raise the stored
# urgency (e.g. a second "cannot_resolve" call that turns out to be worse
# than the first shouldn't stay at the original, lower urgency).
_URGENCY_RANK = {"low": 0, "medium": 1, "high": 2, "emergency": 3}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS escalations (
            id TEXT PRIMARY KEY,
            reason_code TEXT NOT NULL,
            reason_label TEXT NOT NULL,
            urgency TEXT NOT NULL,
            caller TEXT,
            what_happened TEXT,
            checked TEXT NOT NULL DEFAULT '[]',
            language TEXT,
            follow_up_method TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            notified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["checked"] = json.loads(data["checked"] or "[]")
    data["notified"] = bool(data["notified"])
    return data


def _new_id() -> str:
    # Short, human-readable-enough reference id for the agent to read back
    # to a caller over the phone (e.g. "ESC-4F2A9B1C").
    return f"ESC-{uuid.uuid4().hex[:8].upper()}"


def create(summary: dict) -> dict:
    """Insert a new escalation row from a summary dict (see
    escalations.summary.build_summary) and return the stored row.

    Every free-text field in `summary` is expected to already be redacted
    by build_summary() — this function does not redact anything itself.
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    row = {
        "id": _new_id(),
        "reason_code": summary["reason_code"],
        "reason_label": summary["reason_label"],
        "urgency": summary["urgency"],
        "caller": summary.get("caller"),
        "what_happened": summary.get("what_happened"),
        "checked": json.dumps(summary.get("checked") or []),
        "language": summary.get("language"),
        "follow_up_method": summary.get("follow_up_method"),
        "status": "open",
        "notified": 0,
        "created_at": now,
        "updated_at": now,
    }

    with _connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO escalations (
                id, reason_code, reason_label, urgency, caller,
                what_happened, checked, language, follow_up_method,
                status, notified, created_at, updated_at
            ) VALUES (
                :id, :reason_code, :reason_label, :urgency, :caller,
                :what_happened, :checked, :language, :follow_up_method,
                :status, :notified, :created_at, :updated_at
            )
            """,
            row,
        )
        conn.commit()

    return {**row, "checked": summary.get("checked") or [], "notified": False}


def find_open_duplicate(who: str, reason_code: str) -> Optional[dict]:
    """Look for an already-open escalation from the same caller for the
    same reason, so a caller who repeats themselves (or the agent re-tries
    after a dropped connection) doesn't create a pile of duplicate tickets.

    `who` is matched after being run through the same redact() used when
    storing — the store never keeps a second, unredacted copy of caller
    identity just for matching purposes.
    """
    if not who:
        return None

    redacted_who = redact(who)

    with _connect() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            """
            SELECT * FROM escalations
            WHERE caller = ? AND reason_code = ? AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (redacted_who, reason_code),
        )
        row = cursor.fetchone()

    return _row_to_dict(row) if row else None


def update(escalation_id: str, summary: dict) -> Optional[dict]:
    """Merge a new summary into an existing (duplicate) escalation: bump
    urgency up if the new report is more urgent, append the new
    what_happened text and checked items rather than overwriting them, and
    refresh follow-up details. Returns the updated row, or None if the id
    doesn't exist.
    """
    with _connect() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT * FROM escalations WHERE id = ?", (escalation_id,)
        )
        existing_row = cursor.fetchone()
        if existing_row is None:
            return None

        existing = _row_to_dict(existing_row)

        new_urgency = summary.get("urgency") or existing["urgency"]
        if _URGENCY_RANK.get(new_urgency, 0) < _URGENCY_RANK.get(
            existing["urgency"], 0
        ):
            new_urgency = existing["urgency"]

        merged_what_happened = existing["what_happened"] or ""
        if summary.get("what_happened"):
            merged_what_happened = (
                f"{merged_what_happened}\n(update) {summary['what_happened']}"
                if merged_what_happened
                else summary["what_happened"]
            )

        merged_checked = list(existing["checked"])
        for item in summary.get("checked") or []:
            if item not in merged_checked:
                merged_checked.append(item)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE escalations
            SET urgency = ?, what_happened = ?, checked = ?,
                language = COALESCE(?, language),
                follow_up_method = COALESCE(?, follow_up_method),
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_urgency,
                merged_what_happened,
                json.dumps(merged_checked),
                summary.get("language"),
                summary.get("follow_up_method"),
                now,
                escalation_id,
            ),
        )
        conn.commit()

        cursor = conn.execute(
            "SELECT * FROM escalations WHERE id = ?", (escalation_id,)
        )
        return _row_to_dict(cursor.fetchone())


def mark_notified(escalation_id: str, delivered: bool) -> None:
    """Record whether notify.send() succeeded for this row. Best-effort —
    the escalation itself is already durably stored regardless."""
    with _connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE escalations SET notified = ? WHERE id = ?",
            (1 if delivered else 0, escalation_id),
        )
        conn.commit()
