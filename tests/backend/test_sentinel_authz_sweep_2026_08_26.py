"""Regression tests for the 2026-08-26 application-wide authorisation sweep.

Five distinct defects, each pinned by the tests that would have caught it:

1. `POST /trust/v2/deft/webhook` accepted unauthenticated payment notifications.
2. `POST /work-orders/email/ingest` skipped its API key when unconfigured.
3. The four `/portfolio/buildings/{building_id}/onboarding*` routes were a BOLA.
4. `validate_go_live` answered about the caller's session building, not the path one.
5. bi / cutover_admin / finance_intelligence decided building capabilities on
   unhydrated (caller-inherited) claims — GAP-SEC-014 item 2.
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

BUILDING_ID = "13195"
OTHER_BUILDING_ID = "16244"


# ── 1. DEFT payment webhook ─────────────────────────────────────────────────

class TestDeftWebhookAuthentication:
    """The HMAC is the only authentication this endpoint has — mock mode included.

    Mock mode is a deliberate posture here: nothing is connected to a live financial
    institution, and `docs/architecture/transactions_accounting.md` RULE 2 makes "no
    real external API keys required" a design rule. That is honoured — `APP_ENV` does
    NOT override it.

    What it cannot mean is "accept unsigned requests". Mocking selects an OUTBOUND
    implementation; a webhook is INBOUND, so there is no provider to mock, only a
    caller to authenticate — and the handler writes real `trust_transactions_v2`
    receipts and marks real `trust_levy_schedules_v2` rows paid either way. The CRN it
    matches on is not a secret — _generate_deft_crn derives it deterministically from
    biller code, lot number and quarter with a Luhn check digit — so it is computable,
    not merely obtainable. Simulated payments go through POST /trust/v2/deft/simulate
    instead, which is authenticated as a user.
    """

    @staticmethod
    def _verify():
        from routers.trust_phase1 import verify_deft_webhook_signature

        return verify_deft_webhook_signature

    @pytest.mark.asyncio
    async def test_mock_mode_is_honoured_and_not_overridden_by_app_env(self, monkeypatch):
        """Mock mode is a product decision; production does not veto it.

        An earlier revision of this fix made APP_ENV=production ignore the flag. That
        was wrong: it would have broken the intended posture of running the financial
        integrations against mocks in production.

        Now resolved PER BUILDING via the financial_services_mock toggle, with the env
        var still able to force mock on for everyone — so this asserts through the
        async resolver rather than a bare env read.
        """
        from routers.trust_phase1 import deft_mock_mode_enabled

        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("MOCK_EXTERNAL_SERVICES", "true")
        assert await deft_mock_mode_enabled("13195") is True

    def test_mock_mode_still_requires_a_signature(self, monkeypatch):
        """The whole point of the correction: mock ≠ open.

        Previously mock mode was implemented by skipping the signature check, which
        simulated nothing and left the endpoint writing real receipts for anyone.
        """
        monkeypatch.setenv("MOCK_EXTERNAL_SERVICES", "true")
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("DEFT_WEBHOOK_SECRET", "local-dev-secret")

        with pytest.raises(HTTPException) as exc:
            self._verify()(b'{"crn":"123","amount_cents":50000}', None)
        assert exc.value.status_code == 401

    def test_unconfigured_secret_rejects_instead_of_skipping(self, monkeypatch):
        """The original bug: `if webhook_secret:` skipped verification when unset.

        DEFT_WEBHOOK_SECRET is absent from backend/.env, so every deployment was
        running with no verification on this path.
        """
        monkeypatch.delenv("DEFT_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("MOCK_EXTERNAL_SERVICES", "true")

        with pytest.raises(HTTPException) as exc:
            self._verify()(b'{"crn":"123","amount_cents":50000}', "anything")
        assert exc.value.status_code == 503

    def test_bad_signature_is_rejected(self, monkeypatch):
        monkeypatch.setenv("DEFT_WEBHOOK_SECRET", "s3cr3t")

        with pytest.raises(HTTPException) as exc:
            self._verify()(b'{"crn":"123"}', "0" * 64)
        assert exc.value.status_code == 401

    def test_a_signature_from_sign_deft_payload_passes(self, monkeypatch):
        """The mock emulator signs with the same helper the verifier checks."""
        from routers.trust_phase1 import sign_deft_payload

        monkeypatch.setenv("DEFT_WEBHOOK_SECRET", "local-dev-secret")
        body = b'{"crn":"123","amount_cents":50000}'
        self._verify()(body, sign_deft_payload(body))  # must not raise

    def test_verification_runs_before_anything_is_written(self):
        """Order matters: the verifier must not sit inside a swallowing try block.

        The ingestion path returns {"received": true} for almost any failure — DEFT
        retries on non-2xx and an unmatched CRN is a business outcome, not a delivery
        failure. A verifier called from inside that block would have its 401 swallowed
        into a 200 the same way.
        """
        from routers import trust_phase1

        source = inspect.getsource(trust_phase1.deft_webhook)
        assert "verify_deft_webhook_signature(" in source
        assert source.index("verify_deft_webhook_signature(") < source.index("try:")


class TestDeftSimulator:
    """Mock DEFT payments have a door of their own, and it is authenticated."""

    def test_simulator_requires_a_user_session_and_finance_capability(self):
        from routers import trust_phase1

        # The capability name is baked into the closure, so read the source: the
        # rendered signature only shows the dependency function object.
        source = inspect.getsource(trust_phase1.simulate_deft_payment)
        declaration = source[:source.index('"""')]
        assert 'require_feature("trust_accounting")' in declaration, "simulator is not feature-gated"
        assert 'require_capability("building.finance.manage"' in declaration, (
            "simulator has no building-scoped capability guard"
        )
        assert "_require_trust_manage(current_user)" in source

    def test_simulator_and_webhook_share_one_ingestion_path(self):
        """A simulated payment must exercise matching, dedup and posting for real.

        If the simulator had its own write path, testing against the mock would prove
        nothing about the real one.
        """
        from routers import trust_phase1

        for fn in (trust_phase1.deft_webhook, trust_phase1.simulate_deft_payment):
            assert "_process_deft_notification(" in inspect.getsource(fn), fn.__name__

    def test_simulator_is_refused_when_a_real_provider_is_configured(self):
        from routers import trust_phase1

        body = inspect.getsource(trust_phase1.simulate_deft_payment)
        # Resolved per building, so one building going live does not disable the
        # simulator for every other building still on mocks.
        assert "if not await deft_mock_mode_enabled(building_id):" in body
        assert "409" in body

    def test_simulated_rows_are_distinguishable_from_bank_originated_ones(self):
        from routers import trust_phase1

        assert 'origin="deft_simulator"' in inspect.getsource(trust_phase1.simulate_deft_payment)
        assert 'origin="deft_webhook"' in inspect.getsource(trust_phase1.deft_webhook)
        assert '"origin": origin,' in inspect.getsource(trust_phase1._process_deft_notification)

    def test_simulated_payment_body_is_bounded(self):
        """It is a request body an authenticated user can reach (finding 3)."""
        from models.trust_accounting import DeftSimulatedPayment

        with pytest.raises(Exception):
            DeftSimulatedPayment(transaction_id="t", crn="c" * 5_000, amount_cents=1)
        with pytest.raises(Exception):
            DeftSimulatedPayment(transaction_id="t", crn="c", amount_cents=0)
        ok = DeftSimulatedPayment(transaction_id="t", crn="c", amount_cents=5_000)
        assert ok.amount_cents == 5_000


