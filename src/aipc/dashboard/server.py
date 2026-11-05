"""Pure request routing, stdlib HTTP request handler, and server."""

import http.server
import json
import re
import threading
import urllib.parse
from typing import Any, Self

from aipc.dashboard.errors import DashboardError, ProjectNotFoundError
from aipc.dashboard.html import render_dashboard_html, render_projects_index_html
from aipc.dashboard.service import DashboardService
from aipc.domain.models import CanonicalRecord

_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


class DashboardResponse(CanonicalRecord):
    """Immutable HTTP response representation."""

    status_code: int = 200
    content_type: str = "application/json"
    body: str = ""


class DashboardApplication:
    """Read-only request router dispatching API and HTML requests."""

    def __init__(self, service: DashboardService):
        self.service = service

    def handle_request(self, method: str, raw_path: str) -> DashboardResponse:
        """Route request and return a DashboardResponse; fail closed on non-GET."""
        if method.upper() != "GET":
            return DashboardResponse(
                status_code=405,
                content_type="application/json",
                body=json.dumps(
                    {"error": "Method not allowed; dashboard is strictly read-only"},
                    indent=2,
                ),
            )

        parsed = urllib.parse.urlparse(raw_path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # 1. Health check
        if path == "/api/v1/health":
            return DashboardResponse(
                status_code=200,
                content_type="application/json",
                body=json.dumps({"status": "ok", "schema_version": "1.0"}, indent=2),
            )

        # 2. List projects API
        if path == "/api/v1/projects":
            projects = [p.model_dump(mode="json") for p in self.service.list_projects()]
            return DashboardResponse(
                status_code=200,
                content_type="application/json",
                body=json.dumps({"projects": projects}, indent=2),
            )

        # 3. HTML index
        if path == "/":
            return DashboardResponse(
                status_code=200,
                content_type="text/html; charset=utf-8",
                body=render_projects_index_html(self.service.list_projects()),
            )

        # 4. Project-scoped HTML
        if path.startswith("/projects/"):
            project_id = path[len("/projects/") :]
            if not _SAFE_ID_PATTERN.match(project_id):
                return DashboardResponse(
                    status_code=400,
                    content_type="text/html; charset=utf-8",
                    body="<h1>400 Bad Request</h1><p>Invalid project ID format.</p>",
                )
            try:
                snapshot = self.service.get_snapshot(project_id)
                return DashboardResponse(
                    status_code=200,
                    content_type="text/html; charset=utf-8",
                    body=render_dashboard_html(snapshot),
                )
            except ProjectNotFoundError as exc:
                return DashboardResponse(
                    status_code=404,
                    content_type="text/html; charset=utf-8",
                    body=f"<h1>404 Not Found</h1><p>{html_escape(str(exc))}</p>",
                )

        # 5. Project-scoped API endpoints
        if path.startswith("/api/v1/projects/"):
            rest = path[len("/api/v1/projects/") :]
            parts = rest.split("/")
            if len(parts) > 2:
                return DashboardResponse(
                    status_code=404,
                    content_type="application/json",
                    body=json.dumps({"error": f"Path '{path}' not found"}, indent=2),
                )
            project_id = parts[0]

            if not _SAFE_ID_PATTERN.match(project_id):
                return DashboardResponse(
                    status_code=400,
                    content_type="application/json",
                    body=json.dumps({"error": "Invalid project_id format"}, indent=2),
                )

            try:
                snapshot = self.service.get_snapshot(project_id)
            except ProjectNotFoundError as exc:
                return DashboardResponse(
                    status_code=404,
                    content_type="application/json",
                    body=json.dumps({"error": str(exc)}, indent=2),
                )
            except DashboardError as exc:
                return DashboardResponse(
                    status_code=500,
                    content_type="application/json",
                    body=json.dumps({"error": str(exc)}, indent=2),
                )

            # Sub-routes
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.model_dump(mode="json"), indent=2),
                )
            elif sub == "status":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.project_status.model_dump(mode="json"), indent=2),
                )
            elif sub == "phase":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.current_phase.model_dump(mode="json"), indent=2),
                )
            elif sub == "worker":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.worker.model_dump(mode="json"), indent=2),
                )
            elif sub == "reviewer":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.reviewer.model_dump(mode="json"), indent=2),
                )
            elif sub == "approvals":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.approvals.model_dump(mode="json"), indent=2),
                )
            elif sub == "tests":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.test_status.model_dump(mode="json"), indent=2),
                )
            elif sub == "timeline":
                timeline = [t.model_dump(mode="json") for t in snapshot.event_timeline]
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps({"timeline": timeline}, indent=2),
                )
            elif sub == "retries":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.retry_count.model_dump(mode="json"), indent=2),
                )
            elif sub == "cost":
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps(snapshot.cost.model_dump(mode="json"), indent=2),
                )
            elif sub == "phases":
                phases = [p.model_dump(mode="json") for p in snapshot.phase_history]
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps({"phases": phases}, indent=2),
                )
            elif sub == "logs":
                logs = [log_entry.model_dump(mode="json") for log_entry in snapshot.logs]
                return DashboardResponse(
                    status_code=200,
                    content_type="application/json",
                    body=json.dumps({"logs": logs}, indent=2),
                )

        # 6. Unknown path
        return DashboardResponse(
            status_code=404,
            content_type="application/json",
            body=json.dumps({"error": f"Path '{path}' not found"}, indent=2),
        )


def html_escape(s: str) -> str:
    """Helper to escape error message text."""
    import html

    return html.escape(s, quote=True)


def _make_handler(app: DashboardApplication):
    """Create a stdlib request handler dispatching all methods to the application."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _dispatch(self) -> None:
            resp = app.handle_request(self.command, self.path)
            self.send_response(resp.status_code)
            self.send_header("Content-Type", resp.content_type)
            body_bytes = resp.body.encode("utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_PUT(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

        def do_PATCH(self) -> None:
            self._dispatch()

        def log_message(self, format: str, *args: Any) -> None:
            # Suppress default noisy stderr logging during test runs
            pass

    return _Handler


class DashboardHttpServer:
    """Context-managed ThreadingHTTPServer serving DashboardApplication."""

    def __init__(self, app: DashboardApplication, host: str = "127.0.0.1", port: int = 0):
        self.app = app
        self.host = host
        self.port = port
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def server_port(self) -> int:
        """Port actually bound by the OS (especially when port 0 was passed)."""
        if self._server is None:
            raise RuntimeError("Server is not running")
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """Full base URL string for client requests."""
        return f"http://{self.host}:{self.server_port}"

    def start(self) -> None:
        """Start the HTTP server on a background daemon thread."""
        if self._server is not None:
            return
        handler_cls = _make_handler(self.app)
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shutdown and close the HTTP server."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
