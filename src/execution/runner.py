import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from .models import ExecutionResult
from .safety import validate_ephemeral_script

RESULT_MARKER = "__RESULT_JSON__="
TIMEOUT_EXIT_CODE = 124
_ENV_ALLOWLIST = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TZ",
    "ZOEKT_API_URL",
}


class ExecutionRunner:
    def __init__(
        self,
        src_root: Path,
        manifest_path: Path,
        timeout_default: int,
        timeout_max: int,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
    ) -> None:
        self.src_root = src_root
        self.manifest_path = manifest_path
        self.timeout_default = timeout_default
        self.timeout_max = timeout_max
        self.stdout_max_bytes = stdout_max_bytes
        self.stderr_max_bytes = stderr_max_bytes
        self._workflow_index = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        with self.manifest_path.open("r", encoding="utf-8") as manifest_file:
            raw = yaml.safe_load(manifest_file) or {}

        workflow_index: dict[str, dict[str, Any]] = {}
        for workflow in raw.get("workflows", []):
            workflow_id = workflow.get("id")
            if workflow_id:
                workflow_index[workflow_id] = workflow
        return workflow_index

    async def run_workflow_script(
        self,
        workflow_id: str,
        args: dict[str, Any],
        timeout_seconds: int,
    ) -> ExecutionResult:
        workflow = self._workflow_index.get(workflow_id)
        if workflow is None:
            return self._error_result(message=f"unknown workflow_id: {workflow_id}", exit_code=2)

        arg_validation_error = self._validate_required_args(workflow, args)
        if arg_validation_error is not None:
            return self._error_result(message=arg_validation_error, exit_code=2)

        script_rel_path = workflow.get("script_path")
        if not isinstance(script_rel_path, str) or not script_rel_path:
            return self._error_result(message=f"workflow script_path missing: {workflow_id}", exit_code=2)

        script_path = self.src_root / script_rel_path
        if not script_path.exists():
            return self._error_result(message=f"workflow script missing: {script_path}", exit_code=2)

        with tempfile.TemporaryDirectory(prefix=f"zoekt-workflow-{workflow_id}-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            temp_script_path = temp_dir / "workflow_script.py"
            runtime_src = self.src_root / "runtime"
            runtime_dst = temp_dir / "runtime"

            shutil.copy2(script_path, temp_script_path)
            shutil.copytree(runtime_src, runtime_dst, dirs_exist_ok=True)

            command = self._build_isolated_command(temp_script_path, args)

            try:
                return await self._execute(command=command, cwd=temp_dir, timeout_seconds=timeout_seconds)
            finally:
                if temp_script_path.exists():
                    temp_script_path.unlink()

    async def run_ephemeral_script(
        self,
        code: str,
        args: dict[str, Any],
        timeout_seconds: int,
    ) -> ExecutionResult:
        rejections = validate_ephemeral_script(code)
        if rejections:
            return ExecutionResult(
                success=False,
                exit_code=1,
                stderr="ephemeral script rejected by safety policy",
                safety_rejections=rejections,
            )

        with tempfile.TemporaryDirectory(prefix="zoekt-ephemeral-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            script_path = temp_dir / "ephemeral_script.py"
            runtime_src = self.src_root / "runtime"
            runtime_dst = temp_dir / "runtime"

            script_path.write_text(code, encoding="utf-8")
            shutil.copytree(runtime_src, runtime_dst, dirs_exist_ok=True)

            command = self._build_isolated_command(script_path, args)

            try:
                return await self._execute(command=command, cwd=temp_dir, timeout_seconds=timeout_seconds)
            finally:
                if script_path.exists():
                    script_path.unlink()

    async def _execute(
        self,
        command: list[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        normalized_timeout = self._normalize_timeout(timeout_seconds)
        start = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                env=self._build_environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            return self._error_result(
                message=f"runner failed to start subprocess: {exc}",
                exit_code=70,
                timing_ms=self._elapsed_ms(start),
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=normalized_timeout)
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = self._decode_and_cap(stdout_bytes, self.stdout_max_bytes, "stdout")
            stderr = self._decode_and_cap(stderr_bytes, self.stderr_max_bytes, "stderr")
            return ExecutionResult(
                success=False,
                exit_code=TIMEOUT_EXIT_CODE,
                stdout=stdout,
                stderr=(stderr + "\nexecution timed out" if stderr else "execution timed out"),
                timing_ms=self._elapsed_ms(start),
            )

        full_stdout = self._decode_lossy(stdout_bytes)
        full_stderr = self._decode_lossy(stderr_bytes)
        cleaned_stdout_full, result_json, parse_error, marker_found = self._extract_result_json(full_stdout)
        stdout = self._cap_text(cleaned_stdout_full, self.stdout_max_bytes, "stdout")
        stderr = self._cap_text(full_stderr, self.stderr_max_bytes, "stderr")

        if not marker_found:
            marker_error = "result marker not found"
            stderr = f"{stderr}\n{marker_error}" if stderr else marker_error
        if parse_error:
            stderr = f"{stderr}\n{parse_error}" if stderr else parse_error

        exit_code = int(process.returncode or 0)
        return ExecutionResult(
            success=exit_code == 0,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            result_json=result_json,
            timing_ms=self._elapsed_ms(start),
        )

    def _normalize_timeout(self, timeout_seconds: int) -> int:
        if timeout_seconds <= 0:
            return self.timeout_default
        return min(timeout_seconds, self.timeout_max)

    def _validate_required_args(self, workflow: dict[str, Any], args: dict[str, Any]) -> str | None:
        arg_schema = workflow.get("arg_schema", {})
        missing = [
            arg_name
            for arg_name, schema in arg_schema.items()
            if isinstance(schema, dict) and schema.get("required") and arg_name not in args
        ]
        if missing:
            missing_csv = ", ".join(sorted(missing))
            return f"args validation failure: missing required args: {missing_csv}"
        return None

    def _build_environment(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in _ENV_ALLOWLIST:
            value = os.environ.get(key)
            if value:
                env[key] = value
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    @staticmethod
    def _build_isolated_command(script_path: Path, args: dict[str, Any]) -> list[str]:
        args_json = json.dumps(args)
        script = str(script_path)
        script_parent = str(script_path.parent)
        bootstrap = (
            "import runpy,sys;"
            f"script={script!r};"
            f"sys.path.insert(0,{script_parent!r});"
            f"sys.argv=[script,'--args-json',{args_json!r}];"
            "runpy.run_path(script, run_name='__main__')"
        )
        return [sys.executable, "-I", "-u", "-c", bootstrap]

    @staticmethod
    def _decode_and_cap(raw: bytes, max_bytes: int, stream_name: str) -> str:
        if len(raw) <= max_bytes:
            return raw.decode("utf-8", errors="replace")

        capped = raw[:max_bytes].decode("utf-8", errors="replace")
        return f"{capped}\n[{stream_name} truncated at {max_bytes} bytes]"

    @staticmethod
    def _decode_lossy(raw: bytes) -> str:
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _cap_text(value: str, max_bytes: int, stream_name: str) -> str:
        raw = value.encode("utf-8", errors="replace")
        if len(raw) <= max_bytes:
            return value
        capped = raw[:max_bytes].decode("utf-8", errors="replace")
        return f"{capped}\n[{stream_name} truncated at {max_bytes} bytes]"

    @staticmethod
    def _extract_result_json(stdout: str) -> tuple[str, Any, str | None, bool]:
        lines = stdout.splitlines()
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            if not line.startswith(RESULT_MARKER):
                continue

            payload = line[len(RESULT_MARKER) :]
            cleaned_lines = lines[:index] + lines[index + 1 :]
            cleaned_stdout = "\n".join(cleaned_lines)

            try:
                return cleaned_stdout, json.loads(payload), None, True
            except json.JSONDecodeError as exc:
                return cleaned_stdout, None, f"malformed result marker JSON: {exc.msg}", True

        return stdout, None, None, False

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)

    @staticmethod
    def _error_result(message: str, exit_code: int, timing_ms: int = 0) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            exit_code=exit_code,
            stderr=message,
            timing_ms=timing_ms,
        )
