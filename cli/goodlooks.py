from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import click
from cli.recommender_agent import (
    current_recommender_settings,
    diagnose_recommender,
    safe_generate_recommendation,
    save_recommender_config,
)
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


def recommendation_for_task(task: dict[str, Any]) -> dict[str, Any]:
    urgency = task.get("urgency", "normal")
    title = str(task.get("title", "")).strip()
    lowered = title.lower()
    if task.get("done"):
        return {
            "summary": "Task is already complete.",
            "steps": [
                "Confirm the outcome still matches your expectations.",
                "Capture any notes or follow-up tasks.",
            ],
            "estimated_time_minutes": 3,
            "first_action": "Review and archive the result.",
            "risks_or_blockers": [],
        }

    steps: list[str] = []
    if any(k in lowered for k in ("call", "email", "message", "reply")):
        steps.extend(
            [
                "Draft a short message with the desired outcome.",
                "Send it and set a follow-up reminder.",
                "Log the response and next action.",
            ]
        )
    elif any(k in lowered for k in ("buy", "order", "shop", "purchase")):
        steps.extend(
            [
                "List exactly what is needed before purchasing.",
                "Check one quick price/availability source.",
                "Complete purchase and save confirmation details.",
            ]
        )
    elif any(k in lowered for k in ("write", "plan", "doc", "draft")):
        steps.extend(
            [
                "Define the deliverable in one sentence.",
                "Create a rough outline with 3-5 bullets.",
                "Draft first pass, then do a quick edit pass.",
            ]
        )
    else:
        steps.extend(
            [
                "Define the concrete success criteria for this task.",
                "Break it into a 15-minute first step.",
                "Execute the first step and decide the next move.",
            ]
        )

    if urgency == "high":
        steps.insert(0, "Time-box to 25 minutes and start immediately.")
        estimate = 35
    elif urgency == "low":
        steps.insert(0, "Batch this with similar low-priority tasks.")
        estimate = 20
    else:
        estimate = 25

    return {
        "summary": f"Recommended approach for: {title}",
        "steps": steps,
        "estimated_time_minutes": estimate,
        "first_action": steps[0],
        "risks_or_blockers": [
            "Unclear success criteria",
            "Waiting on other people",
        ],
    }


def recommendation_to_text(task: dict[str, Any], rec: dict[str, Any]) -> Text:
    out = Text()
    out.append(f"Task #{task['id']}: ", style="bold bright_cyan")
    out.append(task["title"] + "\n", style="bold white")
    out.append(rec["summary"] + "\n", style="dim")
    out.append(f"First action: {rec['first_action']}\n", style="bold green")
    out.append(f"Estimated time: {rec['estimated_time_minutes']} min\n", style="yellow")
    out.append("Steps:\n", style="bold magenta")
    for idx, step in enumerate(rec["steps"], start=1):
        out.append(f"  {idx}. {step}\n")
    blockers = rec.get("risks_or_blockers") or []
    if blockers:
        out.append("Potential blockers:\n", style="bold red")
        for item in blockers:
            out.append(f"  - {item}\n", style="dim")
    return out


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
        ("status ", "cyan"),
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


