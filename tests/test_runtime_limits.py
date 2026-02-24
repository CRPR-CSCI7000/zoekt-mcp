from unittest.mock import Mock, patch

import pytest
import requests

from runtime.zoekt_tools import (
    MAX_CONTEXT_LINES,
    MAX_FETCH_WINDOW_LINES,
    ZoektRuntime,
    ZoektRuntimeError,
)


def _html_with_inline_pre(lines: list[str]) -> str:
    return "".join(f'<pre class="inline-pre">{line}</pre>' for line in lines)


def test_search_rejects_context_lines_over_max() -> None:
    runtime = ZoektRuntime(base_url="http://zoekt")

    with pytest.raises(ZoektRuntimeError, match=f"between 0 and {MAX_CONTEXT_LINES}"):
        runtime.search(query="ProcessOrder", context_lines=MAX_CONTEXT_LINES + 1)


def test_search_uses_context_line_value_when_in_bounds() -> None:
    runtime = ZoektRuntime(base_url="http://zoekt")
    response = Mock()
    response.json.return_value = {"result": {"FileMatches": []}}

    with patch("runtime.zoekt_tools.requests.get", return_value=response) as mock_get:
        runtime.search(query="ProcessOrder", limit=7, context_lines=2)

    assert mock_get.call_count == 1
    called_kwargs = mock_get.call_args.kwargs
    assert called_kwargs["params"]["ctx"] == 2


def test_fetch_content_rejects_line_windows_above_max() -> None:
    runtime = ZoektRuntime(base_url="http://zoekt")

    with patch("runtime.zoekt_tools.requests.get") as mock_get:
        with pytest.raises(ZoektRuntimeError, match=f"max {MAX_FETCH_WINDOW_LINES}"):
            runtime.fetch_content(
                repo="github.com/org/repo",
                path="src/main.go",
                start_line=1,
                end_line=MAX_FETCH_WINDOW_LINES + 1,
            )
        mock_get.assert_not_called()


def test_fetch_content_allows_window_at_max() -> None:
    runtime = ZoektRuntime(base_url="http://zoekt")
    lines = [f"line-{index}" for index in range(1, MAX_FETCH_WINDOW_LINES + 2)]
    response = Mock()
    response.text = _html_with_inline_pre(lines)

    with patch("runtime.zoekt_tools.requests.get", return_value=response):
        content = runtime.fetch_content(
            repo="github.com/org/repo",
            path="src/main.go",
            start_line=1,
            end_line=MAX_FETCH_WINDOW_LINES,
        )

    expected = "\n".join(lines[:MAX_FETCH_WINDOW_LINES])
    assert content == expected


def test_fetch_content_surfaces_print_error_body() -> None:
    runtime = ZoektRuntime(base_url="http://zoekt")
    response = Mock()
    response.status_code = 418
    response.text = "ambiguous result: []"
    response.raise_for_status.side_effect = requests.HTTPError("418 Client Error")

    with patch("runtime.zoekt_tools.requests.get", return_value=response):
        with pytest.raises(ZoektRuntimeError, match=r"ambiguous result: \[\]"):
            runtime.fetch_content(
                repo="github.com/org/repo",
                path="src/main.go",
                start_line=1,
                end_line=10,
            )
