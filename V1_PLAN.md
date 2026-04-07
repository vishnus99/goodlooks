# GoodLooks — V1 plan

CLI to-do list with a polished terminal experience. Command name: **`goodlooks`**.

## Goals (V1)

- Fast local task list with predictable commands and readable output.
- Subcommand-style interface (feels like a real tool).
- Persistent storage between runs.
- Restrained use of color and Unicode; respect `NO_COLOR`.

## Invocation

- Primary: `goodlooks` (must be on `PATH` after install).
- Optional shortcut: shell alias `gl` → `goodlooks` (document for users; add to `~/.zshrc` after install: `alias gl='goodlooks'`).

## Commands

| Command | Purpose |
|--------|---------|
| `add` | Create a task |
| `list` | Show tasks (with filters as needed) |
| `done` | Mark a task complete |
| `rm` | Remove a task (confirm unless `--force`) |
| `edit` | Change a task title |
| `help` / `--help` | Clear help with examples |
| `version` | Show version |

**Default (no subcommand / no args):** show the main task view — **pending tasks first**, plus a one-line hint (how to add, how to get help).

## Task model

| Field | Notes |
|-------|--------|
| `id` | Integer; **never reused** after delete |
| `title` | String |
| `done` | Boolean |
| `created_at` | Timestamp (recommended for metadata in list output) |

## Listing & sort

- **Sort:** Incomplete first, then complete; within each group, **by `id` ascending**.
- **Symbols (Unicode):** pending `○`, done `✓` (optional later: ASCII fallback `[ ]` / `[x]` for broken terminals).
- **Layout:** Header with counts (total / pending / done); each row: status, id, title, optional muted `created_at`.

## Color

- Use color for meaning: accent (header / success), muted (metadata), semantic warn/error.
- If `NO_COLOR` is set, disable all color.

## Storage

- Single file (e.g. JSON) under a dedicated app directory, e.g. `~/.config/goodlooks/` on Unix.
- Human-readable format is fine for V1.

## UX & errors

- Short, actionable messages (e.g. “Task #12 not found. Run `goodlooks list`.”).
- No silent failures; avoid raw stack traces for user-facing errors.
- Destructive actions: confirm `rm` unless `--force`.
- `--help` per subcommand with examples.

## Out of scope for V1

- Due dates, priorities, tags, search (unless you explicitly add a minimal filter to `list`).
- Sync, accounts, or multi-device.
- Database backend.

## Implementation slices (suggested order)

1. Parse subcommands; default → pending list + hint.
2. `add` + `list` (in-memory or file from the start).
3. `done` + `rm` (with confirmation).
4. `edit`.
5. Persist JSON; never-reuse IDs; load on startup.
6. Polish: colors, `NO_COLOR`, help text, `version`.