def render_status_view(tasks: list[dict[str, Any]]) -> None:
    sorted_tasks = sort_tasks(tasks)
    pending_tasks = [t for t in sorted_tasks if not t["done"]]
    done_tasks = [t for t in sorted_tasks if t["done"]]

    by_urgency: dict[str, list[dict[str, Any]]] = {"high": [], "normal": [], "low": []}
    for task in pending_tasks:
        urgency = task.get("urgency", "normal")
        if urgency not in by_urgency:
            urgency = "normal"
        by_urgency[urgency].append(task)

    counts = Text.assemble(
        ("Status View  ", "bold magenta"),
        ("pending ", "dim"),
        (str(len(pending_tasks)), "bold yellow"),
        ("  │  ", "dim cyan"),
        ("done ", "dim"),
        (str(len(done_tasks)), "bold green"),
        ("  │  ", "dim cyan"),
        ("total ", "dim"),
        (str(len(sorted_tasks)), "bold white"),
    )
    console.print(
        Panel(
            counts,
            title="[bold bright_magenta]═══ GoodLooks ═══[/bold bright_magenta]",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )

    pending_table = Table(
        title="Pending By Urgency",
        show_header=True,
        header_style="bold bright_cyan",
        border_style="blue",
    )
    pending_table.add_column("Urgency", width=14)
    pending_table.add_column("Count", justify="right", width=7)
    pending_table.add_column("Visible items", overflow="fold")
    for urgency in ("high", "normal", "low"):
        group = by_urgency[urgency]
        item_preview = ", ".join(f"#{t['id']} {t['title']}" for t in group[:5])
        if len(group) > 5:
            item_preview += f", +{len(group) - 5} more"
        if not item_preview:
            item_preview = "None"
        pending_table.add_row(
            urgency_markup(urgency),
            str(len(group)),
            item_preview,
        )
    console.print(pending_table)

    done_table = Table(
        title="Recently Completed",
        show_header=True,
        header_style="bold bright_cyan",
        border_style="green",
    )
    done_table.add_column("ID", justify="right", width=4)
    done_table.add_column("Title", overflow="fold")
    done_table.add_column("Urgency", width=14)
    for task in done_tasks[:8]:
        done_table.add_row(str(task["id"]), task["title"], urgency_markup(task.get("urgency", "normal")))
    if not done_tasks:
        done_table.add_row("-", "No completed tasks yet.", "-")
    console.print(done_table)
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


@goodlooks.command("status")
def status_view() -> None:
    """Show grouped visibility view (pending + completed snapshot)."""
    data = load_data()
    render_status_view(data["tasks"])


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
    console.print(
        f"[green]Updated task #[/green][bold]{task_id}[/bold]: " + " · ".join(parts)
    )


@goodlooks.command()
@click.option("-i", "--id", "task_id", type=int, required=True, help="Task ID to recommend.")
def recommend(task_id: int) -> None:
    """Show recommended steps to complete a task."""
    data = load_data()
    task = find_task_by_id(data["tasks"], task_id)
    if task is None:
        raise click.ClickException(f"Task #{task_id} not found. Run `goodlooks list`.")
    rec, used_fallback, fallback_reason = safe_generate_recommendation(
        task,
        fallback_fn=recommendation_for_task,
    )
    if used_fallback:
        reason = f" ({fallback_reason})" if fallback_reason else ""
        console.print(f"[dim]Using local recommender fallback{reason}.[/dim]")
    console.print(Panel(recommendation_to_text(task, rec), title="Recommended steps"))


def render_doctor_report(report: dict[str, Any]) -> None:
    summary = (
        f"Backend: [bold]{report['backend']}[/bold]  "
        f"Provider: [bold]{report['provider']}[/bold]  "
        f"Source: [bold]{report.get('provider_source', 'configured')}[/bold]  "
        f"Model: [bold]{report['model']}[/bold]  "
        f"Timeout: [bold]{report['timeout']}s[/bold]"
    )
    status_title = "Doctor checks: OK" if report["ok"] else "Doctor checks: Needs attention"
    status_style = "green" if report["ok"] else "yellow"
    console.print(Panel(summary, title=status_title, border_style=status_style))

    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE_HEAVY)
    table.add_column("Check", style="white")
    table.add_column("Status", width=10)
    table.add_column("Details", style="dim")
    for item in report["checks"]:
        status = "[green]OK[/green]" if item["status"] == "ok" else "[red]FAIL[/red]"
        table.add_row(item["name"], status, item["detail"])
    console.print(table)
    config_path = report.get("config_path")
    if config_path:
        console.print(f"[dim]Config path:[/dim] {config_path}")


def install_python_package(package_name: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pip", "install", package_name],
            check=False,
        )
    except Exception as exc:
        console.print(f"[yellow]Could not run pip install {package_name}:[/yellow] {exc}")
        return False
    return result.returncode == 0


