"""Cache approved background context per topic, reused across conversations.

A topic is researched once (see agents/context/context_research); the participant
or operator approves the briefing, and we stash it here keyed by topic so later
interviews on the same topic reuse it without re-researching.

Two backends, matching session_store:
  - Postgres (via research_db) when a DB is configured — durable, shared.
  - Local disk otherwise — handy for laptop testing.
"""
import json
import os
import re
from pathlib import Path
from typing import Optional

from src.utils.storage import research_db


def topic_key(topic: str) -> str:
    """Stable slug used as the cache key for a topic string."""
    slug = re.sub(r"[^a-z0-9]+", "-", (topic or "").strip().lower()).strip("-")
    return slug[:120] or "untitled"


def _use_db() -> bool:
    return research_db.is_configured()


def _dir() -> Path:
    base = Path(os.getenv("DATA_DIR", "data")) / "_topic_context"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get(topic: str) -> Optional[dict]:
    """Return the cached context dict for a topic, or None."""
    key = topic_key(topic)
    if _use_db():
        return research_db.get_topic_context(key)
    path = _dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def put(topic: str, context: dict, approved: bool = True) -> None:
    """Store/replace the cached context for a topic."""
    key = topic_key(topic)
    if _use_db():
        research_db.save_topic_context(key, topic, context, approved=approved)
    else:
        (_dir() / f"{key}.json").write_text(json.dumps(context), encoding="utf-8")
