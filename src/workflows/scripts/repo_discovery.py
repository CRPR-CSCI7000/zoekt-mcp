import argparse
import asyncio
import json

from runtime import zoekt_tools

RESULT_MARKER = "__RESULT_JSON__="


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Discover candidate repositories for an objective.")
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
        repo_query = query if "type:repo" in query else f"{query} type:repo"
        results = await asyncio.to_thread(zoekt_tools.search, repo_query, limit, 0)

        repositories = sorted({entry.get("repository", "") for entry in results if entry.get("repository")})
        output = {
            "query": query,
            "search_query": repo_query,
            "total_hits": len(results),
            "repositories": repositories,
            "results": results,
        }
        print(RESULT_MARKER + json.dumps(output, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"repo_discovery failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
