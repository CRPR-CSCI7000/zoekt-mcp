import asyncio
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from .models import ExecutionResult
from .safety import validate_custom_workflow_code

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

    def parse_workflow_cli_command(self, command: str) -> tuple[str, dict[str, Any]]:
        command = command.strip()
        if not command:
            raise ValueError("args validation failure: command must not be empty")

        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError(f"args validation failure: invalid command: {exc}") from exc

        if not tokens:
            raise ValueError("args validation failure: command must not be empty")

        workflow_id = tokens[0]
        workflow = self._workflow_index.get(workflow_id)
        if workflow is None:
            available = ", ".join(sorted(self._workflow_index))
            raise ValueError(
                f"args validation failure: unknown workflow_id: {workflow_id}. Available workflows: {available}"
            )

        arg_schema = workflow.get("arg_schema")
        if not isinstance(arg_schema, dict):
            arg_schema = {}

        usage = self._workflow_usage(workflow_id, arg_schema)
        flag_aliases = self._workflow_flag_aliases(arg_schema)
        parsed_args: dict[str, Any] = {}

        index = 1
        while index < len(tokens):
            token = tokens[index]
            if not token.startswith("--"):
                raise ValueError(f"args validation failure: unexpected positional argument `{token}`. {usage}")

            arg_name = flag_aliases.get(token)
            if arg_name is None:
                raise ValueError(f"args validation failure: unknown flag `{token}`. {usage}")
            if arg_name in parsed_args:
                raise ValueError(f"args validation failure: duplicate flag `{token}`. {usage}")
            if index + 1 >= len(tokens):
                raise ValueError(f"args validation failure: missing value for `{token}`. {usage}")

            value_token = tokens[index + 1]
            if value_token.startswith("--"):
                raise ValueError(f"args validation failure: missing value for `{token}`. {usage}")

            schema = arg_schema.get(arg_name)
            if not isinstance(schema, dict):
                schema = {"type": "string"}
            parsed_args[arg_name] = self._coerce_cli_arg_value(arg_name, value_token, schema, usage)
            index += 2

        for arg_name, schema in arg_schema.items():
            if arg_name in parsed_args:
                continue
            if not isinstance(schema, dict) or "default" not in schema:
                continue
            parsed_args[arg_name] = self._coerce_cli_arg_value(arg_name, schema["default"], schema, usage)

        missing = [
            arg_name
            for arg_name, schema in arg_schema.items()
            if isinstance(schema, dict) and schema.get("required") and arg_name not in parsed_args
        ]
        if missing:
            missing_flags = ", ".join(f"--{arg_name.replace('_', '-')}" for arg_name in missing)
            raise ValueError(f"args validation failure: missing required flags: {missing_flags}. {usage}")

        return workflow_id, parsed_args

    async def run_workflow_cli_command(self, command: str, timeout_seconds: int) -> tuple[str, ExecutionResult]:
        workflow_id, args = self.parse_workflow_cli_command(command)
        result = await self.run_workflow_script(
            workflow_id=workflow_id,
            args=args,
            timeout_seconds=timeout_seconds,
        )
        return workflow_id, result

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

    async def run_custom_workflow_code(
        self,
        code: str,
        args: dict[str, Any],
        timeout_seconds: int,
    ) -> ExecutionResult:
        rejections = validate_custom_workflow_code(code)
        if rejections:
            return ExecutionResult(
                success=False,
                exit_code=1,
                stderr="custom workflow code rejected by safety policy",
                safety_rejections=rejections,
            )

        with tempfile.TemporaryDirectory(prefix="zoekt-custom-workflow-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            script_path = temp_dir / "custom_workflow_code.py"
            runtime_src = self.src_root / "runtime"
            runtime_dst = temp_dir / "runtime"

            script_path.write_text(code, encoding="utf-8")
            shutil.copytree(runtime_src, runtime_dst, dirs_exist_ok=True)

            command = self._build_custom_workflow_command(script_path, args)

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

        if not marker_found and result_json is None:
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

    @staticmethod
    def _workflow_flag_aliases(arg_schema: dict[str, Any]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for arg_name in arg_schema.keys():
            if not isinstance(arg_name, str):
                continue
            aliases[f"--{arg_name}"] = arg_name
            aliases[f"--{arg_name.replace('_', '-')}"] = arg_name
        return aliases

    @staticmethod
    def _workflow_usage(workflow_id: str, arg_schema: dict[str, Any]) -> str:
        parts: list[str] = []
        for arg_name, schema in arg_schema.items():
            if not isinstance(arg_name, str):
                continue
            flag = f"--{arg_name.replace('_', '-')}"
            is_required = isinstance(schema, dict) and bool(schema.get("required"))
            fragment = f"{flag} <value>" if is_required else f"[{flag} <value>]"
            parts.append(fragment)
        suffix = f" {' '.join(parts)}" if parts else ""
        return f"Usage: {workflow_id}{suffix}"

    @staticmethod
    def _coerce_cli_arg_value(arg_name: str, raw_value: Any, schema: dict[str, Any], usage: str) -> Any:
        arg_type = str(schema.get("type", "string")).strip().lower()
        if arg_type == "string":
            return str(raw_value)
        if arg_type == "integer":
            try:
                return int(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"args validation failure: invalid integer for `--{arg_name.replace('_', '-')}`: {raw_value!r}. {usage}"
                ) from exc
        if arg_type == "boolean":
            value = str(raw_value).strip().lower()
            if value in {"true", "1", "yes", "on"}:
                return True
            if value in {"false", "0", "no", "off"}:
                return False
            raise ValueError(
                f"args validation failure: invalid boolean for `--{arg_name.replace('_', '-')}`: {raw_value!r}. {usage}"
            )
        raise ValueError(
            f"args validation failure: unsupported arg type `{arg_type}` for `--{arg_name.replace('_', '-')}`. {usage}"
        )

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
    def _build_custom_workflow_command(script_path: Path, args: dict[str, Any]) -> list[str]:
        args_json = json.dumps(args, ensure_ascii=True)
        script = str(script_path)
        script_parent = str(script_path.parent)
        bootstrap = (
            "import asyncio\n"
            "import inspect\n"
            "import json\n"
            "import runpy\n"
            "import sys\n"
            f"script = {script!r}\n"
            f"args_json = {args_json!r}\n"
            f"result_marker = {RESULT_MARKER!r}\n"
            f"sys.path.insert(0, {script_parent!r})\n"
            "namespace = runpy.run_path(script, run_name='__custom_workflow__')\n"
            "run_fn = namespace.get('run')\n"
            "if callable(run_fn):\n"
            "    payload = json.loads(args_json)\n"
            "    if inspect.iscoroutinefunction(run_fn):\n"
            "        run_result = asyncio.run(run_fn(payload))\n"
            "    else:\n"
            "        run_result = run_fn(payload)\n"
            "    if isinstance(run_result, int) and not isinstance(run_result, bool):\n"
            "        exit_code = run_result\n"
            "        marker_payload = None\n"
            "    else:\n"
            "        exit_code = 0\n"
            "        marker_payload = run_result\n"
            "    print(result_marker + json.dumps(marker_payload, ensure_ascii=True))\n"
            "    raise SystemExit(exit_code)\n"
            "main_fn = namespace.get('main')\n"
            "if callable(main_fn):\n"
            "    sys.argv = [script, '--args-json', args_json]\n"
            "    if inspect.iscoroutinefunction(main_fn):\n"
            "        main_result = asyncio.run(main_fn())\n"
            "    else:\n"
            "        main_result = main_fn()\n"
            "    if isinstance(main_result, int) and not isinstance(main_result, bool):\n"
            "        raise SystemExit(main_result)\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit('missing entrypoint: expected run(args) or legacy main()')\n"
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

        stripped = stdout.strip()
        if stripped:
            try:
                return "", json.loads(stripped), None, False
            except json.JSONDecodeError:
                pass

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
