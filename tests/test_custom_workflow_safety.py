from execution.safety import validate_custom_workflow_code


def test_allows_from_runtime_import_zoekt_tools() -> None:
    code = """
from runtime import zoekt_tools

def run(args):
    return zoekt_tools.list_repos()
"""
    assert validate_custom_workflow_code(code) == []


def test_allows_runtime_zoekt_tools_import() -> None:
    code = """
import runtime.zoekt_tools as zoekt_tools

def run(args):
    return zoekt_tools.list_repos()
"""
    assert validate_custom_workflow_code(code) == []


def test_rejects_non_zoekt_tools_runtime_from_import() -> None:
    code = """
from runtime import dangerous

def run(args):
    return dangerous
"""
    assert validate_custom_workflow_code(code) == ["disallowed_import: runtime.dangerous"]