# ── 2. Work-order email ingestion API key ───────────────────────────────────

def test_email_ingest_api_key_is_required_not_optional():
    """`if expected_key and x_api_key != expected_key` skipped the check when unset.

    EMAIL_INGEST_API_KEY is absent from backend/.env. The router-level
    `require_feature("work_orders")` dependency authenticates but does not
    authorise, so every logged-in user could post to a machine-to-machine endpoint.
    """
    from routers import work_orders

    source = inspect.getsource(work_orders.ingest_email)
    # Comments in this handler quote the old shape verbatim, so match on code only.
    code = "\n".join(l for l in source.splitlines() if not l.strip().startswith("#"))
    assert "if expected_key and" not in code, "fail-open key check reintroduced"
    assert "if not expected_key:" in source
    assert "503" in source, "must refuse when unconfigured, per the stripe_webhook contract"
    assert "hmac.compare_digest" in source, "key comparison must be constant-time"


# ── 3 & 4. Portfolio go-live onboarding ─────────────────────────────────────

def _request_for(building_id: str):
    from starlette.requests import Request as StarletteRequest

    request = StarletteRequest({
        "type": "http",
        "method": "GET",
        "path": f"/api/portfolio/buildings/{building_id}/onboarding",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })
    request.scope["path_params"] = {"building_id": building_id}
    return request


