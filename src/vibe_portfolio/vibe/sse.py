import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SseEvent:
    event_id: str | None
    event_type: str
    data: dict[str, Any]


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Parse SSE frames without binding the gateway to a browser library."""
    event_id: str | None = None
    event_type = "message"
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.removesuffix("\r")
        if line == "":
            if data_lines:
                yield SseEvent(event_id, event_type, _decode_data(data_lines))
            event_type = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "id" and "\x00" not in value:
            event_id = value
        elif field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:
        yield SseEvent(event_id, event_type, _decode_data(data_lines))


def _decode_data(data_lines: list[str]) -> dict[str, Any]:
    raw = "\n".join(data_lines)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return decoded if isinstance(decoded, dict) else {"value": decoded}
