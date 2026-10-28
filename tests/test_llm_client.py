"""LLM client resilience: one retry layer, typed classification, typed failures."""

from typing import Any

import groq
import httpx
import pytest
from app.errors import LLMUnavailableError, StructuredOutputError, UpstreamRateLimitError
from app.models.review_comment import RawFindingList
from app.services.llm_client import LLMClient, is_transient_llm_error

REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _status(
    cls: type[groq.APIStatusError], code: int, headers: dict[str, str] | None = None
) -> Exception:
    return cls(
        "upstream said no", response=httpx.Response(code, request=REQ, headers=headers), body=None
    )


class Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class ScriptedModel:
    """Stands in for ChatGroq: replays a script of exceptions / results."""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls = 0

    def invoke(self, _messages: object) -> Any:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def with_structured_output(self, _schema: object) -> "ScriptedModel":
        return self


def _client(
    script: list[Any], sleeps: list[float] | None = None
) -> tuple[LLMClient, ScriptedModel]:
    model = ScriptedModel(script)
    sink = sleeps if sleeps is not None else []
    return LLMClient("m", chat_model=model, max_attempts=3, sleep=sink.append), model


def test_rate_limit_retries_honour_retry_after() -> None:
    sleeps: list[float] = []
    client, model = _client(
        [_status(groq.RateLimitError, 429, {"retry-after": "4"}), Msg("ok")], sleeps
    )
    assert client.generate(system_prompt="s", user_prompt="u") == "ok"
    assert model.calls == 2
    assert sleeps == [4.0]


def test_persistent_rate_limit_is_typed() -> None:
    client, model = _client([_status(groq.RateLimitError, 429)] * 3)
    with pytest.raises(UpstreamRateLimitError):
        client.generate(system_prompt="s", user_prompt="u")
    assert model.calls == 3


@pytest.mark.parametrize(
    "exc",
    [
        groq.APITimeoutError(request=REQ),
        groq.APIConnectionError(request=REQ),
        _status(groq.InternalServerError, 503),
    ],
)
def test_transient_errors_retry_then_become_llm_unavailable(exc: Exception) -> None:
    client, model = _client([exc] * 3)
    with pytest.raises(LLMUnavailableError):
        client.generate(system_prompt="s", user_prompt="u")
    assert model.calls == 3


def test_non_transient_errors_are_not_retried() -> None:
    client, model = _client([_status(groq.AuthenticationError, 401)])
    with pytest.raises(LLMUnavailableError):
        client.generate(system_prompt="s", user_prompt="u")
    assert model.calls == 1


def test_classification_is_by_type_not_substring() -> None:
    assert not is_transient_llm_error(ValueError("HTTP 500 rate limit timeout"))
    assert is_transient_llm_error(_status(groq.InternalServerError, 500))


def test_structured_output_returns_schema_instance() -> None:
    client, _ = _client([{"comments": [{"file": "a.py", "message": "m"}]}])
    result = client.generate_structured(system_prompt="s", user_prompt="u", schema=RawFindingList)
    assert isinstance(result, RawFindingList)
    assert result.comments[0].message == "m"


def test_structured_output_shape_failure_is_typed_and_not_retried() -> None:
    client, model = _client([ValueError("could not parse tool call")])
    with pytest.raises(StructuredOutputError):
        client.generate_structured(system_prompt="s", user_prompt="u", schema=RawFindingList)
    assert model.calls == 1


def test_real_client_is_built_with_one_retry_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import get_settings

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    get_settings.cache_clear()
    try:
        client = LLMClient(timeout=12.0)
        assert client.model.max_retries == 0
        assert client.model.request_timeout == 12.0
    finally:
        get_settings.cache_clear()
