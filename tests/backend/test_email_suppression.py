"""Outgoing-email kill switch.

The switch exists so an operator can guarantee no mail leaves for a building. Every test
here is about that guarantee holding — including on the paths that do NOT go through
utils.email.send_email_async.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture
def sup(monkeypatch):
    """Fresh module with a clean environment — the flags are read via os.getenv."""
    for var in ("EMAIL_SEND_DISABLED_ALL", "EMAIL_SEND_DISABLED_BUILDING_IDS",
                "EMAIL_ALLOW_UNRESOLVED_BUILDING", "EMAIL_ALLOWED_DOMAINS"):
        monkeypatch.delenv(var, raising=False)
    import utils.email_suppression as m
    return importlib.reload(m)


def _toggle(value: bool):
    """Patch the per-building toggle resolution to a fixed answer."""
    return patch("utils.email_suppression._toggle_allows_email", AsyncMock(return_value=value))


class TestBuildingToggle:
    @pytest.mark.asyncio
    async def test_toggle_off_suppresses_that_building(self, sup):
        with _toggle(False):
            blocked, reason = await sup.check_email_suppressed("owner@example.com", "13195")
        assert blocked is True
        assert "email_notifications_enabled" in reason
        assert "13195" in reason

    @pytest.mark.asyncio
    async def test_toggle_on_allows(self, sup):
        with _toggle(True):
            blocked, _ = await sup.check_email_suppressed("owner@example.com", "13195")
        assert blocked is False

    @pytest.mark.asyncio
    async def test_toggle_is_resolved_per_building_not_globally(self, sup):
        """One muted building must not mute another — the whole point of a per-building switch."""
        async def per_building(building_id):
            return building_id != "13195"

        with patch("utils.email_suppression._toggle_allows_email", AsyncMock(side_effect=per_building)):
            muted, _ = await sup.check_email_suppressed("a@example.com", "13195")
            other, _ = await sup.check_email_suppressed("b@example.com", "UPDEMO5")
        assert muted is True
        assert other is False

    @pytest.mark.asyncio
    async def test_toggle_failure_defaults_to_allowing(self, sup):
        """A toggle store failure must not silently stop a building's mail.

        The env blocklist is the mechanism for a deliberate unconditional stop; an
        infrastructure blip is not, and mail that should have gone out is not recoverable
        by re-running anything.
        """
        import db_postgres.repos.config_repo as repo
        with patch.object(repo, "resolve_feature_toggle", AsyncMock(side_effect=RuntimeError("pg down"))):
            assert await sup._toggle_allows_email("13195") is True


class TestEnvBackstop:
    @pytest.mark.asyncio
    async def test_blocklist_overrides_an_enabled_toggle(self, sup, monkeypatch):
        monkeypatch.setenv("EMAIL_SEND_DISABLED_BUILDING_IDS", "13195")
        with _toggle(True):
            blocked, reason = await sup.check_email_suppressed("a@example.com", "13195")
        assert blocked is True
        assert "EMAIL_SEND_DISABLED_BUILDING_IDS" in reason

    @pytest.mark.asyncio
    async def test_disable_all_blocks_every_building(self, sup, monkeypatch):
        monkeypatch.setenv("EMAIL_SEND_DISABLED_ALL", "true")
        with _toggle(True):
            for bid in ("13195", "UPDEMO5", None):
                blocked, _ = await sup.check_email_suppressed("a@example.com", bid)
                assert blocked is True

    @pytest.mark.asyncio
    async def test_blocklist_fails_closed_on_unknown_building(self, sup, monkeypatch):
        """A kill switch that leaks whenever the building is ambiguous is not a kill switch."""
        monkeypatch.setenv("EMAIL_SEND_DISABLED_BUILDING_IDS", "13195")
        with patch("utils.email_suppression._building_for_recipient", AsyncMock(return_value=None)):
            with _toggle(True):
                blocked, reason = await sup.check_email_suppressed("stranger@example.com", None)
        assert blocked is True
        assert "fail-closed" in reason

    @pytest.mark.asyncio
    async def test_unknown_building_allowed_when_no_blocklist(self, sup):
        """Without an env blocklist the fail-closed rule must NOT apply, or a per-building
        toggle would mute unrelated platform mail."""
        with patch("utils.email_suppression._building_for_recipient", AsyncMock(return_value=None)):
            with _toggle(True):
                blocked, _ = await sup.check_email_suppressed("stranger@example.com", None)
        assert blocked is False


class TestResolutionOrder:
    @pytest.mark.asyncio
    async def test_recipient_lookup_used_when_no_context(self, sup):
        """Cron jobs have no request context — the recipient's own record is the fallback."""
        with patch("utils.email_suppression._building_for_recipient",
                   AsyncMock(return_value="13195")) as lookup:
            with _toggle(False):
                blocked, reason = await sup.check_email_suppressed("owner@example.com", None)
        lookup.assert_awaited_once()
        assert blocked is True
        assert "recipient lookup" in reason

    @pytest.mark.asyncio
    async def test_explicit_building_skips_the_lookup(self, sup):
        with patch("utils.email_suppression._building_for_recipient",
                   AsyncMock(return_value="99999")) as lookup:
            with _toggle(False):
                _, reason = await sup.check_email_suppressed("owner@example.com", "13195")
        lookup.assert_not_awaited()
        assert "13195" in reason


