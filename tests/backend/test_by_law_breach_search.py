# @featuretrace:by-law-breach-register — Guards the shared search grammar + help parity.
# Layer: test
# Data flow: search string -> parse_audit_query(BREACH_FIELD_MAP) -> by_law_breach_reports
#            (building-scoped).
# Related: backend/utils/audit_search.py, backend/routers/by_law_breach.py
"""
The by-law breach register's search, and the reasons it reuses the audit grammar.

`docs/architecture/ui_table_and_search_conventions.md` requires that the help panel be
served from the parser. Two parsers would let the documented syntax drift from what each
one accepts, so this register supplies a vocabulary to the existing parser rather than
defining its own.
"""
import pytest

from utils.audit_search import (
    BREACH_BOOLEAN_FIELDS,
    BREACH_FIELD_MAP,
    BREACH_FREE_TEXT_FIELDS,
    BREACH_NUMERIC_FIELDS,
    BREACH_SEARCH_HELP,
    parse_audit_query,
)


def _parse(q):
    return parse_audit_query(
        q,
        field_map=BREACH_FIELD_MAP,
        free_text_fields=BREACH_FREE_TEXT_FIELDS,
        numeric_fields=BREACH_NUMERIC_FIELDS,
        boolean_fields=BREACH_BOOLEAN_FIELDS,
    )


class TestGrammar:
    def test_status_equality_is_indexable_not_a_regex(self):
        """Status is a lowercase enum, so equality can use an index. A regex cannot."""
        f, unknown = _parse("status:escalated")
        assert f == {"status": {"$eq": "escalated"}}
        assert unknown == []

    def test_severity_equality_is_indexable_too(self):
        f, _ = _parse("severity:MAJOR")
        assert f == {"severity": {"$eq": "major"}}   # normalised, so casing is forgiving

    def test_exclusion_shorthand(self):
        f, _ = _parse("-status:resolved")
        assert f == {"$nor": [{"status": {"$eq": "resolved"}}]}

    def test_not_equals_operator(self):
        f, _ = _parse("status!=withdrawn")
        assert f == {"$nor": [{"status": {"$eq": "withdrawn"}}]}

    def test_contains_operator(self):
        f, _ = _parse("description~=parking")
        assert "$regex" in str(f) and "parking" in str(f)

    def test_bare_word_searches_the_identity_fields(self):
        f, _ = _parse("TH074")
        clauses = f["$or"]
        assert {c for d in clauses for c in d} == set(BREACH_FREE_TEXT_FIELDS)

    def test_terms_combine_with_and(self):
        f, _ = _parse("severity:major unit:UA042")
        assert "$and" in f and len(f["$and"]) == 2

    def test_boolean_field_parses_as_a_bool(self):
        f, _ = _parse("repeat:true")
        assert f == {"is_repeat_offence": True}

    def test_tribunal_alias_maps_to_escalation_target(self):
        f, _ = _parse("tribunal:ACAT")
        assert "escalation_target" in f

    def test_unknown_field_is_reported_and_matches_nothing_extra(self):
        """A typo must warn. Silently matching every row reads as 'no filter applied'."""
        f, unknown = _parse("adress:x")
        assert unknown == ["adress"]
        assert f == {}

    def test_value_is_escaped_so_a_regex_metacharacter_cannot_widen_the_search(self):
        f, _ = _parse("unit:.*")
        assert ".*" not in str(f).replace("\\.\\*", "")   # escaped, not live


class TestSearchHelp:
    def test_help_lists_the_real_field_names(self):
        assert set(BREACH_SEARCH_HELP["fields"]) == set(BREACH_FIELD_MAP)

    @pytest.mark.parametrize("example", BREACH_SEARCH_HELP["examples"], ids=lambda e: e["query"])
    def test_every_documented_example_actually_parses(self, example):
        """The help cannot document syntax the parser rejects."""
        _, unknown = _parse(example["query"])
        assert unknown == [], f"{example['query']} references unknown field(s) {unknown}"

    def test_help_has_a_summary_and_operators(self):
        assert BREACH_SEARCH_HELP["summary"]
        assert BREACH_SEARCH_HELP["operators"]


def test_audit_vocabulary_is_untouched_by_the_new_one():
    """Adding a second vocabulary must not change what the audit log accepts."""
    from utils.audit_search import FIELD_MAP, SEARCH_HELP
    f, unknown = parse_audit_query("status:failed")
    assert unknown == []
    assert f == {"status": {"$eq": "failed"}}
    assert "risk" in FIELD_MAP and "risk" in SEARCH_HELP["fields"]
