"""HTTP call to the Excel plugin responses endpoint."""

from __future__ import annotations

import json
import logging
import queue
import socket
import sys
import threading
import time
from typing import Iterator
from urllib import error, request

from bps_proxy.auth import ChatGPTSession
from bps_proxy.network import description as network_description, open_url
from bps_proxy.wire import UPSTREAM_URL

log = logging.getLogger("bps_proxy")

UPSTREAM_TIMEOUT = 300
UPSTREAM_IDLE_TIMEOUT = 300
UPSTREAM_TOTAL_TIMEOUT = 900
MAX_QUEUED_LINES = 8
KEEPALIVE_SECONDS = 15.0
MAX_EVENT_BYTES = 16 * 1024 * 1024
LARGE_EVENT_BYTES = 4 * 1024 * 1024
MAX_ERROR_BYTES = 4096
LOG_EVENT_TYPES = frozenset({
    'message', 'response.created', 'response.in_progress', 'response.completed',
    'response.failed', 'response.incomplete', 'response.output_item.added',
    'response.output_item.done', 'response.content_part.added',
    'response.content_part.done', 'response.output_text.delta',
    'response.output_text.done', 'response.function_call_arguments.delta',
    'response.function_call_arguments.done', 'response.reasoning_summary_text.delta',
    'response.reasoning_summary_text.done', 'response.compaction.delta',
    'response.compaction.done', 'error',
})


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _SSESizeError(UpstreamError):
    def __init__(self, kind: str, observed_bytes: int, limit_bytes: int) -> None:
        super().__init__(502, f'upstream SSE {kind} was too large')
        self.kind = kind
        self.observed_bytes = observed_bytes
        self.limit_bytes = limit_bytes


def validate_max_sse_event_bytes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < sys.maxsize:
        raise ValueError('max_sse_event_bytes must be a positive integer below the platform read limit')
    return value


def _log_event_type(event: str) -> str:
    # Upstream event names are untrusted and may contain private text.
    return event if event in LOG_EVENT_TYPES else 'other'


def _log_size_error(exc: _SSESizeError, event: str, request_id: str) -> None:
    log.warning('upstream SSE size limit exceeded request_id=%s event_type=%s '
                'limit_kind=%s observed_bytes=%s limit_bytes=%s',
                request_id, _log_event_type(event), exc.kind, exc.observed_bytes, exc.limit_bytes)


def _headers(session: ChatGPTSession) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {session.access_token}",
        "chatgpt-account-id": session.account_id,
        "x-openai-account-id": session.account_id,
        "content-type": "application/json",
        "accept": "text/event-stream",
        "accept-encoding": "identity",
        "origin": "https://bps.openai.com",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "x-basispoints-auth-mode": "chatgpt",
        "x-openai-internal-basispoints-client-agent-profile": "excel",
        "x-openai-internal-basispoints-client-editor": "excel",
        "x-openai-internal-basispoints-client-host": "office",
        "x-openai-internal-basispoints-client-platform": "excel",
        "x-openai-internal-basispoints-client-platform-class": "PC",
        "x-openai-internal-basispoints-client-product": "basispoints-excel-plugin",
        "x-openai-internal-basispoints-client-runtime": "desktop",
        "x-openai-internal-basispoints-office-host": "Excel",
        "x-openai-internal-basispoints-office-platform": "PC",
        "x-stainless-arch": "unknown",
        "x-stainless-lang": "js",
        "x-stainless-os": "Unknown",
        "x-stainless-package-version": "6.31.0",
        "x-stainless-retry-count": "0",
        "x-stainless-runtime": "browser:chrome",
    }
    if session.account_user_id:
        headers["x-openai-account-user-id"] = session.account_user_id
    return headers


def attachment_url() -> str:
    """Return the attachment endpoint used by the Basispoints host."""
    return "https://bps.openai.com/basispoints/api/attachments"


