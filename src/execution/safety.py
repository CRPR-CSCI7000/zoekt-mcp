import ast

_ALLOWED_IMPORTS = {
    "argparse",
    "asyncio",
    "json",
    "sys",
    "runtime.zoekt_tools",
}

_BANNED_IMPORT_PREFIXES = {
    "builtins",
    "ctypes",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "shlex",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
}

_BANNED_CALLS = {
    "compile",
    "eval",
    "exec",
    "input",
    "open",
    "__import__",
}


class SafetyError(ValueError):
    """Raised when script safety validation cannot run."""


def validate_custom_workflow_code(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax_error: {exc.msg} at line {exc.lineno}"]

    rejections: list[str] = []

    has_parse_args = False
    has_main = False
    has_main_guard = False
    has_run = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "parse_args":
            has_parse_args = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            has_main = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            has_run = True

        if isinstance(node, ast.If) and _is_name_main_guard(node.test):
            has_main_guard = True

        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
                _check_import(module_name, rejections)

        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            _check_import(module_name, rejections)

        if isinstance(node, ast.Call):
            call_name = _call_name(node)
            if call_name in _BANNED_CALLS:
                rejections.append(f"banned_call: {call_name}")

    has_legacy_entrypoint = has_parse_args and has_main and has_main_guard
    has_minimal_entrypoint = has_run
    if not has_minimal_entrypoint and not has_legacy_entrypoint:
        if not has_run:
            rejections.append("missing_required_entrypoint: run(args) or async run(args)")
        if not has_parse_args:
            rejections.append("missing_required_entrypoint: parse_args (legacy mode)")
        if not has_main:
            rejections.append("missing_required_entrypoint: main (legacy mode)")
        if not has_main_guard:
            rejections.append("missing_required_entrypoint: if __name__ == '__main__' (legacy mode)")

    seen: set[str] = set()
    unique_rejections: list[str] = []
    for rejection in rejections:
        if rejection not in seen:
            unique_rejections.append(rejection)
            seen.add(rejection)

    return unique_rejections


def validate_ephemeral_script(code: str) -> list[str]:
    return validate_custom_workflow_code(code)


def _check_import(module_name: str, rejections: list[str]) -> None:
    if not module_name:
        return

    if any(module_name == banned or module_name.startswith(f"{banned}.") for banned in _BANNED_IMPORT_PREFIXES):
        rejections.append(f"banned_import: {module_name}")
        return

    if module_name in _ALLOWED_IMPORTS:
        return

    if any(module_name.startswith(f"{allowed}.") for allowed in _ALLOWED_IMPORTS):
        return

    rejections.append(f"disallowed_import: {module_name}")


def _is_name_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False

    comparator = test.comparators[0]
    if isinstance(comparator, ast.Constant):
        return comparator.value == "__main__"
    return False


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None
