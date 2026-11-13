"""Interactive curses TUI for selecting and launching Antigravity conversations."""

from __future__ import annotations

import curses
import os
import textwrap
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .conversations import (
    Conversation,
    LaunchSelection,
    apply_custom_titles,
    build_launch_command,
    filter_conversations,
    format_relative_time,
    get_projects_dir,
    load_custom_titles,
    load_starred_ids,
    make_new_conversation_item,
    resolve_project_dir,
    set_custom_title,
    shorten_home,
    sort_conversations,
    toggle_starred,
)

# Color pair IDs
PAIR_HEADER = 1
PAIR_SELECTED = 2
PAIR_MUTED = 3
PAIR_ACCENT = 4
PAIR_DANGER = 5
PAIR_BORDER = 6
PAIR_STAR = 7


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(PAIR_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(PAIR_MUTED, curses.COLOR_WHITE, -1)
    curses.init_pair(PAIR_ACCENT, curses.COLOR_GREEN, -1)
    curses.init_pair(PAIR_DANGER, curses.COLOR_RED, -1)
    curses.init_pair(PAIR_BORDER, curses.COLOR_BLUE, -1)
    curses.init_pair(PAIR_STAR, curses.COLOR_YELLOW, -1)


def _safe_addstr(win: "curses._CursesWindow", y: int, x: int, text: str, attr: int = 0) -> None:
    max_y, max_x = win.getmaxyx()
    if y < 0 or y >= max_y or x < 0 or x >= max_x:
        return
    avail = max_x - x
    if y == max_y - 1 and avail > 0:
        # Avoid writing to the bottom-right corner cell in curses.
        avail -= 1
    if avail <= 0:
        return
    try:
        win.addnstr(y, x, text, avail, attr)
    except curses.error:
        pass


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 1] + "…"


