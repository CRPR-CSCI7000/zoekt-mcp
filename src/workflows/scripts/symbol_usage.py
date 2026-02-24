import argparse
import asyncio
import json

from runtime import zoekt_tools

RESULT_MARKER = "__RESULT_JSON__="
MAX_CONTEXT_LINES = 2


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Find symbol usage call-sites.")
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
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ValueError("missing required arg: query")

        limit = int(payload.get("limit", 10))
        context_lines = int(payload.get("context_lines", 2))
        if context_lines < 0 or context_lines > MAX_CONTEXT_LINES:
            raise ValueError(f"context_lines must be between 0 and {MAX_CONTEXT_LINES}")
        results = await asyncio.to_thread(zoekt_tools.search, query, limit, context_lines)

        output = {
            "query": query,
            "context_lines": context_lines,
            "total_hits": len(results),
            "results": results,
        }
        print(RESULT_MARKER + json.dumps(output, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"symbol_usage failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
