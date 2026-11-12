"""Unit tests for Antigravity conversation loading, filtering, and command building."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.conversations import (
    NEW_CONVERSATION_ID,
    apply_custom_titles,
    build_description,
    build_launch_command,
    filter_conversations,
    format_relative_time,
    load_conversations,
    load_custom_titles,
    load_starred_ids,
    make_new_conversation_item,
    parse_workspaces,
    set_custom_title,
    sort_conversations,
    toggle_starred,
)


def _create_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '',
            step_count INTEGER NOT NULL DEFAULT 0,
            last_modified_time DATETIME NOT NULL,
            workspace_uris TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            agent_name TEXT NOT NULL DEFAULT '',
            parent_conversation_id TEXT NOT NULL DEFAULT '',
            nesting_depth INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE conversations_fts (
            conversation_id TEXT,
            chunk_index INTEGER,
            user_body TEXT,
            agent_body TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO conversation_summaries VALUES (
            'conv-1', 'Portfolio Animations', 'Portfolio Animations', 14,
            '2026-11-11T06:44:17+00:00',
            '["file:///home/user","file:///home/user/Projects/Portfolio"]',
            'idle', 'cli', '', '', '', 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO conversations_fts VALUES (
            'conv-1', 0, 'Help me build a scroll clip-path animation\nfor my hero section.', 'Sure!'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO conversation_summaries VALUES (
            'conv-2', 'KFC Debate', 'Form a set of agents to debate', 4,
            '2026-11-10T01:00:00+00:00',
            '["file:///home/user/Projects/Customer%20Insights"]',
            'idle', 'cli', '', '', '', 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO conversation_summaries VALUES (
            'conv-sub', 'Subagent Task', 'Subagent', 2,
            '2026-11-12T00:00:00+00:00',
            '[]',
            'idle', 'cli', '', '', 'conv-1', 1
        )
        """
    )
    conn.commit()
    conn.close()


class ConversationsTest(unittest.TestCase):
    def test_load_conversations_excludes_subagents_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            _create_test_db(db_path)

            convs = load_conversations(db_path=db_path)
            self.assertEqual([c.conversation_id for c in convs], ["conv-1", "conv-2"])
            self.assertEqual(
                convs[0].description,
                "Help me build a scroll clip-path animation for my hero section.",
            )
            self.assertEqual(convs[1].description, "Form a set of agents to debate")
            self.assertEqual(
                convs[1].workspaces,
                ["/home/user/Projects/Customer Insights"],
            )

            all_convs = load_conversations(db_path=db_path, include_subagents=True)
            self.assertEqual(len(all_convs), 3)

    def test_build_description_fallbacks(self) -> None:
        self.assertEqual(
            build_description("Title", "Title", "  First\n\tprompt  ", "Agent"),
            "First prompt",
        )
        self.assertEqual(
            build_description("Title", "Different preview", None, "Agent"),
            "Different preview",
        )
        self.assertEqual(
            build_description("Title", "Title", "", "Agent summary"),
            "Agent summary",
        )
        self.assertEqual(
            build_description("Title", "Title", None, None),
            "Title",
        )

    def test_parse_workspaces(self) -> None:
        raw = '["file:///home/user","file:///home/user/My%20Project","file:///home/user"]'
        self.assertEqual(parse_workspaces(raw), ["/home/user", "/home/user/My Project"])
        self.assertEqual(parse_workspaces(""), [])
        self.assertEqual(parse_workspaces("not-json"), [])

    def test_filter_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            _create_test_db(db_path)
            convs = load_conversations(db_path=db_path)

            self.assertEqual(len(filter_conversations(convs, "clip-path hero")), 1)
            self.assertEqual(filter_conversations(convs, "Customer")[0].conversation_id, "conv-2")
            self.assertEqual(len(filter_conversations(convs, "nonexistent")), 0)

    def test_format_relative_time(self) -> None:
        now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_relative_time("2026-11-13T11:59:30+00:00", now=now), "just now")
        self.assertEqual(format_relative_time("2026-11-13T11:45:00+00:00", now=now), "15m ago")
        self.assertEqual(format_relative_time("2026-11-13T09:00:00+00:00", now=now), "3h ago")
        self.assertEqual(format_relative_time("2026-11-11T12:00:00+00:00", now=now), "2d ago")

    def test_build_launch_command(self) -> None:
        self.assertEqual(
            build_launch_command("abc-123", dangerously_skip_permissions=False),
            ["agy", "--conversation", "abc-123"],
        )
        self.assertEqual(
            build_launch_command("abc-123", dangerously_skip_permissions=True),
            ["agy", "--conversation", "abc-123", "--dangerously-skip-permissions"],
        )
        new_item = make_new_conversation_item()
        self.assertTrue(new_item.is_new)
        self.assertEqual(
            build_launch_command(NEW_CONVERSATION_ID, dangerously_skip_permissions=False),
            ["agy"],
        )
        self.assertEqual(
            build_launch_command(NEW_CONVERSATION_ID, dangerously_skip_permissions=True),
            ["agy", "--dangerously-skip-permissions"],
        )

    def test_starring_and_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            starred_path = Path(tmp) / "starred.json"
            _create_test_db(db_path)
            convs = load_conversations(db_path=db_path)

            self.assertEqual(load_starred_ids(path=starred_path), set())

            starred = toggle_starred("conv-2", set(), path=starred_path)
            self.assertEqual(starred, {"conv-2"})
            self.assertEqual(load_starred_ids(path=starred_path), {"conv-2"})

            sorted_convs = sort_conversations(convs, starred)
            self.assertEqual([c.conversation_id for c in sorted_convs], ["conv-2", "conv-1"])

            starred = toggle_starred("conv-2", starred, path=starred_path)
            self.assertEqual(starred, set())
            self.assertEqual(
                [c.conversation_id for c in sort_conversations(convs, starred)],
                ["conv-1", "conv-2"],
            )

    def test_custom_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            titles_path = Path(tmp) / "titles.json"
            _create_test_db(db_path)
            convs = load_conversations(db_path=db_path)

            self.assertEqual(load_custom_titles(path=titles_path), {})

            titles = set_custom_title("conv-1", "  My Custom   Title ", {}, path=titles_path)
            self.assertEqual(titles, {"conv-1": "My Custom Title"})
            self.assertEqual(load_custom_titles(path=titles_path), {"conv-1": "My Custom Title"})

            updated = apply_custom_titles(convs, titles)
            self.assertEqual(updated[0].title, "My Custom Title")
            self.assertEqual(updated[1].title, "KFC Debate")

            # Empty title resets back to original
            titles = set_custom_title("conv-1", "   ", titles, path=titles_path)
            self.assertEqual(titles, {})
            self.assertEqual(apply_custom_titles(convs, titles)[0].title, "Portfolio Animations")


if __name__ == "__main__":
    unittest.main()