def ollama_start_service(wait_for_ready: bool = True) -> tuple[bool, str]:
    is_up, base_url, _ = ollama_status_details()
    if is_up:
        return True, f"Ollama already running at {base_url}."

    try:
        proc = subprocess.Popen(  # noqa: S603
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False, "`ollama` command not found. Install Ollama from https://ollama.com/download."
    except Exception as exc:
        return False, f"Failed to start Ollama: {exc}"

    if not wait_for_ready:
        return True, f"Started Ollama process (pid {proc.pid})."

    deadline = time.time() + 8.0
    while time.time() < deadline:
        time.sleep(0.35)
        up, _, _ = ollama_status_details()
        if up:
            return True, f"Started Ollama at {base_url}."
    return True, f"Started process (pid {proc.pid}) but Ollama not reachable yet at {base_url}."


def ollama_pull_model(model: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            ["ollama", "pull", model],
            check=False,
        )
    except FileNotFoundError:
        console.print("[yellow]`ollama` command not found while trying to pull model.[/yellow]")
        return False
    return result.returncode == 0


def apply_doctor_fixes(report: dict[str, Any]) -> list[dict[str, Any]]:
    provider = str(report.get("provider", ""))
    model = str(report.get("model", ""))
    actions: list[dict[str, Any]] = []

    failed_checks = {item["name"] for item in report.get("checks", []) if item["status"] != "ok"}

    if "python_package" in failed_checks:
        package = "langchain-ollama" if provider == "ollama" else "langchain-openai"
        if click.confirm(f"Install missing package `{package}` now?", default=True):
            ok = install_python_package(package)
            actions.append({"action": "install_package", "target": package, "ok": ok})
            if ok:
                console.print(f"[green]Installed {package}.[/green]")
            else:
                console.print(f"[yellow]Failed to install {package}.[/yellow]")
        else:
            actions.append({"action": "install_package", "target": package, "ok": False, "skipped": True})

    if provider == "ollama":
        if "ollama" in failed_checks:
            if click.confirm("Start Ollama service now?", default=True):
                ok, msg = ollama_start_service(wait_for_ready=True)
                console.print(f"[green]{msg}[/green]" if ok else f"[yellow]{msg}[/yellow]")
                actions.append({"action": "start_ollama", "target": "service", "ok": ok, "message": msg})
            else:
                actions.append({"action": "start_ollama", "target": "service", "ok": False, "skipped": True})
        report_after_start = diagnose_recommender()
        missing_model = any(
            c["name"] == "ollama" and "not found" in c["detail"]
            for c in report_after_start.get("checks", [])
        )
        if missing_model and model and click.confirm(f"Pull model `{model}` now?", default=True):
            ok = ollama_pull_model(model)
            actions.append({"action": "pull_model", "target": model, "ok": ok})
            if ok:
                console.print(f"[green]Pulled model {model}.[/green]")
            else:
                console.print(f"[yellow]Failed to pull model {model}.[/yellow]")
        elif missing_model and model:
            actions.append({"action": "pull_model", "target": model, "ok": False, "skipped": True})
    elif provider == "openai" and "openai_api_key" in failed_checks:
        console.print(
            "[yellow]Cannot auto-fix OpenAI key.[/yellow] Export [bold]OPENAI_API_KEY[/bold] in your shell."
        )
        actions.append(
            {
                "action": "set_openai_api_key",
                "target": "OPENAI_API_KEY",
                "ok": False,
                "manual": True,
            }
        )

    return actions


@goodlooks.command()
@click.option(
    "--fix",
    "apply_fixes",
    is_flag=True,
    help="Attempt to auto-fix common recommender setup issues.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Print machine-readable JSON output.",
)
def doctor(apply_fixes: bool, json_output: bool) -> None:
    """Show recommender health checks and setup hints."""
    report = diagnose_recommender()
    if not json_output:
        render_doctor_report(report)

    if not report["ok"]:
        provider = str(report["provider"])
        model = str(report["model"])
        if not json_output:
            if provider == "ollama":
                console.print(
                    f"[dim]Hint:[/dim] Start Ollama and run [bold]ollama pull {model}[/bold], "
                    "then retry [bold]goodlooks doctor[/bold]."
                )
            elif provider == "openai":
                console.print(
                    "[dim]Hint:[/dim] Export [bold]OPENAI_API_KEY[/bold] and retry "
                    "[bold]goodlooks doctor[/bold]."
                )
        if apply_fixes:
            if not json_output:
                console.print("\n[bold]Applying fixes...[/bold]")
            actions = apply_doctor_fixes(report)
            final_report = diagnose_recommender()
            if json_output:
                console.print_json(
                    data={
                        "initial_report": report,
                        "actions": actions,
                        "final_report": final_report,
                    }
                )
                return
            if actions:
                console.print("\n[bold]Doctor after fixes:[/bold]")
                render_doctor_report(final_report)
    elif apply_fixes:
        if json_output:
            console.print_json(data={"initial_report": report, "actions": [], "final_report": report})
            return
        console.print("[green]No fixes needed.[/green]")
    elif json_output:
        console.print_json(data=report)


def ollama_base_url() -> str:
    settings = current_recommender_settings()
    return str(settings.get("ollama_base_url", "http://127.0.0.1:11434"))


def ollama_status_details() -> tuple[bool, str, int]:
    base_url = ollama_base_url().rstrip("/")
    try:
        with urlopen(f"{base_url}/api/tags", timeout=2.0) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        model_count = len(models) if isinstance(models, list) else 0
        return True, base_url, model_count
    except (URLError, TimeoutError, ValueError, OSError):
        return False, base_url, 0


@goodlooks.group()
def ollama() -> None:
    """Manage local Ollama service for recommendations."""


@ollama.command("status")
def ollama_status() -> None:
    """Show Ollama server status."""
    is_up, base_url, model_count = ollama_status_details()
    if is_up:
        console.print(
            f"[green]Ollama is running[/green] at {base_url} with "
            f"[bold]{model_count}[/bold] model(s) available."
        )
    else:
        console.print(f"[yellow]Ollama is not reachable[/yellow] at {base_url}.")
        console.print("[dim]Run `goodlooks ollama start` to launch it.[/dim]")


@ollama.command("start")
def ollama_start() -> None:
    """Start Ollama server in the background if needed."""
    ok, msg = ollama_start_service(wait_for_ready=True)
    if ok and "not reachable yet" not in msg:
        console.print(f"[green]{msg}[/green]")
        return
    console.print(f"[yellow]{msg}[/yellow]")
    if ok:
        console.print("[dim]Run `goodlooks ollama status` again in a few seconds.[/dim]")
    else:
        raise click.ClickException(msg)


@ollama.command("stop")
def ollama_stop() -> None:
    """Stop Ollama server process if running."""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-f", "ollama serve"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise click.ClickException("`pgrep` is not available on this system.") from exc

    pids = [int(x.strip()) for x in result.stdout.splitlines() if x.strip().isdigit()]
    if not pids:
        up, base_url, _ = ollama_status_details()
        if up:
            console.print(
                f"[yellow]Ollama API is reachable at {base_url}, but no local `ollama serve` process was found to stop.[/yellow]"
            )
        else:
            console.print("[dim]Ollama is not running.[/dim]")
        return

    stopped = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except Exception:
            continue
    if stopped > 0:
        console.print(f"[green]Sent stop signal to {stopped} Ollama process(es).[/green]")
    else:
        console.print("[yellow]Could not stop Ollama processes.[/yellow]")


@goodlooks.command()
def setup() -> None:
    """Interactively create a user-specific recommender config."""
    current = current_recommender_settings()
    console.print("[bold bright_magenta]GoodLooks recommender setup[/bold bright_magenta]")
    console.print("[dim]This writes your user config file for recommender defaults.[/dim]")

    backend_default = str(current.get("backend", "langchain"))
    backend = click.prompt(
        "Backend",
        type=click.Choice(["langchain", "heuristic"], case_sensitive=False),
        default=backend_default,
        show_default=True,
    ).lower()

    provider_default = str(current.get("provider", "ollama"))
    provider_prompt_default = (
        provider_default if provider_default in {"auto", "ollama", "openai"} else "auto"
    )
    provider = click.prompt(
        "Provider",
        type=click.Choice(["auto", "ollama", "openai"], case_sensitive=False),
        default=provider_prompt_default,
        show_default=True,
    ).lower()

    model_default = str(current.get("model", "llama3.1"))
    model = click.prompt("Model name", default=model_default, show_default=True).strip()
    if not model:
        model = model_default

    timeout_default = float(current.get("timeout_sec", 8.0))
    timeout = click.prompt(
        "Timeout (seconds)",
        type=float,
        default=timeout_default,
        show_default=True,
    )
    timeout = max(1.0, min(30.0, timeout))

    ollama_url_default = str(current.get("ollama_base_url", "http://127.0.0.1:11434"))
    ollama_base_url = click.prompt(
        "Ollama base URL",
        default=ollama_url_default,
        show_default=True,
    ).strip()
    if not ollama_base_url:
        ollama_base_url = ollama_url_default

    config = {
        "backend": backend,
        "provider": provider,
        "model": model,
        "timeout_sec": timeout,
        "ollama_base_url": ollama_base_url,
    }
    path = save_recommender_config(config)
    console.print(f"[green]Saved recommender config:[/green] {path}")

    if backend == "langchain" and provider in {"auto", "ollama"}:
        if click.confirm("Start Ollama service now?", default=True):
            ok, msg = ollama_start_service(wait_for_ready=True)
            console.print(f"[green]{msg}[/green]" if ok else f"[yellow]{msg}[/yellow]")
        if model and click.confirm(f"Pull Ollama model `{model}` now?", default=False):
            ok = ollama_pull_model(model)
            if ok:
                console.print(f"[green]Pulled model {model}.[/green]")
            else:
                console.print(f"[yellow]Failed to pull model {model}.[/yellow]")

    if click.confirm("Run doctor with auto-fix now?", default=True):
        doctor.main(args=["--fix"], prog_name="goodlooks doctor", standalone_mode=False)
    else:
        console.print("[dim]Run `goodlooks doctor --fix` to validate and auto-fix setup.[/dim]")


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
    console.print("  goodlooks status")
    console.print("  goodlooks recommend --id 2")
    console.print("  goodlooks setup")
    console.print("  goodlooks doctor")
    console.print("  goodlooks doctor --fix")
    console.print("  goodlooks doctor --json")
    console.print("  goodlooks ollama status")
    console.print("  goodlooks ollama start")
    console.print("  goodlooks ollama stop")
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
            "recommend",
            "status",
            "setup",
            "ollama",
            "start",
            "status",
            "stop",
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
