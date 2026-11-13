# agy-launcher

An interactive terminal launcher for Antigravity CLI (`agy`) conversations.

Browse your past conversations with titles, short descriptions, workspaces, and timestamps, filter by keyword, and launch any conversation in either **default mode** or **`--dangerously-skip-permissions`** mode.

## Features

- Permanent **+ Start New Conversation** option pinned at the top of the list, with an optional prompt to create/select a project folder inside `~/Antigravity` (leave blank to launch in your current directory).
- Reads directly from `~/.gemini/antigravity-cli/conversation_summaries.db` (read-only) with zero external dependencies.
- Displays a short description for each conversation (extracted from the opening user prompt or preview), along with relative timestamp, step count, and workspace path.
- Star conversations (`s`) to pin them to the top, and rename titles (`r`) with local persistence in `~/.config/agy-launcher/`.
- Live search (`/`) across titles, descriptions, workspaces, and conversation IDs.
- Mode selector before launch:
  1. **Default mode**: `agy` (new) or `agy --conversation <id>`
  2. **Dangerously skip permissions**: `agy --dangerously-skip-permissions` (new) or `agy --conversation <id> --dangerously-skip-permissions`

## Usage

Run directly from the project folder:

```bash
./agy-launcher
```

Optional: symlink it into `~/.local/bin` so you can run `agy-launcher` from anywhere:

```bash
ln -s "$(pwd)/agy-launcher" ~/.local/bin/agy-launcher
```

### Controls

| Key | Action |
| --- | --- |
| `↑` / `↓` or `j` / `k` | Navigate conversations / modes |
| `s` or `*` | Star / unstar conversation (starred items pin to top) |
| `r` | Rename conversation title (clear & press Enter to reset) |
| `PgUp` / `PgDn` | Jump 10 items |
| `g` / `G` | Jump to top / bottom |
| `/` | Search / filter conversations |
| `Esc` | Clear search or go back |
| `Enter` | Select conversation / confirm launch mode |
| `1` / `2` | Quick-select launch mode |
| `q` | Quit |

### CLI Flags

- `--db <path>`: Use a custom `conversation_summaries.db` path.
- `--include-subagents`: Also list nested subagent conversations.
- `--dry-run`: Print the `agy` command that would be executed instead of launching it.

## Running Tests

```bash
cd /path/to/agy-launcher
python3 -m unittest discover -s tests -v
```