async def _verified_for(*building_ids):
    async def _hydrate(subject, scope, **_hints):
        return {**subject, "assigned_building_ids": list(building_ids), "governance_offices": []}

    return _hydrate


class TestPortfolioOnboardingBola:
    ROUTES = {
        "get_onboarding_status": "building.onboarding.view",
        "complete_onboarding_step": "building.onboarding.manage",
        "validate_go_live": "building.onboarding.view",
        "complete_onboarding": "building.onboarding.manage",
    }

    def test_every_onboarding_route_scopes_the_path_building(self):
        """The guard must read the PATH building, not the session's.

        These handlers query `db._db.building_onboarding_checklists` — raw Motor,
        bypassing TenantScopedDatabase — keyed on the caller-supplied `building_id`.
        A role-only `_require_manager()` therefore let any ec_member / strata_admin /
        strata_manager of ANY building read and mutate another building's checklist.
        """
        source = (BACKEND / "routers" / "portfolio.py").read_text()
        tree = ast.parse(source)
        seen = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in self.ROUTES:
                seen[node.name] = ast.unparse(node.args)

        assert set(seen) == set(self.ROUTES), f"routes missing: {set(self.ROUTES) - set(seen)}"
        for name, capability in self.ROUTES.items():
            assert "require_capability" in seen[name], f"{name} has no capability dependency"
            assert capability in seen[name], f"{name} must require {capability}"
            assert "scope_params={'building_id': 'building_id'}" in seen[name].replace('"', "'"), (
                f"{name} must scope on the path building_id, not the session building"
            )
            assert "_require_manager(current_user)" not in seen[name]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("capability", sorted(set(ROUTES.values())))
    async def test_manager_of_one_building_cannot_reach_another(self, capability):
        from services.capability_registry import require_capability

        dependency = require_capability(capability, scope_params={"building_id": "building_id"})
        manager = {"id": "u-1", "role": "strata_manager", "effective_role": "strata_manager",
                   "building_id": BUILDING_ID}

        with patch("services.authorisation_context.hydrate_authorisation_claims",
                   new=AsyncMock(side_effect=await _verified_for(BUILDING_ID))):
            assert await dependency(request=_request_for(BUILDING_ID), current_user=manager) is manager
            with pytest.raises(HTTPException) as exc:
                await dependency(request=_request_for(OTHER_BUILDING_ID), current_user=manager)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("capability", sorted(set(ROUTES.values())))
    async def test_owner_is_denied_outright(self, capability):
        from services.capability_registry import require_capability

        dependency = require_capability(capability, scope_params={"building_id": "building_id"})
        owner = {"id": "u-2", "role": "owner", "effective_role": "owner", "building_id": BUILDING_ID}

        with pytest.raises(HTTPException) as exc:
            await dependency(request=_request_for(BUILDING_ID), current_user=owner)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_go_live_checks_are_filtered_on_the_requested_building(self):
        """Checks 1-3 used the session building; check 4 counted the whole platform.

        `TenantScopedDatabase` injects the caller's SESSION building, so a super
        admin validating building B was shown building A's EC members, units and
        folders — and the active-user check, which queried the global `users`
        collection with no membership join at all, passed for every building
        unconditionally.
        """
        from routers.portfolio import validate_go_live

        seen: list[dict] = []

        def _counter(result):
            async def _count(query, *args, **kwargs):
                seen.append(query)
                return result
            return _count

        mock_db = MagicMock()
        mock_db._db = MagicMock()
        mock_db._db.ec_members.count_documents = _counter(3)
        mock_db._db.units.count_documents = _counter(87)
        mock_db._db.document_folders.count_documents = _counter(6)
        mock_db._db.memberships.distinct = AsyncMock(return_value=["u-1"])
        mock_db._db.users.count_documents = _counter(5)
        mock_db._db.building_onboarding_checklists.find_one = AsyncMock(
            return_value={"building_id": OTHER_BUILDING_ID, "steps": []}
        )

        with patch("routers.portfolio.db", mock_db):
            result = await validate_go_live(
                building_id=OTHER_BUILDING_ID,
                current_user={"id": "u-1", "role": "super_admin", "building_id": BUILDING_ID},
            )

        assert len(result["checks"]) == 5
        # Every count is filtered on the requested building; none leaks the session's.
        assert seen, "no queries were issued"
        for query in seen[:-1]:  # the users count is keyed on membership ids, checked below
            assert query.get("building_id") == OTHER_BUILDING_ID, query
        mock_db._db.memberships.distinct.assert_awaited_once_with(
            "user_id", {"building_id": OTHER_BUILDING_ID, "is_active": True}
        )
        assert "id" in seen[-1], "active-user check must be restricted to this building's members"


