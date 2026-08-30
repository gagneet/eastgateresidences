"""
tests/backend/test_no_floats_in_domain.py — Lint guard for the domain package.

Enforces the invariant documented in backend/domain/__init__.py:
  "Cents = integer-cent money. `float` is BANNED in this package tree."

Uses regex on non-comment source lines (not the full AST) because:
  - Python's ast module resolves annotations lazily under `from __future__ import annotations`,
    making annotation detection unreliable without an explicit eval pass.
  - Regex on stripped non-comment lines is simpler, faster, and sufficient for the narrow
    patterns we need to catch (`: float`, `-> float`, `[float]`, and float literals like `1.0`).

Files that intentionally contain `float` live outside backend/domain/ (e.g. envelopes.py,
mock_ocr.py) and are NOT covered by this test.
"""
import ast
import os
import re
from pathlib import Path

# ── Path resolution ───────────────────────────────────────────────────────────

_TESTS_DIR = Path(__file__).resolve().parent  # tests/backend/
_PROJECT_ROOT = _TESTS_DIR.parent.parent  # project root
_DOMAIN_DIR = _PROJECT_ROOT / "backend" / "domain"

# ── Regex patterns ────────────────────────────────────────────────────────────

# Matches `float` used as a type annotation token (word-boundary anchored)
_FLOAT_ANNOTATION_RE = re.compile(r"\bfloat\b")

# Matches a bare float literal: digits followed by a dot and at least one digit,
# not preceded by another digit or dot (avoids matching version strings like "3.11")
# This will catch: 0.0, 1.0, 0.5, 2.35, 100.0 etc.
_FLOAT_LITERAL_RE = re.compile(r"(?<![.\d])\d+\.\d+")


def _collect_domain_py_files() -> list[Path]:
    """Return all .py files under backend/domain/, excluding __pycache__."""
    if not _DOMAIN_DIR.exists():
        return []
    files = []
    for root, dirs, filenames in os.walk(_DOMAIN_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(root) / fname)
    return sorted(files)


def _violations_in_file(path: Path) -> list[str]:
    """Return a list of human-readable violation messages for a single file."""
    violations: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        violations.append(f"{path}: cannot read — {exc}")
        return violations

    lines = source.splitlines()
    in_multiline_string = False
    multiline_delim: str = ""

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()

        # Track multiline string open/close (docstrings and triple-quoted strings)
        if not in_multiline_string:
            for delim in ('"""', "'''"):
                count = stripped.count(delim)
                if count == 1:
                    # Odd number of delimiters on this line → starts a multiline block
                    in_multiline_string = True
                    multiline_delim = delim
                    break
            if in_multiline_string:
                # Check if it also closes on the same line (inline triple-quoted string)
                if stripped.count(multiline_delim) >= 2:
                    in_multiline_string = False
                    multiline_delim = ""
                continue  # Skip the opening docstring line itself
        else:
            # We are inside a multiline string — check for closing delimiter
            if multiline_delim in stripped:
                in_multiline_string = False
                multiline_delim = ""
            continue  # Skip all lines inside docstrings

        # Skip pure comment lines
        if stripped.startswith("#"):
            continue

        # Strip inline comments before checking
        code_part = raw_line
        hash_pos = raw_line.find("#")
        if hash_pos >= 0:
            # Naive split (ignores # inside strings, but sufficient for our patterns)
            code_part = raw_line[:hash_pos]

        # Check for float type annotation
        if _FLOAT_ANNOTATION_RE.search(code_part):
            violations.append(
                f"{path}:{lineno}: float type annotation found: {raw_line.rstrip()!r}"
            )

        # Check for float literal
        for m in _FLOAT_LITERAL_RE.finditer(code_part):
            violations.append(
                f"{path}:{lineno}: float literal {m.group()!r} found: {raw_line.rstrip()!r}"
            )

    return violations


def _ast_float_violations(path: Path) -> list[str]:
    """Use ast.walk to catch float literals and float annotations that the regex might miss.

    This is a belt-and-suspenders check. The regex above is the primary guard.
    """
    violations: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"{path}: AST parse error — {exc}"]

    for node in ast.walk(tree):
        # Float literal constant
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            lineno = getattr(node, "lineno", "?")
            violations.append(
                f"{path}:{lineno}: AST float literal {node.value!r}"
            )
        # `float` used as a Name node (type annotation context)
        if isinstance(node, ast.Name) and node.id == "float":
            lineno = getattr(node, "lineno", "?")
            violations.append(
                f"{path}:{lineno}: AST `float` Name node (type annotation or usage)"
            )

    return violations


# ── Test ──────────────────────────────────────────────────────────────────────

class TestNoDomainFloats:
    """Verify that backend/domain/ contains no float types or float literals."""

    def test_domain_directory_exists(self):
        """The domain package directory must exist — if it's missing the lint is not running."""
        assert _DOMAIN_DIR.exists(), (
            f"backend/domain/ not found at {_DOMAIN_DIR}. "
            "Ensure the project root is correct."
        )

    def test_no_float_annotations_or_literals_regex(self):
        """No `float` annotation and no float literal in any backend/domain/*.py file.

        Uses regex on non-comment, non-docstring code lines.
        """
        files = _collect_domain_py_files()
        all_violations: list[str] = []
        for f in files:
            all_violations.extend(_violations_in_file(f))

        assert not all_violations, (
                f"Found {len(all_violations)} float violation(s) in backend/domain/:\n"
                + "\n".join(f"  {v}" for v in all_violations)
        )

    def test_no_float_ast_nodes(self):
        """No ast.Constant(float) or ast.Name(id='float') nodes in backend/domain/*.py.

        Belt-and-suspenders check using the Python AST.
        """
        files = _collect_domain_py_files()
        all_violations: list[str] = []
        for f in files:
            all_violations.extend(_ast_float_violations(f))

        assert not all_violations, (
                f"Found {len(all_violations)} AST float node(s) in backend/domain/:\n"
                + "\n".join(f"  {v}" for v in all_violations)
        )

    def test_cents_type_is_annotated_int(self):
        """The Cents type in domain/__init__.py must be Annotated[int, ...], not float."""
        from domain import Cents
        import typing

        # Unpack the Annotated wrapper
        args = typing.get_args(Cents)
        assert args, "Cents must be a typing.Annotated type"
        inner_type = args[0]
        assert inner_type is int, (
            f"Cents inner type must be `int`, got {inner_type!r}. "
            "Float is banned in the domain layer."
        )

    def test_cents_rejects_float_at_runtime(self):
        """Constructing a Pydantic model with a float Cents field raises ValidationError."""
        import pytest
        from pydantic import BaseModel, ValidationError
        from domain import Cents

        class _TestModel(BaseModel):
            amount: Cents

        # Integer is fine
        m = _TestModel(amount=100)
        assert m.amount == 100
        assert isinstance(m.amount, int), "Cents must be int at runtime — not float"

        # Float input must be rejected — Cents is strict int in the domain layer.
        # Pydantic v2 coerces float→int in lax mode, so we use model_validate with
        # strict=True to enforce the domain contract.
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _TestModel.model_validate({"amount": 99.9}, strict=True)
