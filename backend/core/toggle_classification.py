# @featuretrace:cutover-control-plane — Canonical safety classification for feature toggles.
# Layer: config
# Data flow: config_repo.create/update_global_feature_toggle → assert_global_enable_allowed(key) → raise on protected keys
#            scripts/audits/toggle_drift.py → PROTECTED_TOGGLE_KEYS / safety metadata (global)
# Related: backend/db_postgres/repos/config_repo.py
#          backend/services/cutover_config_service.py
#          backend/services/bi_toggle_service.py
#          backend/alembic/versions/0058_toggle_safety_backfill.py
#          docs/architecture/feature-toggle-governance.md
# Table: core.feature_toggles
# Tests: tests/backend/test_toggle_classification_safety.py
"""Canonical feature-toggle safety classification.

Why this exists (P0.3, 2026-06-11): a blanket enable-all pass on 2026-06-09
flipped every row in core.feature_toggles to is_enabled=TRUE, including
``bi_pg_primary_enabled`` — a PG-primary data-source toggle that the seed of
record and feature-toggle-governance.md §5 require to stay globally FALSE
until cutover readiness gates pass. The policy existed only in docs; nothing
in code distinguished a harmless visibility flag from a data-source cutover
flag. This module is that distinction.

Toggle classes:

  visibility           page/module visibility; safe to flip either way
  ui_only              pure presentation (layout/theme variants); no data-path change
  experimental         shell/preview features (e.g. Powerhouse); Mongo-write only
  admin_only           operator tooling (impersonation, admin diagnostics)
  cutover_sensitive    controls Mongo→PG migration machinery or adapters
  data_source_primary  switches which datastore serves reads (Mongo vs PG)
  shadow_read          parallel read + divergence logging
  shadow_write         parallel write paths during cutover soak
  finance_write        routes financial writes to a different store
  trust_write          routes trust-accounting writes/reconciliation to a different store
  mock_boundary        selects mock vs LIVE external financial integration

Every class from ``cutover_sensitive`` down to ``trust_write`` is PROTECTED:
enabling such a key globally is forbidden through application write paths. The
only sanctioned promotion path is a per-building row in
core.feature_toggle_overrides after the building passes its readiness gates
(governance doc §5). Disabling is always allowed — the fail-safe direction never
needs permission.

``mock_boundary`` is the INVERSE and is guarded separately. Its keys read
"…_mock" and default to ENABLED, so for them the fail-safe direction is ON:
turning one OFF is what points a building at a real financial institution. They
are therefore DISABLE-protected — ``assert_global_disable_allowed`` blocks a
global disable, while enabling (returning to mock) needs no permission, exactly
mirroring the rule above. They are deliberately NOT in PROTECTED_TOGGLE_KEYS:
that set means "cannot be globally ENABLED", which for a mock key would forbid
the safe direction and leave the dangerous one open.

Keys absent from TOGGLE_CLASSIFICATION default to ``visibility`` and are
unrestricted. When adding a new toggle that changes a data path, classify it
here in the same commit that introduces it.
"""
from __future__ import annotations

from enum import Enum


class ToggleClass(str, Enum):
    VISIBILITY = "visibility"
    UI_ONLY = "ui_only"
    EXPERIMENTAL = "experimental"
    ADMIN_ONLY = "admin_only"
    CUTOVER_SENSITIVE = "cutover_sensitive"
    DATA_SOURCE_PRIMARY = "data_source_primary"
    SHADOW_READ = "shadow_read"
    SHADOW_WRITE = "shadow_write"
    FINANCE_WRITE = "finance_write"
    TRUST_WRITE = "trust_write"
    MOCK_BOUNDARY = "mock_boundary"


PROTECTED_TOGGLE_CLASSES: frozenset[ToggleClass] = frozenset({
    ToggleClass.CUTOVER_SENSITIVE,
    ToggleClass.DATA_SOURCE_PRIMARY,
    ToggleClass.SHADOW_READ,
    ToggleClass.SHADOW_WRITE,
    ToggleClass.FINANCE_WRITE,
    ToggleClass.TRUST_WRITE,
})