# ── 5. Unhydrated capability decisions (GAP-SEC-014 item 2) ─────────────────

class TestCapabilityHydrationAtHandlerCallSites:
    """bi / cutover_admin / finance_intelligence called the synchronous
    assert_capability(), which hydrates nothing.

    `_building_matches` then had only the two INHERITED claims to test —
    `building_id` / `current_building_id` — because the three verified ones
    (`building_ids`, `assigned_building_ids`, `managed_building_ids`) are written by
    hydration and were simply absent. The inherited pair is the user's stored
    `default_scheme_id` whenever the JWT names no building, i.e. a preference, not
    proof of a live assignment.
    """

    GUARDS = {
        "routers.bi": ["_require_manager", "_require_bi_manage", "_require_bi_cutover",
                       "_require_platform_bi", "_require_admin", "_require_super_admin"],
        "routers.cutover_admin": ["_assert_building_access"],
        "routers.finance_intelligence": ["_require_finance_view", "_require_finance_manage",
                                         "_require_generate", "_require_super_admin",
                                         "_require_plan_edit"],
    }

    @pytest.mark.parametrize("module_name", sorted(GUARDS))
    def test_guards_are_async_so_they_can_hydrate(self, module_name):
        import importlib

        module = importlib.import_module(module_name)
        for guard_name in self.GUARDS[module_name]:
            guard = getattr(module, guard_name)
            assert inspect.iscoroutinefunction(guard), f"{module_name}.{guard_name} is not async"

    @pytest.mark.parametrize("module_name", sorted(GUARDS))
    def test_no_bare_assert_capability_remains(self, module_name):
        """The sync entry point must not creep back into these three routers."""
        path = BACKEND / (module_name.replace(".", "/") + ".py")
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "assert_capability(" not in stripped or "assert_capability_hydrated(" in stripped, (
                f"{path}:{lineno} uses the non-hydrating assert_capability: {stripped}"
            )

    @pytest.mark.asyncio
    async def test_hydration_replaces_a_caller_asserted_building_claim(self):
        """The property the async form buys: self-asserted claims stop counting."""
        from services.capability_registry import assert_capability_hydrated

        liar = {
            "id": "u-3", "role": "strata_manager", "effective_role": "strata_manager",
            "building_id": "99999",
            "assigned_building_ids": [BUILDING_ID],  # never verified
        }
        with pytest.raises(HTTPException) as exc:
            await assert_capability_hydrated(liar, "building.bi.view", {"building_id": BUILDING_ID})
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_verified_claims_are_honoured(self):
        from services.capability_registry import assert_capability_hydrated

        manager = {"id": "u-4", "role": "strata_manager", "effective_role": "strata_manager"}
        with patch("services.authorisation_context.hydrate_authorisation_claims",
                   new=AsyncMock(side_effect=await _verified_for(BUILDING_ID))):
            decision = await assert_capability_hydrated(
                manager, "building.bi.view", {"building_id": BUILDING_ID}
            )
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_can_hydrated_does_not_raise_but_still_verifies(self):
        from services.capability_registry import can_hydrated

        liar = {"id": "u-5", "role": "strata_manager", "effective_role": "strata_manager",
                "building_id": "99999", "assigned_building_ids": [BUILDING_ID]}
        assert await can_hydrated(liar, "building.bi.view", {"building_id": BUILDING_ID}) is False


def test_onboarding_capabilities_are_registered_with_the_roles_the_routes_enforced():
    """The fix adds building scoping, not reach.

    `_require_manager` allowed ec_member, strata_admin, strata_manager and
    super_admin; the new capabilities must match that set exactly, or the change
    would be a silent permission grant or removal rather than a scoping fix.
    """
    from models.user import UserRole
    from services.capability_registry import CAPABILITY_REGISTRY

    expected = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER}
    for name in ("building.onboarding.view", "building.onboarding.manage"):
        definition = CAPABILITY_REGISTRY[name]
        assert definition.scope_type == "building"
        assert set(definition.roles) == expected, name


# ── 6. HTML injection in outbound email (audit finding 4, at the real sink) ──

