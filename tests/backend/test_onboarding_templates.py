"""Onboarding import templates: column-contract parity + CSV/XLSX round-trip.

The wizard used to generate its own templates client-side from hardcoded header
strings that had drifted from every endpoint's real column contract — a manager
who downloaded a template, filled it in and uploaded it got a 422. These tests
pin the two together: the registry that renders the downloadable template is
asserted against the ``required_columns=[...]`` literals the import endpoints
actually validate with, read straight out of the router's AST.
"""
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.onboarding_templates import (  # noqa: E402
    HISTORICAL_FINANCIALS_FILE_GROUP,
    ONBOARDING_TEMPLATES,
    render_template_csv,
    render_template_xlsx,
    template_columns,
)
from utils.tabular_upload import (  # noqa: E402
    TABULAR_CONTENT_TYPES,
    looks_like_legacy_xls,
    looks_like_xlsx,
    parse_tabular_bytes,
    tabular_bytes_to_csv_bytes,
)

ROUTER = BACKEND / "routers" / "onboarding.py"


def _endpoint_required_columns() -> dict[str, list[str]]:
    """Map ``_parse_csv(<upload_arg>, required_columns=[...])`` → columns, from the AST.

    Reading the router's source rather than importing it keeps this test free of
    the router's DB/Mongo import chain.
    """
    tree = ast.parse(ROUTER.read_text())
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_parse_csv"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Name):
            continue
        upload_arg = node.args[0].id
        for kw in node.keywords:
            if kw.arg == "required_columns" and isinstance(kw.value, ast.List):
                found[upload_arg] = [
                    e.value for e in kw.value.elts if isinstance(e, ast.Constant)
                ]
    return found


# The multipart field name each template is uploaded under == the router's own
# parameter name for that UploadFile, which is what the AST scan keys on.
_FIELD_TO_TEMPLATE = {
    spec["field"]: key
    for key, spec in ONBOARDING_TEMPLATES.items()
    if spec["field"]
}


def test_every_endpoint_required_column_set_is_covered_by_a_template():
    """No import endpoint may demand a column no template offers."""
    endpoint_columns = _endpoint_required_columns()
    assert endpoint_columns, "AST scan found no _parse_csv(required_columns=...) calls"

    for field, required in endpoint_columns.items():
        template_type = _FIELD_TO_TEMPLATE.get(field)
        assert template_type, (
            f"Endpoint field '{field}' has required_columns={required} but no entry in "
            f"ONBOARDING_TEMPLATES — a manager has no template to fill in for it."
        )
        assert sorted(ONBOARDING_TEMPLATES[template_type]["required"]) == sorted(required), (
            f"Template '{template_type}' declares required={ONBOARDING_TEMPLATES[template_type]['required']} "
            f"but the endpoint enforces {required}. These must match exactly."
        )


def test_every_template_field_maps_to_a_real_endpoint_parameter():
    """No template may name a multipart field the router does not declare."""
    router_src = ROUTER.read_text()
    for key, spec in ONBOARDING_TEMPLATES.items():
        field = spec["field"]
        if field is None:
            continue  # lots is parsed client-side into JSON
        assert f"{field}: UploadFile = File(" in router_src, (
            f"Template '{key}' posts multipart field '{field}', which no endpoint in "
            f"routers/onboarding.py declares."
        )


def test_historical_financials_group_is_the_endpoints_full_file_set():
    """The five-file group must be exactly what the endpoint requires — no more, no less."""
    endpoint_columns = _endpoint_required_columns()
    group_fields = {ONBOARDING_TEMPLATES[t]["field"] for t in HISTORICAL_FINANCIALS_FILE_GROUP}
    endpoint_fields = {
        f for f in endpoint_columns
        if ONBOARDING_TEMPLATES.get(_FIELD_TO_TEMPLATE.get(f, ""), {}).get("endpoint", "")
        .endswith("import-historical-financials")
    }
    assert group_fields == endpoint_fields


@pytest.mark.parametrize("template_type", sorted(ONBOARDING_TEMPLATES))
def test_csv_template_round_trips_through_the_upload_parser(template_type):
    """A downloaded CSV template parses back to exactly its own columns."""
    headers, rows = parse_tabular_bytes(render_template_csv(template_type), filename="t.csv")
    assert headers == template_columns(template_type)
    assert len(rows) == 1
    for column in ONBOARDING_TEMPLATES[template_type]["required"]:
        assert rows[0][column] != "", f"{template_type}: example row leaves required '{column}' blank"


