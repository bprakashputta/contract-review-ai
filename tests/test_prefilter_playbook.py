from contract_review_ai.models import Clause, Quote
from contract_review_ai.parsing import from_text
from contract_review_ai.playbook import Playbook, evaluate, jurisdictions, notice_days
from contract_review_ai.prefilter import BM25, select_sections
from tests.conftest import CONTRACT


def test_bm25_ranks_matching_doc_first():
    bm = BM25([["governing", "law", "ohio"], ["payment", "terms"], ["law", "firm"]])
    s = bm.scores(["governing", "law"])
    assert s[0] > s[2] > s[1]


def test_prefilter_selects_the_governing_law_section():
    doc = from_text("c.txt", CONTRACT)
    sel = select_sections(doc, k=1)
    gl = [doc.section_text(doc.sections[i]) for i in sel.by_category["governing_law"]]
    assert any("governed by the laws" in t for t in gl)
    assert sel.union == sorted(set(sel.union))


def test_notice_days_parses_words_digits_and_units():
    assert notice_days("at least ninety (90) days' prior written notice") == [90]
    assert notice_days("no later than 3 months before") == [90]
    assert notice_days("fifteen (15) business days") == [15]


def test_jurisdictions():
    assert jurisdictions("governed by the laws of the State of New York") == ["New York"]
    assert jurisdictions("laws of West Virginia") == ["West Virginia"]


def _clause(cat, text=None):
    if text is None:
        return Clause(category=cat, label=cat, found=False)
    q = Quote(text=text, model_text=text, status="exact", start=0, end=len(text))
    return Clause(category=cat, label=cat, found=True, quotes=[q])


PB = Playbook.model_validate(
    {
        "name": "t",
        "rules": [
            {
                "id": "cap",
                "type": "required",
                "category": "cap_on_liability",
                "severity": "high",
                "message": "no cap",
            },
            {
                "id": "nc",
                "type": "forbidden",
                "category": "non_compete",
                "severity": "medium",
                "message": "nc",
            },
            {
                "id": "renew",
                "type": "min_notice_days",
                "category": "renewal_term",
                "notice_category": "notice_to_terminate_renewal",
                "min_days": 30,
                "severity": "medium",
                "message": "short notice",
                "missing_message": "no notice",
            },
            {
                "id": "law",
                "type": "allowed_values",
                "category": "governing_law",
                "allowed": ["Delaware"],
                "severity": "low",
                "message": "law",
            },
        ],
    }
)


def test_rules_fire_on_verified_clauses():
    flags = evaluate(
        PB,
        [
            _clause("cap_on_liability"),
            _clause("non_compete", "shall not compete"),
            _clause("renewal_term", "automatically renew"),
            _clause("notice_to_terminate_renewal", "fifteen (15) days prior to the end"),
            _clause("governing_law", "laws of the State of Ohio"),
        ],
    )
    ids = [f.rule_id for f in flags]
    assert ids == ["cap", "nc", "renew", "law"]  # sorted by severity
    assert "15 days" in flags[2].message
    assert "Ohio" in flags[3].message


def test_rules_quiet_when_policy_is_met():
    flags = evaluate(
        PB,
        [
            _clause("cap_on_liability", "shall not exceed"),
            _clause("renewal_term", "automatically renew"),
            _clause("notice_to_terminate_renewal", "sixty (60) days"),
            _clause("governing_law", "laws of Delaware"),
        ],
    )
    assert flags == []


def test_auto_renewal_without_notice_clause_is_flagged():
    flags = evaluate(PB, [_clause("cap_on_liability", "x"), _clause("renewal_term", "renews")])
    assert [f.message for f in flags] == ["no notice"]


def test_unknown_category_rejected():
    import pytest

    with pytest.raises(ValueError):
        Playbook.model_validate(
            {
                "name": "x",
                "rules": [
                    {
                        "id": "a",
                        "type": "required",
                        "category": "nope",
                        "severity": "high",
                        "message": "m",
                    }
                ],
            }
        )
