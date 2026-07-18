"""Live dashboard for boundary validation events.

Run this in one terminal, then run live_boundary_demo.py in another terminal.
The dashboard reads results/boundary_events.jsonl and refreshes every 2 seconds.
"""

from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_EVENTS = ROOT / "results" / "boundary_events.jsonl"
DEFAULT_RECOVERY_SUMMARY = ROOT / "results" / "recovery_demo_summary.json"


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def load_recovery_summary() -> dict:
    if not DEFAULT_RECOVERY_SUMMARY.exists():
        return {}
    try:
        return json.loads(DEFAULT_RECOVERY_SUMMARY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _short(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    if len(text) > 180:
        text = text[:177] + "..."
    return html.escape(text)


def render_page(path: Path) -> str:
    events = load_events(path)
    recovery_summary = load_recovery_summary()
    alerts = sum(1 for event in events if event.get("alert"))
    clean = len(events) - alerts
    rows = []
    for event in reversed(events[-100:]):
        status = event.get("status", "")
        alert = "YES" if event.get("alert") else "no"
        recovery = event.get("recovery") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(event.get('timestamp', '')))}</td>"
            f"<td><code>{html.escape(str(event.get('boundary', '')))}</code></td>"
            f"<td class='{status}'>{html.escape(status)}</td>"
            f"<td class='alert-{str(bool(event.get('alert'))).lower()}'>{alert}</td>"
            f"<td><code>{html.escape(str(recovery.get('action', 'n/a')))}</code></td>"
            f"<td>{_short(event.get('expected'))}</td>"
            f"<td>{_short(event.get('observed'))}</td>"
            f"<td>{_short(event.get('difference'))}</td>"
            f"<td>{_short(event.get('violations'))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='9'>No boundary events yet. Run live_boundary_demo.py.</td></tr>"
    recovery_rows = []
    for case in recovery_summary.get("cases", []):
        applied = "YES" if case.get("applied") else "no"
        recovery_rows.append(
            "<tr>"
            f"<td>{html.escape(str(case.get('case', '')))}</td>"
            f"<td><code>{html.escape(str(case.get('boundary', '')))}</code></td>"
            f"<td>{_short(case.get('flag_seen'))}</td>"
            f"<td><code>{html.escape(str(case.get('recovery_action', '')))}</code></td>"
            f"<td class='alert-{str(bool(case.get('applied'))).lower()}'>{applied}</td>"
            f"<td>{_short(case.get('outcome'))}</td>"
            "</tr>"
        )
    recovery_body = "\n".join(recovery_rows) or "<tr><td colspan='6'>No recovery summary yet. Run live_boundary_demo.py.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="2">
  <title>LLM-MAS Boundary Events</title>
  <style>
    body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #202124; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #5f6368; margin-bottom: 18px; }}
    .cards {{ display: flex; gap: 12px; margin-bottom: 18px; }}
    .card {{ border: 1px solid #dadce0; border-radius: 8px; padding: 12px 16px; min-width: 140px; }}
    .value {{ font-size: 28px; font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e8eaed; padding: 8px; vertical-align: top; }}
    th {{ text-align: left; background: #f8fafd; position: sticky; top: 0; }}
    code {{ background: #f1f3f4; padding: 2px 4px; border-radius: 4px; }}
    .signal_escape, .alert-true {{ color: #b3261e; font-weight: 700; }}
    .clean, .alert-false {{ color: #137333; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>LLM-MAS Boundary Events</h1>
  <div class="meta">Source: <code>{html.escape(str(path))}</code>. Auto-refreshes every 2 seconds.</div>
  <div class="cards">
    <div class="card"><div>Total events</div><div class="value">{len(events)}</div></div>
    <div class="card"><div>Alerts</div><div class="value">{alerts}</div></div>
    <div class="card"><div>Clean checks</div><div class="value">{clean}</div></div>
  </div>
  <table>
    <thead>
    <tr><th>Time</th><th>Boundary</th><th>Status</th><th>Alert</th><th>Recovery</th><th>Expected</th><th>Observed</th><th>Difference</th><th>Violations</th></tr>
    </thead>
    <tbody>{body}</tbody>
  </table>
    <h2>Execution Recovery Evidence</h2>
    <div class="meta">Shows whether the decision was applied in the agent/service trace.</div>
    <table>
        <thead>
            <tr><th>Case</th><th>Boundary</th><th>Flag Seen</th><th>Recovery</th><th>Applied</th><th>Outcome</th></tr>
        </thead>
        <tbody>{recovery_body}</tbody>
    </table>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    events_path = DEFAULT_EVENTS

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/events":
            payload = json.dumps(load_events(self.events_path), indent=2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
            return
        if route not in {"/", "/index.html"}:
            self.send_error(404)
            return
        payload = render_page(self.events_path)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve live LLM-MAS boundary events")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS), help="JSONL event file to read")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    DashboardHandler.events_path = Path(args.events)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Boundary dashboard: http://{args.host}:{args.port}")
    print(f"Reading events from: {DashboardHandler.events_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()