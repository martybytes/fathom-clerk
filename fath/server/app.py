"""The local web server.

Security posture for an unauthenticated loopback port that can write files and
hold an API key:

  * Bind 127.0.0.1 explicitly. Never 0.0.0.0.
  * Send no CORS headers at all. The absence is load-bearing: it is what stops
    a page on another origin reading the token out of the served HTML.
  * Check the Host header against an allowlist, which is what defeats DNS
    rebinding -- an attacker's domain resolving to 127.0.0.1.
  * Require a token on every mutating route. The Host allowlist does not cover
    this: a cross-site form POST carries the *target* Host, so any page you
    visit could otherwise start a sync or overwrite your API key.

Token comparison uses secrets.compare_digest rather than ==, since there is no
reason not to.

Long work never happens on a handler thread. A sync runs on its own named daemon
thread and the browser follows it over SSE, so a ten-minute backfill does not
block the page it is being reported to.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from fath import __version__, config, db, keystore, naming, paths, schema, sync, writers
from fath.api import ApiError, AuthError, FathomClient

log = logging.getLogger(__name__)

TOKEN_PLACEHOLDER = "__FATH_TOKEN__"
TOKEN_HEADER = "X-Fath-Token"
MAX_BODY = 512 * 1024

WEB_DIST = paths.repo_root() / "web" / "dist"


def load_or_create_token() -> str:
    """The shared secret the page sends back on every mutating request."""
    path = paths.token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError as exc:
        # An unwritable token is survivable: it just means a new one per start,
        # which logs the browser out rather than refusing to serve.
        log.warning("could not persist the web token: %s", exc)
    return token


class SyncRunner:
    """One sync at a time, on its own thread, recorded in the database."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.run_id: int | None = None
        self._cancel = False

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def cancel(self) -> None:
        self._cancel = True

    def start(self, options: sync.SyncOptions, cfg: config.Config) -> int:
        with self.lock:
            if self.running:
                raise RuntimeError("a sync is already running")
            self._cancel = False
            database = db.shared()
            # Close anything a previous process left open, or the guard above
            # would refuse forever after a crash.
            database.mark_stale_runs()
            run_id = database.start_run(options.as_json())
            self.run_id = run_id

            def work() -> None:
                engine = sync.SyncEngine(cfg, database, lambda: self._cancel)
                status = "done"
                try:
                    for event in engine.run(options):
                        database.add_event(run_id, event.message, event.level, event.as_json())
                        if event.kind == sync.ERROR and event.level == "error":
                            status = "failed"
                        if event.kind == sync.FINISHED and "cancelled" in event.message:
                            status = "cancelled"
                except Exception as exc:
                    status = "failed"
                    database.add_event(run_id, f"{type(exc).__name__}: {exc}", "error")
                    log.exception("sync failed")
                finally:
                    database.finish_run(
                        run_id,
                        status,
                        meetings_seen=engine.counts.seen,
                        written=engine.counts.written,
                        skipped=engine.counts.skipped,
                        errors=engine.counts.errors,
                        requests_made=engine.counts.requests,
                    )

            self.thread = threading.Thread(target=work, name="fath-sync", daemon=True)
            self.thread.start()
            return run_id


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"fath/{__version__}"

    # Writes need the token. Reads over loopback do not: the page has to be able
    # to load before it has a token to send.
    # -- plumbing --------------------------------------------------------- #

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    @property
    def app(self) -> Server:
        return self.server.app  # type: ignore[attr-defined]

    def _send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        # Explicit, because protocol_version is HTTP/1.1: without it the browser
        # waits for a body that never ends.
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # See docs/decisions.md: protect the local dashboard from clickjacking.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, code: int, payload: Any) -> None:
        self._send_bytes(
            code,
            json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, code: int, message: str) -> None:
        self._send_json(code, {"error": message})

    def _host_ok(self) -> bool:
        """Reject a Host header we do not recognise.

        This is the anti-DNS-rebinding check: an attacker's domain can resolve
        to 127.0.0.1, and the browser would then treat their page as same-origin
        with this server. The Host header is what gives that away.
        """
        host = self.headers.get("Host")
        if not host:
            return True
        name = host.rsplit(":", 1)[0].strip("[]").lower()
        bound = str(_bound_host(self.server.server_address)).lower()
        return name in {"127.0.0.1", "localhost", "::1", bound}

    def _token_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), self.app.token)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # -- routing ---------------------------------------------------------- #

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(403, "unexpected Host header")
            return
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if route == "/healthz":
                self._send_json(200, {"ok": True, "version": __version__})
                return
            if route.startswith("/api/"):
                self._api_get(route, query)
                return
            self._serve_static(route)
        except BrokenPipeError:
            pass  # the tab closed mid-response; normal, not an error
        except Exception as exc:
            log.exception("GET %s failed", route)
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if not self._host_ok():
            self._error(403, "unexpected Host header")
            return
        route = unquote(urlparse(self.path).path)
        if not self._token_ok():
            self._error(401, f"this route needs {TOKEN_HEADER}")
            return
        try:
            self._api_post(route)
        except BrokenPipeError:
            pass
        except Exception as exc:
            log.exception("POST %s failed", route)
            self._error(500, f"{type(exc).__name__}: {exc}")

    # -- reads ------------------------------------------------------------ #

    def _api_get(self, route: str, query: dict[str, list[str]]) -> None:
        app = self.app
        cfg = app.cfg
        database = db.shared()

        if route == "/api/status":
            root, source = paths.resolution()
            out_root = cfg.output_root()
            self._send_json(
                200,
                {
                    "version": __version__,
                    "settingsRoot": str(root),
                    "settingsSource": source,
                    "outputRoot": str(out_root),
                    "outputExists": out_root.is_dir(),
                    "key": keystore.describe(),
                    "meetings": database.count_meetings(),
                    "searchIndex": "fts5" if database.fts_available else "like",
                    "lastRun": database.last_run(),
                    "activeRun": database.active_run(),
                    "running": app.runner.running,
                    "rateLimit": {
                        "requestsPerWindow": 10,
                        "windowSeconds": 60,
                        "note": (
                            "Measured from Fathom's own headers. One request returns "
                            "about 10 meetings with their transcripts."
                        ),
                    },
                },
            )
            return

        if route == "/api/meetings":
            limit = _int(query, "limit", 50)
            offset = _int(query, "offset", 0)
            rows, total = database.list_meetings(
                query=_str(query, "q"),
                limit=min(500, max(1, limit)),
                offset=max(0, offset),
                order=_str(query, "order") or "started_at",
                descending=_str(query, "dir", "desc") != "asc",
            )
            self._send_json(200, {"total": total, "items": [r.as_json() for r in rows]})
            return

        match = re.fullmatch(r"/api/meetings/(\d+)", route)
        if match:
            self._meeting_detail(int(match.group(1)))
            return

        if route == "/api/search":
            term = _str(query, "q")
            self._send_json(
                200,
                {"query": term, "hits": database.search(term, _int(query, "limit", 60))},
            )
            return

        if route == "/api/settings":
            self._send_json(200, {"groups": list(schema.GROUPS), "settings": schema.snapshot(cfg)})
            return

        if route == "/api/sync/stream":
            self._stream(_int(query, "run_id", 0), _int(query, "after", 0))
            return

        if route == "/api/doctor":
            # The same checks `fath doctor` runs. Surfacing them here is the
            # difference between the tool knowing something is wrong and the
            # person using it knowing: over-length folders were reported by
            # the CLI for days while the dashboard showed a clean overview.
            from fath.commands import doctor

            results = doctor.run_checks()
            self._send_json(
                200,
                {
                    "ok": not any(r.status == doctor.FAIL for r in results),
                    "checks": [r.as_json() for r in results],
                },
            )
            return

        if route == "/api/schedule":
            self._send_json(200, app.schedule_status())
            return

        self._error(404, "not found")

    def _meeting_detail(self, recording_id: int) -> None:
        row = db.shared().get_meeting(recording_id)
        if row is None:
            self._error(404, "no such meeting")
            return
        folder = self.app.cfg.output_root() / row.folder
        payload = row.as_json()
        payload["files"] = (
            sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
        )
        payload["folderPath"] = str(folder)
        for label, pattern in (
            ("transcript", "*transcript.md"),
            ("summary", "*summary.md"),
            ("actionItems", "*action-items.md"),
        ):
            found = next(folder.glob(pattern), None) if folder.is_dir() else None
            try:
                payload[label] = found.read_text(encoding="utf-8") if found else ""
            except OSError:
                payload[label] = ""
        self._send_json(200, payload)

    def _stream(self, run_id: int, after: int) -> None:
        """Server-sent events for a running sync.

        Hand-rolled because BaseHTTPRequestHandler has no notion of streaming.
        Two details are load-bearing: the connection is closed explicitly, since
        HTTP/1.1 without a Content-Length leaves the browser waiting forever; and
        every write can raise once the tab closes, which is a normal end of
        stream rather than an error worth logging.

        Snapshot-then-stream, replaying from the database, so a reconnecting tab
        loses nothing and closing the page mid-sync costs no progress.
        """
        database = db.shared()
        if not run_id:
            latest = database.last_run()
            run_id = int(latest["id"]) if latest else 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.close_connection = True
        self.end_headers()

        def emit(event: str, payload: dict[str, Any]) -> None:
            self.wfile.write(
                f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n".encode()
            )
            self.wfile.flush()

        last_id = after
        idle_since = time.monotonic()
        try:
            emit("meta", {"runId": run_id, "replaying": True})
            while not self.app.stopping.is_set():
                events = database.events_since(run_id, last_id, limit=200)
                for item in events:
                    last_id = int(item["id"])
                    emit("progress", item)
                    idle_since = time.monotonic()

                run = database.get_run(run_id)
                if run and run["status"] != "running" and not events:
                    emit("done", run)
                    return

                if time.monotonic() - idle_since > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    idle_since = time.monotonic()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # the tab went away

    # -- writes ----------------------------------------------------------- #

    def _api_post(self, route: str) -> None:
        app = self.app
        cfg = app.cfg
        body = self._read_json()

        if route == "/api/settings":
            if body is None:
                self._error(400, "expected a JSON object of settings")
                return
            errors = schema.validate_many(body)
            if errors:
                self._error(422, "; ".join(f"{k}: {v}" for k, v in errors.items()))
                return
            cfg.set_many(schema.coerce_many(body))
            self._send_json(200, {"saved": sorted(body), "settings": schema.snapshot(cfg)})
            return

        if route == "/api/key":
            if body is None or "key" not in body:
                self._error(400, 'expected {"key": "..."}')
                return
            value = str(body["key"]).strip()
            if value and not keystore.looks_like_api_key(value):
                self._error(422, "that does not look like an API key")
                return
            if not keystore.set_api_key(value):
                self._error(500, f"could not write {paths.env_file()}")
                return
            # Never echo it back, not even to the page that just sent it.
            self._send_json(200, {"key": keystore.describe()})
            return

        if route == "/api/naming/preview":
            self._send_json(200, app.naming_preview(body or {}))
            return

        if route == "/api/sync":
            options = sync.SyncOptions.from_config(
                cfg,
                dry_run=bool((body or {}).get("dryRun")),
                overwrite=bool((body or {}).get("overwrite")),
                limit=int((body or {}).get("limit") or 0),
            )
            if body and isinstance(body.get("files"), dict):
                options.selection = writers.FileSelection.from_overrides(body["files"], cfg)
            try:
                run_id = app.runner.start(options, cfg)
            except RuntimeError as exc:
                self._error(409, str(exc))
                return
            self._send_json(202, {"runId": run_id})
            return

        if route == "/api/sync/cancel":
            app.runner.cancel()
            self._send_json(200, {"cancelling": True})
            return

        if route == "/api/import":
            source = Path(str((body or {}).get("path") or cfg.output_root())).expanduser()
            if not source.is_dir():
                self._error(400, f"no such folder: {source}")
                return
            engine = sync.SyncEngine(cfg, db.shared())
            adopted = sum(1 for _ in engine.reimport(source))
            self._send_json(200, {"adopted": adopted, "path": str(source)})
            return

        if route == "/api/key/verify":
            value, key_source = keystore.resolve()
            if not value:
                self._error(400, "no API key set")
                return
            try:
                result = FathomClient.from_config(value, cfg).verify_key()
            except AuthError as exc:
                self._error(401, str(exc))
                return
            except ApiError as exc:
                self._error(502, str(exc))
                return
            self._send_json(200, {**result, "source": key_source})
            return

        self._error(404, "not found")

    # -- static ----------------------------------------------------------- #

    def _serve_static(self, route: str) -> None:
        """The built SPA, with the token spliced into index.html at serve time.

        The page is same-origin and no route sends CORS headers, so cross-origin
        script cannot read the token out of it. That, plus the Host allowlist, is
        what makes an unauthenticated loopback read surface acceptable.
        """
        # Path validation first, before the bundle-exists check. A security
        # guard that only applies once something else is true is a guard that
        # stops applying the day that changes.
        relative = route.lstrip("/") or "index.html"
        target = (WEB_DIST / relative).resolve()
        try:
            target.relative_to(WEB_DIST.resolve())
        except ValueError:
            self._error(403, "outside the web root")  # ../ traversal
            return

        if not WEB_DIST.is_dir():
            self._send_bytes(
                503,
                _NOT_BUILT.encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return

        # SPA fallback: any unknown path that is not an asset serves the shell.
        if not target.is_file():
            shell = WEB_DIST / "index.html"
            if not shell.is_file():
                self._error(404, "not found")
                return
            target = shell

        if target.name == "index.html":
            page = target.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, self.app.token)
            self._send_bytes(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        guessed, _ = mimetypes.guess_type(target.name)
        self._send_bytes(200, target.read_bytes(), guessed or "application/octet-stream")


_NOT_BUILT = """<!doctype html><html><head><meta charset="utf-8">
<title>fathom-helper</title>
<style>body{font:14px ui-monospace,Consolas,monospace;background:#1e1e2e;color:#cdd6f4;
padding:3rem;line-height:1.6}code{background:#313244;padding:.15rem .4rem;border-radius:3px}
a{color:#89b4fa}</style></head><body>
<h1>The web bundle has not been built</h1>
<p>The API is running on this port, but <code>web/dist</code> is missing.</p>
<pre>  cd web
  npm install
  npm run build</pre>
<p>Then reload. Meanwhile the API answers, for example
<a href="/healthz">/healthz</a> and <a href="/api/status">/api/status</a>.</p>
</body></html>"""


def _bound_host(address: Any) -> str:
    """The host this socket is bound to, whatever shape the address tuple is."""
    if isinstance(address, tuple) and address:
        return str(address[0])
    return "127.0.0.1"


def _int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [""])[0])
    except (TypeError, ValueError):
        return default