class TestEverySendPathIsGuarded:
    """Every module that can put a message on the wire must be guarded.

    utils.email.send_email_async is the choke point. Historically three crons bypassed
    it entirely, transmitting via the Resend HTTP API or smtplib, so a switch wired only
    into utils/email.py looked complete while those three kept sending.

    A module may satisfy the invariant two ways, and the distinction matters:

      * calling suppress_if_blocked itself, or
      * delegating to send_email_async, which calls it.

    Delegation is now the preferred form and is strictly stronger: as of 2026-08-27 it
    also routes the message through the outbound review queue (GAP-COMMS-003), whereas a
    direct guard call only honours the kill switch and still transmits un-reviewed.
    cron_approval_escalation and cron_admin_auto_approve were converted for exactly that
    reason. This test therefore accepts either, rather than forcing a weaker pattern.
    """

    @pytest.mark.parametrize("relpath", [
        "utils/email.py",
        "server.py",
        "cron/cron_approval_escalation.py",
        "cron/cron_admin_auto_approve.py",
        "cron/cron_expiration_check.py",
    ])
    def test_module_calls_the_guard(self, relpath):
        source = (BACKEND / relpath).read_text()
        guarded = "suppress_if_blocked" in source or "send_email_async" in source
        assert guarded, (
            f"{relpath} can transmit email but neither consults the kill switch nor "
            f"delegates to send_email_async"
        )

    @pytest.mark.parametrize("relpath", [
        "cron/cron_approval_escalation.py",
        "cron/cron_admin_auto_approve.py",
    ])
    def test_converted_crons_no_longer_hold_their_own_transport(self, relpath):
        """These two must stay delegated, or they silently leave the review queue again.

        Guarding against a well-meant "restore the direct send for reliability" change:
        it would still pass the kill-switch check above while making the console's
        "nothing queued" a false statement about outgoing mail.
        """
        source = (BACKEND / relpath).read_text()
        assert "send_email_async" in source, f"{relpath} must delegate to the choke point"
        for transport in ("smtplib.SMTP", "resend.Emails.send", "api.resend.com"):
            assert transport not in source, (
                f"{relpath} regained a direct {transport} path, bypassing the outbound queue"
            )

    def test_no_new_unguarded_transport(self):
        """Fail if a module gains a direct transport without consulting the guard."""
        guarded = {
            "utils/email.py",
            # server.py carries a second, near-duplicate send_email_async; this test is
            # what found it.
            "server.py",
            "cron/cron_approval_escalation.py",
            "cron/cron_admin_auto_approve.py",
            "cron/cron_expiration_check.py",
        }
        # Known non-senders: inbound intake parses mail, it never transmits.
        allowed_without_guard = {"services/email_intake_service.py"}

        offenders = []
        for path in BACKEND.rglob("*.py"):
            if "venv" in path.parts or "scripts" in path.parts or "tests" in path.parts:
                continue
            rel = path.relative_to(BACKEND).as_posix()
            if rel in guarded or rel in allowed_without_guard:
                continue
            text = path.read_text(errors="ignore")
            transmits = ("smtplib.SMTP" in text or "resend.Emails.send" in text
                         or "api.resend.com" in text)
            # Delegation counts: send_email_async applies the guard (and the queue).
            if transmits and "suppress_if_blocked" not in text and "send_email_async" not in text:
                offenders.append(rel)
        assert not offenders, (
            "these modules transmit email without consulting the kill switch: " + ", ".join(offenders)
        )


