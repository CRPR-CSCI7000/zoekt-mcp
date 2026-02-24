# Zoekt MCP Server

`zoekt-mcp` is a Model Context Protocol (MCP) server that exposes a workflow-first interface for Zoekt-backed code intelligence.

## Architecture

Single service/process with embedded modules:

- `capabilities/`: capability discovery and full capability docs
- `execution/`: request/result models, AST safety checks, and isolated runner
- `workflows/`: workflow manifest and prebuilt scripts
- `runtime/zoekt_tools.py`: safe Python wrappers over Zoekt HTTP endpoints

There is no separate executor service.

## MCP Tools (Breaking Change)

The server exposes only these 4 tools:

1. `search_capabilities(query: str, limit: int = 8)`
2. `read_capability(capability_id: str)`
3. `run_workflow(workflow_id: str, args: dict, timeout_seconds: int = 30)`
4. `execute_ephemeral_script(code: str, args: dict = {}, timeout_seconds: int = 30)`

All tool responses are rendered as markdown text for agent readability.

Removed tools:

- `search`
- `search_symbols`
- `search_prompt_guide`
- `fetch_content`
- `list_dir`
- `list_repos`

## Recommended Flow

1. Call `search_capabilities` for the objective.
2. Call `read_capability` for selected ids.
3. Prefer `run_workflow` for known tasks.
4. Use `execute_ephemeral_script` only when workflows do not fit.

## Ephemeral Script Constraints

Generated scripts are AST-validated before execution:

- Required entrypoint shape:
  - `def parse_args(...)`
  - `async def main()`
  - `if __name__ == "__main__": ...`
- Import allowlist centered on: `argparse`, `asyncio`, `json`, `sys`, and `runtime.zoekt_tools`
- Banned imports include modules such as `os`, `subprocess`, `socket`, `ctypes`, `multiprocessing`, `pathlib`
- Banned calls include `eval`, `exec`, `compile`, `open`, `__import__`, `input`

## Execution Behavior

- Every run executes in an isolated temp working directory.
- Subprocess invocation uses `python -I -u`.
- Environment is reduced to an allowlist.
- Timeout and stdout/stderr caps are enforced.
- Scripts can emit a final marker line:
  - `__RESULT_JSON__=<json>`
  - parsed into `ExecutionResult.result_json`

This is process-level sandboxing, not container-grade isolation.

## Configuration

Required:

- `ZOEKT_API_URL`

Optional:

- `MCP_SSE_PORT` (default `8000`)
- `MCP_STREAMABLE_HTTP_PORT` (default `8080`)
- `EXECUTION_TIMEOUT_DEFAULT` (default `30`)
- `EXECUTION_TIMEOUT_MAX` (default `120`)
- `EXECUTION_STDOUT_MAX_BYTES` (default `32768`)
- `EXECUTION_STDERR_MAX_BYTES` (default `32768`)

## Local Dev

```bash
uv sync
uv run python src/main.py
```

Lint:

```bash
uv run ruff check src
```
