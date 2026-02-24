import pathlib
from typing import Any, Callable

from ..execution.models import ExecutionResult


def format_workflow_result_markdown(workflow_id: str, result: ExecutionResult) -> str:
    process_status = "success" if result.success else "failure"
    output_status = _infer_output_status(result)
    lines = [
        f"## Workflow: `{workflow_id}`",
        "",
        f"- Process status: `{process_status}`",
        f"- Output status: `{output_status}`",
        f"- Exit code: `{result.exit_code}`",
        f"- Timing (ms): `{result.timing_ms}`",
    ]

    if not result.success:
        if result.safety_rejections:
            lines.append(f"- Safety rejections: `{len(result.safety_rejections)}`")
            lines.extend([f"  - {rejection}" for rejection in result.safety_rejections])
        if result.stderr:
            lines.extend(["", "### Error", "```text", result.stderr, "```"])
        if result.stdout:
            lines.extend(["", "### Stdout", "```text", result.stdout, "```"])
        return "\n".join(lines)

    payload = result.result_json
    if payload is None:
        lines.extend(
            [
                "",
                "No structured workflow payload was produced.",
                "This means execution completed, but output parsing or marker contract failed.",
            ]
        )
        if result.stderr:
            lines.extend(["", "### Parser / Runtime Details", "```text", result.stderr, "```"])
        if result.stdout:
            lines.extend(["", "### Stdout", "```text", result.stdout, "```"])
        return "\n".join(lines)

    workflow_renderers: dict[str, Callable[[Any], list[str]]] = {
        "repo_discovery": _render_repo_discovery_result,
        "symbol_definition": _render_symbol_search_result,
        "symbol_usage": _render_symbol_search_result,
        "file_context_reader": _render_file_context_result,
        "cross_repo_trace": _render_cross_repo_trace_result,
    }
    renderer = workflow_renderers.get(workflow_id, _render_generic_workflow_result)
    body = renderer(payload)

    if body:
        lines.extend(["", *body])
    if result.stderr:
        lines.extend(["", "### Stderr", "```text", result.stderr, "```"])
    if result.stdout:
        lines.extend(["", "### Stdout", "```text", result.stdout, "```"])
    return "\n".join(lines)


def _infer_output_status(result: ExecutionResult) -> str:
    if result.result_json is not None:
        return "parsed"

    stderr_lc = (result.stderr or "").lower()
    if "malformed result marker json" in stderr_lc:
        return "parse_error"
    if "result marker not found" in stderr_lc:
        return "missing_result_marker"
    if result.success:
        return "missing_payload"
    return "not_available"


