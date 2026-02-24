import argparse
import asyncio
import importlib.util
import json
from pathlib import Path


def _load_file_context_reader_module():
    script_path = Path(__file__).resolve().parents[1] / "src" / "workflows" / "scripts" / "file_context_reader.py"
    spec = importlib.util.spec_from_file_location("file_context_reader_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load file_context_reader script module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


file_context_reader = _load_file_context_reader_module()


def _set_args_json(monkeypatch, payload: dict[str, object]) -> None:
    args_json = json.dumps(payload)
    monkeypatch.setattr(file_context_reader, "parse_args", lambda argv=None: argparse.Namespace(args_json=args_json))


def test_file_context_reader_allows_window_of_60_lines(monkeypatch, capsys) -> None:
    call_log: list[tuple[str, str, int, int]] = []

    def fake_fetch_content(repo: str, path: str, start_line: int, end_line: int) -> str:
        call_log.append((repo, path, start_line, end_line))
        return "line content"

    _set_args_json(
        monkeypatch,
        {
            "repo": "github.com/org/repo",
            "path": "src/main.go",
            "start_line": 1,
            "end_line": 60,
        },
    )
    monkeypatch.setattr(file_context_reader.zoekt_tools, "fetch_content", fake_fetch_content)

    exit_code = asyncio.run(file_context_reader.main())
    captured = capsys.readouterr()

    assert exit_code == 0
    assert call_log == [("github.com/org/repo", "src/main.go", 1, 60)]
    assert file_context_reader.RESULT_MARKER in captured.out


def test_file_context_reader_rejects_window_above_60_lines(monkeypatch, capsys) -> None:
    called = {"fetch_content": False}

    def fake_fetch_content(repo: str, path: str, start_line: int, end_line: int) -> str:
        called["fetch_content"] = True
        return "unused"

    _set_args_json(
        monkeypatch,
        {
            "repo": "github.com/org/repo",
            "path": "src/main.go",
            "start_line": 1,
            "end_line": 61,
        },
    )
    monkeypatch.setattr(file_context_reader.zoekt_tools, "fetch_content", fake_fetch_content)

    exit_code = asyncio.run(file_context_reader.main())
    captured = capsys.readouterr()

    assert exit_code == 1
    assert called["fetch_content"] is False
    assert "narrow range and retry" in captured.out
