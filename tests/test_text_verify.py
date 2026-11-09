from contract_review_ai.text import normalize, normalize_with_map
from contract_review_ai.verify import DocumentIndex
from tests.conftest import CONTRACT


def test_normalize_maps_back_to_original_offsets():
    text = "The  “Supplier” shall\n\nNOT — assign."
    norm, offsets = normalize_with_map(text)
    assert norm == 'the "supplier" shall not - assign.'
    assert len(norm) == len(offsets)
    i = norm.index("shall")
    assert text[offsets[i] : offsets[i + 4] + 1] == "shall"


def test_separator_lines_count_as_whitespace():
    assert normalize("party ---------- without") == "party without"


def test_exact_match_ignores_case_quotes_and_whitespace():
    idx = DocumentIndex(CONTRACT)
    v = idx.verify("in NO event shall either party's aggregate   liability exceed the fees")
    assert v.status == "exact"
    assert CONTRACT[v.start : v.end].startswith("In no event")


def test_quote_across_page_break_is_repaired_to_document_text():
    text = "Neither party may assign this Agreement without\n\nAcme Confidential  Page 7\n\nthe prior written consent of the other party."
    v = DocumentIndex(text).verify(
        "Neither party may assign this Agreement without the prior written consent of the other party."
    )
    assert v.status == "repaired"
    assert text[v.start : v.end].endswith("other party.")
    assert "Page 7" in text[v.start : v.end]


def test_small_wording_change_is_repaired_by_fuzzy_match():
    v = DocumentIndex(CONTRACT).verify(
        "This Agreement shall be governed by the law of the State of Ohio."
    )
    assert v.status == "repaired"


def test_completed_sentence_the_document_truncates_is_unverified():
    text = "12. Assignment\n\nNeither party may assign its rights without the consent of the other, which consent shall\n\nGeneral Liability: Vendor shall maintain insurance."
    v = DocumentIndex(text).verify(
        "Neither party may assign its rights without the consent of the other, which consent "
        "shall not be unreasonably withheld."
    )
    assert v.status == "unverified"


def test_fabricated_quote_is_unverified():
    v = DocumentIndex(CONTRACT).verify("Supplier shall have the exclusive right to sell in Canada.")
    assert not v.ok


def test_elided_quote_in_order_is_verified():
    v = DocumentIndex(CONTRACT).verify(
        "Either party may terminate this Agreement ... ninety (90) days' prior written notice"
    )
    assert v.status == "exact"


def test_exact_only_mode_refuses_repairs():
    v = DocumentIndex(CONTRACT).verify(
        "This Agreement shall be governed by the law of the State of Ohio.", allow_repair=False
    )
    assert v.status == "unverified"
