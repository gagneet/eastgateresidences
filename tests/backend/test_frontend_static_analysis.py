"""
Static analysis tests — Frontend undefined-reference detection.

Catches "X is not defined" ReferenceErrors before deployment by scanning
every JSX/TSX file in frontend/src/ and verifying that:

  1. Every `icon: Identifier` value in object literals is in scope
  2. Every `<Identifier` JSX component is in scope

"In scope" means: imported, locally defined, or a known global.

False positives avoided by:
  - Filtering out TypeScript type annotations (names ending in Props, Type, Dir…)
  - Filtering out TypeScript built-in types (HTMLElement, NodeJS, Metadata…)
  - Handling TypeScript `type Foo = …` and `interface Foo` definitions
  - Handling TypeScript-annotated consts (`const Foo: SomeType = …`)
  - Treating `Icon` as a universal prop-alias convention (never a missing import)

Run:
    cd backend && python -m pytest tests/test_frontend_static_analysis.py -v
"""

import re
from pathlib import Path

import pytest

FRONTEND_SRC = Path(__file__).parent.parent.parent / "frontend" / "src"

# ── Globals that never need an import ─────────────────────────────────────

_REACT_GLOBALS = {
    "React", "Fragment", "Suspense", "StrictMode", "Component", "PureComponent",
    "Children", "createElement", "cloneElement",
}

_JS_GLOBALS = {
    "Object", "Array", "Promise", "Error", "TypeError", "RangeError", "SyntaxError",
    "Date", "Math", "JSON", "Map", "Set", "WeakMap", "WeakSet", "Symbol",
    "Boolean", "Number", "String", "BigInt",
    "console", "window", "document", "navigator", "location", "history",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval", "requestAnimationFrame",
    "fetch", "URL", "URLSearchParams", "FormData", "Blob", "File", "FileReader",
    "process", "Buffer", "global", "globalThis",
    "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent",
    "Infinity", "NaN",
}

# Next.js / framework injected values
_FRAMEWORK_GLOBALS = {
    "Image", "Link", "Script", "Head",  # next/
    "NextResponse", "NextRequest",
    "Inter", "Manrope",  # next/font
    "useRouter", "usePathname", "useSearchParams", "useParams",
}

# Common prop-alias convention: `function Card({ icon: Icon })` → Icon is always
# a locally bound destructured prop, never a missing import.
_PROP_ALIAS_CONVENTION = {"Icon"}

_ALL_GLOBALS = _REACT_GLOBALS | _JS_GLOBALS | _FRAMEWORK_GLOBALS | _PROP_ALIAS_CONVENTION

# ── TypeScript-specific exclusions ─────────────────────────────────────────
# Identifiers with these suffixes are TypeScript type annotations, not JSX components.
_TS_TYPE_SUFFIXES = (
    "Props", "Type", "State", "Config", "Options", "Result", "Data",
    "Args", "Params", "Response", "Request", "Ref", "Refs",
    "Event", "Handler", "Schema", "Model", "Values", "Context",
    "Action", "Store", "Dir", "Field", "Mode", "Summary",
    "Distribution", "Projection", "Payload", "Record", "Selection",
)

# TypeScript built-in and third-party type identifiers
_TS_BUILTINS = {
    # Single-letter generic type parameters
    "T", "P", "K", "V", "E", "R", "S", "C", "N", "U",
    # Next.js types
    "Metadata",
    # Third-party types
    "UUID", "NodeJS",
    # DOM / Web API types
    "HTMLElement", "HTMLInputElement", "HTMLDivElement", "HTMLButtonElement",
    "HTMLFormElement", "HTMLAnchorElement", "HTMLImageElement", "HTMLTextAreaElement",
    "HTMLSelectElement", "HTMLSpanElement", "HTMLTableElement",
    "SVGElement", "SVGSVGElement",
    "MouseEvent", "KeyboardEvent", "ChangeEvent", "FormEvent", "InputEvent",
    "DragEvent", "TouchEvent", "WheelEvent", "FocusEvent", "PointerEvent",
    "EventTarget", "EventListener",
    # Utility types
    "Partial", "Required", "Readonly", "Record", "Pick", "Omit",
    "Exclude", "Extract", "NonNullable", "ReturnType", "InstanceType",
    "Parameters", "ConstructorParameters",
}


def _is_ts_type(name: str) -> bool:
    """Return True if the identifier is a TypeScript type annotation, not a JSX component."""
    if name in _TS_BUILTINS:
        return True
    # Single uppercase letter (T, P, K …)
    if len(name) == 1 and name.isupper():
        return True
    # Names ending with known TypeScript-type suffixes
    if any(name.endswith(suffix) for suffix in _TS_TYPE_SUFFIXES):
        return True
    return False


# ── File walking ───────────────────────────────────────────────────────────

def _read_files(extensions=("*.jsx", "*.tsx")):
    files = []
    for ext in extensions:
        files.extend(FRONTEND_SRC.rglob(ext))
    return files


# ── Import extraction ──────────────────────────────────────────────────────

