from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

APP_NAME = "goodlooks"
APP_TITLE = "GoodLooks"
VERSION = "0.1.0"

NO_COLOR = os.getenv("NO_COLOR") is not None
console = Console(no_color=NO_COLOR)


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
    return data


def save_data(data: dict[str, Any]) -> None:
    path = ensure_data_file()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def find_task_by_id(tasks: list[dict[str, Any]], task_id: int) -> dict[str, Any] | None:
    for task in tasks:
        if task["id"] == task_id:
            return task
    return None


def sort_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tasks, key=lambda t: (t["done"], t["id"]))


def render_tasks(tasks: list[dict[str, Any]], mode: str) -> None:
    total = len(tasks)
    pending = sum(1 for t in tasks if not t["done"])
    done = total - pending

    console.print(
        f"[bold cyan]{APP_TITLE}[/bold cyan]  total: {total}  pending: {pending}  done: {done}"
    )

    filtered: list[dict[str, Any]]
    if mode == "done":
        filtered = [t for t in tasks if t["done"]]
    elif mode == "all":
        filtered = tasks
    else:
        filtered = [t for t in tasks if not t["done"]]

    filtered = sort_tasks(filtered)

    if not filtered:
        if mode == "pending":
            console.print("[yellow]No pending tasks.[/yellow]")
            console.print("Add one with [bold]goodlooks add \"Your task\"[/bold].")
        elif mode == "done":
            console.print("[yellow]No completed tasks yet.[/yellow]")
        else:
            console.print("[yellow]No tasks yet.[/yellow]")
            console.print("Add one with [bold]goodlooks add \"Your task\"[/bold].")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Status", width=8)
    table.add_column("ID", justify="right", width=4)
    table.add_column("Title", overflow="fold")
    table.add_column("Created", style="dim", width=20)

    for task in filtered:
        status = "✓" if task["done"] else "○"
        status_style = "green" if task["done"] else "white"
        table.add_row(
            f"[{status_style}]{status}[/{status_style}]",
            str(task["id"]),
            task["title"],
            task.get("created_at", ""),
        )

    console.print(table)
    console.print("Hint: [bold]goodlooks --help[/bold] for commands.")


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
def add(title: str) -> None:
    """Add a task to the to-do list."""
    cleaned = title.strip()
    if not cleaned:
        raise click.UsageError("Task title cannot be empty.")

    data = load_data()
    next_id = int(data["meta"]["last_id"]) + 1
    data["meta"]["last_id"] = next_id
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    task = {"id": next_id, "title": cleaned, "done": False, "created_at": now}
    data["tasks"].append(task)
    save_data(data)
    console.print(f"[green]Added task #[/green][bold]{next_id}[/bold]: {cleaned}")


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
def list_tasks(show_mode: str) -> None:
    """Show tasks."""
    data = load_data()
    render_tasks(data["tasks"], mode=show_mode)


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
    console.print(f"[green]Removed task #[/green][bold]{task_id}[/bold].")


@goodlooks.command()
@click.option("-i", "--id", "task_id", type=int, required=True, help="Task ID to edit.")
@click.option("-n", "--new-title", required=True, help="New task title.")
def edit(task_id: int, new_title: str) -> None:
    """Change task title."""
    cleaned = new_title.strip()
    if not cleaned:
        raise click.UsageError("New title cannot be empty.")

    data = load_data()
    task = find_task_by_id(data["tasks"], task_id)
    if task is None:
        raise click.ClickException(f"Task #{task_id} not found. Run `goodlooks list`.")

    old_title = task["title"]
    task["title"] = cleaned
    save_data(data)
    console.print(
        f"[green]Updated task #[/green][bold]{task_id}[/bold]: '{old_title}' -> '{cleaned}'"
    )


@goodlooks.command("help")
@click.pass_context
def help_command(ctx: click.Context) -> None:
    """Show help and examples."""
    console.print(ctx.parent.get_help())
    console.print("\nExamples:")
    console.print("  goodlooks add \"Buy milk\"")
    console.print("  goodlooks list --all")
    console.print("  goodlooks done --id 2")
    console.print("  goodlooks edit --id 2 --new-title \"Buy oat milk\"")
    console.print("  goodlooks rm --id 2")


@goodlooks.command()
def version() -> None:
    """Show version."""
    console.print(f"{APP_TITLE} {VERSION}")


if __name__ == "__main__":
    goodlooks(prog_name=APP_NAME)
