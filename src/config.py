import os


class ServerConfig:
    def __init__(self) -> None:
        self.sse_port = int(os.getenv("MCP_SSE_PORT", "8000"))
        self.streamable_http_port = int(os.getenv("MCP_STREAMABLE_HTTP_PORT", "8080"))
        self.zoekt_api_url = self._get_required_env("ZOEKT_API_URL")
        self.execution_timeout_default = int(os.getenv("EXECUTION_TIMEOUT_DEFAULT", "30"))
        self.execution_timeout_max = int(os.getenv("EXECUTION_TIMEOUT_MAX", "120"))
        self.execution_stdout_max_bytes = int(os.getenv("EXECUTION_STDOUT_MAX_BYTES", "32768"))
        self.execution_stderr_max_bytes = int(os.getenv("EXECUTION_STDERR_MAX_BYTES", "32768"))

    @staticmethod
    def _get_required_env(key: str) -> str:
        """Get required environment variable or raise descriptive error."""
        value = os.getenv(key)
        if not value:
            raise ValueError(f"Required environment variable {key} is not set")
        return value