def _extract_imports(content: str) -> set:
    """Return all identifiers brought into scope via import statements."""
    imported = set()

    def _add_named_imports(block: str) -> None:
        for raw in re.split(r"[,\n]", block):
            token = raw.strip()
            # handle 'Foo as Bar' — the local name is the last word
            name = re.split(r"\bas\b", token)[-1].strip()
            if re.match(r'^[A-Za-z_$]', name):
                imported.add(name)

    # Named imports — import { Foo, Bar as Baz } from '…'
    for block in re.findall(r'import\s*\{([^}]+)\}\s*from\s*[\'"]', content):
        _add_named_imports(block)

    # Default imports — import Foo from '…'
    for name in re.findall(
            r'import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s*[\'"]', content
    ):
        imported.add(name)

    # Combined default + named imports — import Foo, { Bar, Baz } from '…'
    # The simple default-import regex above misses this pattern because ',' follows
    # the default name instead of whitespace + 'from'.
    for name in re.findall(
            r'import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*,\s*\{', content
    ):
        imported.add(name)
    for block in re.findall(
            r'import\s+[A-Za-z_$][A-Za-z0-9_$]*\s*,\s*\{([^}]+)\}\s*from\s*[\'"]', content
    ):
        _add_named_imports(block)

    # Namespace imports — import * as Foo from '…'
    for name in re.findall(
            r'import\s+\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s*[\'"]', content
    ):
        imported.add(name)

    return imported


# ── Local definition extraction ────────────────────────────────────────────

