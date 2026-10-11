"""Authenticated, read-only SIW transports for the local Windows OOJ workflow.

The session performs a real SIW probe before selecting mcscli. In ``auto`` mode a
failed mcscli probe, or a later transport-level mcscli failure, switches exactly
once to same-origin fetch inside the authenticated persistent Edge profile.

Only the fixed SIW host and the read-only endpoints in ``ALLOWED_PATHS`` are
reachable through this module. No local server is opened and no credentials are
logged or copied out of their owning authentication mechanism.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlparse

BASE_URL = "https://siw-in-dub.dub.proxy.amazon.com"
MCSCLI = Path(r"C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe")
from ooj_paths import EDGE_PROFILE
ALLOWED_PATHS = frozenset({"gpsn", "grbi", "gs", "gr"})


class TransportError(RuntimeError):
    """A sanitized SIW transport/authentication failure."""


@dataclass(frozen=True)
class SiwReply:
    payload: object
    elapsed_seconds: float


def _safe_message(value: object, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _validate_path(path: str) -> str:
    clean = path.strip().lstrip("/")
    if clean not in ALLOWED_PATHS:
        raise ValueError(f"SIW path is not allowed: {path!r}")
    return clean


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    expected = urlparse(BASE_URL)
    if (
        parsed.scheme != expected.scheme
        or parsed.hostname != expected.hostname
        or parsed.port != expected.port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("SIW base URL must match the approved HTTPS origin")
    return BASE_URL


def _is_siw_origin(url: str) -> bool:
    parsed = urlparse(url)
    expected = urlparse(BASE_URL)
    return parsed.scheme == expected.scheme and parsed.hostname == expected.hostname and parsed.port == expected.port


def _validate_probe_payload(payload: object) -> None:
    values = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        raise TransportError("SIW probe returned an unexpected polygon-set response")


def _create_closed_temp_file(prefix: str, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    path = Path(name)
    try:
        os.close(descriptor)
    except OSError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


class McsCliTransport:
    name = "mcscli"

    def __init__(self, executable: Path = MCSCLI, base_url: str = BASE_URL):
        self.executable = Path(executable)
        self.base_url = _validate_base_url(base_url)

    def post_form(self, path: str, fields: dict[str, object], timeout_s: int = 180) -> SiwReply:
        path = _validate_path(path)
        request_file: Path | None = None
        response_file: Path | None = None
        try:
            if not self.executable.exists():
                raise TransportError("mcscli executable is missing")
            try:
                response_file = _create_closed_temp_file("ooj-siw-response-", ".json")
                request_file = _create_closed_temp_file("ooj-siw-request-", ".form")
                request_file.write_text(
                    urlencode({key: "" if value is None else str(value) for key, value in fields.items()}),
                    encoding="utf-8",
                )
            except (OSError, UnicodeError) as exc:
                raise TransportError(f"mcscli {path} temporary I/O setup failed") from exc
            try:
                os.chmod(request_file, 0o600)
                os.chmod(response_file, 0o600)
            except OSError:
                pass
            command = [
                str(self.executable),
                "curl",
                "-s",
                "-o",
                str(response_file),
                "-w",
                "%{http_code}",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/x-www-form-urlencoded; charset=utf-8",
                "--data-binary",
                f"@{request_file}",
                f"{self.base_url}/{path}",
            ]
            started = time.monotonic()
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise TransportError(f"mcscli {path} timed out after {timeout_s}s") from exc
            except (OSError, UnicodeError) as exc:
                raise TransportError(f"mcscli {path} could not execute") from exc
            elapsed = time.monotonic() - started
            if completed.returncode != 0:
                raise TransportError(f"mcscli {path} exited {completed.returncode}")
            match = re.search(r"(\d{3})\s*$", completed.stdout or "")
            if not match:
                raise TransportError(f"mcscli {path} did not report an HTTP status")
            status = int(match.group(1))
            try:
                body = response_file.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError) as exc:
                raise TransportError(f"mcscli {path} response could not be read") from exc
            if status != 200:
                raise TransportError(f"mcscli {path} returned HTTP {status}")
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, TypeError) as exc:
                raise TransportError(f"mcscli {path} returned non-JSON data") from exc
            return SiwReply(payload=payload, elapsed_seconds=elapsed)
        except TransportError:
            raise
        except (OSError, UnicodeError) as exc:
            raise TransportError(f"mcscli {path} local I/O failure") from exc
        finally:
            for temporary in (request_file, response_file):
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def probe(self) -> None:
        _validate_probe_payload(self.post_form("gpsn", {}, timeout_s=90).payload)

    def close(self) -> None:
        return None


_EDGE_POST = """
async ({url, form, timeoutMs}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const body = new URLSearchParams(form || {}).toString();
        const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'},
            body,
            credentials: 'include',
            redirect: 'error',
            signal: controller.signal,
        });
        return {status: response.status, text: await response.text()};
    } finally {
        clearTimeout(timer);
    }
}
"""


class EdgeInPageTransport:
    """Thread-safe facade over Playwright objects owned by one worker thread."""

    name = "edge"

    def __init__(
        self,
        profile_dir: Path = EDGE_PROFILE,
        base_url: str = BASE_URL,
        log: Callable[[str], None] = print,
        startup_timeout_s: int = 420,
    ):
        self.profile_dir = Path(profile_dir)
        self.base_url = _validate_base_url(base_url)
        self.log = log
        self._requests: queue.Queue[tuple[Future, str, dict[str, object], int] | None] = queue.Queue()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._closed = False
        self._close_lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, name="ooj-siw-edge", daemon=True)
        self._thread.start()
        if not self._ready.wait(startup_timeout_s):
            self.close()
            raise TransportError(f"Edge SIW authentication did not complete within {startup_timeout_s}s")
        if self._startup_error is not None:
            startup_error = self._startup_error
            self.close()
            raise TransportError(f"Edge SIW startup failed: {_safe_message(startup_error)}") from startup_error

    def _worker(self) -> None:
        playwright = context = None
        try:
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="msedge",
                headless=False,
                viewport={"width": 1200, "height": 800},
                ignore_default_args=[
                    "--disable-extensions",
                    "--disable-component-extensions-with-background-pages",
                    "--disable-component-update",
                    "--disable-background-networking",
                ],
                timeout=90_000,
            )
            page = context.new_page()
            page.set_default_timeout(90_000)
            try:
                page.goto(f"{self.base_url}/query", timeout=90_000, wait_until="domcontentloaded")
            except Exception as exc:
                self.log(f"SIW Edge initial navigation interrupted; waiting for AEA/SSO ({type(exc).__name__})")
            self._wait_for_authentication(page)
            self._page = page
        except BaseException as exc:  # preserve startup details for the caller
            self._startup_error = exc
            self._ready.set()
        else:
            self._ready.set()
            while True:
                request = self._requests.get()
                if request is None:
                    break
                future, path, fields, timeout_s = request
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = self._post_on_page(path, fields, timeout_s)
                except BaseException as exc:
                    if not future.done():
                        future.set_exception(exc)
                else:
                    if not future.done():
                        future.set_result(result)
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass
            self._ready.set()

    def _wait_for_authentication(self, page) -> None:
        deadline = time.monotonic() + 360
        login_notice_shown = False
        while time.monotonic() < deadline:
            current_url = page.url
            if "midway" in current_url.lower():
                if not login_notice_shown:
                    self.log("SIW Edge requires Midway authentication in the opened browser window")
                    login_notice_shown = True
                time.sleep(2)
                continue
            if current_url == "about:blank":
                try:
                    page.goto(f"{self.base_url}/query", timeout=30_000, wait_until="domcontentloaded")
                except Exception:
                    pass
                time.sleep(2)
                continue
            if _is_siw_origin(current_url):
                try:
                    response = page.evaluate(
                        "async (url) => { const r = await fetch(url, {credentials: 'include', redirect: 'error'}); "
                        "return {status:r.status,text:await r.text()}; }",
                        f"{self.base_url}/sso/login",
                    )
                    if response.get("status") == 200:
                        payload = json.loads(response.get("text") or "{}")
                        if payload.get("is_authenticated") is True:
                            self.log("SIW Edge SSO authenticated")
                            return
                except Exception:
                    pass
            time.sleep(2)
        raise TransportError("Edge profile did not authenticate to SIW within 360s")

    def _post_on_page(self, path: str, fields: dict[str, object], timeout_s: int) -> SiwReply:
        path = _validate_path(path)
        started = time.monotonic()
        try:
            response = self._page.evaluate(
                _EDGE_POST,
                {
                    "url": f"{self.base_url}/{path}",
                    "form": {key: "" if value is None else str(value) for key, value in fields.items()},
                    "timeoutMs": timeout_s * 1000,
                },
            )
        except Exception as exc:
            raise TransportError(f"Edge SIW {path} request failed: {_safe_message(exc)}") from exc
        elapsed = time.monotonic() - started
        status = int(response.get("status") or 0)
        if status != 200:
            raise TransportError(f"Edge SIW {path} returned HTTP {status}")
        try:
            payload = json.loads(response.get("text") or "")
        except json.JSONDecodeError as exc:
            raise TransportError(f"Edge SIW {path} returned non-JSON data") from exc
        return SiwReply(payload=payload, elapsed_seconds=elapsed)

    def post_form(self, path: str, fields: dict[str, object], timeout_s: int = 180) -> SiwReply:
        future: Future = Future()
        with self._close_lock:
            if self._closed:
                raise TransportError("Edge SIW transport is closed")
            self._requests.put((future, path, fields, timeout_s))
        try:
            return future.result(timeout=timeout_s + 30)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TransportError(f"Edge SIW {path} timed out after {timeout_s}s") from exc

    def probe(self) -> None:
        _validate_probe_payload(self.post_form("gpsn", {}, timeout_s=90).payload)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._requests.put(None)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=30)
            if self._thread.is_alive():
                self.log("WARNING: SIW Edge worker did not stop within 30 seconds")


class FailoverSiwSession:
    """Select mcscli only after a real probe; otherwise switch once to Edge."""

    def __init__(self, mode: str = "auto", log: Callable[[str], None] = print):
        if mode not in {"auto", "mcscli", "edge"}:
            raise ValueError("SIW transport mode must be auto, mcscli, or edge")
        self.mode = mode
        self.log = log
        self._transport: McsCliTransport | EdgeInPageTransport | None = None
        self._lock = threading.RLock()
        self._fallback_count = 0
        self._fallback_reason = ""
        self._closed = False

    def _activate_edge(self, reason: str = "") -> EdgeInPageTransport:
        if self._fallback_count >= 1 and self._transport is None:
            raise TransportError("SIW Edge fallback was already attempted")
        if reason:
            self._fallback_count = 1
            self._fallback_reason = _safe_message(reason)
            self.log(f"SIW mcscli unavailable; switching once to authenticated Edge ({self._fallback_reason})")
        edge = EdgeInPageTransport(log=self.log)
        try:
            edge.probe()
        except BaseException:
            edge.close()
            raise
        self._transport = edge
        self.log("SIW transport selected: edge")
        return edge

    def ensure_ready(self):
        with self._lock:
            if self._closed:
                raise TransportError("SIW session is closed")
            if self._transport is not None:
                return self._transport
            if self.mode == "edge":
                return self._activate_edge()
            mcscli = McsCliTransport()
            try:
                mcscli.probe()
            except TransportError as exc:
                mcscli.close()
                if self.mode == "mcscli":
                    raise
                return self._activate_edge(str(exc))
            self._transport = mcscli
            self.log("SIW transport selected: mcscli")
            return mcscli

    def post_json(self, path: str, fields: dict[str, object], timeout_s: int = 180) -> SiwReply:
        transport = self.ensure_ready()
        try:
            return transport.post_form(path, fields, timeout_s)
        except TransportError as exc:
            if self.mode != "auto" or getattr(transport, "name", "") != "mcscli":
                raise
            with self._lock:
                if self._transport is transport:
                    transport.close()
                    self._transport = None
                    replacement = self._activate_edge(str(exc))
                else:
                    replacement = self._transport
            if replacement is None:
                raise TransportError("SIW fallback did not produce a transport") from exc
            return replacement.post_form(path, fields, timeout_s)

    @property
    def transport_name(self) -> str | None:
        return getattr(self._transport, "name", None)

    def summary(self) -> dict[str, object]:
        return {
            "requested_mode": self.mode,
            "selected_transport": self.transport_name,
            "fallback_count": self._fallback_count,
            "fallback_reason": self._fallback_reason,
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            transport = self._transport
            self._transport = None
        if transport is not None:
            transport.close()

    def __enter__(self) -> "FailoverSiwSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