class TestSendEmailAsyncIntegration:
    @pytest.mark.asyncio
    async def test_suppressed_send_never_reaches_a_transport_and_is_audited(self, sup):
        import utils.email as em

        with patch("utils.email_suppression.suppress_if_blocked", AsyncMock(return_value=True)), \
             patch.object(em, "_log_email_sent", AsyncMock()) as logged, \
             patch("smtplib.SMTP", side_effect=AssertionError("SMTP must not be called")), \
             patch("smtplib.SMTP_SSL", side_effect=AssertionError("SMTP_SSL must not be called")):
            result = await em.send_email_async(
                to_email="owner@example.com", subject="s", html_content="<p>x</p>",
            )

        assert result["suppressed"] is True
        assert result["success"] is False
        # Suppression is recorded, never a silent drop.
        logged.assert_awaited_once()
        assert logged.await_args.args[2] is False        # success
        assert logged.await_args.args[3] == "suppressed"  # provider


class TestRecipientLookupEscaping:
    """The address goes into a $regex, so it must be escaped.

    Interpolating it raw was a two-way bug: `.` is a wildcard, so one address could
    resolve a DIFFERENT user's building; and `+` is a quantifier, so a plus-addressed
    recipient failed to match itself, came back unresolved, and was therefore NOT
    suppressed.
    """

    @pytest.mark.asyncio
    async def test_plus_addressed_recipient_still_resolves(self, sup):
        captured = []

        class _Users:
            async def find_one(self, q, *a, **k):
                captured.append(q)
                # Only the escaped regex form is allowed to match, mirroring Mongo.
                import re as _re
                if "email" in q and isinstance(q["email"], str):
                    return None  # force the regex fallback
                pattern = q["email"]["$regex"]
                if _re.match(pattern, "owner+notices@example.com"):
                    return {"id": "u1", "building_id": "13195"}
                return None

        class _DB:
            users = _Users()
            memberships = _Users()

        with patch.dict(sys.modules, {"database": type("m", (), {"db": _DB()})}):
            got = await sup._building_for_recipient("owner+notices@example.com")

        assert got == "13195", "plus-addressed recipient failed to resolve — it would not be suppressed"

    @pytest.mark.asyncio
    async def test_dot_in_address_is_not_a_wildcard(self, sup):
        class _Users:
            async def find_one(self, q, *a, **k):
                if isinstance(q.get("email"), str):
                    return None
                import re as _re
                # A different address that a raw (unescaped) "a.b@x" pattern would match.
                if _re.match(q["email"]["$regex"], "axb@example.com"):
                    return {"id": "wrong", "building_id": "99999"}
                return None

        class _DB:
            users = _Users()
            memberships = _Users()

        with patch.dict(sys.modules, {"database": type("m", (), {"db": _DB()})}):
            got = await sup._building_for_recipient("a.b@example.com")

        assert got is None, "'.' behaved as a wildcard and matched the wrong user's building"