def upload_headers(session: ChatGPTSession) -> dict[str, str]:
    headers = _headers(session)
    headers.pop("content-type", None)
    headers["accept"] = "application/json"
    return headers


def _content_type(response) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    value = getter("Content-Type", "") if callable(getter) else ""
    return str(value or "").lower()


def _close(response) -> None:
    if response is None:
        return
    fp = getattr(response, 'fp', None)
    raw = getattr(fp, 'raw', None)
    sock = getattr(raw, '_sock', None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        response.close()
    except Exception:
        log.debug('failed to close upstream response')


def _http_error_message(exc: error.HTTPError) -> str:
    raw = b""
    try:
        raw = exc.read(MAX_ERROR_BYTES + 1)
    finally:
        _close(getattr(exc, "fp", None))
    if len(raw) > MAX_ERROR_BYTES:
        raw = raw[:MAX_ERROR_BYTES]
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        detail = parsed.get("error")
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"][:500]
        if isinstance(parsed.get("message"), str):
            return parsed["message"][:500]
    return f"upstream returned HTTP {exc.code} via {network_description()}"


def _decode_line(raw_line: bytes) -> str:
    try:
        return raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise UpstreamError(502, "upstream SSE contained invalid UTF-8") from exc


def _event_payload(event_name: str, data_lines: list[str]) -> tuple[str, dict] | None:
    if not data_lines:
        return None
    raw_data = "\n".join(data_lines)
    if raw_data == "[DONE]":
        return ("__done__", {})
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise UpstreamError(502, "upstream returned malformed SSE data") from exc
    if not isinstance(payload, dict):
        raise UpstreamError(502, "upstream SSE event was not a JSON object")
    event = event_name
    if event == "message" and isinstance(payload.get("type"), str):
        event = payload["type"]
    return event, payload


def _clear_body_timeout(response) -> None:
    """Leave the body read blocking. A short socket timeout marks the socket
    unreadable on Python 3.9 after the first timeout."""
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None) if raw is not None else None
    if sock is None:
        return
    try:
        sock.settimeout(None)
    except OSError:
        log.debug("could not clear upstream body timeout", exc_info=True)


def _read_upstream(response, lines, stopped, max_event_bytes: int) -> None:
    def send(value):
        while not stopped.is_set():
            try:
                lines.put(value, timeout=0.1)
                return
            except queue.Full:
                continue
    try:
        while not stopped.is_set():
            line = response.readline(max_event_bytes + 1)
            if len(line) > max_event_bytes:
                raise _SSESizeError('line', len(line), max_event_bytes)
            send(line)
            if not line:
                return
    except Exception as exc:
        send(exc)


