from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

import click
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

APP_NAME = "goodlooks"
APP_TITLE = "GoodLooks"
VERSION = "0.2.0"

URGENCY_LEVELS = ("low", "normal", "high")
URGENCY_RANK = {"high": 0, "normal": 1, "low": 2}

NO_COLOR = os.getenv("NO_COLOR") is not None
console = Console(no_color=NO_COLOR)
WALLPAPER_FILE_NAME = "goodlooks.png"


def data_file_path() -> Path:
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        base_dir = Path(xdg_config_home)
    else:
        base_dir = Path.home() / ".config"
    return base_dir / APP_NAME / "tasks.json"


def ensure_data_file() -> Path:
    path = data_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        initial_data = {"meta": {"last_id": 0}, "tasks": []}
        path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")
    return path


def normalize_task(task: dict[str, Any]) -> None:
    u = task.get("urgency", "normal")
    if u not in URGENCY_RANK:
        u = "normal"
    task["urgency"] = u


def load_data() -> dict[str, Any]:
    path = ensure_data_file()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "meta" not in data:
        data["meta"] = {"last_id": 0}
    if "last_id" not in data["meta"]:
        data["meta"]["last_id"] = 0
    if "tasks" not in data:
        data["tasks"] = []
    for task in data["tasks"]:
        normalize_task(task)
    return data


