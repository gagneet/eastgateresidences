import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

SCAN_ROOTS = (
    FRONTEND_SRC / "app" / "(app)",
    FRONTEND_SRC / "pages" / "dashboard" / "requests",
)

SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
RESOLVE_SUFFIXES = (*SOURCE_SUFFIXES, ".json")
LOCAL_IMPORT = re.compile(
    r"""(?:from\s+|import\s*\(|require\()\s*["'](?P<specifier>(?:@/|\./|\../)[^"']+)["']"""
)


def _candidate_paths(module_path: Path) -> list[Path]:
    candidates = [module_path]
    candidates.extend(module_path.with_suffix(suffix) for suffix in RESOLVE_SUFFIXES)
    candidates.extend(module_path / f"index{suffix}" for suffix in RESOLVE_SUFFIXES)
    return candidates


def _resolve_local_import(importer: Path, specifier: str) -> bool:
    if specifier.startswith("@/"):
        module_path = FRONTEND_SRC / specifier[2:]
    else:
        module_path = importer.parent / specifier

    # Keep this resolver intentionally narrow: it mirrors local file/module
    # imports that Turbopack resolves during the App Router production build.
    return any(path.exists() for path in _candidate_paths(module_path))


def test_frontend_local_imports_used_by_app_routes_resolve():
    unresolved: list[str] = []

    for root in SCAN_ROOTS:
        for source_file in root.rglob("*"):
            if source_file.suffix not in SOURCE_SUFFIXES:
                continue

            source = source_file.read_text(encoding="utf-8")
            for match in LOCAL_IMPORT.finditer(source):
                specifier = match.group("specifier")
                if not _resolve_local_import(source_file, specifier):
                    unresolved.append(f"{source_file.relative_to(REPO_ROOT)} -> {specifier}")

    assert unresolved == []
