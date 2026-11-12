"""Load, parse, and filter Antigravity CLI conversations."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

DEFAULT_DB_PATH = Path.home() / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
DEFAULT_STARRED_PATH = Path.home() / ".config" / "agy-launcher" / "starred.json"
DEFAULT_TITLES_PATH = Path.home() / ".config" / "agy-launcher" / "titles.json"
NEW_CONVERSATION_ID = "__new__"


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    title: str
    description: str
    step_count: int
    last_modified_time: str
    workspaces: List[str]
    status: str
    agent_name: str

    @property
    def is_new(self) -> bool:
        return self.conversation_id == NEW_CONVERSATION_ID

    @property
    def primary_workspace_display(self) -> str:
        if not self.workspaces:
            return ""
        # Prefer the most specific workspace path (longest) for display.
        best = max(self.workspaces, key=len)
        return shorten_home(best)


def make_new_conversation_item() -> Conversation:
    return Conversation(
        conversation_id=NEW_CONVERSATION_ID,
        title="+ Start New Conversation",
        description="Launch a fresh Antigravity CLI session in the current directory.",
        step_count=0,
        last_modified_time="",
        workspaces=[],
        status="new",
        agent_name="",
    )


def shorten_home(path_str: str) -> str:
    home = str(Path.home())
    if path_str == home:
        return "~"
    if path_str.startswith(home + os.sep):
        return "~" + path_str[len(home):]
    return path_str


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def build_description(
    title: str,
    preview: str,
    first_user_body: Optional[str],
    first_agent_body: Optional[str],
) -> str:
    user_clean = clean_text(first_user_body)
    if user_clean:
        return user_clean

    preview_clean = clean_text(preview)
    title_clean = clean_text(title)
    if preview_clean and preview_clean.lower() != title_clean.lower():
        return preview_clean

    agent_clean = clean_text(first_agent_body)
    if agent_clean:
        return agent_clean

    if preview_clean:
        return preview_clean

    return "No description available."


def parse_workspaces(workspace_uris: Optional[str]) -> List[str]:
    if not workspace_uris:
        return []
    try:
        raw = json.loads(workspace_uris)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(raw, list):
        return []

    paths: List[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            continue
        if item.startswith("file://"):
            parsed = urlparse(item)
            path = unquote(parsed.path)
        else:
            path = item
        if path and path not in paths:
            paths.append(path)
    return paths


def parse_timestamp(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    normalized = ts.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def format_relative_time(ts: str, now: Optional[datetime] = None) -> str:
    dt = parse_timestamp(ts)
    if dt is None:
        return ts[:10] if ts else "unknown"

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    return dt.astimezone().strftime("%Y-%m-%d")


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri_ro = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri_ro, uri=True)
        conn.execute("SELECT 1 FROM conversation_summaries LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError:
        uri_imm = f"file:{db_path}?immutable=1"
        return sqlite3.connect(uri_imm, uri=True)


def load_conversations(
    db_path: Optional[Path] = None,
    include_subagents: bool = False,
) -> List[Conversation]:
    path = Path(db_path) if db_path else Path(os.environ.get("AGY_DB_PATH", DEFAULT_DB_PATH))
    if not path.exists():
        raise FileNotFoundError(f"Conversation database not found: {path}")

    where_clause = "" if include_subagents else "WHERE s.parent_conversation_id = '' AND s.nesting_depth = 0"
    query = f"""
        SELECT
            s.conversation_id,
            s.title,
            s.preview,
            s.step_count,
            s.last_modified_time,
            s.workspace_uris,
            s.status,
            s.agent_name,
            (
                SELECT f.user_body
                FROM conversations_fts f
                WHERE f.conversation_id = s.conversation_id
                  AND trim(f.user_body) != ''
                ORDER BY f.chunk_index ASC
                LIMIT 1
            ) AS first_user_body,
            (
                SELECT f.agent_body
                FROM conversations_fts f
                WHERE f.conversation_id = s.conversation_id
                  AND trim(f.agent_body) != ''
                ORDER BY f.chunk_index ASC
                LIMIT 1
            ) AS first_agent_body
        FROM conversation_summaries s
        {where_clause}
        ORDER BY s.last_modified_time DESC
    """

    conn = _connect_readonly(path)
    try:
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()

    conversations: List[Conversation] = []
    for row in rows:
        (
            conv_id,
            title,
            preview,
            step_count,
            last_modified_time,
            workspace_uris,
            status,
            agent_name,
            first_user_body,
            first_agent_body,
        ) = row

        title_clean = clean_text(title) or conv_id
        description = build_description(title_clean, preview, first_user_body, first_agent_body)
        workspaces = parse_workspaces(workspace_uris)

        conversations.append(
            Conversation(
                conversation_id=conv_id,
                title=title_clean,
                description=description,
                step_count=int(step_count or 0),
                last_modified_time=last_modified_time or "",
                workspaces=workspaces,
                status=status or "",
                agent_name=agent_name or "",
            )
        )
    return conversations


def filter_conversations(conversations: List[Conversation], query: str) -> List[Conversation]:
    q = query.strip().lower()
    if not q:
        return conversations
    terms = q.split()
    results: List[Conversation] = []
    for conv in conversations:
        haystack = " ".join(
            [
                conv.title,
                conv.description,
                conv.conversation_id,
                " ".join(conv.workspaces),
            ]
        ).lower()
        if all(term in haystack for term in terms):
            results.append(conv)
    return results


def build_launch_command(
    conversation_id: str,
    dangerously_skip_permissions: bool = False,
    agy_bin: str = "agy",
) -> List[str]:
    if conversation_id == NEW_CONVERSATION_ID:
        cmd = [agy_bin]
    else:
        cmd = [agy_bin, "--conversation", conversation_id]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return cmd


def _resolve_starred_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("AGY_STARRED_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_STARRED_PATH


def load_starred_ids(path: Optional[Path] = None) -> Set[str]:
    target = _resolve_starred_path(path)
    if not target.exists():
        return set()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {item for item in data if isinstance(item, str) and item}


def save_starred_ids(starred_ids: Set[str], path: Optional[Path] = None) -> None:
    target = _resolve_starred_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sorted(starred_ids), indent=2) + "\n", encoding="utf-8")


def toggle_starred(
    conversation_id: str,
    starred_ids: Set[str],
    path: Optional[Path] = None,
) -> Set[str]:
    updated = set(starred_ids)
    if conversation_id in updated:
        updated.remove(conversation_id)
    else:
        updated.add(conversation_id)
    save_starred_ids(updated, path=path)
    return updated


def sort_conversations(
    conversations: List[Conversation],
    starred_ids: Set[str],
) -> List[Conversation]:
    """Return conversations with starred items first, preserving recency order within each group."""
    starred = [c for c in conversations if c.conversation_id in starred_ids]
    unstarred = [c for c in conversations if c.conversation_id not in starred_ids]
    return starred + unstarred


def _resolve_titles_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("AGY_TITLES_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_TITLES_PATH


def load_custom_titles(path: Optional[Path] = None) -> Dict[str, str]:
    target = _resolve_titles_path(path)
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(k, str) and isinstance(v, str) and k and v.strip()
    }


def save_custom_titles(titles: Dict[str, str], path: Optional[Path] = None) -> None:
    target = _resolve_titles_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(titles, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_custom_title(
    conversation_id: str,
    new_title: str,
    custom_titles: Dict[str, str],
    path: Optional[Path] = None,
) -> Dict[str, str]:
    updated = dict(custom_titles)
    cleaned = clean_text(new_title)
    if cleaned:
        updated[conversation_id] = cleaned
    else:
        updated.pop(conversation_id, None)
    save_custom_titles(updated, path=path)
    return updated


def apply_custom_titles(
    conversations: List[Conversation],
    custom_titles: Dict[str, str],
) -> List[Conversation]:
    if not custom_titles:
        return conversations
    return [
        replace(c, title=custom_titles[c.conversation_id])
        if c.conversation_id in custom_titles
        else c
        for c in conversations
    ]
