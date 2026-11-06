"""LLM adapter. AnthropicLLM uses the official anthropic sdk (2.x):
client.beta.messages.create with output_config json_schema, effort, server side
refusal fallbacks, and top level cache_control for the stable system prompt.
FakeLLM is for tests and demos and never touches the network."""
import json
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass
class LLMResult:
    data: dict | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    refused: bool = False
    refusal_category: str | None = None
    request_id: str | None = None


class LLM(Protocol):
    def json_call(self, *, role: str, system: str, user: str, schema: dict) -> LLMResult: ...
    def count_input_tokens(self, *, role: str, system: str, user: str) -> int | None: ...


class AnthropicLLM:
    def __init__(self, cfg: dict, client=None):
        import anthropic
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()

    def _role(self, role):
        return self.cfg[role]

    def json_call(self, *, role, system, user, schema) -> LLMResult:
        r = self._role(role)
        kwargs = dict(
            model=r["model"], max_tokens=r["max_tokens"],
            system=system, messages=[{"role": "user", "content": user}],
            output_config={"effort": r["effort"], "format": {"type": "json_schema", "schema": schema}},
            cache_control={"type": "ephemeral"},
        )
        if self.cfg.get("fallbacks"):
            kwargs.update(betas=[self.cfg["fallback_beta"]], fallbacks=self.cfg["fallbacks"])
        resp = self.client.beta.messages.create(**kwargs)
        u = resp.usage
        base = dict(model=resp.model, input_tokens=u.input_tokens + (u.cache_creation_input_tokens or 0),
                    output_tokens=u.output_tokens, cache_read_tokens=u.cache_read_input_tokens or 0,
                    request_id=getattr(resp, "_request_id", None))
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            return LLMResult(data=None, refused=True, refusal_category=cat, **base)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        return LLMResult(data=data, **base)

    def count_input_tokens(self, *, role, system, user) -> int | None:
        r = self._role(role)
        return self.client.messages.count_tokens(
            model=r["model"], system=system, messages=[{"role": "user", "content": user}]).input_tokens


class FakeLLM:
    """deterministic stand in. `writer` and `qa` are functions (user_json: dict) -> dict."""
    is_fake = True

    def __init__(self, writer: Callable[[dict], dict], qa: Callable[[dict], dict]):
        self.writer, self.qa = writer, qa
        self.calls = []

    def json_call(self, *, role, system, user, schema) -> LLMResult:
        payload = json.loads(user)
        self.calls.append(role)
        data = (self.writer if role == "writer" else self.qa)(payload)
        return LLMResult(data=data, model="fake", input_tokens=len(system + user) // 4, output_tokens=200)

    def count_input_tokens(self, *, role, system, user):
        return None
