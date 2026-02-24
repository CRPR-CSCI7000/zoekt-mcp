import argparse
import asyncio
import importlib.util
import json
from pathlib import Path


def _load_cross_repo_trace_module():
    script_path = Path(__file__).resolve().parents[1] / "src" / "workflows" / "scripts" / "cross_repo_trace.py"
    spec = importlib.util.spec_from_file_location("cross_repo_trace_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load cross_repo_trace script module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cross_repo_trace = _load_cross_repo_trace_module()


def _set_args_json(monkeypatch, payload: dict[str, object]) -> None:
    args_json = json.dumps(payload)
    monkeypatch.setattr(cross_repo_trace, "parse_args", lambda argv=None: argparse.Namespace(args_json=args_json))


def _parse_result_payload(stdout: str) -> dict:
    marker = cross_repo_trace.RESULT_MARKER
    for line in stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker) :])
    raise AssertionError("result marker not found in stdout")


def test_cross_repo_trace_prefers_discovery_ranked_repos(monkeypatch, capsys) -> None:
    discovery_repos = [f"github.com/org/repo-{index}" for index in range(1, 11)]
    visited_usage_repos: list[str] = []

    def fake_search(query: str, limit: int, context_lines: int) -> list[dict]:
        if "type:repo" in query:
            return [{"repository": repo, "filename": "", "matches": []} for repo in discovery_repos]
        _, _, repo = query.partition(" r:")
        visited_usage_repos.append(repo)
        return []

    def fake_search_symbols(query: str, limit: int) -> list[dict]:
        return []

    def fail_list_repos() -> list[str]:
        raise AssertionError("list_repos should not be called when discovery provides enough repositories")

    _set_args_json(monkeypatch, {"symbol": "NATS"})
    monkeypatch.setattr(cross_repo_trace.zoekt_tools, "search", fake_search)
    monkeypatch.setattr(cross_repo_trace.zoekt_tools, "search_symbols", fake_search_symbols)
    monkeypatch.setattr(cross_repo_trace.zoekt_tools, "list_repos", fail_list_repos)

    exit_code = asyncio.run(cross_repo_trace.main())
    captured = capsys.readouterr()
    payload = _parse_result_payload(captured.out)

    assert exit_code == 0
    assert payload["inspected_repos"] == 8
    assert visited_usage_repos == discovery_repos[:8]


def test_cross_repo_trace_falls_back_to_list_repos_when_needed(monkeypatch, capsys) -> None:
    discovery_repos = [
        "github.com/org/discovered-a",
        "github.com/org/discovered-b",
    ]
    fallback_repos = [
        "github.com/org/discovered-b",
        "github.com/org/fallback-c",
        "github.com/org/fallback-d",
    ]
    visited_usage_repos: list[str] = []

    def fake_search(query: str, limit: int, context_lines: int) -> list[dict]:
        if "type:repo" in query:
            return [{"repository": repo, "filename": "", "matches": []} for repo in discovery_repos]
        _, _, repo = query.partition(" r:")
        visited_usage_repos.append(repo)
        return []

    def fake_search_symbols(query: str, limit: int) -> list[dict]:
        return []

    def fake_list_repos() -> list[str]:
        return fallback_repos

    _set_args_json(monkeypatch, {"symbol": "NATS", "max_repos": 4})
    monkeypatch.setattr(cross_repo_trace.zoekt_tools, "search", fake_search)
    monkeypatch.setattr(cross_repo_trace.zoekt_tools, "search_symbols", fake_search_symbols)
    monkeypatch.setattr(cross_repo_trace.zoekt_tools, "list_repos", fake_list_repos)

    exit_code = asyncio.run(cross_repo_trace.main())
    captured = capsys.readouterr()
    payload = _parse_result_payload(captured.out)

    assert exit_code == 0
    assert payload["inspected_repos"] == 4
    assert visited_usage_repos == [
        "github.com/org/discovered-a",
        "github.com/org/discovered-b",
        "github.com/org/fallback-c",
        "github.com/org/fallback-d",
    ]