# Classes whose DANGEROUS direction is OFF rather than ON. Kept separate from
# PROTECTED_TOGGLE_CLASSES rather than folded into it: that set is consumed as
# "must never be globally enabled", and a mock key must stay freely enableable.
DISABLE_PROTECTED_TOGGLE_CLASSES: frozenset[ToggleClass] = frozenset({
    ToggleClass.MOCK_BOUNDARY,
})


# Key strings deliberately duplicated from cutover_config_service /
# bi_toggle_service rather than imported: config_repo imports this module and
# cutover_config_service imports config_repo, so importing the constants here
# would create a cycle. test_toggle_classification_safety.py asserts the two
# sets stay in sync.
TOGGLE_CLASSIFICATION: dict[str, ToggleClass] = {
    # Outgoing-email master switch. Classified admin_only (operator tooling) rather than
    # visibility: it does not hide a page, it stops real messages leaving the system.
    # The actual suppression for a building is carried as a PER-BUILDING override, which
    # a global bulk-enable does not touch — so turning site-wide defaults back on cannot
    # silently resume mail for a building that was deliberately muted.
    "email_notifications_enabled": ToggleClass.ADMIN_ONLY,

    # ── Cutover umbrella + machinery ─────────────────────────────────────────
    "financial_integration_layer_v2": ToggleClass.CUTOVER_SENSITIVE,
    "bank_integration_abstraction_enabled": ToggleClass.CUTOVER_SENSITIVE,
    "demo_bank_feed_enabled": ToggleClass.CUTOVER_SENSITIVE,
    "bank_feeds_sync_enabled": ToggleClass.CUTOVER_SENSITIVE,
    "disable_strata_sync_direct_write": ToggleClass.CUTOVER_SENSITIVE,
    "onboarding_current_balance_adapters_enabled": ToggleClass.CUTOVER_SENSITIVE,

    # ── Data-source primary (which store serves reads) ───────────────────────
    "bi_pg_primary_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    "financial_pg_reads_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    "owner_read_pg_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    "governance_read_pg_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    "external_api_finance_pg_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    "settings_pg_reads_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    "users_pg_reads_enabled": ToggleClass.DATA_SOURCE_PRIMARY,
    # Operator shadow-soak waiver: skips the 7-day soak gate so a building whose PG↔source
    # parity is verified out-of-band can serve finance PG reads immediately. It directly
    # advances which store serves reads, so it is DATA_SOURCE_PRIMARY (protected): never
    # bulk-enable, per-building promotion only after readiness gates.
    "financial_pg_reads_bypass_shadow": ToggleClass.DATA_SOURCE_PRIMARY,
    # Legacy aliases still present in the seed catalogue
    "financial_core.read_from_postgres": ToggleClass.DATA_SOURCE_PRIMARY,

    # ── Shadow read ──────────────────────────────────────────────────────────
    "financial_shadow_reads_enabled": ToggleClass.SHADOW_READ,
    "financial_core.shadow_read_postgres": ToggleClass.SHADOW_READ,

    # ── Write-path routing ───────────────────────────────────────────────────
    "financial_pg_writes_enabled": ToggleClass.FINANCE_WRITE,
    "trust_pg_ledger_enabled": ToggleClass.TRUST_WRITE,
    "trust_reconciliation_pg_enabled": ToggleClass.TRUST_WRITE,
    # Capital-funding (GAP-FIN-034) write-side gates. Their own seed descriptions
    # name them "Protected write-side gate … posting approved capital-funding levy
    # runs into the canonical ledger. Default disabled during finance cutover" and
    # "actual capital-funding levy notice delivery … keep disabled until legal
    # review". They were previously absent here, so they defaulted to `visibility`
    # (unrestricted) and slipped through assert_global_enable_allowed. No live code
    # path consumes them yet (feature not wired), so this is a latent-not-live fix:
    # classify them protected now so global enable is blocked before the posting
    # path lands. Per-building promotion after readiness gates is still the
    # sanctioned path (governance §5).
    "capital_funding_ledger_posting": ToggleClass.FINANCE_WRITE,
    "capital_funding_notice_issuance": ToggleClass.FINANCE_WRITE,

    # ── Mock boundary (disable-protected; see the module docstring) ─────────
    # Umbrella: while ON, the financial integrations that talk to a real institution
    # run against their mock implementations for this building. Turning it OFF is
    # what connects real money, so OFF is the guarded direction.
    #
    # Demo Bank is deliberately OUT of scope. It is a first-party emulator with its
    # own gates (demo_bank_feed_enabled / historical_financial_reconstruction) and is
    # already mock by construction, so folding it in here would give one switch two
    # unrelated meanings and let a building disable this key and silently lose its
    # reconstruction staging.
    "financial_services_mock": ToggleClass.MOCK_BOUNDARY,
    # Separate key, per product decision: bank direct debit and real transaction-history
    # retrieval carry a different risk profile from payment initiation (they pull
    # customer bank data and can debit an owner directly), so they are promoted on
    # their own schedule rather than riding the umbrella.
    "bank_direct_debit_mock": ToggleClass.MOCK_BOUNDARY,

    # ── Non-protected classes (examples; default for unlisted keys is
    #    visibility, so listing here is documentation, not restriction) ───────
    "ft_dashboard_v2": ToggleClass.UI_ONLY,
    "impersonation": ToggleClass.ADMIN_ONLY,
    # Gates approve/generate/sync/reverse of a reconstruction batch and the
    # levy-item GST regeneration apply step — all of which mutate
    # finance.levy_items/journal_entries. Router-level RBAC (super_admin/
    # strata_admin only) is the actual enforcement; this entry documents intent
    # per this module's own docstring ("classify it here in the same commit
    # that introduces it") rather than silently relying on the visibility
    # default. Not PROTECTED — admin_only carries no write-path restriction.
    "historical_reconstruction_posting": ToggleClass.ADMIN_ONLY,
    # Gates create/extract/preview/submit-review of a reconstruction batch and
    # the levy-item GST regeneration dry-run — preparation only, no ledger
    # mutation (that's historical_reconstruction_posting, above). Previously
    # unclassified (silently fell back to the visibility default despite
    # gating a write-path-adjacent workflow entry point) — classified here per
    # docs/migration/build-demo-data-ui-system.md's finding 3.3.
    "historical_financial_reconstruction": ToggleClass.ADMIN_ONLY,
    # Gates historical-expense-import evidence ingestion feeding the same
    # reconstruction-batch workflow (evidence_type="historical_expense_import").
    # Same rationale/history as historical_financial_reconstruction above.
    "historical_expense_reconstruction": ToggleClass.ADMIN_ONLY,
    "powerhouse_conversations": ToggleClass.EXPERIMENTAL,
    "powerhouse_shared_inbox": ToggleClass.EXPERIMENTAL,
    "powerhouse_email_intake": ToggleClass.EXPERIMENTAL,
    "powerhouse_ai_summary": ToggleClass.EXPERIMENTAL,
    "powerhouse_workflow_engine": ToggleClass.EXPERIMENTAL,
    "powerhouse_automation_rules": ToggleClass.EXPERIMENTAL,
}


