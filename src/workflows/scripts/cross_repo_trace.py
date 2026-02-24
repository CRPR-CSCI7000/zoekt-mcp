import argparse
import asyncio
import json

from runtime import zoekt_tools

RESULT_MARKER = "__RESULT_JSON__="


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build a lightweight cross-repository trace for a symbol.")
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

        symbol = str(payload.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("missing required arg: symbol")

        max_repos = int(payload.get("max_repos", 8))
        definitions_limit = int(payload.get("definitions_limit", 2))
        usages_limit = int(payload.get("usages_limit", 3))

        repos = await asyncio.to_thread(zoekt_tools.list_repos)
        selected_repos = repos[: max(1, max_repos)]

        trace = []
        errors = []
        for repo in selected_repos:
            try:
                definition_query = f"{symbol} r:{repo}"
                usage_query = f"{symbol} r:{repo}"
                definitions = await asyncio.to_thread(zoekt_tools.search_symbols, definition_query, definitions_limit)
                usages = await asyncio.to_thread(zoekt_tools.search, usage_query, usages_limit, 1)

                if definitions or usages:
                    trace.append(
                        {
                            "repo": repo,
                            "definition_hits": len(definitions),
                            "usage_hits": len(usages),
                            "definitions": definitions,
                            "usages": usages,
                        }
                    )
            except Exception as exc:
                errors.append({"repo": repo, "error": str(exc)})

        output = {
            "symbol": symbol,
            "inspected_repos": len(selected_repos),
            "trace": trace,
            "errors": errors,
        }
        print(RESULT_MARKER + json.dumps(output, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"cross_repo_trace failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
