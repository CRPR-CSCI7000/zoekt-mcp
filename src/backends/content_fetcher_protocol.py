from typing import Protocol, runtime_checkable

MAX_FILE_SIZE = 25_000
MAX_FETCH_LINE_WINDOW = 120


@runtime_checkable
class ContentFetcherProtocol(Protocol):
    """Protocol defining the interface for content fetchers.

    This is similar to Go interfaces - any class that implements
    these methods will satisfy the protocol.
    """

    def fetch_content(
        self, repository: str, path: str, start_line: int, end_line: int
    ) -> str:
        """Get file content from repository.

        Args:
            repository: Repository path (e.g., "github.com/example/project")
            path: File path (e.g., "src/main.py")
            start_line: Starting line number (1-indexed)
            end_line: Ending line number (1-indexed)

        Returns:
            File content
            
        Raises:
            ValueError: If repository or path does not exist or is not a file
        """
        ...
        
    def list_dir(
        self, repository: str, path: str = "", depth: int = 2
    ) -> str:
        """Get directory structure from repository.

        Args:
            repository: Repository path (e.g., "github.com/example/project")
            path: Directory path (e.g., "src/")
            depth: Tree depth for directory listings

        Returns:
            Directory tree string
            
        Raises:
            ValueError: If repository or directory does not exist
        """
        ...
