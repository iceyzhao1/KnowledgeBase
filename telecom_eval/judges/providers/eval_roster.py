"""Judge provider backed by EVAL_ROSTER and EVAL_ANSWER_CHANNELS."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .base import ProviderResult, ProviderUsage


@dataclass
class EvalChannelConfig:
    base_url: str
    api_key: str
    type: str = "openai_compat"


@dataclass
class EvalRosterSelection:
    roster_id: str
    label: str
    channel: str
    model: str
    channel_config: EvalChannelConfig


@dataclass
class EvalRosterConfig:
    roster_json: str = ""
    channels_json: str = ""
    timeout: int = 120
    preferred_id: str = ""


HttpPost = Callable[[str], dict[str, Any]]


def _strip_shell_quotes(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _load_json(value: str, *, name: str) -> Any:
    text = _strip_shell_quotes(value)
    if not text:
        raise RuntimeError(f"{name} is empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} is not valid JSON: {exc}") from exc


def _channel_config(raw: Any) -> EvalChannelConfig:
    if not isinstance(raw, dict):
        raise RuntimeError("channel config must be an object")
    base_url = str(raw.get("base_url") or raw.get("url") or "").strip()
    api_key = str(raw.get("api_key") or raw.get("key") or "").strip()
    if not base_url:
        raise RuntimeError("channel base_url is required")
    if not api_key:
        raise RuntimeError("channel api_key is required")
    return EvalChannelConfig(base_url=base_url.rstrip("/"), api_key=api_key, type=str(raw.get("type") or "openai_compat"))


def parse_eval_roster(roster_json: str, channels_json: str, *, preferred_id: str = "") -> EvalRosterSelection:
    roster = _load_json(roster_json, name="EVAL_ROSTER")
    channels = _load_json(channels_json, name="EVAL_ANSWER_CHANNELS")
    if not isinstance(roster, list):
        raise RuntimeError("EVAL_ROSTER must be a JSON array")
    if not isinstance(channels, dict):
        raise RuntimeError("EVAL_ANSWER_CHANNELS must be a JSON object")

    candidates: list[dict[str, Any]] = []
    for item in roster:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        channel = str(item.get("channel") or "").strip()
        if channel == "claude_cli":
            continue
        channel_raw = channels.get(channel)
        if channel_raw is None:
            continue
        model = str(item.get("model") or "").strip()
        if not model:
            continue
        candidates.append(item)

    selected_items = candidates
    if preferred_id.strip():
        wanted = preferred_id.strip()
        selected_items = [
            item
            for item in candidates
            if str(item.get("id") or "").strip() == wanted or str(item.get("model") or "").strip() == wanted
        ]

    for item in selected_items:
        channel = str(item.get("channel") or "").strip()
        model = str(item.get("model") or "").strip()
        channel_raw = channels.get(channel)
        return EvalRosterSelection(
            roster_id=str(item.get("id") or model),
            label=str(item.get("label") or model),
            channel=channel,
            model=model,
            channel_config=_channel_config(channel_raw),
        )
    if preferred_id.strip():
        raise RuntimeError(f"EVAL_ROSTER has no enabled non-claude model matching {preferred_id!r}")
    raise RuntimeError("EVAL_ROSTER has no enabled non-claude model with a configured channel")


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _default_http_post(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM channel HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM channel connection failed: {exc}") from exc


def _usage(raw: Any) -> ProviderUsage:
    if not isinstance(raw, dict):
        return ProviderUsage()
    prompt = int(raw.get("prompt_tokens", 0) or 0)
    completion = int(raw.get("completion_tokens", 0) or 0)
    total = int(raw.get("total_tokens", prompt + completion) or 0)
    return ProviderUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


class EvalRosterProvider:
    name = "eval_roster"

    def __init__(
        self,
        config: EvalRosterConfig,
        *,
        http_post: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.selection = parse_eval_roster(config.roster_json, config.channels_json, preferred_id=config.preferred_id)
        self.model_name = self.selection.model
        self.roster_id = self.selection.roster_id
        self.channel = self.selection.channel
        self._http_post = http_post or _default_http_post

    def invoke(self, *, system: str, user: str) -> ProviderResult:
        channel_config = self.selection.channel_config
        if channel_config.type != "openai_compat":
            raise RuntimeError(f"unsupported EVAL_ANSWER_CHANNELS type: {channel_config.type}")
        payload = {
            "model": self.selection.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {channel_config.api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        raw = self._http_post(
            _chat_completions_url(channel_config.base_url),
            headers=headers,
            payload=payload,
            timeout=self.config.timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if not choices:
            raise RuntimeError(f"LLM response has no choices: {str(raw)[:500]}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = str((message or {}).get("content") or choices[0].get("text") or "")
        if not text:
            raise RuntimeError(f"LLM response has empty content: {str(raw)[:500]}")
        return ProviderResult(text=text, usage=_usage(raw.get("usage")), raw=raw, latency_ms=latency_ms)
