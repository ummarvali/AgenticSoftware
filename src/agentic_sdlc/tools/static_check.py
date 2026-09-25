"""Static safety scan for generated code — standard library only (``ast``).

The Validator compiles and *runs* generated code, which proves it works; it does not
prove it is safe or hygienic. This scan adds a cheap, deterministic review pass that
flags the things a bank's code reviewer would refuse on sight:

* **dangerous calls** — ``eval``/``exec``/``compile``/``__import__``, ``os.system``,
  ``os.popen``, ``subprocess`` with ``shell=True``, ``pickle``/``marshal`` loads;
* **imports outside the standard library** — the generated service is required to be
  stdlib-only (no unpinned or outdated third-party packages can sneak in);
* **hard-coded secrets** — string literals that look like API keys or tokens;
* **stdlib shadowing** — a generated top-level module named like a standard-library
  module (e.g. ``unittest.py``) could subvert the test run itself.

Import aliases are resolved (``import subprocess as sp``, ``from os import system``),
and ``shell=True`` is flagged on any call.

Findings are ``high`` (fails the check) or ``low`` (reported only). It is a guardrail,
not a full linter/SAST: production would add ruff/bandit/pip-audit in CI.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_DANGEROUS_CALLS = {
    "eval": "high", "exec": "high", "compile": "low", "__import__": "high",
    "os.system": "high", "os.popen": "high", "os.execv": "high", "os.execvp": "high",
    "pickle.load": "high", "pickle.loads": "high", "marshal.load": "high", "marshal.loads": "high",
    "ctypes.CDLL": "high", "shutil.rmtree": "low",
}
_SECRET_RE = re.compile(r"(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,})")

# Names that are importable in every supported interpreter but not listed as stdlib.
_ALWAYS_OK = {"__future__", "typing_extensions"}


@dataclass
class Finding:
    path: str
    line: int
    severity: str   # "high" | "low"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.severity}] {self.rule}: {self.detail}"


def _call_name(node: ast.Call, aliases: dict[str, str] | None = None) -> str:
    """Dotted name of the callee with import aliases resolved: ``sp.run`` →
    ``subprocess.run``, ``system`` (from os) → ``os.system``, ``builtins.eval`` → ``eval``."""

    aliases = aliases or {}
    f = node.func
    if isinstance(f, ast.Name):
        name = aliases.get(f.id, f.id)
    elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        name = f"{aliases.get(f.value.id, f.value.id)}.{f.attr}"
    else:
        return ""
    return name.removeprefix("builtins.")


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names bound by imports to the fully qualified names they refer to."""

    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _is_stdlib(top: str) -> bool | None:
    """True/False, or None when the interpreter cannot tell (Python < 3.10)."""

    names = getattr(sys, "stdlib_module_names", None)
    if names is None:
        return None
    return top in names or top in _ALWAYS_OK or top in sys.builtin_module_names


def scan_file(path: Path, *, local_packages: set[str] | None = None) -> list[Finding]:
    """Scan one Python file. ``local_packages`` are top-level names generated alongside
    it (so ``from url_shortener import x`` is not mistaken for a third-party import)."""

    rel = str(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as exc:  # the compile check reports this separately
        return [Finding(rel, exc.lineno or 0, "high", "syntax", str(exc.msg))]

    findings: list[Finding] = []
    local = local_packages or set()
    aliases = _aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node, aliases)
            sev = _DANGEROUS_CALLS.get(name)
            if sev:
                findings.append(Finding(rel, node.lineno, sev, "dangerous-call", f"{name}()"))
            if any(kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                   for kw in node.keywords):
                findings.append(Finding(rel, node.lineno, "high", "dangerous-call",
                                        f"{name or 'call'}(shell=True)"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level:      # relative import → local
                continue
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                top = mod.split(".")[0]
                if not top or top in local:
                    continue
                ok = _is_stdlib(top)
                if ok is False:
                    findings.append(Finding(rel, node.lineno, "high", "non-stdlib-import",
                                            f"'{top}' is not in the standard library"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SECRET_RE.search(node.value):
                findings.append(Finding(rel, node.lineno, "high", "hardcoded-secret",
                                        "string literal looks like an API key/token"))
    return findings


def scan_tree(root: Path) -> list[Finding]:
    """Scan every .py file under ``root``; package names under ``root`` count as local."""

    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    local = {p.relative_to(root).parts[0].removesuffix(".py") for p in files}
    out: list[Finding] = []
    # Modules importable by top-level name during the test run: the project root and
    # the tests/ directory (unittest discovery puts it on sys.path).
    for f in files:
        parts = f.relative_to(root).parts
        if len(parts) == 1 or (len(parts) == 2 and parts[0] == "tests"):
            top = f.stem                      # top-level module: foo.py / tests/foo.py
        elif len(parts) == 2 and f.name == "__init__.py":
            top = parts[0]                    # top-level package: foo/__init__.py
        else:
            continue
        if _is_stdlib(top):
            out.append(Finding(str(f.relative_to(root)), 1, "high", "stdlib-shadowing",
                               f"'{top}' shadows a standard-library module"))
    for f in files:
        out += [Finding(str(f.relative_to(root)), x.line, x.severity, x.rule, x.detail)
                for x in scan_file(f, local_packages=local)]
    return out