class TestCronImportPath:
    """The crons must be able to IMPORT the guard when run the way they are actually run.

    They are launched as bare scripts (`cd backend && python3 cron/<name>.py`), so
    sys.path[0] is backend/cron and nothing under backend/ is importable by default. An
    earlier version imported the guard inside the function under `except ImportError:
    pass` — which fails OPEN: the import raised, the guard silently vanished, and the
    cron sent anyway. Only a test harness that pre-injected backend/ made it look fine.
    """

    CRONS = [
        "cron/cron_approval_escalation.py",
        "cron/cron_admin_auto_approve.py",
        "cron/cron_expiration_check.py",
    ]

    @pytest.mark.parametrize("relpath", CRONS)
    def test_puts_backend_on_sys_path(self, relpath):
        src = (BACKEND / relpath).read_text()
        assert "_BACKEND_DIR" in src and "sys.path.insert" in src, (
            f"{relpath} imports the kill switch but never puts backend/ on sys.path; "
            "the import will raise ImportError under real invocation"
        )

    @pytest.mark.parametrize("relpath", CRONS)
    def test_guard_import_is_not_swallowed(self, relpath):
        """A failed import must crash the cron, not silently disable the switch.

        Checked against CODE only — the modules describe the old fail-open pattern in
        their comments, and a naive substring search matches that prose.
        """
        code_lines = [
            ln for ln in (BACKEND / relpath).read_text().splitlines()
            if not ln.lstrip().startswith("#")
        ]
        offenders = [ln.strip() for ln in code_lines if "except ImportError" in ln]
        assert not offenders, (
            f"{relpath} swallows an ImportError around the kill switch — that fails OPEN: "
            f"{offenders}"
        )

    @pytest.mark.parametrize("relpath", CRONS)
    def test_import_actually_succeeds_as_a_bare_script(self, relpath, tmp_path):
        """Run a probe exactly as cron does and assert the guard imports."""
        import subprocess
        probe = BACKEND / "cron" / "_pytest_import_probe.py"
        probe.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "_B = Path(__file__).resolve().parent.parent\n"
            "sys.path.insert(0, str(_B))\n"
            "from utils.email_suppression import suppress_if_blocked\n"
            "print('OK')\n"
        )
        try:
            r = subprocess.run(
                [sys.executable, "cron/_pytest_import_probe.py"],
                cwd=str(BACKEND), capture_output=True, text=True, timeout=60,
            )
        finally:
            probe.unlink(missing_ok=True)
        assert "OK" in r.stdout, f"guard not importable from backend/cron: {r.stderr[-300:]}"