def iter_events(session: ChatGPTSession, body: dict, *, max_event_bytes: int | None = None,
                request_id: str = '-') -> Iterator[tuple[str, dict]]:
    max_event_bytes = validate_max_sse_event_bytes(MAX_EVENT_BYTES if max_event_bytes is None else max_event_bytes)
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(UPSTREAM_URL, data=payload, headers=_headers(session), method="POST")
    response = None
    stopped = threading.Event()
    reader = None
    started_at = time.monotonic()
    try:
        try:
            response = open_url(req, timeout=UPSTREAM_TIMEOUT)
        except error.HTTPError as exc:
            raise UpstreamError(exc.code, _http_error_message(exc)) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise UpstreamError(504, "upstream request timed out") from exc
        except (error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            reason_type = type(reason).__name__ if reason is not None else type(exc).__name__
            log.warning(
                "upstream connection failed stage=open route=%s exception_type=%s reason_type=%s",
                network_description(),
                type(exc).__name__,
                reason_type,
            )
            raise UpstreamError(
                502,
                f"could not reach upstream via {network_description()} "
                f"(reason={reason_type})",
            ) from exc

        if "text/event-stream" not in _content_type(response):
            raise UpstreamError(502, "upstream did not return an event stream")

        pending_event = "message"
        data_lines: list[str] = []
        data_bytes = 0
        first_line = True
        response_id = None
        last_activity = time.monotonic()
        done = False
        lines: queue.Queue = queue.Queue(maxsize=MAX_QUEUED_LINES)
        _clear_body_timeout(response)
        reader = threading.Thread(
            target=_read_upstream,
            args=(response, lines, stopped, max_event_bytes),
            name="bps-upstream",
            daemon=True,
        )
        reader.start()

        def flush() -> Iterator[tuple[str, dict]]:
            nonlocal pending_event, data_lines, data_bytes, response_id, last_activity, done
            parsed = _event_payload(pending_event, data_lines)
            event_bytes = data_bytes
            pending_event = "message"
            data_lines = []
            data_bytes = 0
            if parsed is None:
                return
            event, event_payload = parsed
            if event == "__done__":
                done = True
                return
            if event_bytes >= LARGE_EVENT_BYTES:
                log.info('large upstream SSE event request_id=%s event_type=%s '
                         'event_bytes=%s limit_bytes=%s',
                         request_id, _log_event_type(event), event_bytes, max_event_bytes)
            last_activity = time.monotonic()
            if event == "response.created":
                created = event_payload.get("response")
                if isinstance(created, dict) and isinstance(created.get("id"), str):
                    response_id = created["id"]
            yield event, event_payload

        def keepalive() -> Iterator[tuple[str, dict]]:
            if response_id is None:
                return
            yield "response.in_progress", {
                "type": "response.in_progress",
                "response": {"id": response_id},
            }

        last_byte = time.monotonic()
        while not done:
            now = time.monotonic()
            if now - started_at >= UPSTREAM_TOTAL_TIMEOUT or now - last_byte >= UPSTREAM_IDLE_TIMEOUT:
                raise UpstreamError(504, 'upstream stream timed out')
            try:
                raw_line = lines.get(timeout=max(0.001, min(KEEPALIVE_SECONDS, UPSTREAM_TOTAL_TIMEOUT - (now - started_at), UPSTREAM_IDLE_TIMEOUT - (now - last_byte))))
            except queue.Empty:
                if time.monotonic() - last_activity >= KEEPALIVE_SECONDS:
                    yield from keepalive()
                continue
            if isinstance(raw_line, Exception):
                if isinstance(raw_line, _SSESizeError):
                    _log_size_error(raw_line, pending_event, request_id)
                    raise raw_line
                log.warning("upstream connection failed stage=read exception_type=%s", type(raw_line).__name__)
                if isinstance(raw_line, (TimeoutError, socket.timeout)):
                    raise UpstreamError(504, "upstream stream timed out") from raw_line
                if isinstance(raw_line, UpstreamError):
                    raise raw_line
                raise UpstreamError(502, "could not reach upstream") from raw_line
            now = time.monotonic()
            last_byte = now
            if not raw_line:
                if now - last_activity >= KEEPALIVE_SECONDS:
                    yield from keepalive()
                if data_lines:
                    yield from flush()
                break
            if first_line:
                first_line = False
                raw_line = raw_line.lstrip(b"\xef\xbb\xbf")
            line = _decode_line(raw_line)
            if line == "":
                yield from flush()
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                pending_event = line[6:].strip() or "message"
                continue
            if line.startswith("data:"):
                piece = line[5:].lstrip()
                data_bytes += len(piece.encode('utf-8')) + (1 if data_lines else 0)
                if data_bytes > max_event_bytes:
                    exc = _SSESizeError('event', data_bytes, max_event_bytes)
                    _log_size_error(exc, pending_event, request_id)
                    raise exc
                data_lines.append(piece)
                continue
            # SSE permits unknown fields; keep strictness for malformed event data by ignoring them.
        if data_lines and not done:
            yield from flush()
    finally:
        stopped.set()
        if response is not None:
            _close(response)
        if reader is not None:
            reader.join(timeout=0.2)
