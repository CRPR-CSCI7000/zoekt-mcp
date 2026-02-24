import html
import os
import re
from typing import Any

import requests

DEFAULT_SEARCH_LIMIT = 10
DEFAULT_CONTEXT_LINES = 2
MAX_SEARCH_LIMIT = 25
MAX_CONTEXT_LINES = 3


class ZoektRuntimeError(RuntimeError):
    """Raised when runtime wrappers fail to communicate with Zoekt."""


class ZoektRuntime:
    def __init__(self, base_url: str | None = None) -> None:
        configured_base_url = base_url or os.getenv("ZOEKT_API_URL")
        if not configured_base_url:
            raise ZoektRuntimeError("ZOEKT_API_URL is not set")
        self.base_url = configured_base_url.rstrip("/")

    def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT, context_lines: int = DEFAULT_CONTEXT_LINES) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "num": min(max(1, int(limit)), MAX_SEARCH_LIMIT),
            "format": "json",
            "ctx": min(max(0, int(context_lines)), MAX_CONTEXT_LINES),
        }
        response = requests.get(f"{self.base_url}/search", params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
        return _format_search_results(payload, params["num"])

    def search_symbols(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[dict[str, Any]]:
        if "sym:" not in query:
            query = f"sym:{query}"
        return self.search(query=query, limit=limit, context_lines=0)

    def fetch_content(self, repo: str, path: str, start_line: int, end_line: int) -> str:
        params = {
            "r": _clean_repository_path(repo),
            "f": path,
        }
        response = requests.get(f"{self.base_url}/print", params=params, timeout=15)
        response.raise_for_status()
        all_lines = _extract_lines_from_html(response.text)

        if start_line <= 0 or end_line <= 0 or end_line < start_line:
            raise ZoektRuntimeError("invalid line range")

        if not all_lines:
            raise ZoektRuntimeError("file not found or unreadable")

        start_index = start_line - 1
        end_index = min(len(all_lines), end_line)

        if start_index >= len(all_lines):
            return ""

        selected = all_lines[start_index:end_index]
        return "\n".join(selected)

    def list_dir(self, repo: str, path: str = "", depth: int = 2) -> str:
        clean_repo = _clean_repository_path(repo)
        normalized_path = path.strip("/")

        if normalized_path:
            query = f"r:{clean_repo} file:^{normalized_path}/"
        else:
            query = f"r:{clean_repo} f:\\.*"

        params = {
            "q": query,
            "num": 1000,
            "format": "json",
        }
        response = requests.get(f"{self.base_url}/search", params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()

        file_matches = payload.get("result", {}).get("FileMatches") or []
        file_paths = sorted(match.get("FileName", "") for match in file_matches if match.get("FileName"))

        if normalized_path:
            prefix = f"{normalized_path}/"
            file_paths = [path for path in file_paths if path.startswith(prefix)]

        if not file_paths and normalized_path:
            raise ZoektRuntimeError("directory not found")

        return _format_directory_tree(file_paths=file_paths, base_path=normalized_path, max_depth=max(1, int(depth)))

    def list_repos(self) -> list[str]:
        response = requests.post(f"{self.base_url}/api/list", json={}, timeout=15)
        response.raise_for_status()
        payload = response.json()

        repos = []
        for item in (payload.get("List", {}).get("Repos") or []):
            repo_info = item.get("Repository") or {}
            name = repo_info.get("Name")
            if name:
                repos.append(name)

        return sorted(set(repos))


_RUNTIME: ZoektRuntime | None = None


def _get_runtime() -> ZoektRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = ZoektRuntime()
    return _RUNTIME


def search(query: str, limit: int = DEFAULT_SEARCH_LIMIT, context_lines: int = DEFAULT_CONTEXT_LINES) -> list[dict[str, Any]]:
    return _get_runtime().search(query=query, limit=limit, context_lines=context_lines)


def search_symbols(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[dict[str, Any]]:
    return _get_runtime().search_symbols(query=query, limit=limit)


def fetch_content(repo: str, path: str, start_line: int, end_line: int) -> str:
    return _get_runtime().fetch_content(repo=repo, path=path, start_line=start_line, end_line=end_line)


def list_dir(repo: str, path: str = "", depth: int = 2) -> str:
    return _get_runtime().list_dir(repo=repo, path=path, depth=depth)


def list_repos() -> list[str]:
    return _get_runtime().list_repos()


def _clean_repository_path(repository: str) -> str:
    return repository.replace("https://", "").replace("http://", "")


def _extract_lines_from_html(html_content: str) -> list[str]:
    lines: list[str] = []
    pre_pattern = r'<pre[^>]*class="inline-pre"[^>]*>(.*?)</pre>'

    for match in re.finditer(pre_pattern, html_content, re.DOTALL):
        line_content = match.group(1)
        line_content = re.sub(r'<span[^>]*class="noselect"[^>]*>.*?</span>', "", line_content)
        line_content = re.sub(r"<[^>]+>", "", line_content)
        lines.append(html.unescape(line_content))

    return lines


def _format_search_results(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []

    repos = payload.get("repos", {}).get("Repos") or []
    if repos:
        for repo in repos[:limit]:
            name = repo.get("Name", "")
            formatted.append(
                {
                    "filename": "",
                    "repository": name,
                    "url": repo.get("URL", f"https://{name}"),
                    "matches": [{"line_number": 0, "text": f"Repository: {name}"}],
                }
            )
        return formatted

    file_matches = payload.get("result", {}).get("FileMatches") or []
    for file_match in file_matches:
        if len(formatted) >= limit:
            break

        matches = []
        for match in file_match.get("Matches", []):
            full_line = "".join(fragment.get("Pre", "") + fragment.get("Match", "") + fragment.get("Post", "") for fragment in match.get("Fragments", []))
            before = match.get("Before", "").strip().splitlines() if match.get("Before") else []
            after = match.get("After", "").strip().splitlines() if match.get("After") else []
            context = before + [full_line.strip()] + after
            matches.append({
                "line_number": int(match.get("LineNum", 0)),
                "text": "\n".join(line for line in context if line),
            })

        if not matches:
            continue

        formatted.append(
            {
                "filename": file_match.get("FileName", ""),
                "repository": file_match.get("Repo", ""),
                "url": (file_match.get("Matches", [{}])[0].get("URL", "").split("#L")[0] if file_match.get("Matches") else None),
                "matches": matches,
            }
        )

    return formatted


def _format_directory_tree(file_paths: list[str], base_path: str, max_depth: int) -> str:
    if not file_paths:
        return ""

    tree_lines: list[str] = []
    printed: set[str] = set()

    for path in file_paths:
        relative = path
        if base_path and path.startswith(f"{base_path}/"):
            relative = path[len(base_path) + 1 :]

        parts = [part for part in relative.split("/") if part]
        if not parts:
            continue

        max_parts = min(len(parts), max_depth + 1)
        for index in range(max_parts):
            prefix = "/".join(parts[: index + 1])
            if prefix in printed:
                continue
            printed.add(prefix)

            indent = "  " * index
            is_file = index == len(parts) - 1
            label = parts[index] if is_file else f"{parts[index]}/"
            tree_lines.append(f"{indent}{label}")

    return "\n".join(tree_lines)
