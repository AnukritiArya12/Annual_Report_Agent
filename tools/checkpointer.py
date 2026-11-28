"""Resume support: saves the agent's progress to SQLite using LangGraph's own saver."""

from __future__ import annotations

import sqlite3


def SqliteCheckpointSaver(db_path: str):
    """Return a LangGraph checkpointer that stores progress in `db_path` (pip install langgraph-checkpoint-sqlite)."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    return SqliteSaver(sqlite3.connect(db_path, check_same_thread=False))
