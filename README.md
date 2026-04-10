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

Optional shortcut:

```bash
alias gl='goodlooks'
```