def save_data(data: dict[str, Any]) -> None:
    path = ensure_data_file()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def wallpaper_enabled() -> bool:
    return os.getenv("GOODLOOKS_WALLPAPER", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def wallpaper_output_path() -> Path:
    out_dir = Path.home() / "Documents" / "Goodlooks Wallpaper"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / WALLPAPER_FILE_NAME


def get_main_screen_size() -> tuple[int, int]:
    try:
        appkit = import_module("AppKit")
        screens = getattr(appkit.NSScreen, "screens")()
        if screens and len(screens) > 0:
            frame = screens[0].frame()
            scale = float(getattr(screens[0], "backingScaleFactor")())
            width = max(1, int(frame.size.width * scale))
            height = max(1, int(frame.size.height * scale))
            return width, height
    except Exception:
        pass
    return (2560, 1440)


def task_lines_for_wallpaper(tasks: list[dict[str, Any]]) -> list[str]:
    pending = [t for t in sort_tasks(tasks) if not t["done"]]
    if not pending:
        return ['No pending tasks. Add one with: goodlooks add "..."']
    lines: list[str] = []
    for task in pending:
        urgency = task.get("urgency", "normal")
        urg_mark = "▲" if urgency == "high" else ("▼" if urgency == "low" else "●")
        lines.append(f"#{task['id']:>3}  {urg_mark}  {task['title']}")
    return lines


def truncate_text_to_width(
    draw: Any, font: Any, text: str, max_width: int, suffix: str = "..."
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + suffix, font=font) > max_width:
        trimmed = trimmed[:-1]
    if not trimmed:
        return suffix
    return trimmed + suffix


def render_wallpaper_image(tasks: list[dict[str, Any]], output_path: Path) -> None:
    image_mod = import_module("PIL.Image")
    draw_mod = import_module("PIL.ImageDraw")
    font_mod = import_module("PIL.ImageFont")
    width, height = get_main_screen_size()
    image = image_mod.new("RGB", (width, height), color=(12, 16, 27))
    draw = draw_mod.Draw(image)

    title_size = max(26, width // 44)
    body_size = max(18, width // 88)
    try:
        title_font = font_mod.truetype("Menlo.ttc", title_size)
        body_font = font_mod.truetype("Menlo.ttc", body_size)
    except Exception:
        title_font = font_mod.load_default()
        body_font = font_mod.load_default()

    outer_margin_x = max(160, width // 10)
    outer_margin_y = max(90, height // 12)
    panel_x0 = outer_margin_x
    panel_y0 = outer_margin_y
    panel_x1 = width - outer_margin_x
    panel_y1 = height - outer_margin_y
    panel_radius = max(18, width // 80)
    draw.rounded_rectangle(
        (panel_x0, panel_y0, panel_x1, panel_y1),
        radius=panel_radius,
        fill=(20, 26, 40),
        outline=(53, 68, 104),
        width=max(2, width // 900),
    )

    padding_x = panel_x0 + max(36, width // 40)
    right_limit = panel_x1 - max(36, width // 40)
    y = panel_y0 + max(34, height // 40)
    total = len(tasks)
    pending = sum(1 for t in tasks if not t["done"])
    done_n = total - pending
    header = f"{APP_TITLE}   total {total} · pending {pending} · done {done_n}"
    header = truncate_text_to_width(draw, title_font, header, max(100, right_limit - padding_x))
    draw.text(
        (padding_x, y),
        header,
        font=title_font,
        fill=(181, 140, 255),
    )
    y += title_size + max(20, height // 60)

    lines = task_lines_for_wallpaper(tasks)
    line_gap = max(8, body_size // 3)
    max_lines = max(
        4,
        (panel_y1 - y - max(22, height // 48)) // (body_size + line_gap),
    )
    display_lines = lines[: max_lines - 1] if len(lines) > max_lines else lines
    for line in display_lines:
        safe_line = truncate_text_to_width(draw, body_font, line, max(100, right_limit - padding_x))
        draw.text((padding_x, y), safe_line, font=body_font, fill=(231, 236, 246))
        y += body_size + line_gap
    if len(lines) > max_lines:
        remaining = len(lines) - len(display_lines)
        draw.text(
            (padding_x, y),
            f"... and {remaining} more",
            font=body_font,
            fill=(146, 156, 178),
        )

    image.save(output_path, format="PNG")


def apply_wallpaper(path: Path) -> None:
    script = f'tell application "System Events" to tell current desktop to set picture to "{path}"'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)


def refresh_wallpaper(tasks: list[dict[str, Any]]) -> bool:
    if not wallpaper_enabled():
        return False
    output_path = wallpaper_output_path().resolve()
    try:
        render_wallpaper_image(tasks, output_path)
        apply_wallpaper(output_path)
        return True
    except Exception as exc:
        console.print(f"[yellow]Wallpaper refresh skipped:[/yellow] {exc}")
        return False


def find_task_by_id(tasks: list[dict[str, Any]], task_id: int) -> dict[str, Any] | None:
    for task in tasks:
        if task["id"] == task_id:
            return task
    return None


def sort_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(t: dict[str, Any]) -> tuple[bool, int, int]:
        done = bool(t["done"])
        urgency_order = (
            URGENCY_RANK.get(t.get("urgency", "normal"), 1) if not done else 0
        )
        return (done, urgency_order, t["id"])

    return sorted(tasks, key=sort_key)


def print_command_footer() -> None:
    foot = Text.assemble(
        ("Commands: ", "dim"),
        ("add ", "cyan"),
        ("done ", "cyan"),
        ("rm ", "cyan"),
        ("edit ", "cyan"),
        ("list ", "cyan"),
        ("\n", ""),
        ("Help: ", "dim"),
        ("goodlooks --help", "bold green"),
    )
    console.print(Panel(foot, border_style="dim", box=box.SIMPLE))


def urgency_markup(urgency: str) -> str:
    if urgency == "high":
        return "[bold red]▲ high[/bold red]"
    if urgency == "low":
        return "[dim green]▼ low[/dim green]"
    return "[bold blue]● normal[/bold blue]"


def render_tasks(
    tasks: list[dict[str, Any]],
    mode: str,
    urgency_filter: str | None = None,
) -> None:
    total = len(tasks)
    pending = sum(1 for t in tasks if not t["done"])
    done_n = total - pending

    filtered: list[dict[str, Any]]
    if mode == "done":
        filtered = [t for t in tasks if t["done"]]
    elif mode == "all":
        filtered = list(tasks)
    else:
        filtered = [t for t in tasks if not t["done"]]

    if urgency_filter:
        filtered = [t for t in filtered if t.get("urgency", "normal") == urgency_filter]

    filtered = sort_tasks(filtered)

    stats_line = Text.assemble(
        (f"{APP_TITLE}  ", "bold magenta"),
        ("total ", "dim"),
        (str(total), "bold white"),
        ("  │  ", "dim cyan"),
        ("pending ", "dim"),
        (str(pending), "bold yellow"),
        ("  │  ", "dim cyan"),
        ("done ", "dim"),
        (str(done_n), "bold green"),
    )

    if mode == "done":
        mode_label = "[bold yellow]Completed[/bold yellow]"
    elif mode == "all":
        mode_label = "[bold white]All tasks[/bold white]"
    else:
        mode_label = "[bold bright_cyan]Pending[/bold bright_cyan]"
    if urgency_filter:
        mode_label += f"  [dim]· urgency=[/dim][bold] {urgency_filter}[/bold]"

    if not filtered:
        empty = Text()
        if mode == "pending":
            empty.append("No pending tasks.\n", style="yellow")
            empty.append('Add one with goodlooks add "Your task"', style="bold cyan")
            if urgency_filter:
                empty.append(f" --urgency {urgency_filter}", style="bold cyan")
            empty.append(".", style="bold cyan")
        elif mode == "done":
            empty.append("No completed tasks yet.\n", style="yellow")
        else:
            empty.append("No tasks yet.\n", style="yellow")
            empty.append('Add one with goodlooks add "Your task"', style="bold cyan")
            empty.append(".", style="bold cyan")
        body = Group(stats_line, Rule(style="bright_blue"), Text.from_markup(mode_label), empty)
        console.print(
            Panel(
                body,
                title="[bold bright_magenta]═══ GoodLooks ═══[/bold bright_magenta]",
                border_style="bright_blue",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        print_command_footer()
        return

    table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        border_style="blue",
        row_styles=["none", "dim"],
    )
    table.add_column("✦", width=3, justify="center")
    table.add_column("ID", justify="right", style="bold white", width=4)
    table.add_column("Urgency", width=14)
    table.add_column("Title", overflow="fold", style="default")
    table.add_column("Created", style="dim italic", width=20, overflow="ellipsis")

    for task in filtered:
        status = "✓" if task["done"] else "○"
        status_style = "bold green" if task["done"] else "bright_white"
        urg = task.get("urgency", "normal")
        table.add_row(
            f"[{status_style}]{status}[/{status_style}]",
            str(task["id"]),
            urgency_markup(urg),
            task["title"],
            task.get("created_at", ""),
        )

    body = Group(
        stats_line,
        Rule(style="bright_blue"),
        Text.from_markup(mode_label),
        table,
        Rule(style="dim blue"),
        Text.from_markup(
            "[dim]Tip:[/dim] [bold]goodlooks add \"…\" --urgency high[/bold]  ·  "
            "[dim]goodlooks --help[/dim]"
        ),
    )
    console.print(
        Panel(
            body,
            title="[bold bright_magenta]═══ GoodLooks ═══[/bold bright_magenta]",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    print_command_footer()


@click.group(invoke_without_command=True)
@click.version_option(version=VERSION, prog_name=APP_NAME)
@click.pass_context
def goodlooks(ctx: click.Context) -> None:
    """GoodLooks - Get it done today."""
    if ctx.invoked_subcommand is None:
        data = load_data()
        render_tasks(data["tasks"], mode="pending")


@goodlooks.command()
@click.argument("title", required=True)
@click.option(
    "-u",
    "--urgency",
    type=click.Choice(list(URGENCY_LEVELS)),
    default="normal",
    show_default=True,
    help="Task urgency: low, normal, or high.",
)
def add(title: str, urgency: str) -> None:
    """Add a task to the to-do list."""
    cleaned = title.strip()
    if not cleaned:
        raise click.UsageError("Task title cannot be empty.")

    data = load_data()
    next_id = int(data["meta"]["last_id"]) + 1
    data["meta"]["last_id"] = next_id
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    task = {
        "id": next_id,
        "title": cleaned,
        "done": False,
        "created_at": now,
        "urgency": urgency,
    }
    data["tasks"].append(task)
    save_data(data)
    refresh_wallpaper(data["tasks"])
    console.print(
        f"[green]Added task #[/green][bold]{next_id}[/bold] "
        f"[dim]({urgency})[/dim]: {cleaned}"
    )


@goodlooks.command("list")
@click.option("--all", "show_mode", flag_value="all", default=False, help="Show all tasks.")
@click.option("--done", "show_mode", flag_value="done", help="Show only completed tasks.")
@click.option(
    "--pending",
    "show_mode",
    flag_value="pending",
    default=True,
    help="Show only pending tasks (default).",
)
@click.option(
    "--urgency",
    type=click.Choice(list(URGENCY_LEVELS)),
    default=None,
    help="Only show tasks with this urgency (combine with list mode).",
)
def list_tasks(show_mode: str, urgency: str | None) -> None:
    """Show tasks."""
    data = load_data()
    render_tasks(data["tasks"], mode=show_mode, urgency_filter=urgency)


@goodlooks.command()
@click.option("-i", "--id", "task_id", type=int, required=True, help="Task ID to mark complete.")
def done(task_id: int) -> None:
    """Mark a task as complete."""
    data = load_data()
    task = find_task_by_id(data["tasks"], task_id)
    if task is None:
        raise click.ClickException(f"Task #{task_id} not found. Run `goodlooks list`.")
    if task["done"]:
        console.print(f"[yellow]Task #{task_id} is already completed.[/yellow]")
        return
    task["done"] = True
    save_data(data)
    refresh_wallpaper(data["tasks"])
    console.print(f"[green]Completed task #[/green][bold]{task_id}[/bold].")


@goodlooks.command()
@click.option("-i", "--id", "task_id", type=int, required=True, help="Task ID to remove.")
@click.option("-f", "--force", is_flag=True, help="Remove without confirmation.")
def rm(task_id: int, force: bool) -> None:
    """Remove a task (confirm unless --force)."""
    data = load_data()
    task = find_task_by_id(data["tasks"], task_id)
    if task is None:
        raise click.ClickException(f"Task #{task_id} not found. Run `goodlooks list`.")

    if not force:
        confirmed = click.confirm(f"Remove task #{task_id}: '{task['title']}'?", default=False)
        if not confirmed:
            console.print("[yellow]Canceled.[/yellow]")
            return

    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    save_data(data)
    refresh_wallpaper(data["tasks"])
    console.print(f"[green]Removed task #[/green][bold]{task_id}[/bold].")


@goodlooks.command()
@click.option("-i", "--id", "task_id", type=int, required=True, help="Task ID to edit.")
@click.option("-n", "--new-title", default=None, help="New task title.")
@click.option(
    "-u",
    "--urgency",
    type=click.Choice(list(URGENCY_LEVELS)),
    default=None,
    help="Set urgency: low, normal, or high.",
)
def edit(task_id: int, new_title: str | None, urgency: str | None) -> None:
    """Change task title and/or urgency."""
    if new_title is None and urgency is None:
        raise click.UsageError("Provide --new-title and/or --urgency.")

    data = load_data()
    task = find_task_by_id(data["tasks"], task_id)
    if task is None:
        raise click.ClickException(f"Task #{task_id} not found. Run `goodlooks list`.")

    normalize_task(task)
    parts: list[str] = []
    if new_title is not None:
        cleaned = new_title.strip()
        if not cleaned:
            raise click.UsageError("New title cannot be empty.")
        old_title = task["title"]
        task["title"] = cleaned
        parts.append(f"'{old_title}' -> '{cleaned}'")
    if urgency is not None:
        old_u = task["urgency"]
        task["urgency"] = urgency
        parts.append(f"urgency {old_u} -> {urgency}")

    save_data(data)
    refresh_wallpaper(data["tasks"])
    console.print(
        f"[green]Updated task #[/green][bold]{task_id}[/bold]: " + " · ".join(parts)
    )


@goodlooks.command()
def wallpaper() -> None:
    """Regenerate and apply wallpaper from current tasks."""
    data = load_data()
    if not wallpaper_enabled():
        console.print("[yellow]Wallpaper integration disabled via GOODLOOKS_WALLPAPER.[/yellow]")
        return
    applied = refresh_wallpaper(data["tasks"])
    if applied:
        console.print(f"[green]Wallpaper updated:[/green] {wallpaper_output_path()}")


@goodlooks.command("help")
@click.pass_context
def help_command(ctx: click.Context) -> None:
    """Show help and examples."""
    console.print(ctx.parent.get_help())
    console.print("\nExamples:")
    console.print("  goodlooks add \"Buy milk\" --urgency high")
    console.print("  goodlooks list --all --urgency normal")
    console.print("  goodlooks done --id 2")
    console.print("  goodlooks edit --id 2 --new-title \"Buy oat milk\"")
    console.print("  goodlooks edit --id 2 --urgency low")
    console.print("  goodlooks rm --id 2")
    console.print("  goodlooks wallpaper")
    console.print("  goodlooks shell")


def run_interactive_command(raw_line: str) -> bool:
    line = raw_line.strip()
    if not line:
        return True

    if line in {"quit", "exit", ":q"}:
        return False

    if line in {"clear", "cls"}:
        console.clear()
        data = load_data()
        render_tasks(data["tasks"], mode="pending")
        return True

    try:
        args = shlex.split(line)
    except ValueError as exc:
        console.print(f"[red]Invalid input:[/red] {exc}")
        return True

    if not args:
        return True

    if args[0] in {"shell", "interactive"}:
        console.print("[yellow]Already in shell mode.[/yellow]")
        return True

    try:
        goodlooks.main(args=args, prog_name=APP_NAME, standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
    except click.UsageError as exc:
        exc.show()
    except click.Abort:
        console.print("[yellow]Canceled.[/yellow]")
    except SystemExit as exc:
        # Click may raise SystemExit in some code paths.
        if exc.code not in (0, None):
            console.print(f"[red]Command exited with code {exc.code}.[/red]")

    if args[0] in {"add", "done", "rm", "edit"}:
        data = load_data()
        render_tasks(data["tasks"], mode="pending")
    return True


def build_shell_session() -> Any | None:
    try:
        prompt_toolkit = import_module("prompt_toolkit")
        completion_mod = import_module("prompt_toolkit.completion")
        history_mod = import_module("prompt_toolkit.history")
    except ImportError:
        return None
    PromptSession = getattr(prompt_toolkit, "PromptSession")
    WordCompleter = getattr(completion_mod, "WordCompleter")
    FileHistory = getattr(history_mod, "FileHistory")
    history_path = ensure_data_file().parent / "shell_history.txt"
    completer = WordCompleter(
        [
            "add",
            "list",
            "done",
            "rm",
            "edit",
            "help",
            "wallpaper",
            "version",
            "clear",
            "exit",
            "quit",
            "--help",
            "--all",
            "--pending",
            "--done",
            "--urgency",
            "--id",
            "--new-title",
            "--force",
            "low",
            "normal",
            "high",
        ],
        ignore_case=True,
        sentence=True,
    )
    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=completer,
        complete_while_typing=True,
    )


@goodlooks.command("shell")
def shell_mode() -> None:
    """Start interactive GoodLooks shell mode."""
    console.print("[bold bright_magenta]GoodLooks interactive shell[/bold bright_magenta]")
    console.print("[dim]Type commands as usual, e.g. add/list/done/rm/edit.[/dim]")
    console.print("[dim]Use 'exit' or 'quit' to leave shell mode.[/dim]")
    shell_session = build_shell_session()
    if shell_session is None:
        console.print(
            "[yellow]Tip:[/yellow] Install [bold]prompt_toolkit[/bold] for tab completion and history."
        )
    data = load_data()
    render_tasks(data["tasks"], mode="pending")
    while True:
        try:
            if shell_session is not None:
                line = shell_session.prompt("goodlooks> ")
            else:
                line = click.prompt(
                    "goodlooks", prompt_suffix="> ", default="", show_default=False
                )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Leaving shell mode.[/dim]")
            break
        should_continue = run_interactive_command(line)
        if not should_continue:
            console.print("[dim]Leaving shell mode.[/dim]")
            break


@goodlooks.command()
def version() -> None:
    """Show version."""
    console.print(f"{APP_TITLE} {VERSION}")


if __name__ == "__main__":
    goodlooks(prog_name=APP_NAME)