# Safety metadata every protected key must carry in core.feature_toggles.
# Backfilled by migration 0058 and enforced (as a floor) on writes through
# config_repo. depends_on mirrors gates already enforced in code:
# cutover_config_service gates _CHILD_FEATURE_KEYS behind the umbrella key,
# and bi_service requires bi_analytics visibility before PG-primary matters.
PROTECTED_TOGGLE_SAFETY_METADATA: dict[str, dict[str, list[str]]] = {
    "financial_integration_layer_v2": {
        "allowed_roles": ["super_admin"], "depends_on": [],
    },
    "bank_integration_abstraction_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "demo_bank_feed_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "bank_feeds_sync_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["demo_bank_feed_enabled"],
    },
    "disable_strata_sync_direct_write": {
        "allowed_roles": ["super_admin"], "depends_on": ["bank_feeds_sync_enabled"],
    },
    "onboarding_current_balance_adapters_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "bi_pg_primary_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["bi_analytics_enabled"],
    },
    "financial_pg_reads_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "owner_read_pg_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "governance_read_pg_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "external_api_finance_pg_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "settings_pg_reads_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "users_pg_reads_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    # depends_on names the functional prerequisite (like bi_pg_primary depends on
    # bi_analytics): waiving the shadow soak is meaningless unless finance PG reads are
    # already enabled — the resolver blocks on financial_pg_reads_enabled before it ever
    # consults this waiver.
    "financial_pg_reads_bypass_shadow": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_pg_reads_enabled"],
    },
    "financial_core.read_from_postgres": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "financial_shadow_reads_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "financial_core.shadow_read_postgres": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "financial_pg_writes_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "trust_pg_ledger_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    "trust_reconciliation_pg_enabled": {
        "allowed_roles": ["super_admin"], "depends_on": ["financial_integration_layer_v2"],
    },
    # Added alongside eb955f21's TOGGLE_CLASSIFICATION entries for these two keys —
    # that commit classified them FINANCE_WRITE but didn't add the matching safety
    # metadata, which crashed _protected_metadata_violations() (bare dict index,
    # not .get()) with a KeyError on every drift-audit run since. depends_on mirrors
    # the seed's own chain (ledger_posting -> notice_issuance -> notice_preview),
    # allowed_roles matches the seed's "roles" field for both.
    "capital_funding_ledger_posting": {
        "allowed_roles": ["super_admin", "strata_admin"], "depends_on": ["capital_funding_notice_issuance"],
    },
    "capital_funding_notice_issuance": {
        "allowed_roles": ["super_admin", "strata_admin"], "depends_on": ["capital_funding_notice_preview"],
    },
}


