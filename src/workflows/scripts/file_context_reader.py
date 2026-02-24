import argparse
import asyncio
import json

from runtime import zoekt_tools

RESULT_MARKER = "__RESULT_JSON__="
MAX_LINE_WINDOW = 60


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Read a bounded line range from one file.")
    parser.add_argument("--args-json", required=True, help="JSON object for workflow args")
    return parser.parse_args(argv)


def _ensure_mapping(raw: str) -> dict:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("args-json must decode to an object")
    return payload


async def main():
    try:
        cli = parse_args()
        payload = _ensure_mapping(cli.args_json)

        repo = str(payload.get("repo", "")).strip()
        path = str(payload.get("path", "")).strip()
        if not repo:
            raise ValueError("missing required arg: repo")
        if not path:
            raise ValueError("missing required arg: path")

        start_line = int(payload.get("start_line", 0))
        end_line = int(payload.get("end_line", 0))
        if start_line <= 0 or end_line <= 0:
            raise ValueError("start_line and end_line must be positive integers")
        if end_line < start_line:
            raise ValueError("end_line must be >= start_line")
        requested_window = end_line - start_line + 1
        if requested_window > MAX_LINE_WINDOW:
            raise ValueError(
                f"requested line window {requested_window} exceeds max {MAX_LINE_WINDOW}; narrow range and retry"
            )

        content = await asyncio.to_thread(zoekt_tools.fetch_content, repo, path, start_line, end_line)

        output = {
            "repo": repo,
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "content": content,
        }
        print(RESULT_MARKER + json.dumps(output, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"file_context_reader failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
