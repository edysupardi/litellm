import uuid
from typing import Any, Optional


def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split OpenAI messages into system_prompt + history list in Kiro format."""
    system_parts: list[str] = []
    history: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if not pending_tool_results:
            return
        history.append({
            "userInputMessage": {
                "content": "",
                "modelId": "",
                "origin": "AI_EDITOR",
                "userInputMessageContext": {
                    "toolResults": list(pending_tool_results),
                },
            }
        })
        pending_tool_results.clear()

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "system":
            if isinstance(content, list):
                system_parts.append(" ".join(p.get("text", "") for p in content if p.get("type") == "text"))
            else:
                system_parts.append(str(content))

        elif role == "tool":
            pending_tool_results.append({
                "content": [{"text": str(content)}],
                "status": "success",
                "toolUseId": msg.get("tool_call_id", ""),
            })

        elif role == "user":
            flush_tool_results()
            text = _extract_text(content)
            entry: dict[str, Any] = {
                "userInputMessage": {
                    "content": text,
                    "modelId": "",
                    "origin": "AI_EDITOR",
                }
            }
            history.append(entry)

        elif role == "assistant":
            flush_tool_results()
            text = _extract_text(content)
            tool_uses = [
                {
                    "name": tc["function"]["name"],
                    "input": _safe_json(tc["function"].get("arguments", "{}")),
                    "toolUseId": tc.get("id", str(uuid.uuid4())),
                }
                for tc in (msg.get("tool_calls") or [])
            ]
            entry = {"assistantResponseMessage": {"content": text}}
            if tool_uses:
                entry["assistantResponseMessage"]["toolUses"] = tool_uses
            history.append(entry)

    flush_tool_results()
    system_prompt = "\n".join(system_parts)
    return system_prompt, history


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
    return ""


def _safe_json(s: str) -> Any:
    import json
    try:
        return json.loads(s)
    except Exception:
        return s


def build_payload(
    model: str,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    system_prompt, history = _convert_messages(messages)

    # Last message must be userInputMessage — pop it as currentMessage
    current: dict[str, Any] = {}
    while history:
        last = history[-1]
        if "userInputMessage" in last:
            current = history.pop()["userInputMessage"]
            break
        history.pop()

    if not current:
        current = {"content": "", "modelId": model, "origin": "AI_EDITOR"}

    current["modelId"] = model
    current["origin"] = "AI_EDITOR"

    if system_prompt:
        existing = current.get("content", "")
        current["content"] = f"<system>{system_prompt}</system>\n\n{existing}".strip()

    if tools:
        kiro_tools = []
        for t in tools:
            fn = t.get("function", t)
            kiro_tools.append({
                "toolSpecification": {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", "")[:10000],
                    "inputSchema": {"json": fn.get("parameters", fn.get("input_schema", {}))},
                }
            })
        ctx = current.setdefault("userInputMessageContext", {})
        ctx["tools"] = kiro_tools

    payload: dict[str, Any] = {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "conversationId": str(uuid.uuid4()),
            "currentMessage": {"userInputMessage": current},
        }
    }
    if history:
        payload["conversationState"]["history"] = history

    return payload