# Safety metadata for the disable-protected keys. Separate from
# PROTECTED_TOGGLE_SAFETY_METADATA because that dict is pinned to exactly
# PROTECTED_TOGGLE_KEYS by test_safety_metadata_covers_exactly_protected_keys.
#
# allowed_roles is wider than super_admin here by design: turning a building back
# to mock is the safe direction and is the manager's call, so strata_admin and
# strata_manager can hold this switch for buildings they are assigned to. The
# building scoping is enforced by the `building.integrations.manage` capability on
# the route, not by this list — this list is what the row must carry so the
# contract exists in the database as well as in code (the P0.3 lesson).
MOCK_BOUNDARY_SAFETY_METADATA: dict[str, dict[str, list[str]]] = {
    "financial_services_mock": {
        "allowed_roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": [],
    },
    "bank_direct_debit_mock": {
        "allowed_roles": ["super_admin", "strata_admin", "strata_manager"],
        "depends_on": [],
    },
}


# ── Graduation: which cutover domain makes each protected key safe globally ──
#
# The classification above says a key is dangerous to enable globally. It never
# said for how long. Until 2026-08-27 the answer was "forever": PROTECTED_TOGGLE_KEYS
# is a static frozenset, so a key stayed vetoed no matter how far the migration it
# guards had actually progressed, and toggle_drift_autoheal.py reverted it on every
# deploy without consulting a single row of live state.
#
# That is the wrong shape for a gate whose whole purpose is to hold a door shut
# until a migration finishes. This map is the missing half: for each protected key,
# the core.domain_cutover_status domain(s) whose promotion is the evidence that
# flipping the GLOBAL default is no longer a data-source decision at all, because
# every production building is already served from PostgreSQL for that domain.
#
# A key graduates only when EVERY active, non-demo, non-test scheme has EVERY
# domain listed here at mode postgres_write or mongo_archive. Read-only promotion
# (postgres_read) deliberately does not count: while writes still land in MongoDB
# the global default still decides where a write goes. Demo and newly-onboarded
# buildings are excluded from the vote because they are protected by a different
# mechanism — require_domain_source fails closed on a missing row, so a building
# with no cutover state stays on MongoDB whatever the global default says.
#
# Live evaluation is services/toggle_graduation_service.py. This module stays pure
# (config_repo imports it, so it can never import the database layer).
PROTECTED_TOGGLE_CUTOVER_DOMAINS: dict[str, tuple[str, ...]] = {
    # ── Cutover umbrella + machinery: all finance-ledger intake machinery ────
    "financial_integration_layer_v2": ("finance_ledger",),
    "bank_integration_abstraction_enabled": ("finance_ledger",),
    "demo_bank_feed_enabled": ("finance_ledger",),
    "bank_feeds_sync_enabled": ("finance_ledger",),
    "disable_strata_sync_direct_write": ("finance_ledger",),
    "onboarding_current_balance_adapters_enabled": ("finance_ledger",),

    # ── Data-source primary ──────────────────────────────────────────────────
    "bi_pg_primary_enabled": ("finance_ledger",),
    "financial_pg_reads_enabled": ("finance_ledger",),
    "external_api_finance_pg_enabled": ("finance_ledger",),
    "financial_pg_reads_bypass_shadow": ("finance_ledger",),
    "financial_core.read_from_postgres": ("finance_ledger",),
    "governance_read_pg_enabled": ("governance",),
    "settings_pg_reads_enabled": ("settings",),
    "users_pg_reads_enabled": ("identity_core",),
    # Owner reads resolve a person against their lot, so both halves must be
    # authoritative in PostgreSQL before the global default may change.
    "owner_read_pg_enabled": ("identity_core", "occupancy"),

    # ── Shadow read ──────────────────────────────────────────────────────────
    # Shadow comparison is only meaningful before promotion; once the domain is
    # authoritative everywhere it is redundant rather than dangerous.
    "financial_shadow_reads_enabled": ("finance_ledger",),
    "financial_core.shadow_read_postgres": ("finance_ledger",),

    # ── Write-path routing ───────────────────────────────────────────────────
    "financial_pg_writes_enabled": ("finance_ledger",),
    "capital_funding_ledger_posting": ("finance_ledger",),
    "capital_funding_notice_issuance": ("finance_ledger",),
    "trust_pg_ledger_enabled": ("trust_ledger",),
    "trust_reconciliation_pg_enabled": ("trust_reconciliation",),
}

