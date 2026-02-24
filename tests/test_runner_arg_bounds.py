from pathlib import Path

import pytest
import yaml

from execution.runner import ExecutionRunner


def _write_manifest(path: Path) -> None:
    manifest = {
        "workflows": [
            {
                "id": "symbol_usage",
                "script_path": "workflows/scripts/symbol_usage.py",
                "arg_schema": {
                    "query": {"type": "string", "required": True},
                    "context_lines": {"type": "integer", "required": False, "default": 2, "minimum": 0, "maximum": 2},
                },
            }
        ]
    }
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")


def _build_runner(tmp_path: Path) -> ExecutionRunner:
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(manifest_path)
    return ExecutionRunner(
        src_root=tmp_path,
        manifest_path=manifest_path,
        timeout_default=30,
        timeout_max=120,
        stdout_max_bytes=32768,
        stderr_max_bytes=32768,
    )


def test_parse_workflow_cli_rejects_integer_above_maximum(tmp_path: Path) -> None:
    runner = _build_runner(tmp_path)

    with pytest.raises(ValueError, match="must be <= 2"):
        runner.parse_workflow_cli_command('symbol_usage --query "ProcessOrder" --context-lines 3')


def test_parse_workflow_cli_accepts_integer_within_bounds(tmp_path: Path) -> None:
    runner = _build_runner(tmp_path)

    workflow_id, args = runner.parse_workflow_cli_command('symbol_usage --query "ProcessOrder" --context-lines 2')

    assert workflow_id == "symbol_usage"
    assert args["query"] == "ProcessOrder"
    assert args["context_lines"] == 2


def test_parse_workflow_cli_applies_default_and_keeps_it_bounded(tmp_path: Path) -> None:
    runner = _build_runner(tmp_path)

    workflow_id, args = runner.parse_workflow_cli_command('symbol_usage --query "ProcessOrder"')

    assert workflow_id == "symbol_usage"
    assert args["context_lines"] == 2