def _extract_local_definitions(content: str) -> set:
    """
    Return identifiers defined locally in the file.

    Handles:
      - const/let/var (with or without TypeScript type annotation)
      - function declarations
      - class declarations
      - TypeScript `type Foo = …` aliases
      - TypeScript `interface Foo { … }` declarations
      - export variants of all the above
    """
    defined = set()

    patterns = [
        # const/let/var — with optional TypeScript `: Type` annotation
        r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[=:({]',
        r'\blet\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[=:;]',
        r'\bvar\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[=:;]',
        # function / class
        r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[({]',
        r'\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)\b',
        # TypeScript type aliases and interfaces
        r'\btype\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[=<]',
        r'\binterface\s+([A-Za-z_$][A-Za-z0-9_$]*)\b',
        r'\benum\s+([A-Za-z_$][A-Za-z0-9_$]*)\b',
        # export prefixed variants
        r'\bexport\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+'
        r'([A-Za-z_$][A-Za-z0-9_$]*)',
    ]

    for pat in patterns:
        for m in re.finditer(pat, content):
            defined.add(m.group(1))

    # Dynamic imports: `const { default: Foo, Bar } = await import('...')`
    # Capture aliases after `default:` and plain destructured names.
    for m in re.finditer(r'await\s+import\s*\([^)]+\)', content):
        block = content[:m.start()]
        brace_start = block.rfind('{')
        if brace_start != -1:
            destructure = block[brace_start:]
            # `default: Name` alias
            for dm in re.finditer(r'\bdefault\s*:\s*([A-Za-z_$][A-Za-z0-9_$]*)', destructure):
                defined.add(dm.group(1))
            # plain `{ Name }` (PascalCase only to avoid noise)
            for dm in re.finditer(r'\b([A-Z][A-Za-z0-9]*)\s*[},]', destructure):
                defined.add(dm.group(1))

    return defined


# ── Reference extraction ───────────────────────────────────────────────────

def _extract_icon_key_refs(content: str) -> set:
    """
    Identifiers used as   icon: Identifier   in object literals (nav/menu arrays).

    Skips destructured prop aliases like `{ icon: Icon }` in function parameters
    by filtering out `Icon` (handled via _ALL_GLOBALS) and TypeScript types.
    """
    refs = set()
    for m in re.finditer(r'\bicon\s*:\s*([A-Z][A-Za-z0-9]*)\b', content):
        name = m.group(1)
        if not _is_ts_type(name):
            refs.add(name)
    return refs


def _extract_jsx_component_refs(content: str) -> set:
    """
    Uppercase identifiers used as JSX opening tags: <Identifier followed by
    whitespace, `/`, or `.` (but NOT `>` which is a TypeScript generic closing).

    This avoids matching TypeScript generic annotations like:
        React.FC<MyProps>   useState<ViewMode>   Array<LotSummary>

    Only single-word root identifiers are captured (no dots — namespaced
    components like Toast.Root are fine as long as Toast is imported).
    """
    refs = set()
    # Match <Upper followed by whitespace, dot, or self-close slash — NOT `>`
    for m in re.finditer(r'<([A-Z][A-Za-z0-9]*)[\s./]', content):
        name = m.group(1)
        if not _is_ts_type(name):
            refs.add(name)
    return refs


# ── Scope check ────────────────────────────────────────────────────────────

def _in_scope(name: str, imported: set, defined: set) -> bool:
    return name in imported or name in defined or name in _ALL_GLOBALS


# ── Violation collector ────────────────────────────────────────────────────

def _collect_violations():
    """
    Walk every JSX/TSX file and return a list of
    (relative_path, identifier, pattern_type) for every undefined reference.
    """
    if not FRONTEND_SRC.exists():
        return []

    violations = []

    for filepath in _read_files():
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            continue

        imported = _extract_imports(content)
        defined = _extract_local_definitions(content)
        rel = str(filepath.relative_to(FRONTEND_SRC.parent.parent))

        # ── icon: X ────────────────────────────────────────────────────
        for name in _extract_icon_key_refs(content):
            if not _in_scope(name, imported, defined):
                violations.append((rel, name, "icon:"))

        # ── <X (JSX component) ─────────────────────────────────────────
        for name in _extract_jsx_component_refs(content):
            if not _in_scope(name, imported, defined):
                violations.append((rel, name, "<Component>"))

    return violations


# Pre-compute once — all tests share the same scan result
_VIOLATIONS = _collect_violations()


# ── Tests ──────────────────────────────────────────────────────────────────

class TestFrontendStaticAnalysis:
    """
    Generic static analysis: every component or icon identifier used in JSX
    files must be in scope (imported or locally defined).

    Catches the class of bug where a component/icon is USED but its import
    line is omitted — e.g. the 'Dog is not defined' crash in DashboardLayout.
    """

    def test_frontend_src_directory_exists(self):
        """Sanity check — frontend/src/ must be present for the scan to be meaningful."""
        assert FRONTEND_SRC.exists(), (
            f"frontend/src not found at {FRONTEND_SRC}. "
            "Run tests from project root or check FRONTEND_SRC path."
        )

    def test_jsx_tsx_files_found(self):
        """At least some JSX/TSX source files must exist."""
        files = _read_files()
        assert len(files) > 10, f"Expected >10 JSX/TSX files, found {len(files)}"

    def test_no_undefined_icon_references(self):
        """
        No file may use  icon: X  (in nav/menu arrays) where X is not in scope.

        This is the exact pattern that caused 'Dog is not defined':
        DashboardLayout.jsx used  icon: Dog  without importing Dog.
        """
        icon_violations = [(f, n) for f, n, t in _VIOLATIONS if t == "icon:"]
        if icon_violations:
            lines = [f"  {f}: `icon: {n}` — not imported" for f, n in icon_violations]
            pytest.fail(
                f"Found {len(icon_violations)} undefined icon reference(s):\n"
                + "\n".join(lines)
                + "\n\nFix: add each identifier to the import statement "
                  "at the top of the file."
            )

    def test_no_undefined_jsx_component_references(self):
        """
        No file may use  <Component  where Component is not in scope.

        Catches cases like <Dog ... /> or <MyWidget ... /> where the import
        was omitted. TypeScript type annotations (e.g. React.FC<Props>) are
        excluded to avoid false positives.
        """
        jsx_violations = [(f, n) for f, n, t in _VIOLATIONS if t == "<Component>"]
        if jsx_violations:
            lines = [f"  {f}: `<{n}>` — not imported" for f, n in jsx_violations]
            pytest.fail(
                f"Found {len(jsx_violations)} undefined JSX component reference(s):\n"
                + "\n".join(lines)
                + "\n\nFix: add each component to the import statement "
                  "at the top of the file."
            )

    def test_dashboard_layout_imports_all_icon_usages(self):
        """
        Specific regression guard for DashboardLayout.tsx — the file where
        'Dog is not defined' first occurred.

        Every icon used in nav arrays must be imported.
        """
        layout = FRONTEND_SRC / "components" / "layout" / "DashboardLayout.tsx"
        if not layout.exists():
            pytest.skip("DashboardLayout.tsx not found")

        content = layout.read_text(encoding="utf-8")
        imported = _extract_imports(content)
        defined = _extract_local_definitions(content)

        icon_refs = _extract_icon_key_refs(content)
        missing = sorted(
            n for n in icon_refs if not _in_scope(n, imported, defined)
        )

        assert not missing, (
                "DashboardLayout.tsx uses these icon identifiers without importing them:\n"
                + "\n".join(f"  icon: {n}" for n in missing)
        )

    def test_no_total_violations(self):
        """
        Summary assertion — zero undefined references across all JSX/TSX files.
        This single test gives a clear pass/fail in CI.
        """
        if not _VIOLATIONS:
            return

        summary: dict[str, list[str]] = {}
        for f, n, t in _VIOLATIONS:
            summary.setdefault(f, []).append(f"{t} {n}")

        lines = [
            f"  {f}:\n" + "\n".join(f"    {u}" for u in usages)
            for f, usages in summary.items()
        ]
        pytest.fail(
            f"{len(_VIOLATIONS)} undefined reference(s) across "
            f"{len(summary)} file(s):\n" + "\n".join(lines)
        )

    def test_extract_imports_handles_combined_default_and_named_imports(self):
        content = "import Foo, { Bar as Baz, Qux } from '@/components/example'"
        imported = _extract_imports(content)
        assert {"Foo", "Baz", "Qux"}.issubset(imported)
