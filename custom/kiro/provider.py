import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Union

import httpx
import litellm
from litellm.llms.custom_llm import CustomLLM, CustomLLMError
from litellm.types.utils import ModelResponse, Usage, Choices, Message
from litellm.utils import CustomStreamWrapper

from .event_stream import iter_event_stream, parse_event_stream
from .headers import build_headers
from .payload import build_payload

_KIRO_REGION = "us-east-1"

_FALLBACK_MODELS: list[dict[str, Any]] = [
    {"modelId": "claude-opus-4.6",   "modelName": "Claude Opus 4.6"},
    {"modelId": "claude-sonnet-4.6", "modelName": "Claude Sonnet 4.6"},
    {"modelId": "claude-opus-4.5",   "modelName": "Claude Opus 4.5"},
    {"modelId": "claude-sonnet-4.5", "modelName": "Claude Sonnet 4.5"},
    {"modelId": "claude-haiku-4.5",  "modelName": "Claude Haiku 4.5"},
    {"modelId": "claude-sonnet-4",   "modelName": "Claude Sonnet 4"},
]


def _read_token() -> str:
    token_file = os.environ.get("KIRO_SSO_TOKEN_FILE", "")
    if token_file:
        try:
            return json.loads(Path(token_file).read_text()).get("access_token", "")
        except Exception:
            pass
    return os.environ.get("KIRO_ACCESS_TOKEN", "")


def _api_url(region: str) -> str:
    return f"https://q.{region}.amazonaws.com/generateAssistantResponse"


def _list_models_url(region: str) -> str:
    return f"https://q.{region}.amazonaws.com/ListAvailableModels"


def fetch_available_models(access_token: str, region: str = _KIRO_REGION) -> list[dict[str, Any]]:
    import sys
    headers = build_headers(access_token)
    try:
        resp = httpx.get(
            _list_models_url(region),
            headers=headers,
            params={"origin": "AI_EDITOR"},
            timeout=10.0,
        )
        resp.raise_for_status()
        models = resp.json().get("models", [])
        print(f"[Kiro] Fetched {len(models)} models from API", file=sys.stderr)
        return models
    except Exception as e:
        print(f"[Kiro] Failed to fetch model list: {e}, using fallback", file=sys.stderr)
        return _FALLBACK_MODELS


class KiroProvider(CustomLLM):
    def __init__(self) -> None:
        super().__init__()
        self.region = os.environ.get("KIRO_REGION", _KIRO_REGION)

    def completion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding: Any,
        api_key: str,
        logging_obj: Any,
        optional_params: dict,
        acompletion: Any = None,
        litellm_params: Any = None,
        logger_fn: Any = None,
        headers: dict = {},
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client: Optional[Any] = None,
    ) -> Union[ModelResponse, CustomStreamWrapper]:
        region = optional_params.get("aws_region_name", self.region)
        token = _read_token()
        kiro_model = model.removeprefix("kiro/")
        payload = build_payload(kiro_model, messages, optional_params.get("tools"))
        req_headers = build_headers(token, stream=False)

        timeout_val = timeout.connect if isinstance(timeout, httpx.Timeout) else (timeout or 120.0)
        with httpx.Client(timeout=float(timeout_val)) as c:
            resp = c.post(_api_url(region), json=payload, headers=req_headers)
            resp.raise_for_status()
            events = parse_event_stream(resp.content)

        return _events_to_response(model, events, model_response)

    def streaming(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding: Any,
        api_key: str,
        logging_obj: Any,
        optional_params: dict,
        acompletion: Any = None,
        litellm_params: Any = None,
        logger_fn: Any = None,
        headers: dict = {},
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client: Optional[Any] = None,
    ) -> Iterator[Any]:
        region = optional_params.get("aws_region_name", self.region)
        token = _read_token()
        kiro_model = model.removeprefix("kiro/")
        payload = build_payload(kiro_model, messages, optional_params.get("tools"))
        req_headers = build_headers(token, stream=True)

        timeout_val = timeout.connect if isinstance(timeout, httpx.Timeout) else (timeout or 120.0)
        tool_accum: dict[str, Any] = {}
        with httpx.Client(timeout=float(timeout_val)) as c:
            with c.stream("POST", _api_url(region), json=payload, headers=req_headers) as resp:
                resp.raise_for_status()
                for event in iter_event_stream(resp.iter_bytes()):
                    chunk = _event_to_generic_chunk(event, tool_accum)
                    if chunk:
                        yield chunk


def _events_to_response(
    model: str,
    events: list[dict[str, Any]],
    base: ModelResponse,
) -> ModelResponse:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    current_tool: Optional[dict[str, Any]] = None
    tool_input_buf = ""

    for ev in events:
        t = ev["type"]
        if t == "content":
            text_parts.append(ev["text"])
        elif t == "tool_start":
            current_tool = {"id": ev["tool_use_id"], "name": ev["name"]}
            tool_input_buf = ""
        elif t == "tool_input":
            tool_input_buf += ev["input"]
        elif t == "tool_stop" and current_tool:
            tool_calls.append({
                "id": current_tool["id"],
                "type": "function",
                "function": {"name": current_tool["name"], "arguments": tool_input_buf},
            })
            current_tool = None
            tool_input_buf = ""

    msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        msg["tool_calls"] = tool_calls

    base.id = f"chatcmpl-kiro-{uuid.uuid4().hex[:12]}"
    base.created = int(time.time())
    base.model = model
    base.choices = [
        Choices(index=0, message=Message(**msg), finish_reason="tool_calls" if tool_calls else "stop")
    ]
    base.usage = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    return base


def _event_to_generic_chunk(
    event: dict[str, Any],
    tool_accum: dict[str, Any],
) -> Optional[dict[str, Any]]:
    from litellm.types.utils import GenericStreamingChunk
    t = event["type"]

    if t == "content":
        return GenericStreamingChunk(
            text=event["text"],
            tool_use=None,
            is_finished=False,
            finish_reason="",
            usage=None,
            index=0,
        )
    if t == "tool_start":
        tool_accum.update({"id": event["tool_use_id"], "name": event["name"], "args": ""})
        return GenericStreamingChunk(
            text="",
            tool_use={"id": event["tool_use_id"], "type": "function",
                      "function": {"name": event["name"], "arguments": ""}},
            is_finished=False,
            finish_reason="",
            usage=None,
            index=0,
        )
    if t == "tool_input":
        tool_accum["args"] = tool_accum.get("args", "") + event["input"]
        return GenericStreamingChunk(
            text="",
            tool_use={"function": {"arguments": event["input"]}},
            is_finished=False,
            finish_reason="",
            usage=None,
            index=0,
        )
    if t == "tool_stop":
        return GenericStreamingChunk(
            text="",
            tool_use=None,
            is_finished=True,
            finish_reason="tool_calls",
            usage=None,
            index=0,
        )
    return None
