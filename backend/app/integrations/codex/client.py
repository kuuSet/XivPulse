"""Structured model generation through the local Codex CLI."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ValidationError

from ...schemas import ProviderTestResult
from ...services.provider import (
    GenerationResult,
    ProviderError,
    ProviderProtocolError,
    TokenUsage,
    _strict_json_schema,
)
from ...settings import ProcessSettings, ProviderSettings, get_process_settings


class CodexAdapter:
    """Run one isolated, non-interactive Codex process per generation request."""

    def __init__(
        self,
        settings: ProviderSettings,
        *,
        process: ProcessSettings | None = None,
    ) -> None:
        self.settings = settings
        self.process = process or get_process_settings()

    async def generate_json_with_metadata[TModel: BaseModel](
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],
        schema_name: str | None = None,
        max_output_tokens: int = 3000,
    ) -> GenerationResult[TModel]:
        del schema_name
        prompt = (
            f"{system_prompt}\n\n"
            f"Return only JSON matching the supplied output schema. Keep the response within "
            f"approximately {max_output_tokens} output tokens.\n\n"
            f"{user_prompt}"
        )
        with TemporaryDirectory(prefix="xivpulse-codex-") as temporary_dir:
            root = Path(temporary_dir)
            workspace = root / "workspace"
            schema_path = root / "schema.json"
            output_path = root / "output.json"
            workspace.mkdir()
            schema_path.write_text(
                json.dumps(_strict_json_schema(response_model), ensure_ascii=False),
                encoding="utf-8",
            )
            environment = self._environment(root)
            command = self._command(schema_path, output_path)
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=workspace,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise ProviderError("Codex CLI is not installed") from exc
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=self.settings.api_timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise ProviderError("Codex CLI timed out") from exc
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
                suffix = f": {detail[-1][:500]}" if detail else ""
                raise ProviderError(f"Codex CLI failed with exit code {process.returncode}{suffix}")
            try:
                data = response_model.model_validate_json(output_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, UnicodeError, ValidationError, ValueError) as exc:
                raise ProviderProtocolError("Codex returned invalid structured output") from exc
            return GenerationResult(
                data=data,
                transport="codex",
                provider="codex",
                model=self.settings.selected_model,
                usage=TokenUsage(),
            )

    def _environment(self, root: Path) -> dict[str, str]:
        environment = os.environ.copy()
        auth_file = self.process.codex_auth_file
        if auth_file is None:
            return environment
        if not auth_file.is_file():
            raise ProviderError("The configured Codex login file does not exist")
        codex_home = root / "codex-home"
        codex_home.mkdir()
        copied_auth = codex_home / "auth.json"
        shutil.copyfile(auth_file, copied_auth)
        copied_auth.chmod(0o600)
        environment["CODEX_HOME"] = str(codex_home)
        return environment

    def _command(self, schema_path: Path, output_path: Path) -> list[str]:
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--color",
            "never",
        ]
        command.extend(("--model", self.settings.codex_model))
        command.extend(
            ("--config", f'model_reasoning_effort="{self.settings.codex_reasoning_effort}"')
        )
        command.append("-")
        return command

    async def test_connection(self) -> ProviderTestResult:
        class Ping(BaseModel):
            ok: bool

        try:
            result = await self.generate_json_with_metadata(
                system_prompt="Return the requested JSON and nothing else.",
                user_prompt='Return {"ok": true}.',
                response_model=Ping,
                max_output_tokens=128,
            )
        except ProviderError as exc:
            return ProviderTestResult(
                ok=False,
                provider_name="codex",
                model=self.settings.selected_model,
                transport="codex",
                message=str(exc),
            )
        return ProviderTestResult(
            ok=result.data.ok,
            provider_name=result.provider,
            model=result.model,
            transport=result.transport,
            message="本机 Codex 调用成功。",
        )


__all__ = ["CodexAdapter"]