def _str(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    return values[0] if values else default


class Server:
    def __init__(self, cfg: config.Config | None = None, port: int | None = None) -> None:
        self.cfg = cfg or config.shared()
        self.port = port if port is not None else self.cfg.get_int("web.port", 8899)
        self.token = load_or_create_token()
        self.runner = SyncRunner()
        self.stopping = threading.Event()
        self.httpd: ThreadingHTTPServer | None = None

    def naming_preview(self, body: dict[str, Any]) -> dict[str, Any]:
        """Folder names under proposed settings, without saving them."""
        overrides = body.get("settings") if isinstance(body.get("settings"), dict) else body
        rules = naming.NamingRules.from_overrides(dict(overrides or {}), self.cfg)
        out_root = self.cfg.output_root()
        rows, _ = db.shared().list_meetings(limit=int(body.get("limit") or 15))

        items = []
        for row in rows:
            meeting = {
                "recording_id": row.recording_id,
                "title": row.title,
                "meeting_title": row.meeting_title,
                "recording_start_time": row.started_at,
                "recorded_by": {"name": row.recorded_by},
                "calendar_invitees": [{"name": n} for n in row.attendees],
            }
            try:
                proposed = naming.build_folder_name(meeting, rules, out_root)
                error = ""
            except ValueError as exc:
                proposed, error = "", str(exc)
            items.append(
                {
                    "recordingId": row.recording_id,
                    "current": row.folder,
                    "proposed": proposed,
                    "changed": bool(proposed) and proposed != row.folder,
                    "error": error,
                }
            )
        return {
            "items": items,
            "maxNameLength": naming.max_name_len(out_root, rules),
            "outputRoot": str(out_root),
        }

    def schedule_status(self) -> dict[str, Any]:
        from fath.commands import schedule as schedule_cmd

        if not paths.is_windows():
            return {"supported": False, "reason": "Scheduled Tasks are Windows-only"}
        return {
            "supported": True,
            "script": str(paths.repo_root() / "scripts" / schedule_cmd.SCRIPT),
        }

    def serve_forever(self, on_ready: Callable[[str], None] | None = None) -> None:
        handler: Any = Handler
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self  # type: ignore[attr-defined]
        bound_port = (
            self.httpd.server_address[1]
            if isinstance(self.httpd.server_address, tuple)
            else self.port
        )
        url = f"http://127.0.0.1:{bound_port}/"
        if on_ready:
            on_ready(url)
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            self.stopping.set()

    def shutdown(self) -> None:
        # Set before shutdown(): shutdown stops the accept loop but says nothing
        # to an SSE response already in flight.
        self.stopping.set()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()


def serve(
    cfg: config.Config | None = None,
    port: int | None = None,
    on_ready: Callable[[str], None] | None = None,
) -> Server:
    server = Server(cfg, port)
    server.serve_forever(on_ready)
    return server