class TestOutboundEmailHtmlEscaping:
    """Escaping belongs at the HTML sink, not at write time.

    The audit's finding 4 recommended escaping user text "before storing in the
    database". That would be wrong here: every one of these fields is also rendered
    by React, which escapes on output, so a stored `&amp;` shows the reader a literal
    `&amp;`. It is also incomplete — it protects only values written after the change.

    Outbound HTML email is the one sink in this application that interpolates these
    values into markup, so that is where the escaping goes. Several bodies already
    escaped some values (`safe_b_name`, `safe_b_addr`) while interpolating the
    sender's own `full_name` — a user-editable profile field — raw beside them.
    """

    SINKS = [
        ("routers/communication.py", ["safe_sender", "safe_sender_role"]),
        ("cron/cron_admin_auto_approve.py", ["_e_name", "_e_email", "_e_unit", "_e_user_name"]),
        ("cron/cron_expiration_check.py", ["html_lib.escape(str(full_name"]),
        ("cron/cron_suburb_radar.py", ["html_lib.escape(str(i.get('title')"]),
    ]

    @pytest.mark.parametrize("path,markers", SINKS, ids=[p for p, _ in SINKS])
    def test_sink_escapes_its_user_supplied_values(self, path, markers):
        source = (BACKEND / path).read_text()
        for marker in markers:
            assert marker in source, f"{path} lost the escaping for {marker}"

    def test_no_unescaped_user_field_reaches_an_html_body(self):
        """The audit, as a scan — over the wired code only.

        routers/bookings.py is excluded: CLAUDE.md documents it as one of the six
        unwired duplicate routers (no `from routers.bookings import` in server.py),
        pending F-011 Phase B deletion. Its paths are served inline by server.py.
        """
        safe = re.compile(r"safe_|_escaped|html_lib\.escape|nh3\.clean|escape\(|_e_|_q_")
        htmlish = re.compile(r"<(div|p|h1|h2|h3|table|td|tr|span|a|strong|body|br|li)\b", re.I)
        risky = re.compile(
            r"current_user\[|current_user\.get|\bfull_name\b|\bunit_number\b|"
            r"\.get\(['\"](name|title|subject|message|description|notes|comment|body|content|address|email)['\"]"
        )
        # Escaped by rebinding the parameter itself, which the expression-level scan
        # below cannot see.
        allowed = {("cron/cron_expiration_check.py", "full_name"),
                   ("cron/cron_expiration_check.py", "unit_number")}

        offenders = []
        roots = [BACKEND / "server.py", *sorted((BACKEND / "routers").glob("*.py")),
                 *sorted((BACKEND / "services").glob("*.py")), *sorted((BACKEND / "cron").glob("*.py"))]
        for path in roots:
            rel = str(path.relative_to(BACKEND))
            if rel == "routers/bookings.py":
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.JoinedStr) or not htmlish.search(ast.unparse(node)):
                    continue
                for value in node.values:
                    if not isinstance(value, ast.FormattedValue):
                        continue
                    expr = ast.unparse(value.value)
                    if safe.search(expr) or (rel, expr) in allowed:
                        continue
                    if risky.search(expr):
                        offenders.append(f"{rel}:{node.lineno}: {expr}")
        assert not offenders, "unescaped user data in an HTML body:\n  " + "\n  ".join(offenders)

    def test_review_link_uses_url_quoting_not_html_escaping(self):
        """`?search={name}` needs quote(), not html.escape().

        html.escape leaves `&` and `=` intact, so a crafted display name could graft
        extra query parameters onto the admin review link.
        """
        source = (BACKEND / "server.py").read_text()
        assert "_q_target_name = quote(" in source
        assert "?tab=owners&amp;search={_q_target_name}" in source
        assert "search={_target_name}" not in source


# ── 7. Frontend findings the audit raised that are NOT defects ──────────────