def _draw_list_view(
    stdscr: "curses._CursesWindow",
    all_conversations: List[Conversation],
    filtered: List[Conversation],
    starred_ids: Set[str],
    selected_idx: int,
    scroll_offset: int,
    search_query: str,
    searching: bool,
) -> int:
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    # Header (row 0)
    title = " Antigravity Conversations "
    listed_count = sum(1 for c in filtered if not c.is_new)
    starred_count = sum(1 for c in filtered if not c.is_new and c.conversation_id in starred_ids)
    star_suffix = f"★ {starred_count}  ·  " if starred_count else ""
    count_str = f"{star_suffix}{listed_count}/{len(all_conversations)} "
    _safe_addstr(stdscr, 0, 0, title, curses.color_pair(PAIR_HEADER) | curses.A_BOLD)
    _safe_addstr(
        stdscr,
        0,
        max(len(title), width - len(count_str)),
        count_str,
        curses.A_DIM,
    )

    # Search bar (row 1)
    if searching:
        search_label = f" Search: {search_query}_"
        _safe_addstr(stdscr, 1, 0, search_label, curses.color_pair(PAIR_ACCENT) | curses.A_BOLD)
    elif search_query:
        search_label = f" Filter: {search_query}  (press / to edit, Esc to clear)"
        _safe_addstr(stdscr, 1, 0, search_label, curses.color_pair(PAIR_ACCENT))
    else:
        _safe_addstr(stdscr, 1, 0, " Press / to search by title, description, or workspace", curses.A_DIM)

    _safe_addstr(stdscr, 2, 0, "─" * max(0, width - 1), curses.color_pair(PAIR_BORDER) | curses.A_DIM)

    # Layout calculation: reserve bottom 6 rows for detail pane + footer
    detail_height = 5 if height >= 16 else 0
    footer_row = height - 1
    detail_start = footer_row - detail_height
    list_start = 3
    list_height = max(1, detail_start - list_start)

    if list_height >= 9:
        rows_per_item = 3  # title + description + blank separator line
    elif list_height >= 6:
        rows_per_item = 2
    else:
        rows_per_item = 1
    visible_items = max(1, list_height // rows_per_item)

    if selected_idx < scroll_offset:
        scroll_offset = selected_idx
    elif selected_idx >= scroll_offset + visible_items:
        scroll_offset = selected_idx - visible_items + 1
    scroll_offset = max(0, scroll_offset)

    if not filtered:
        _safe_addstr(stdscr, list_start + 1, 2, "No matching conversations.", curses.A_DIM)
    else:
        for i in range(visible_items):
            idx = scroll_offset + i
            if idx >= len(filtered):
                break
            conv = filtered[idx]
            is_selected = idx == selected_idx
            is_starred = (not conv.is_new) and (conv.conversation_id in starred_ids)
            y = list_start + i * rows_per_item

            if conv.is_new:
                meta = "New session"
            else:
                rel_time = format_relative_time(conv.last_modified_time)
                ws = conv.primary_workspace_display
                meta_parts = [rel_time, f"{conv.step_count} steps"]
                if ws:
                    meta_parts.append(ws)
                meta = " · ".join(meta_parts)

            prefix = "▸ " if is_selected else "  "
            star_marker = "★ " if is_starred else ""
            avail_title = max(10, width - len(prefix) - len(star_marker) - len(meta) - 3)
            title_text = _truncate(conv.title, avail_title)
            line1 = f"{prefix}{star_marker}{title_text}"
            padding = max(1, width - len(line1) - len(meta) - 1)
            full_line1 = f"{line1}{' ' * padding}{meta}"

            if is_selected:
                _safe_addstr(
                    stdscr,
                    y,
                    0,
                    _truncate(full_line1.ljust(max(0, width - 1)), width - 1),
                    curses.color_pair(PAIR_SELECTED) | curses.A_BOLD,
                )
            else:
                _safe_addstr(stdscr, y, 0, prefix, curses.A_BOLD)
                if is_starred:
                    _safe_addstr(stdscr, y, len(prefix), star_marker, curses.color_pair(PAIR_STAR) | curses.A_BOLD)
                title_attr = (curses.color_pair(PAIR_ACCENT) | curses.A_BOLD) if conv.is_new else curses.A_BOLD
                _safe_addstr(stdscr, y, len(prefix) + len(star_marker), title_text, title_attr)
                _safe_addstr(stdscr, y, len(line1) + padding, meta, curses.A_DIM)

            if rows_per_item >= 2 and y + 1 < detail_start:
                desc_line = "  " + _truncate(conv.description, max(10, width - 4))
                desc_attr = curses.A_NORMAL if is_selected else curses.A_DIM
                _safe_addstr(stdscr, y + 1, 0, desc_line, desc_attr)

    # Detail pane
    if detail_height > 0 and filtered and 0 <= selected_idx < len(filtered):
        conv = filtered[selected_idx]
        _safe_addstr(stdscr, detail_start, 0, "─" * max(0, width - 1), curses.color_pair(PAIR_BORDER) | curses.A_DIM)
        if conv.is_new:
            id_line = f" New session  |  Directory: {shorten_home(os.getcwd())}"
        else:
            ws_list = ", ".join(shorten_home(w) for w in conv.workspaces) if conv.workspaces else "None"
            id_line = f" ID: {conv.conversation_id}  |  Workspace: {ws_list}"
        _safe_addstr(stdscr, detail_start + 1, 0, _truncate(id_line, width - 1), curses.color_pair(PAIR_HEADER))

        wrapped = textwrap.wrap(conv.description, width=max(20, width - 3))
        for line_i in range(2):
            if line_i < len(wrapped):
                _safe_addstr(stdscr, detail_start + 2 + line_i, 1, wrapped[line_i], curses.A_NORMAL)

    # Footer
    if searching:
        footer = " Type to filter  |  Enter: Done  |  Esc: Clear search"
    else:
        footer = " ↑/↓ j/k: Move  |  s: Star  |  r: Rename  |  /: Search  |  Enter: Launch  |  q: Quit"
    _safe_addstr(stdscr, footer_row, 0, _truncate(footer, width - 1), curses.A_REVERSE)
    stdscr.refresh()
    return scroll_offset


def _prompt_new_project_dir(
    stdscr: "curses._CursesWindow",
) -> Tuple[bool, Optional[Path]]:
    """Prompt for an optional new project folder inside ~/Antigravity.

    Returns:
        (confirmed, target_dir) where confirmed=False means cancelled (Esc),
        and target_dir=None means leave blank to use current directory.
    """
    buffer = ""
    error_msg = ""
    projects_root = get_projects_dir()
    projects_display = shorten_home(str(projects_root))

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        _safe_addstr(stdscr, 0, 0, " New Conversation — Project Folder ", curses.color_pair(PAIR_HEADER) | curses.A_BOLD)
        _safe_addstr(stdscr, 1, 0, "─" * max(0, width - 1), curses.color_pair(PAIR_BORDER) | curses.A_DIM)

        _safe_addstr(
            stdscr,
            3,
            2,
            _truncate(f"Optional: create or select a folder inside {projects_display}", width - 4),
            curses.A_NORMAL,
        )
        _safe_addstr(
            stdscr,
            4,
            2,
            _truncate("Leave blank and press Enter to launch in your current directory.", width - 4),
            curses.A_DIM,
        )

        _safe_addstr(stdscr, 6, 2, "Project folder name:", curses.color_pair(PAIR_HEADER) | curses.A_BOLD)
        max_input = max(10, width - 6)
        display_buf = buffer[-(max_input - 1) :] if len(buffer) >= max_input else buffer
        _safe_addstr(stdscr, 7, 2, f"> {display_buf}_", curses.color_pair(PAIR_ACCENT) | curses.A_BOLD)

        if error_msg:
            _safe_addstr(stdscr, 9, 2, _truncate(error_msg, width - 4), curses.color_pair(PAIR_DANGER) | curses.A_BOLD)
        else:
            try:
                candidate = resolve_project_dir(buffer, projects_dir=projects_root)
            except ValueError as exc:
                _safe_addstr(stdscr, 9, 2, _truncate(str(exc), width - 4), curses.color_pair(PAIR_DANGER))
            else:
                if candidate is None:
                    preview = f"Will launch in current directory: {shorten_home(os.getcwd())}"
                elif candidate.exists():
                    preview = f"Will use existing folder: {shorten_home(str(candidate))}"
                else:
                    preview = f"Will create and launch in: {shorten_home(str(candidate))}"
                _safe_addstr(stdscr, 9, 2, _truncate(preview, width - 4), curses.A_DIM)

        footer = " Enter: Continue  |  Ctrl-U: Clear  |  Esc: Back"
        _safe_addstr(stdscr, height - 1, 0, _truncate(footer, width - 1), curses.A_REVERSE)
        stdscr.refresh()

        key = stdscr.getch()
        error_msg = ""
        if key == 27:  # Esc
            return False, None
        elif key in (curses.KEY_ENTER, 10, 13):
            try:
                target = resolve_project_dir(buffer, projects_dir=projects_root)
                return True, target
            except ValueError as exc:
                error_msg = str(exc)
        elif key == 21:  # Ctrl-U
            buffer = ""
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            buffer = buffer[:-1]
        elif 32 <= key <= 126:
            buffer += chr(key)


def _select_launch_mode(
    stdscr: "curses._CursesWindow",
    conv: Conversation,
    target_dir: Optional[Path] = None,
) -> Optional[bool]:
    """Prompt the user to choose default mode or --dangerously-skip-permissions.

    Returns:
        False for default mode, True for --dangerously-skip-permissions, None if cancelled.
    """
    selected_mode = 0  # 0 = default, 1 = dangerously-skip-permissions
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        _safe_addstr(stdscr, 0, 0, " Launch Conversation ", curses.color_pair(PAIR_HEADER) | curses.A_BOLD)
        _safe_addstr(stdscr, 1, 0, "─" * max(0, width - 1), curses.color_pair(PAIR_BORDER) | curses.A_DIM)

        _safe_addstr(stdscr, 3, 2, _truncate(conv.title, width - 4), curses.A_BOLD)
        if conv.is_new:
            _safe_addstr(stdscr, 4, 2, "Mode: Fresh Antigravity CLI session", curses.A_DIM)
            dir_display = shorten_home(str(target_dir)) if target_dir else shorten_home(os.getcwd())
            suffix = " (will be created)" if (target_dir and not target_dir.exists()) else ""
            _safe_addstr(stdscr, 5, 2, _truncate(f"Directory: {dir_display}{suffix}", width - 4), curses.A_DIM)
        else:
            _safe_addstr(stdscr, 4, 2, f"ID: {conv.conversation_id}", curses.A_DIM)
            ws_list = ", ".join(shorten_home(w) for w in conv.workspaces) if conv.workspaces else "Current directory"
            _safe_addstr(stdscr, 5, 2, _truncate(f"Workspace: {ws_list}", width - 4), curses.A_DIM)

        wrapped = textwrap.wrap(conv.description, width=max(20, width - 4))
        desc_rows = min(3, len(wrapped))
        for i in range(desc_rows):
            _safe_addstr(stdscr, 7 + i, 2, wrapped[i], curses.A_NORMAL)

        mode_y = 8 + desc_rows
        _safe_addstr(stdscr, mode_y, 2, "Select launch mode:", curses.color_pair(PAIR_HEADER) | curses.A_BOLD)

        default_cmd = " ".join(build_launch_command(conv.conversation_id, dangerously_skip_permissions=False))
        danger_cmd = " ".join(build_launch_command(conv.conversation_id, dangerously_skip_permissions=True))

        options = [
            (
                "1. Default mode",
                default_cmd,
                "Standard interactive mode — prompts before running tools/commands.",
                False,
            ),
            (
                "2. Dangerously skip permissions (--dangerously-skip-permissions)",
                danger_cmd,
                "Auto-approves all tool permission requests without prompting.",
                True,
            ),
        ]

        for idx, (label, cmd_preview, note, is_danger) in enumerate(options):
            y = mode_y + 2 + idx * 4
            if y + 2 >= height - 1:
                break
            is_sel = idx == selected_mode
            prefix = "▸ " if is_sel else "  "
            if is_sel:
                attr = (curses.color_pair(PAIR_DANGER) if is_danger else curses.color_pair(PAIR_SELECTED)) | curses.A_BOLD
                if is_danger:
                    attr |= curses.A_REVERSE
            else:
                attr = curses.color_pair(PAIR_DANGER) if is_danger else curses.A_BOLD

            _safe_addstr(stdscr, y, 2, _truncate(f"{prefix}{label}", width - 4), attr)
            _safe_addstr(stdscr, y + 1, 6, _truncate(cmd_preview, width - 8), curses.A_DIM)
            _safe_addstr(stdscr, y + 2, 6, _truncate(note, width - 8), curses.A_NORMAL if is_sel else curses.A_DIM)

        footer = " ↑/↓ or 1/2: Choose mode  |  Enter: Launch agy  |  Esc/b: Back"
        _safe_addstr(stdscr, height - 1, 0, _truncate(footer, width - 1), curses.A_REVERSE)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected_mode = 0
        elif key in (curses.KEY_DOWN, ord("j")):
            selected_mode = 1
        elif key == ord("\t"):
            selected_mode = 1 - selected_mode
        elif key == ord("1"):
            selected_mode = 0
        elif key == ord("2"):
            selected_mode = 1
        elif key in (curses.KEY_ENTER, 10, 13):
            return selected_mode == 1
        elif key in (27, ord("b"), ord("q")):
            return None


def _prompt_rename(
    stdscr: "curses._CursesWindow",
    conv: Conversation,
    has_custom_title: bool,
) -> Optional[str]:
    """Prompt the user to enter a new title for the conversation.

    Returns:
        The new title string (empty string means reset to original), or None if cancelled.
    """
    buffer = conv.title
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        _safe_addstr(stdscr, 0, 0, " Rename Conversation ", curses.color_pair(PAIR_HEADER) | curses.A_BOLD)
        _safe_addstr(stdscr, 1, 0, "─" * max(0, width - 1), curses.color_pair(PAIR_BORDER) | curses.A_DIM)

        _safe_addstr(stdscr, 3, 2, f"ID: {conv.conversation_id}", curses.A_DIM)
        _safe_addstr(stdscr, 4, 2, _truncate(f"Current title: {conv.title}", width - 4), curses.A_DIM)

        _safe_addstr(stdscr, 6, 2, "New title:", curses.color_pair(PAIR_HEADER) | curses.A_BOLD)
        max_input = max(10, width - 6)
        display_buf = buffer[-(max_input - 1) :] if len(buffer) >= max_input else buffer
        _safe_addstr(stdscr, 7, 2, f"> {display_buf}_", curses.color_pair(PAIR_ACCENT) | curses.A_BOLD)

        hint = "Leave empty and press Enter to reset to the original title." if has_custom_title else "Edit the title above (Ctrl-U clears the line)."
        _safe_addstr(stdscr, 9, 2, _truncate(hint, width - 4), curses.A_DIM)

        footer = " Enter: Save  |  Ctrl-U: Clear  |  Esc: Cancel"
        _safe_addstr(stdscr, height - 1, 0, _truncate(footer, width - 1), curses.A_REVERSE)
        stdscr.refresh()

        key = stdscr.getch()
        if key == 27:  # Esc
            return None
        elif key in (curses.KEY_ENTER, 10, 13):
            return buffer
        elif key == 21:  # Ctrl-U
            buffer = ""
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            buffer = buffer[:-1]
        elif 32 <= key <= 126:
            buffer += chr(key)


def _run_tui(
    stdscr: "curses._CursesWindow",
    conversations: List[Conversation],
) -> Optional[LaunchSelection]:
    curses.curs_set(0)
    stdscr.keypad(True)
    _init_colors()

    new_item = make_new_conversation_item()
    starred_ids = load_starred_ids()
    custom_titles = load_custom_titles()
    titled_conversations = apply_custom_titles(conversations, custom_titles)
    ordered_conversations = sort_conversations(titled_conversations, starred_ids)

    search_query = ""
    searching = False
    filtered = [new_item] + ordered_conversations
    selected_idx = 0
    scroll_offset = 0

    def _rebuild_filtered(query: str) -> List[Conversation]:
        return [new_item] + filter_conversations(ordered_conversations, query)

    while True:
        if filtered:
            selected_idx = max(0, min(selected_idx, len(filtered) - 1))
        else:
            selected_idx = 0

        scroll_offset = _draw_list_view(
            stdscr,
            ordered_conversations,
            filtered,
            starred_ids,
            selected_idx,
            scroll_offset,
            search_query,
            searching,
        )

        key = stdscr.getch()

        if searching:
            if key == 27:  # Esc
                searching = False
                search_query = ""
                filtered = _rebuild_filtered("")
                selected_idx = 0
                scroll_offset = 0
            elif key in (curses.KEY_ENTER, 10, 13):
                searching = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                search_query = search_query[:-1]
                filtered = _rebuild_filtered(search_query)
                selected_idx = 0
                scroll_offset = 0
            elif 32 <= key <= 126:
                search_query += chr(key)
                filtered = _rebuild_filtered(search_query)
                selected_idx = 0
                scroll_offset = 0
            continue

        if key in (ord("q"), 3):  # q or Ctrl-C
            return None
        elif key == 27:  # Esc clears active filter
            if search_query:
                search_query = ""
                filtered = _rebuild_filtered("")
                selected_idx = 0
                scroll_offset = 0
        elif key == ord("/"):
            searching = True
        elif key in (ord("s"), ord("*")):
            if filtered and 0 <= selected_idx < len(filtered):
                target_conv = filtered[selected_idx]
                if target_conv.is_new:
                    continue
                target_id = target_conv.conversation_id
                starred_ids = toggle_starred(target_id, starred_ids)
                ordered_conversations = sort_conversations(titled_conversations, starred_ids)
                filtered = _rebuild_filtered(search_query)
                for new_idx, conv in enumerate(filtered):
                    if conv.conversation_id == target_id:
                        selected_idx = new_idx
                        break
        elif key == ord("r"):
            if filtered and 0 <= selected_idx < len(filtered):
                target_conv = filtered[selected_idx]
                if target_conv.is_new:
                    continue
                target_id = target_conv.conversation_id
                new_title = _prompt_rename(
                    stdscr,
                    target_conv,
                    has_custom_title=target_id in custom_titles,
                )
                if new_title is not None:
                    custom_titles = set_custom_title(target_id, new_title, custom_titles)
                    titled_conversations = apply_custom_titles(conversations, custom_titles)
                    ordered_conversations = sort_conversations(titled_conversations, starred_ids)
                    filtered = _rebuild_filtered(search_query)
                    for new_idx, conv in enumerate(filtered):
                        if conv.conversation_id == target_id:
                            selected_idx = new_idx
                            break
        elif key in (curses.KEY_UP, ord("k")):
            if filtered:
                selected_idx = max(0, selected_idx - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            if filtered:
                selected_idx = min(len(filtered) - 1, selected_idx + 1)
        elif key == curses.KEY_PPAGE:
            if filtered:
                selected_idx = max(0, selected_idx - 10)
        elif key == curses.KEY_NPAGE:
            if filtered:
                selected_idx = min(len(filtered) - 1, selected_idx + 10)
        elif key == ord("g"):
            selected_idx = 0
        elif key == ord("G"):
            if filtered:
                selected_idx = len(filtered) - 1
        elif key in (curses.KEY_ENTER, 10, 13):
            if filtered and 0 <= selected_idx < len(filtered):
                chosen = filtered[selected_idx]
                target_dir: Optional[Path] = None
                if chosen.is_new:
                    confirmed, target_dir = _prompt_new_project_dir(stdscr)
                    if not confirmed:
                        continue
                mode = _select_launch_mode(stdscr, chosen, target_dir=target_dir)
                if mode is not None:
                    return LaunchSelection(
                        conversation=chosen,
                        dangerously_skip_permissions=mode,
                        working_dir=target_dir,
                    )


def pick_and_choose_mode(
    conversations: List[Conversation],
) -> Optional[LaunchSelection]:
    """Run the interactive TUI and return a LaunchSelection or None."""
    return curses.wrapper(_run_tui, conversations)
