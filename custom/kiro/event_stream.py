import json
import re
from typing import Any, Generator, Iterator


_CONTENT_RE = re.compile(rb'"content"\s*:\s*"((?:[^"\\]|\\.)*)"')
_TOOL_NAME_RE = re.compile(rb'"name"\s*:\s*"([^"]+)".*?"toolUseId"\s*:\s*"([^"]+)"')
_TOOL_INPUT_RE = re.compile(rb'"input"\s*:\s*"((?:[^"\\]|\\.)*)"')
_TOOL_STOP_RE = re.compile(rb'"stop"\s*:\s*true')
_USAGE_RE = re.compile(rb'"usage"\s*:\s*(\{[^}]+\})')


def _unescape(b: bytes) -> str:
    try:
        return json.loads(b'"' + b + b'"')
    except Exception:
        return b.decode("utf-8", errors="replace")


def parse_event_stream(raw: bytes) -> list[dict[str, Any]]:
    """Parse AWS binary event stream into a list of typed events."""
    events: list[dict[str, Any]] = []
    for chunk in _split_chunks(raw):
        event = _parse_chunk(chunk)
        if event:
            events.append(event)
    return events


def iter_event_stream(response_iter: Iterator[bytes]) -> Generator[dict[str, Any], None, None]:
    """Parse streaming AWS event stream chunks incrementally."""
    buf = b""
    for chunk in response_iter:
        buf += chunk
        # Try to find complete JSON objects
        i = 0
        while i < len(buf):
            if buf[i:i+1] == b"{":
                end = _find_json_end(buf, i)
                if end == -1:
                    break
                obj = buf[i:end]
                event = _parse_chunk(obj)
                if event:
                    yield event
                i = end
            else:
                i += 1
        buf = buf[i:]


def _split_chunks(raw: bytes) -> list[bytes]:
    chunks = []
    i = 0
    while i < len(raw):
        if raw[i:i+1] == b"{":
            end = _find_json_end(raw, i)
            if end == -1:
                break
            chunks.append(raw[i:end])
            i = end
        else:
            i += 1
    return chunks


def _find_json_end(data: bytes, start: int) -> int:
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(data)):
        c = data[i:i+1]
        if escape:
            escape = False
            continue
        if c == b"\\":
            escape = True
            continue
        if c == b'"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _parse_chunk(chunk: bytes) -> dict[str, Any] | None:
    if b'"content"' in chunk:
        m = _CONTENT_RE.search(chunk)
        if m:
            return {"type": "content", "text": _unescape(m.group(1))}

    if b'"name"' in chunk and b'"toolUseId"' in chunk:
        m = _TOOL_NAME_RE.search(chunk)
        if m:
            return {"type": "tool_start", "name": m.group(1).decode(), "tool_use_id": m.group(2).decode()}

    if b'"input"' in chunk and b'"name"' not in chunk:
        m = _TOOL_INPUT_RE.search(chunk)
        if m:
            return {"type": "tool_input", "input": _unescape(m.group(1))}

    if _TOOL_STOP_RE.search(chunk):
        return {"type": "tool_stop"}

    if b'"usage"' in chunk:
        m = _USAGE_RE.search(chunk)
        if m:
            try:
                return {"type": "usage", "data": json.loads(m.group(1))}
            except Exception:
                pass

    return None
