"""Persistent SQLite Checkpointer implementation for LangGraph / DeepAgents."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, AsyncIterator, Iterator, Optional, Sequence, Tuple
from contextlib import contextmanager

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class SqliteCheckpointSaver(BaseCheckpointSaver):
    """A persistent SQLite checkpointer for saving and resuming DeepAgent execution state."""

    def __init__(self, db_path: str):
        super().__init__(serde=JsonPlusSerializer())
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    type TEXT,
                    checkpoint BLOB NOT NULL,
                    metadata BLOB,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    type TEXT,
                    value BLOB,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                );
            """)

    def get_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")

        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cur = conn.cursor()
            if checkpoint_id:
                cur.execute(
                    """SELECT checkpoint, metadata, parent_checkpoint_id, checkpoint_id, type
                       FROM checkpoints
                       WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?""",
                    (thread_id, checkpoint_ns, checkpoint_id),
                )
            else:
                cur.execute(
                    """SELECT checkpoint, metadata, parent_checkpoint_id, checkpoint_id, type
                       FROM checkpoints
                       WHERE thread_id = ? AND checkpoint_ns = ?
                       ORDER BY checkpoint_id DESC LIMIT 1""",
                    (thread_id, checkpoint_ns),
                )
            row = cur.fetchone()
            if not row:
                return None

            checkpoint_blob, metadata_blob, parent_id, found_id, chk_type = row
            checkpoint = self.serde.loads_typed((chk_type, checkpoint_blob))

            metadata = {}
            if metadata_blob:
                metadata = self.serde.loads_typed((chk_type, metadata_blob))

            cur.execute(
                """SELECT task_id, channel, value, type
                   FROM writes
                   WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?""",
                (thread_id, checkpoint_ns, found_id),
            )
            pending_writes = []
            for task_id, channel, val, v_type in cur.fetchall():
                pending_writes.append((task_id, channel, self.serde.loads_typed((v_type, val))))

            parent_config = (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            )

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": found_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes,
            )

    def list(
        self,
        config: Optional[dict],
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"] if config else None
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") if config else ""

        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cur = conn.cursor()
            query = "SELECT checkpoint, metadata, parent_checkpoint_id, checkpoint_id, type FROM checkpoints"
            params = []
            if thread_id:
                query += " WHERE thread_id = ? AND checkpoint_ns = ?"
                params.extend([thread_id, checkpoint_ns])
            query += " ORDER BY checkpoint_id DESC"
            if limit:
                query += f" LIMIT {int(limit)}"
            cur.execute(query, params)
            for checkpoint_blob, metadata_blob, parent_id, found_id, chk_type in cur.fetchall():
                chk = self.serde.loads_typed((chk_type, checkpoint_blob))
                meta = self.serde.loads_typed((chk_type, metadata_blob)) if metadata_blob else {}
                parent_cfg = (
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": parent_id,
                        }
                    }
                    if parent_id
                    else None
                )
                yield CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": found_id,
                        }
                    },
                    checkpoint=chk,
                    metadata=meta,
                    parent_config=parent_cfg,
                )

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> dict:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        chk_type, chk_bytes = self.serde.dumps_typed(checkpoint)
        meta_type, meta_bytes = self.serde.dumps_typed(metadata)

        chk_data = chk_bytes if isinstance(chk_bytes, bytes) else chk_bytes.encode("utf-8")
        meta_data = meta_bytes if isinstance(meta_bytes, bytes) else meta_bytes.encode("utf-8")

        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO checkpoints
                   (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thread_id, checkpoint_ns, checkpoint_id, parent_id, chk_type, chk_data, meta_data),
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with sqlite3.connect(self.db_path, timeout=30) as conn:
            for idx, (channel, val) in enumerate(writes):
                v_type, v_bytes = self.serde.dumps_typed(val)
                v_data = v_bytes if isinstance(v_bytes, bytes) else v_bytes.encode("utf-8")
                conn.execute(
                    """INSERT OR REPLACE INTO writes
                       (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, v_type, v_data),
                )