def _render_repo_discovery_result(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return _render_generic_workflow_result(payload)

    query = str(payload.get("query", "")).strip()
    repositories = payload.get("repositories") if isinstance(payload.get("repositories"), list) else []
    results = payload.get("results") if isinstance(payload.get("results"), list) else []

    lines = [
        f"Found `{len(repositories)}` repositories for `{query}`." if query else f"Found `{len(repositories)}` repositories.",
        "",
    ]
    if repositories:
        lines.append("### Repositories")
        lines.extend([f"{index}. `{repo}`" for index, repo in enumerate(repositories, start=1)])
    else:
        lines.append("No repositories found.")

    if results:
        lines.extend(["", "### Top Matches", *_render_search_results(results)])
    return lines


def _render_symbol_search_result(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return _render_generic_workflow_result(payload)

    query = str(payload.get("query", "")).strip()
    total_hits = payload.get("total_hits", 0)
    results = payload.get("results") if isinstance(payload.get("results"), list) else []

    lines = [f"Found `{total_hits}` matches for `{query}`." if query else f"Found `{total_hits}` matches.", ""]
    if results:
        lines.extend(_render_search_results(results))
    else:
        lines.append("No matches found.")
    return lines


def _render_file_context_result(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return _render_generic_workflow_result(payload)

    repo = str(payload.get("repo", "")).strip()
    path = str(payload.get("path", "")).strip()
    start_line = _coerce_int(payload.get("start_line"), default=1)
    end_line = _coerce_int(payload.get("end_line"), default=start_line)
    content = str(payload.get("content", ""))

    header = f"`{repo}/{path}` lines `{start_line}-{end_line}`" if repo and path else f"Lines `{start_line}-{end_line}`"
    lines = [header, ""]

    if not content:
        lines.append("No content returned for the requested range.")
        return lines

    language = _language_from_path(path)
    numbered_code = _with_line_numbers(content, start_line=start_line)
    lines.extend([f"```{language}", numbered_code, "```"])
    return lines


def _render_cross_repo_trace_result(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return _render_generic_workflow_result(payload)

    symbol = str(payload.get("symbol", "")).strip()
    inspected_repos = _coerce_int(payload.get("inspected_repos"), default=0)
    trace = payload.get("trace") if isinstance(payload.get("trace"), list) else []
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []

    lines = [
        f"Cross-repo trace for `{symbol}` across `{inspected_repos}` repos." if symbol else f"Cross-repo trace across `{inspected_repos}` repos.",
        "",
    ]
    if not trace:
        lines.append("No trace results found.")
    else:
        for index, repo_entry in enumerate(trace, start=1):
            if not isinstance(repo_entry, dict):
                continue
            repo = str(repo_entry.get("repo", "(unknown repo)"))
            definition_hits = _coerce_int(repo_entry.get("definition_hits"), default=0)
            usage_hits = _coerce_int(repo_entry.get("usage_hits"), default=0)
            lines.extend(
                [
                    f"### {index}. `{repo}`",
                    f"- Definition hits: `{definition_hits}`",
                    f"- Usage hits: `{usage_hits}`",
                ]
            )

            definitions = repo_entry.get("definitions") if isinstance(repo_entry.get("definitions"), list) else []
            usages = repo_entry.get("usages") if isinstance(repo_entry.get("usages"), list) else []
            if definitions:
                lines.extend(["- Sample definitions:", *_indent_markdown(_render_search_results(definitions, max_files=2))])
            if usages:
                lines.extend(["- Sample usages:", *_indent_markdown(_render_search_results(usages, max_files=2))])

    if errors:
        lines.extend(["", "### Errors"])
        for error in errors:
            if isinstance(error, dict):
                lines.append(f"- `{error.get('repo', '(unknown repo)')}`: {error.get('error', '(unknown error)')}")
            else:
                lines.append(f"- {error}")

    return lines


def _render_generic_workflow_result(payload: Any) -> list[str]:
    if payload is None:
        return ["No structured workflow payload returned."]
    if isinstance(payload, (str, int, float, bool)):
        return [f"Result: `{payload}`"]
    if isinstance(payload, list):
        if not payload:
            return ["Result list is empty."]
        lines = [f"Result list with `{len(payload)}` items:"]
        for index, item in enumerate(payload[:10], start=1):
            lines.append(f"{index}. `{_stringify_scalar(item)}`")
        if len(payload) > 10:
            lines.append(f"... and `{len(payload) - 10}` more items.")
        return lines
    if isinstance(payload, dict):
        lines = ["Result fields:"]
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                lines.append(f"- `{key}`: `{value}`")
            elif isinstance(value, list):
                lines.append(f"- `{key}`: list with `{len(value)}` items")
            elif isinstance(value, dict):
                lines.append(f"- `{key}`: object with `{len(value)}` fields")
            else:
                lines.append(f"- `{key}`: `{type(value).__name__}`")
        return lines
    return [f"Result type: `{type(payload).__name__}`"]


def _render_search_results(results: list[Any], max_files: int = 10, max_matches_per_file: int = 4) -> list[str]:
    lines: list[str] = []
    for index, entry in enumerate(results[:max_files], start=1):
        if not isinstance(entry, dict):
            lines.append(f"{index}. `{_stringify_scalar(entry)}`")
            continue

        repository = str(entry.get("repository", "")).strip()
        filename = str(entry.get("filename", "")).strip()
        location = "/".join(part for part in [repository, filename] if part) or "(unknown location)"
        lines.append(f"{index}. `{location}`")

        matches = entry.get("matches") if isinstance(entry.get("matches"), list) else []
        for match in matches[:max_matches_per_file]:
            if not isinstance(match, dict):
                lines.append(f"   - `{_stringify_scalar(match)}`")
                continue
            line_number = _coerce_int(match.get("line_number"), default=0)
            text = str(match.get("text", "")).replace("\n", " ").strip()
            if len(text) > 220:
                text = f"{text[:217]}..."
            lines.append(f"   - L{line_number}: `{text}`")

        if len(matches) > max_matches_per_file:
            lines.append(f"   - ... `{len(matches) - max_matches_per_file}` more matches")

        url = str(entry.get("url", "")).strip()
        if url:
            lines.append(f"   {url}")

    if len(results) > max_files:
        lines.append(f"... and `{len(results) - max_files}` more files.")
    return lines


def _indent_markdown(lines: list[str], spaces: int = 2) -> list[str]:
    prefix = " " * spaces
    return [f"{prefix}{line}" if line else "" for line in lines]


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stringify_scalar(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return type(value).__name__


def _with_line_numbers(content: str, start_line: int) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    max_line = start_line + len(lines) - 1
    width = max(2, len(str(max_line)))
    return "\n".join(f"{line_no:>{width}} | {line}" for line_no, line in enumerate(lines, start=start_line))


def _language_from_path(path: str) -> str:
    suffix = pathlib.Path(path).suffix.lower()
    mapping = {
        ".py": "python",
        ".ts": "ts",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".go": "go",
        ".java": "java",
        ".rb": "ruby",
        ".rs": "rust",
        ".c": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sh": "bash",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
    }
    return mapping.get(suffix, "text")
