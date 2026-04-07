# GoodLooks — V2 plan

Builds on [V1_PLAN.md](V1_PLAN.md): same CLI commands, data model, and storage. V2 adds **desktop wallpaper sync** (main display) and a **richer terminal dashboard** (ASCII/box layout, full width).

## Goals (V2)

- **Wallpaper:** Render the current task list to an image and set it as the **main monitor** desktop picture on macOS.
- **Auto-refresh:** After **every successful mutation** that persists tasks (`add`, `done`, `rm`, `edit`), regenerate the wallpaper and reapply it.
- **Terminal UI:** Replace compact table-only output with a **full-width, box-heavy dashboard** in the same terminal (no new window).
- **Reliability:** Wallpaper failures must **not** block task saves; surface a short warning only.
- **Opt-out:** Allow disabling wallpaper via env (e.g. `GOODLOOKS_WALLPAPER=0`) or config for debugging and machines where automation is restricted.

## Wallpaper (macOS)

### Behavior

- **Target:** **Main display only** for V2. Document that other displays / Spaces may keep their own wallpaper until a later release.
- **Trigger:** Call wallpaper refresh from the same code path that **saves** tasks (after `save_data` or equivalent), so every user-visible list change updates the desktop image.
- **Output:** Write a fixed-path image under the app config directory (e.g. next to `tasks.json`), e.g. `wallpaper.png`.
- **Setter:** Use **AppleScript via `osascript`** (or equivalent) to set the desktop picture from an absolute POSIX path. Expect possible **Automation / Accessibility** prompts on first use.

### Implementation notes

- **Renderer:** e.g. **Pillow** to draw text/layout onto a bitmap sized for the **main screen** (account for Retina scale where practical).
- **Data:** Reuse the same sorted/filtered task list as the CLI; wallpaper layout can differ from terminal layout but should show the same tasks.
- **Failures:** Log or print a one-line warning; do not change exit code for successful task operations.
- **Terminal:** Wallpaper refresh is **silent**; do not open new terminal windows on save.

### Opt-out

- Support **`GOODLOOKS_WALLPAPER=0`** (or documented equivalent) to skip render + `osascript` entirely.

## Terminal dashboard (locked spec)

**Surface:** All list views run in the **current terminal** (no new window). Layout is **ASCII/box-forward** and uses **full terminal width** (no artificial max-width cap; still respect normal margins/padding so borders behave on narrow terminals).

**Outer frame:** One **large bordered panel** branded **GoodLooks** as the outer container for any **list-style** output (`goodlooks` with no args, `goodlooks list` with any filter).

**Header (minimal):** Top **stats strip** only: **`total · pending · done`**, with subtle separators. **Do not** show the data file path in the UI.

**Mode clarity:** Below the stats, a **horizontal rule** and a **mode label** matching the active filter: **Pending** (default / `--pending`), **Completed** (`--done`), **All tasks** (`--all`).

**Task rows:** Fixed visual columns: **status glyph** (`○` / `✓`), **ID** (narrow, aligned), **title** (primary). **`created_at` on the same line** as the title, **dim**, **right-aligned** when width allows. If the line would overflow, **truncate the title with ellipsis** before dropping `created_at` from the same line (same-line metadata is the priority).

**Empty states:** Keep the **outer panel + stats**; inside, a **spacious placard** with a short message and the **exact next command** (`goodlooks add "..."`).

**Footer:** Inside the panel, **two compact hint lines**: primary verbs (`add`, `done`, `rm`, `edit`, `list --all`) and how to get help (`goodlooks help` / `--help`). **Version** stays on `goodlooks version` / `--version`, not in the dashboard footer.

**After mutations:** On successful **`add`**, **`done`**, **`rm`**, and **`edit`**, print the **one-line success message**, then immediately render the **full pending dashboard** (same layout as default `goodlooks` / `goodlooks list --pending`).

**`NO_COLOR`:** Preserve **layout, boxes, and spacing**; disable **color styling** only.

## New / optional commands (V2)

- **`goodlooks wallpaper`** (optional): Manually regenerate and apply wallpaper without mutating tasks (useful for testing).
- Document wallpaper behavior and opt-out in `README` and `goodlooks help`.

## Dependencies (additions)

- **Pillow** (or chosen image renderer) for wallpaper bitmap generation.
- Existing: **Click**, **Rich**.

## Implementation slices (suggested order)

1. Refactor **render path** for lists into a dedicated “dashboard” builder (panels, rules, full width).
2. Implement **mutation success → full pending dashboard** after `add` / `done` / `rm` / `edit`.
3. Add **wallpaper renderer** (PNG) + **main-screen dimensions** detection on macOS.
4. Add **`osascript`** apply step + **opt-out** env var.
5. Hook wallpaper refresh **after every successful save**.
6. Harden: truncation/width edge cases, `NO_COLOR`, narrow terminals, wallpaper failure warnings.

## Out of scope for V2

- Multi-monitor wallpaper, per-Space customization.
- Opening a **new terminal window** for the list (deferred; same-terminal dashboard only).
- Linux / Windows wallpaper (macOS-only V2 unless explicitly extended).
- Sync, accounts, cloud backup.