@pytest.mark.parametrize("template_type", sorted(ONBOARDING_TEMPLATES))
def test_xlsx_template_round_trips_through_the_upload_parser(template_type):
    """A downloaded Excel template parses back to exactly its own columns.

    This is the end-to-end guarantee for the XLSX path: download → (fill in) →
    upload → same columns, same values as the CSV route would have produced.
    """
    payload = render_template_xlsx(template_type)
    assert looks_like_xlsx(payload)
    headers, rows = parse_tabular_bytes(payload, filename="t.xlsx")
    assert headers == template_columns(template_type)
    assert len(rows) == 1

    csv_headers, csv_rows = parse_tabular_bytes(render_template_csv(template_type), filename="t.csv")
    assert headers == csv_headers
    assert rows == csv_rows, "CSV and XLSX templates must carry identical values"


def test_xlsx_transcodes_to_csv_bytes_for_the_financial_import_processors():
    csv_bytes = tabular_bytes_to_csv_bytes(render_template_xlsx("arrears"), filename="a.xlsx")
    assert csv_bytes.decode("utf-8").splitlines()[0] == ",".join(template_columns("arrears"))


def test_plain_csv_bytes_pass_through_the_transcoder_unchanged():
    raw = b"lot_number,amount\r\n1,\"1,234.00\"\r\n"
    assert tabular_bytes_to_csv_bytes(raw, filename="x.csv") == raw


def test_numeric_cells_do_not_gain_a_float_suffix():
    """``lot_number`` is a lookup key — an Excel 7 must not arrive as "7.0"."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["lot_number", "admin_arrears"])
    wb.active.append([7, 0])
    buf = io.BytesIO()
    wb.save(buf)

    _, rows = parse_tabular_bytes(buf.getvalue(), filename="lots.xlsx")
    assert rows[0]["lot_number"] == "7"


def test_legacy_xls_is_rejected_with_an_actionable_message():
    from fastapi import HTTPException

    fake_xls = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    assert looks_like_legacy_xls(fake_xls)
    with pytest.raises(HTTPException) as exc:
        parse_tabular_bytes(fake_xls, filename="old.xls")
    assert exc.value.status_code == 415
    assert ".xlsx" in exc.value.detail


def test_xlsx_mime_type_is_on_the_upload_allowlist():
    """The real .xlsx MIME used to be missing, so every Excel upload 415'd."""
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        in TABULAR_CONTENT_TYPES
    )


# ── Cross-stack guard: the wizard's multipart field names ─────────────────────
# The onboarding wizard posts these uploads itself, so a correct backend contract
# is not enough — the JSX must name the same fields. It previously posted every
# file as a single field called `file`, which no endpoint declares, so every
# historical upload 422'd. This reads the JSX directly rather than mocking it.

WIZARD_JSX = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "pages" / "dashboard" / "admin" / "OnboardingWizard.jsx"
)


def _wizard_upload_fields() -> set[str]:
    import re

    src = WIZARD_JSX.read_text()
    start = src.index("const UPLOAD_STEP_SPECS = {")
    end = src.index("const UPLOAD_STEPS_WITHOUT_ENDPOINT")
    return set(re.findall(r"field:\s*'([a-z_]+)'", src[start:end]))


@pytest.mark.skipif(not WIZARD_JSX.exists(), reason="frontend not present")
def test_wizard_posts_only_multipart_fields_the_router_declares():
    router_src = ROUTER.read_text()
    for field in _wizard_upload_fields():
        assert f"{field}: UploadFile = File(" in router_src, (
            f"OnboardingWizard.jsx posts multipart field '{field}', which no endpoint in "
            f"routers/onboarding.py declares — this upload would 422."
        )


@pytest.mark.skipif(not WIZARD_JSX.exists(), reason="frontend not present")
def test_wizard_does_not_post_the_generic_file_field_to_import_endpoints():
    """Regression guard for the original defect."""
    assert "'file'" not in _wizard_upload_fields()


@pytest.mark.skipif(not WIZARD_JSX.exists(), reason="frontend not present")
def test_wizard_template_types_all_exist_in_the_registry():
    import re

    src = WIZARD_JSX.read_text()
    for template_type in set(re.findall(r"templateType:\s*'([a-z_]+)'", src)):
        assert template_type in ONBOARDING_TEMPLATES, (
            f"OnboardingWizard.jsx offers a '{template_type}' template download, which the "
            f"backend registry does not serve — the download would 404."
        )
