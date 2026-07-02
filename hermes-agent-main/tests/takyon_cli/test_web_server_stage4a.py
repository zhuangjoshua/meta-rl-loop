"""Stage 4a subuser-plane process-model tests.

Two invariants land here:

1. The app-plane /generate and /search dispatch branches run their (blocking, up-to-180s
   safebox→provider) broker calls OFF the event loop via asyncio.to_thread, so one slow
   provider call cannot head-of-line block every other product customer on the process.
2. ``start_server`` opts into uvicorn ``workers=N`` (import-string target) ONLY when the
   resolved host role is ``subuser`` AND ``TAKYON_UVICORN_WORKERS`` asks for >1 worker;
   the operator/combined dashboard (module-global PTY/worker state) stays single-process.

Hermetic: no sockets, no Postgres — the broker fns and uvicorn.run are monkeypatched.
"""

import asyncio
import json
import threading

from starlette.requests import Request

from takyon_cli import web_server


def _make_post_request(path: str, body: bytes = b"{}") -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"authorization", b"Bearer app-session-token"),
            (b"host", b"demo.example"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 9119),
    }
    return Request(scope, receive)


class TestBrokerDispatchOffLoop:
    """/generate and /search must await their sync brokers through asyncio.to_thread."""

    def _run_dispatch(self, monkeypatch, *, route: str, broker_attr: str):
        seen: dict = {}

        def fake_broker(*, business, body, session_token):
            seen["thread"] = threading.current_thread()
            seen["business"] = business
            seen["session_token"] = session_token
            return 200, {"success": True, "route": route}

        monkeypatch.setattr(web_server, broker_attr, fake_broker)

        async def run():
            loop_thread = threading.current_thread()
            request = _make_post_request(f"/api/takyon/apps/demo/{route}")
            response = await web_server._takyon_app_post(request, "demo", route)
            return loop_thread, response

        loop_thread, response = asyncio.run(run())
        return seen, loop_thread, response

    def test_generate_runs_broker_off_event_loop(self, monkeypatch):
        seen, loop_thread, response = self._run_dispatch(
            monkeypatch, route="generate", broker_attr="_takyon_app_broker_generate"
        )
        assert seen["business"] == "demo"
        assert seen["session_token"] == "app-session-token"
        # The whole point of Stage 4a fix 1: the broker executed on a worker thread,
        # not on the thread driving the event loop.
        assert seen["thread"] is not loop_thread
        assert response.status_code == 200
        assert json.loads(response.body)["success"] is True

    def test_search_runs_broker_off_event_loop(self, monkeypatch):
        seen, loop_thread, response = self._run_dispatch(
            monkeypatch, route="search", broker_attr="_takyon_app_broker_search"
        )
        assert seen["business"] == "demo"
        assert seen["thread"] is not loop_thread
        assert response.status_code == 200
        assert json.loads(response.body)["route"] == "search"


class TestStartServerWorkersMode:
    """workers=N is subuser-role-only and env-gated; everything else stays single-process."""

    def _run_start_server(self, monkeypatch, *, role: str, workers_env: str | None):
        import uvicorn

        calls: list = []

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(uvicorn, "run", fake_run)
        # Keep the test hermetic: no Postgres seeding, no embedded worker thread, no env
        # setdefault side effects from the local-product-publish helper.
        monkeypatch.setattr(web_server, "_seed_platform_owner_if_postgres", lambda: None)
        monkeypatch.setattr(web_server, "_start_dashboard_worker_if_postgres", lambda: None)
        monkeypatch.setattr(web_server, "_configure_local_product_publish", lambda host, port: None)

        monkeypatch.setenv("TAKYON_HOST_ROLE", role)
        if workers_env is None:
            monkeypatch.delenv(web_server._UVICORN_WORKERS_ENV, raising=False)
        else:
            monkeypatch.setenv(web_server._UVICORN_WORKERS_ENV, workers_env)
        # Pre-touch the pass-through env keys so monkeypatch restores them after the
        # workers branch writes them.
        monkeypatch.setenv(web_server._WORKER_BOUND_HOST_ENV, "")
        monkeypatch.setenv(web_server._WORKER_BOUND_PORT_ENV, "")

        web_server.start_server(host="127.0.0.1", port=9119, open_browser=False)
        assert len(calls) == 1
        return calls[0]

    def test_subuser_with_env_runs_import_string_workers(self, monkeypatch):
        import os

        args, kwargs = self._run_start_server(monkeypatch, role="subuser", workers_env="2")
        assert args == ("takyon_cli.web_server:app",)
        assert kwargs["workers"] == 2
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9119
        assert kwargs["log_level"] == "warning"
        assert kwargs["proxy_headers"] is False
        # The bound interface must ride the environment into the worker processes so
        # host_header_middleware keeps validating Host headers there.
        assert os.environ[web_server._WORKER_BOUND_HOST_ENV] == "127.0.0.1"
        assert os.environ[web_server._WORKER_BOUND_PORT_ENV] == "9119"

    def test_subuser_without_env_stays_single_process(self, monkeypatch):
        args, kwargs = self._run_start_server(monkeypatch, role="subuser", workers_env=None)
        assert args == (web_server.app,)
        assert "workers" not in kwargs
        assert kwargs["proxy_headers"] is False

    def test_operator_with_env_stays_single_process(self, monkeypatch):
        args, kwargs = self._run_start_server(monkeypatch, role="operator", workers_env="2")
        assert args == (web_server.app,)
        assert "workers" not in kwargs

    def test_invalid_workers_env_stays_single_process(self, monkeypatch):
        monkeypatch.setenv("TAKYON_HOST_ROLE", "subuser")
        monkeypatch.setenv(web_server._UVICORN_WORKERS_ENV, "banana")
        assert web_server._subuser_uvicorn_workers("subuser") == 1
        monkeypatch.setenv(web_server._UVICORN_WORKERS_ENV, "1")
        assert web_server._subuser_uvicorn_workers("subuser") == 1
        monkeypatch.setenv(web_server._UVICORN_WORKERS_ENV, "3")
        assert web_server._subuser_uvicorn_workers("subuser") == 3
        assert web_server._subuser_uvicorn_workers("operator") == 1
        assert web_server._subuser_uvicorn_workers("combined") == 1
