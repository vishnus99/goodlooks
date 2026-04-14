from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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

# Live board (SSE): queues receive JSON task snapshots after each save_data.
_board_sse_queues: list[queue.Queue[str | None]] = []
_board_sse_lock = threading.Lock()
_board_http_server: HTTPServer | None = None
_board_http_thread: threading.Thread | None = None
_board_http_start_lock = threading.Lock()


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
    notify_board_clients_after_save(data.get("tasks", []))


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


def render_board_page_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GoodLooks Board</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0d1220; color: #ecf0ff; }
    .wrap { display: grid; grid-template-columns: 360px 1fr; min-height: 100vh; }
    .left { border-right: 1px solid #2a3657; padding: 20px; overflow: auto; }
    .right { padding: 24px; }
    h1 { margin: 0 0 8px 0; font-size: 24px; color: #c79bff; }
    .meta { color: #9ea8c3; margin-bottom: 16px; }
    .live { font-size: 12px; color: #6ee7b7; margin-bottom: 8px; }
    .task { width: 100%; text-align: left; border: 1px solid #2a3657; border-radius: 10px; background: #141d33; color: inherit; padding: 12px; margin-bottom: 10px; cursor: pointer; }
    .task:hover { border-color: #5a7bff; }
    .task.done { opacity: 0.6; }
    .task.selected { border-color: #c79bff; box-shadow: 0 0 0 1px #c79bff; }
    .id { color: #86a2ff; font-size: 12px; }
    .title { font-weight: 600; margin-top: 4px; }
    .urgency { font-size: 12px; color: #f4c66f; margin-top: 4px; }
    .panel { background: #141d33; border: 1px solid #2a3657; border-radius: 12px; padding: 16px; }
    .steps li { margin-bottom: 8px; }
    .pill { font-size: 12px; color: #9ea8c3; margin-left: 8px; }
    .muted { color: #9ea8c3; }
  </style>
</head>
<body>
  <div class="wrap">
    <aside class="left">
      <h1>GoodLooks Board</h1>
      <div class="live" id="live">Live · waiting for updates…</div>
      <div class="meta">Click a task for recommended steps. The list updates when you change tasks in the CLI.</div>
      <div id="tasks"></div>
    </aside>
    <main class="right">
      <div id="panel" class="panel">
        <h2 style="margin-top:0">Recommendation</h2>
        <div class="muted">Select a task from the left.</div>
      </div>
    </main>
  </div>
  <script>
    let tasks = [];
    let selectedId = null;

    function esc(s) {
      return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function recommend(task) {
      const title = (task.title || "").toLowerCase();
      if (task.done) {
        return {
          summary: "Task is already complete.",
          first_action: "Review and archive the result.",
          estimated_time_minutes: 3,
          steps: [
            "Confirm the outcome still matches your expectations.",
            "Capture any notes or follow-up tasks."
          ],
          risks_or_blockers: []
        };
      }

      let steps = [];
      if (title.includes("call") || title.includes("email") || title.includes("message") || title.includes("reply")) {
        steps = [
          "Draft a short message with the desired outcome.",
          "Send it and set a follow-up reminder.",
          "Log the response and next action."
        ];
      } else if (title.includes("buy") || title.includes("order") || title.includes("shop") || title.includes("purchase")) {
        steps = [
          "List exactly what is needed before purchasing.",
          "Check one quick price/availability source.",
          "Complete purchase and save confirmation details."
        ];
      } else if (title.includes("write") || title.includes("plan") || title.includes("doc") || title.includes("draft")) {
        steps = [
          "Define the deliverable in one sentence.",
          "Create a rough outline with 3-5 bullets.",
          "Draft first pass, then do a quick edit pass."
        ];
      } else {
        steps = [
          "Define the concrete success criteria for this task.",
          "Break it into a 15-minute first step.",
          "Execute the first step and decide the next move."
        ];
      }

      let estimate = 25;
      if (task.urgency === "high") {
        steps.unshift("Time-box to 25 minutes and start immediately.");
        estimate = 35;
      } else if (task.urgency === "low") {
        steps.unshift("Batch this with similar low-priority tasks.");
        estimate = 20;
      }

      return {
        summary: "Recommended approach for: " + task.title,
        first_action: steps[0],
        estimated_time_minutes: estimate,
        steps,
        risks_or_blockers: ["Unclear success criteria", "Waiting on other people"]
      };
    }

    function renderPanel(task) {
      const rec = recommend(task);
      const panel = document.getElementById("panel");
      const steps = rec.steps.map((s) => "<li>" + esc(s) + "</li>").join("");
      const blockers = (rec.risks_or_blockers || []).map((b) => "<li>" + esc(b) + "</li>").join("");
      panel.innerHTML =
        '<h2 style="margin-top:0">' + esc(task.title) + ' <span class="pill">#' + task.id + "</span></h2>" +
        '<p class="muted">' + esc(rec.summary) + "</p>" +
        '<p><strong>First action:</strong> ' + esc(rec.first_action) + "</p>" +
        '<p><strong>Estimated time:</strong> ' + rec.estimated_time_minutes + " min</p>" +
        '<h3>Steps</h3><ol class="steps">' + steps + "</ol>" +
        "<h3>Potential blockers</h3><ul>" + blockers + "</ul>";
    }

    function emptyPanel() {
      document.getElementById("panel").innerHTML =
        '<h2 style="margin-top:0">Recommendation</h2><div class="muted">Select a task from the left.</div>';
    }

    function renderTasks() {
      const host = document.getElementById("tasks");
      if (!tasks.length) {
        host.innerHTML = '<div class="muted">No tasks yet. Add one with goodlooks add "…".</div>';
        emptyPanel();
        selectedId = null;
        return;
      }
      host.innerHTML = "";
      tasks.forEach((task) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "task" + (task.done ? " done" : "") + (selectedId === task.id ? " selected" : "");
        btn.innerHTML =
          '<div class="id">#' + task.id + "</div>" +
          '<div class="title">' + (task.done ? "✓ " : "○ ") + esc(task.title) + "</div>" +
          '<div class="urgency">urgency: ' + esc(task.urgency || "normal") + "</div>";
        btn.addEventListener("click", () => {
          selectedId = task.id;
          renderTasks();
          renderPanel(task);
        });
        host.appendChild(btn);
      });
      if (selectedId != null) {
        const t = tasks.find((x) => x.id === selectedId);
        if (t) renderPanel(t);
        else { selectedId = null; emptyPanel(); }
      }
    }

    function setLive(msg) {
      const el = document.getElementById("live");
      if (el) el.textContent = msg;
    }

    const es = new EventSource("/events");
    es.onmessage = (e) => {
      try {
        tasks = JSON.parse(e.data);
      } catch (err) {
        setLive("Live · update parse error");
        return;
      }
      setLive("Live · list updated " + new Date().toLocaleTimeString());
      renderTasks();
    };
    es.onerror = () => {
      setLive("Live · reconnecting…");
    };
  </script>
</body>
</html>
"""


class _BoardHTTPServer(HTTPServer):
    allow_reuse_address = True


class BoardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path or "/"
        if path == "/":
            body = render_board_page_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/tasks":
            data = load_data()
            body = json.dumps(sort_tasks(data["tasks"])).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q: queue.Queue[str | None] = queue.Queue(maxsize=8)
            with _board_sse_lock:
                _board_sse_queues.append(q)
            data_path = ensure_data_file()
            last_payload = ""
            last_mtime_ns = 0
            try:
                initial = json.dumps(sort_tasks(load_data()["tasks"]))
                last_payload = initial
                try:
                    last_mtime_ns = data_path.stat().st_mtime_ns
                except OSError:
                    last_mtime_ns = 0
                q.put_nowait(initial)
            except Exception:
                pass
            try:
                while True:
                    try:
                        payload = q.get(timeout=2.0)
                    except queue.Empty:
                        # Cross-process fallback: if tasks.json changed, push latest.
                        try:
                            new_mtime_ns = data_path.stat().st_mtime_ns
                        except OSError:
                            new_mtime_ns = last_mtime_ns
                        if new_mtime_ns != last_mtime_ns:
                            last_mtime_ns = new_mtime_ns
                            latest = json.dumps(sort_tasks(load_data()["tasks"]))
                            if latest != last_payload:
                                last_payload = latest
                                line = ("data: " + latest + "\n\n").encode("utf-8")
                                self.wfile.write(line)
                                self.wfile.flush()
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if payload is None:
                        break
                    last_payload = payload
                    line = ("data: " + payload + "\n\n").encode("utf-8")
                    self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with _board_sse_lock:
                    try:
                        _board_sse_queues.remove(q)
                    except ValueError:
                        pass
            return
        self.send_error(404, "Not Found")


def default_board_port() -> int:
    raw = os.getenv("GOODLOOKS_BOARD_PORT", "9876")
    try:
        p = int(raw)
        return p if 1 <= p <= 65535 else 9876
    except ValueError:
        return 9876


def ensure_board_server_running() -> int:
    global _board_http_server, _board_http_thread
    with _board_http_start_lock:
        if (
            _board_http_thread is not None
            and _board_http_thread.is_alive()
            and _board_http_server is not None
        ):
            return int(_board_http_server.server_address[1])
        port_start = default_board_port()
        server: HTTPServer | None = None
        for port in range(port_start, min(port_start + 30, 65536)):
            try:
                server = _BoardHTTPServer(("127.0.0.1", port), BoardRequestHandler)
                break
            except OSError:
                continue
        if server is None:
            server = _BoardHTTPServer(("127.0.0.1", 0), BoardRequestHandler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="goodlooks-board-http",
            daemon=True,
        )
        thread.start()
        _board_http_server = server
        _board_http_thread = thread
        return int(server.server_address[1])


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


def notify_board_clients_after_save(tasks: list[dict[str, Any]]) -> None:
    """Push latest tasks to all connected board tabs (SSE)."""
    with _board_sse_lock:
        if not _board_sse_queues:
            return
        clients = list(_board_sse_queues)
    payload = json.dumps(sort_tasks(tasks))
    for q in clients:
        try:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put_nowait(payload)
        except Exception:
            pass


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


@goodlooks.command()
@click.option("-i", "--id", "task_id", type=int, required=True, help="Task ID to recommend.")
def recommend(task_id: int) -> None:
    """Show recommended steps to complete a task."""
    data = load_data()
    task = find_task_by_id(data["tasks"], task_id)
    if task is None:
        raise click.ClickException(f"Task #{task_id} not found. Run `goodlooks list`.")
    rec = recommendation_for_task(task)
    console.print(Panel(recommendation_to_text(task, rec), title="Recommended steps"))


@goodlooks.command()
def board() -> None:
    """Open interactive task board (local server + live SSE updates)."""
    port = ensure_board_server_running()
    url = f"http://127.0.0.1:{port}/"
    opened = webbrowser.open(url)
    if opened:
        console.print(f"[green]Opened board:[/green] {url}")
    else:
        console.print(f"[yellow]Could not auto-open browser. Open manually:[/yellow] {url}")
    console.print(
        "[dim]Server is running on 127.0.0.1 — leave this terminal open. "
        "Press Ctrl+C to stop.[/dim]"
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        console.print("\n[dim]Board server stopped.[/dim]")


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
    console.print("  goodlooks recommend --id 2")
    console.print("  goodlooks board")
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
            "recommend",
            "board",
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
