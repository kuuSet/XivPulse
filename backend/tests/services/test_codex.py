"""Codex CLI adapter tests without consuming a real Codex session."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app.integrations.codex.client import CodexAdapter
from app.services.provider import ProviderError, create_model_adapter
from app.settings import ProcessSettings, ProviderSettings
from pydantic import BaseModel


class _Answer(BaseModel):
    ok: bool


class _Process:
    def __init__(self, output_path: Path, *, returncode: int = 0) -> None:
        self.output_path = output_path
        self.returncode = returncode
        self.input: bytes | None = None
        self.killed = False

    async def communicate(self, value: bytes | None = None) -> tuple[bytes, bytes]:
        self.input = value
        if self.returncode == 0:
            self.output_path.write_text('{"ok": true}', encoding="utf-8")
            return b"", b""
        return b"", b"login required"

    def kill(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_codex_adapter_runs_one_isolated_structured_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"token":"secret"}', encoding="utf-8")
    provider = ProviderSettings(model_source="codex", codex_model="gpt-test")
    process_settings = ProcessSettings(
        data_dir=tmp_path / "data",
        provider=provider,
        codex_auth_file=auth_file,
    )
    captured: dict[str, object] = {}

    async def fake_exec(*command: str, **kwargs: object) -> _Process:
        captured["command"] = command
        captured["kwargs"] = kwargs
        output_path = Path(command[command.index("--output-last-message") + 1])
        schema_path = Path(command[command.index("--output-schema") + 1])
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        copied_auth = Path(environment["CODEX_HOME"]) / "auth.json"
        assert copied_auth.read_text(encoding="utf-8") == '{"token":"secret"}'
        assert json.loads(schema_path.read_text(encoding="utf-8"))["type"] == "object"
        assert list(Path(str(kwargs["cwd"])).iterdir()) == []
        return _Process(output_path)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await CodexAdapter(provider, process=process_settings).generate_json_with_metadata(
        system_prompt="System rules",
        user_prompt="Paper data",
        response_model=_Answer,
        max_output_tokens=128,
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    assert command[:2] == ("codex", "exec")
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-test"
    assert result.data.ok is True
    assert result.provider == "codex"
    assert result.transport == "codex"
    assert result.usage.available is False


@pytest.mark.asyncio
async def test_codex_failure_is_returned_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    async def fake_exec(*command: str, **kwargs: object) -> _Process:
        nonlocal calls
        calls += 1
        output_path = Path(command[command.index("--output-last-message") + 1])
        return _Process(output_path, returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    provider = ProviderSettings(model_source="codex")
    process_settings = ProcessSettings(data_dir=tmp_path, provider=provider)

    with pytest.raises(ProviderError, match="login required"):
        await CodexAdapter(provider, process=process_settings).generate_json_with_metadata(
            system_prompt="System rules",
            user_prompt="Paper data",
            response_model=_Answer,
        )

    assert calls == 1


def test_model_adapter_factory_uses_selected_source(tmp_path: Path) -> None:
    provider = ProviderSettings(model_source="codex")
    process = ProcessSettings(data_dir=tmp_path, provider=provider)

    assert isinstance(create_model_adapter(provider, process=process), CodexAdapter)
