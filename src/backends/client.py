from typing import List

import requests
from .models import FormattedResult, Match
from .search_protocol import SearchClientProtocol


class ZoektClient(SearchClientProtocol):
    def __init__(
        self,
        base_url: str,
        max_line_length: int = 220,
        max_output_length: int = 20_000,
        max_match_text_length: int = 1_200,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_line_length = max_line_length
        self.max_output_length = max_output_length
        self.max_match_text_length = max_match_text_length

    def search(self, query: str, num: int, context_lines: int = 2) -> dict:
        # Zoekt limit is 10 for context lines
        if context_lines < 0 or context_lines > 10:
            context_lines = 2

        params = {
            "q": query,
            "num": num,
            "format": "json",
            "ctx": context_lines,
        }

        url = f"{self.base_url}/search"
        response = requests.get(url, params=params)

        if response.status_code != 200:
            raise requests.exceptions.HTTPError(
                f"Search failed with status code: {response.status_code}. Response: {response.text}"
            )
        return response.json()
        
    def search_symbols(self, query: str, num: int) -> dict:
        # Force a symbol match using 'sym:' query syntax
        prefix = "sym:"
        
        # Avoid prepending if the LLM already included it
        if not "sym:" in query:
            # We must handle if the LLM sent repo filters too, e.g. 'r:myrepo myfunction' -> 'r:myrepo sym:myfunction'
            # For simplicity, we can just append it: 'query sym:query_without_filters' but extracting is hard.
            # Best effort: Just inject it if it's missing, or instruct the LLM properly.
            pass
            
        # Actually, let's just use the regular search endpoint with context lines = 0
        return self.search(f"{prefix}{query}" if "sym:" not in query else query, num, context_lines=0)

    def _truncate_line(self, line: str) -> str:
        if len(line) > self.max_line_length:
            return line[: self.max_line_length - 3] + "..."
        return line
    
    @staticmethod
    def _truncate_text(text: str, max_len: int) -> str:
        if max_len <= 0:
            return ""
        if len(text) <= max_len:
            return text
        if max_len <= 3:
            return text[:max_len]
        return text[: max_len - 3] + "..."

    def format_results(self, results: dict, num: int) -> List[FormattedResult]:
        formatted = []
        remaining_budget = self.max_output_length

        # Handle repository results (when using r: queries)
        if "repos" in results and "Repos" in results["repos"]:
            for repo in results["repos"]["Repos"][:num]:
                if remaining_budget <= 0:
                    break

                repo_name = repo.get("Name", "")
                repo_url = repo.get("URL", f"https://{repo_name}")
                repo_text = self._truncate_text(f"Repository: {repo_name}", self.max_match_text_length)
                repo_text = self._truncate_text(repo_text, remaining_budget)
                if not repo_text:
                    break

                formatted.append(
                    FormattedResult(
                        filename="",
                        repository=repo_name,
                        matches=[
                            Match(
                                line_number=0,
                                text=repo_text,
                            )
                        ],
                        url=repo_url,
                    )
                )
                remaining_budget -= len(repo_text)
            return formatted

        # Handle file match results
        if not results or "result" not in results or "FileMatches" not in results["result"]:
            return formatted

        if results["result"]["FileMatches"] is None:
            return formatted

        # Track total matches processed across all files
        total_matches_processed = 0

        for file_match in results["result"]["FileMatches"]:
            if total_matches_processed >= num:
                break
            if remaining_budget <= 0:
                break

            matches = []
            file_matches = file_match.get("Matches", [])
            if not file_matches:
                continue

            # Calculate how many matches we can process from this file
            remaining_matches = num - total_matches_processed
            matches_to_process = min(remaining_matches, len(file_matches))

            for match in file_matches[:matches_to_process]:
                if remaining_budget <= 0:
                    break

                # Combine fragments to get the full line
                full_line = ""
                for fragment in match["Fragments"]:
                    full_line += fragment["Pre"] + fragment["Match"] + fragment["Post"]

                # Create match with the full context
                full_text = []
                if match.get("Before"):
                    full_text.extend(match["Before"].strip().splitlines())
                full_text.append(full_line.strip())
                if match.get("After"):
                    full_text.extend(match["After"].strip().splitlines())

                # Truncate each line in the text for readability
                truncated_text = [self._truncate_line(line) for line in full_text]
                match_text = "\n".join(truncated_text).strip()
                if not match_text:
                    continue

                match_text = self._truncate_text(match_text, self.max_match_text_length)
                match_text = self._truncate_text(match_text, remaining_budget)
                if not match_text:
                    break

                matches.append(
                    Match(
                        line_number=match["LineNum"],
                        text=match_text,
                    )
                )
                remaining_budget -= len(match_text)

            if matches:  # Only add file to results if it has matches
                formatted.append(
                    FormattedResult(
                        filename=file_match["FileName"],
                        repository=file_match["Repo"],
                        matches=matches,
                        url=(
                            file_matches[0]["URL"].split("#L")[0]
                            if file_matches and "URL" in file_matches[0]
                            else None
                        ),
                    )
                )
                total_matches_processed += len(matches)

        return formatted