# Modes in which PostgreSQL is authoritative for BOTH reads and writes. Anything
# short of this leaves a write path that the global default still steers.
GRADUATING_CUTOVER_MODES: frozenset[str] = frozenset({
    "postgres_write",
    "mongo_archive",
})


def cutover_domains_for(feature_key: str) -> tuple[str, ...]:
    """Domains whose full promotion would graduate this key out of protection.

    Empty for an unprotected key, and empty for a protected key with no mapping —
    which is treated as "never graduates", so an unmapped new protected key fails
    safe rather than silently becoming globally enableable.
    """
    return PROTECTED_TOGGLE_CUTOVER_DOMAINS.get(feature_key, ())


def evaluate_graduation(
        feature_key: str,
        promoted_domains_by_building: dict[str, set[str]],
) -> bool:
    """Pure graduation check: does live state justify a global enable of this key?

    ``promoted_domains_by_building`` maps each active production building_id to the
    set of domains that building has at a mode in GRADUATING_CUTOVER_MODES. The
    caller is responsible for having excluded demo and test schemes.

    Returns False when there are no production buildings to vote — an empty
    control plane is absence of evidence, not evidence of promotion.
    """
    if not is_protected_toggle(feature_key):
        return True
    domains = cutover_domains_for(feature_key)
    if not domains:
        return False
    if not promoted_domains_by_building:
        return False
    return all(
        set(domains).issubset(promoted)
        for promoted in promoted_domains_by_building.values()
    )


PROTECTED_TOGGLE_KEYS: frozenset[str] = frozenset(
    key for key, cls in TOGGLE_CLASSIFICATION.items()
    if cls in PROTECTED_TOGGLE_CLASSES
)

DISABLE_PROTECTED_TOGGLE_KEYS: frozenset[str] = frozenset(
    key for key, cls in TOGGLE_CLASSIFICATION.items()
    if cls in DISABLE_PROTECTED_TOGGLE_CLASSES
)

# Backward-compatible string map used by guardrail tests and lightweight audits.
TOGGLE_SAFETY_CLASSES: dict[str, str] = {
    key: cls.value for key, cls in TOGGLE_CLASSIFICATION.items()
}


class ToggleWriteBlockedError(RuntimeError):
    """An application write path was refused because of a toggle's safety class.

    The common base for both guards. It exists so a caller can catch "this toggle
    write was blocked" without having to know which DIRECTION was blocked — the
    enable guard and the disable guard are opposites, and every HTTP handler wants
    to turn either into the same 403.

    Introduced when MockBoundaryToggleError was added: it was originally a bare
    RuntimeError, so `except ProtectedToggleError` in routers/feature_toggles.py did
    not catch it and a blocked global disable surfaced as a 500 instead of a 403.
    The write was correctly refused either way, but the HTTP contract was wrong.
    """

    def __init__(self, feature_key: str, toggle_class: ToggleClass, message: str):
        self.feature_key = feature_key
        self.toggle_class = toggle_class
        super().__init__(message)