class TestSettingsContactFallback:
    """A building's own settings contacts must resolve to that building.

    When a building's user records are removed, addresses named in its settings
    (ec_email, notify_bcc_email, the sender) resolve to NO building via users or
    memberships — and so escape a per-building suppression entirely. East Gate's purge
    left exactly that situation: two real contact addresses that the kill switch could
    not see. The fallback is generic — whichever building's settings names the address —
    with no hardcoded building or domain.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["ec_email", "notify_bcc_email", "sender_email"])
    async def test_settings_contact_resolves_to_its_building(self, sup, field):
        class _Coll:
            async def find_one(self, q, *a, **k):
                return None  # no user, no membership

        class _RawSettings:
            async def find_one(self, q, *a, **k):
                # Mirrors Mongo: only the queried field matches.
                if field in q:
                    return {"building_id": "13195"}
                return None

        class _Raw:
            settings = _RawSettings()

        class _DB:
            users = _Coll()
            memberships = _Coll()
            _db = _Raw()

        with patch.dict(sys.modules, {"database": type("m", (), {"db": _DB()})}):
            got = await sup._building_for_recipient("contact@example.com")
        assert got == "13195", f"{field} did not resolve to its building"

    @pytest.mark.asyncio
    async def test_unknown_address_still_resolves_to_nothing(self, sup):
        """The fallback must not invent a building for an unrelated address."""
        class _Coll:
            async def find_one(self, q, *a, **k):
                return None

        class _Raw:
            settings = _Coll()

        class _DB:
            users = _Coll()
            memberships = _Coll()
            _db = _Raw()

        with patch.dict(sys.modules, {"database": type("m", (), {"db": _DB()})}):
            got = await sup._building_for_recipient("stranger@example.com")
        assert got is None

    @pytest.mark.asyncio
    async def test_settings_lookup_is_escaped(self, sup):
        """Same escaping requirement as the users lookup — it is also a $regex."""
        captured = {}

        class _Coll:
            async def find_one(self, q, *a, **k):
                return None

        class _RawSettings:
            async def find_one(self, q, *a, **k):
                captured.update(q)
                return None

        class _Raw:
            settings = _RawSettings()

        class _DB:
            users = _Coll()
            memberships = _Coll()
            _db = _Raw()

        with patch.dict(sys.modules, {"database": type("m", (), {"db": _DB()})}):
            await sup._building_for_recipient("owner+tag@example.com")

        pattern = next(v["$regex"] for k, v in captured.items() if isinstance(v, dict))
        import re as _re
        assert _re.match(pattern, "owner+tag@example.com"), "address was not escaped"


class TestRecipientDomainAllowlist:
    """EMAIL_ALLOWED_DOMAINS: nothing may reach an address off the list.

    Requested 2026-08-27. Restoring East Gate put ~100 real personal addresses back into
    the database. Rewriting them onto the building domain removed them from user
    records, but that is a data state, not a control — it cannot stop a message reaching
    a real person by another route (a manually entered address, an imported contact, a
    future feature). This gate is the control.

    Placed AFTER the unconditional stop and BEFORE any building resolution, so it holds
    for cron mail and for recipients whose building cannot be determined — the two cases
    the building blocklist alone has historically failed to cover.
    """

    @pytest.mark.asyncio
    async def test_the_building_domain_is_allowed_through(self, sup, monkeypatch):
        monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "eastgateresidences.com.au")
        with _toggle(True):
            suppressed, _ = await sup.check_email_suppressed(
                "owner@eastgateresidences.com.au", "13195")
        assert suppressed is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("addr", [
        "riyuroy@gmail.com",                          # a real, exempted owner
        "someone@hotmail.com",
        "owner@eastgateresidences.com.au.evil.test",  # suffix, not the domain
        "owner@notEASTGATEresidences.com.au",
    ])
    async def test_everything_else_is_suppressed(self, sup, monkeypatch, addr):
        monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "eastgateresidences.com.au")
        suppressed, why = await sup.check_email_suppressed(addr, "13195")
        assert suppressed is True
        assert "EMAIL_ALLOWED_DOMAINS" in why

    @pytest.mark.asyncio
    @pytest.mark.parametrize("addr", ["", "malformed-address", "@", "no-at-sign"])
    async def test_it_fails_closed_on_an_unparseable_address(self, sup, monkeypatch, addr):
        """No usable domain must be refused, never allowed by default."""
        monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "eastgateresidences.com.au")
        suppressed, _ = await sup.check_email_suppressed(addr, "13195")
        assert suppressed is True

    @pytest.mark.asyncio
    async def test_matching_is_case_insensitive(self, sup, monkeypatch):
        monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "eastgateresidences.com.au")
        with _toggle(True):
            suppressed, _ = await sup.check_email_suppressed(
                "Owner@EastGateResidences.COM.AU", "13195")
        assert suppressed is False

    @pytest.mark.asyncio
    async def test_unset_means_no_restriction(self, sup):
        """Existing deployments must be unaffected by the mere presence of this gate."""
        with _toggle(True):
            suppressed, _ = await sup.check_email_suppressed("anyone@anywhere.example", "13195")
        assert suppressed is False

    @pytest.mark.asyncio
    async def test_the_unconditional_stop_still_outranks_it(self, sup, monkeypatch):
        """Order matters: ALL wins, so relaxing the allowlist cannot re-open sending."""
        monkeypatch.setenv("EMAIL_SEND_DISABLED_ALL", "true")
        monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "eastgateresidences.com.au")
        suppressed, why = await sup.check_email_suppressed(
            "owner@eastgateresidences.com.au", "13195")
        assert suppressed is True
        assert "EMAIL_SEND_DISABLED_ALL" in why
