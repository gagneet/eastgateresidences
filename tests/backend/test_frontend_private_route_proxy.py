import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROUTE_ROOT = REPO_ROOT / "frontend" / "src" / "app" / "(app)"
PROXY_FILE = REPO_ROOT / "frontend" / "src" / "proxy.ts"


def _array_literals_from_proxy(array_name: str) -> set[str]:
    proxy_source = PROXY_FILE.read_text(encoding="utf-8")
    match = re.search(
        rf"const {array_name} = \[(?P<body>.*?)\] as const",
        proxy_source,
        flags=re.DOTALL,
    )
    assert match, f"{array_name} was not found in frontend/src/proxy.ts"
    return set(re.findall(r"""["']([^"']+)["']""", match.group("body")))


def _private_route_roots_from_app_group() -> set[str]:
    roots: set[str] = set()

    for page_file in APP_ROUTE_ROOT.rglob("page.tsx"):
        route_dir = page_file.relative_to(APP_ROUTE_ROOT).parent
        if route_dir == Path("."):
            continue

        # The first URL segment is the middleware protection boundary. Dynamic
        # child routes inherit the same private namespace from their root.
        roots.add(f"/{route_dir.parts[0]}")

    return roots


def test_private_app_route_roots_are_protected_by_proxy():
    protected_roots = _array_literals_from_proxy("PRIVATE_APP_PATHS")
    expected_roots = _private_route_roots_from_app_group() | {"/dashboard"}

    assert sorted(expected_roots - protected_roots) == []


def test_public_browser_roots_are_not_registered_as_private_app_paths():
    protected_roots = _array_literals_from_proxy("PRIVATE_APP_PATHS")

    assert protected_roots.isdisjoint({
        "/",
        "/about",
        "/blog",
        "/emergency-services",
        "/forgot-password",
        "/login",
        "/marketplace",
        "/privacy",
        "/register",
        "/terms",
    })
