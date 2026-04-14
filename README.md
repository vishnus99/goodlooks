# GoodLooks CLI

Local to-do manager with a polished terminal experience.

## Install (editable for development)

```bash
python3 -m pip install -e .
```

## Run

```bash
goodlooks --help
```

Wallpaper integration (macOS) is enabled by default and refreshes after successful task updates (`add`, `done`, `rm`, `edit`).

```bash
goodlooks wallpaper
```

Disable it when needed:

```bash
GOODLOOKS_WALLPAPER=0 goodlooks add "No wallpaper refresh"
```

## V3 interactive features (MVP)

Get recommendation steps for a task:

```bash
goodlooks recommend --id 2
```

Open a clickable board in your browser. This starts a **local HTTP server** on `127.0.0.1` and opens the board; the task list **updates live** (Server-Sent Events) whenever you change tasks from the CLI (`add`, `done`, `rm`, `edit`). Leave the tab open while you work in the terminal.

```bash
goodlooks board
```

Default port is **9876**. Override if something else is using it:

```bash
GOODLOOKS_BOARD_PORT=9988 goodlooks board
```

Optional shortcut:

```bash
alias gl='goodlooks'
```