class ProtectedToggleError(ToggleWriteBlockedError):
    """Raised when an application write path tries to globally enable a protected toggle."""

    def __init__(self, feature_key: str, toggle_class: ToggleClass):
        super().__init__(
            feature_key,
            toggle_class,
            f"Feature toggle '{feature_key}' is classified {toggle_class.value} and must not "
            f"be enabled globally. Promote it per-building via core.feature_toggle_overrides "
            f"after readiness gates pass (docs/architecture/feature-toggle-governance.md §5).",
        )


class MockBoundaryToggleError(ToggleWriteBlockedError):
    """Raised when a write path tries to globally disable a mock-boundary toggle.

    A sibling of ProtectedToggleError, not a subclass of it: this is the opposite
    direction, and something catching ProtectedToggleError by name should not be
    silently told that an *enable* was blocked when a *disable* was. Handlers that
    want both catch the shared base.
    """

    def __init__(self, feature_key: str, toggle_class: ToggleClass):
        super().__init__(
            feature_key,
            toggle_class,
            f"Feature toggle '{feature_key}' is classified {toggle_class.value} and must not "
            f"be disabled globally: that would point every building at a live financial "
            f"institution at once. Disable it per-building via core.feature_toggle_overrides "
            f"after that building's readiness gates pass "
            f"(docs/architecture/feature-toggle-governance.md §5).",
        )


def get_toggle_class(feature_key: str) -> ToggleClass:
    """Return the safety class for a feature key (default: visibility)."""
    return TOGGLE_CLASSIFICATION.get(feature_key, ToggleClass.VISIBILITY)


def is_protected_toggle(feature_key: str) -> bool:
    """True when the key belongs to a protected (cutover/data-path) class."""
    return feature_key in PROTECTED_TOGGLE_KEYS


def is_disable_protected_toggle(feature_key: str) -> bool:
    """True when the key's DANGEROUS direction is off (a mock-boundary key)."""
    return feature_key in DISABLE_PROTECTED_TOGGLE_KEYS


def assert_global_disable_allowed(
        feature_key: str,
        *,
        _allow_global_mock_disable: bool = False,
) -> None:
    """Raise MockBoundaryToggleError when globally disabling a mock-boundary toggle.

    The mirror image of :func:`assert_global_enable_allowed`. Without it the
    inverted keys would be unguarded in exactly the direction that matters: the
    existing machinery only ever asks permission to enable, on the reasoning that
    disabling is always fail-safe — true for every other class, and false for these.

    Same keyword-only escape hatch as its counterpart, following the
    ``_is_test_data`` pattern: no HTTP caller can reach it.
    """
    if _allow_global_mock_disable:
        return
    if feature_key in DISABLE_PROTECTED_TOGGLE_KEYS:
        raise MockBoundaryToggleError(feature_key, get_toggle_class(feature_key))


def assert_global_enable_allowed(
        feature_key: str,
        *,
        _allow_protected_global_enable: bool = False,
        graduated: bool = False,
) -> None:
    """Raise ProtectedToggleError when globally enabling a protected toggle.

    The keyword-only escape hatch follows the ``_is_test_data`` pattern: HTTP
    callers can never reach it; only deliberate internal scripts (e.g. a dev
    environment exercising the cutover path) may pass it explicitly.

    ``graduated`` is the state-derived permission added 2026-08-27: the caller has
    checked live ``core.domain_cutover_status`` and every active production building
    is already authoritative in PostgreSQL for every domain this key routes (see
    PROTECTED_TOGGLE_CUTOVER_DOMAINS). It differs from the escape hatch in kind, not
    just in name — the hatch overrides the gate, graduation means the gate has been
    satisfied — so it is passed by ordinary application write paths rather than being
    reserved for scripts. Callers must compute it via
    services.toggle_graduation_service, never assert it from a document or memory.
    """
    if _allow_protected_global_enable:
        return
    if graduated:
        return
    if is_protected_toggle(feature_key):
        raise ProtectedToggleError(feature_key, get_toggle_class(feature_key))