class TestFrontendRawHtmlSinksAreAlreadySafe:
    """Audit finding 5 named two components; both were already correct.

    Pinned rather than "fixed", because the plausible fix breaks things: running
    mermaid's SVG through DOMPurify strips `<style>` and `foreignObject`, which is
    most of a rendered diagram.
    """

    FRONTEND = BACKEND.parent / "frontend" / "src"

    def test_structured_data_escapes_json_ld_for_a_script_tag(self):
        """JSON-LD in a <script> needs `<`, `>`, `&` escaped, not HTML sanitising.

        That is exactly what safeJsonLd does, and it is what prevents a `</script>`
        breakout. DOMPurify would be the wrong tool: the content is JSON, not HTML.
        """
        source = (self.FRONTEND / "components/shared/StructuredData.tsx").read_text()
        assert "function safeJsonLd" in source
        for escaped in ("\\\\u003c", "\\\\u003e", "\\\\u0026"):
            assert escaped in source, f"safeJsonLd no longer escapes {escaped}"
        assert "__html: safeJsonLd(" in source
        # One definition plus one call per dangerouslySetInnerHTML: every raw-HTML
        # sink in this file goes through the escaper.
        assert source.count("safeJsonLd(") == source.count("dangerouslySetInnerHTML") + 1, (
            "a raw interpolation was added beside the escaped ones"
        )

    def test_mindmap_viewer_renders_mermaid_in_strict_mode(self):
        """The SVG is mermaid's own output, from an allowlisted first-party doc.

        `securityLevel: "strict"` makes mermaid sanitise labels itself; the slug is
        allowlisted against mindmapDocs, the href is a static path under
        /tech-docs/mindmap/, and the page is super_admin-only.
        """
        viewer = (self.FRONTEND / "components/tech-docs/MindmapDocViewer.tsx").read_text()
        assert 'securityLevel: "strict"' in viewer
        assert "mermaid.render(" in viewer

        page = (self.FRONTEND / "app/tech-docs/mindmap-view/[slug]/page.tsx").read_text()
        assert "SLUG_MAP" in page and "notFound()" in page, "slug allowlist removed"
        assert "isAdmin()" in page, "super_admin gate removed"

    def test_legal_pages_escape_every_interpolated_line(self):
        """The only unescaped strings are this component's own class attributes."""
        for name in ("TermsOfUsePage.jsx", "PrivacyPolicyPage.jsx"):
            source = (self.FRONTEND / "pages/public" / name).read_text()
            assert "const escape = (text) => text" in source, name
            for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#039;"):
                assert entity in source, f"{name} dropped the {entity} replacement"
            assert "${line}" not in source, f"{name} interpolates a raw line"


# ── 8. Public endpoints resolving an unverified building_id ─────────────────

class TestPublicEndpointBuildingHeader:
    """`get_building_or_400` takes `X-Building-ID` / `?building_id=` on trust.

    That is correct for a public endpoint — there is no session to resolve one from —
    but it means an anonymous caller chooses which building's rows the handler reads.
    Any such endpoint must therefore return only data that is genuinely public for
    EVERY building, since ids are short numeric plan numbers and trivially enumerated.
    """

    @pytest.mark.asyncio
    async def test_ec_member_contact_details_are_masked_for_anonymous_callers(self):
        import server

        rows = [{
            "id": "ec-1", "name": "A Chairperson", "position": "Chairperson",
            "email": "chair@example.test", "phone": "0400 000 000",
            "order": 1, "created_at": "2026-01-01T00:00:00Z",
        }]

        def _cursor():
            cursor = MagicMock()
            cursor.sort = MagicMock(return_value=cursor)
            cursor.to_list = AsyncMock(return_value=[dict(r) for r in rows])
            return cursor

        mock_db = MagicMock()
        mock_db.ec_members.find = MagicMock(side_effect=lambda *a, **k: _cursor())

        with patch("server.db", mock_db):
            anonymous = await server.get_ec_members(current_user=None, building_id=BUILDING_ID)
            resident = await server.get_ec_members(
                current_user={"id": "u-1", "role": "owner", "is_approved": True},
                building_id=BUILDING_ID,
            )

        # Public listing keeps what the marketing HomePage renders...
        assert anonymous[0].name == "A Chairperson"
        assert anonymous[0].position == "Chairperson"
        # ...and drops the direct contact details.
        assert anonymous[0].email is None
        assert anonymous[0].phone is None
        # A signed-in resident still gets them, for AboutPage's mailto:/tel: links.
        assert resident[0].email == "chair@example.test"
        assert resident[0].phone == "0400 000 000"

    def test_units_all_returns_counts_not_occupant_identities(self):
        """The sibling endpoint on the same unverified header — already correct."""
        import server

        source = inspect.getsource(server.get_all_units_with_occupants)
        assert '"unit_number": 1, "role_at_unit": 1' in source, (
            "the user_units projection widened; it must not select names or emails"
        )
        for leaked in ("full_name", "email"):
            assert leaked not in source, f"{leaked} reached a publicly-resolvable endpoint"
