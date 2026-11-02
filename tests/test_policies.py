import re
from difflib import SequenceMatcher

import pytest
from pypdf import PdfReader

from emporio.config import PROJECT_ROOT
from emporio.data import normalize
from emporio.policies import DEADLINES, SECTION_SUMMARIES, load_policies

DATA = PROJECT_ROOT / "data"


@pytest.fixture(scope="module")
def policies():
    return load_policies(DATA / "policies.md")


def test_all_sections_present(policies):
    assert list(policies) == list(SECTION_SUMMARIES)


def test_transcription_is_faithful_to_pdf():
    """Word-level comparison of the PDF text with the markdown transcription."""
    pdf_text = " ".join(page.extract_text() for page in PdfReader(DATA / "politicas_da_loja.pdf").pages)
    pdf = normalize(pdf_text)
    pdf = pdf.replace("emporio da musica manual de politicas e procedimentos", " ")  # running page header
    pdf = re.sub(r"pagina \d+", " ", pdf)  # page footer
    markdown = re.sub(r"<!--.*?-->", " ", (DATA / "policies.md").read_text(encoding="utf-8"), flags=re.S)
    pdf_words, md_words = pdf.split(), normalize(markdown).split()

    diffs = [
        (pdf_words[i1:i2], md_words[j1:j2])
        for op, i1, i2, j1, j2 in SequenceMatcher(None, pdf_words, md_words, autojunk=False).get_opcodes()
        if op != "equal"
    ]
    assert not diffs, f"transcription differs from the PDF (pdf, markdown): {diffs[:5]}"


@pytest.mark.parametrize(
    "section, fact",
    [
        ("about_and_contact", "Rua 14 de Maio, 3200"),
        ("opening_hours", "09:00 às 18:00"),
        ("payment_methods", "5% de desconto"),
        ("returns_and_exchanges", "até 7 (sete) dias corridos após o recebimento"),
        ("returns_and_exchanges", "até 30 (trinta) dias corridos após a compra"),
        ("warranty", "90 (noventa) dias"),
        ("customer_service_guidelines", "24 horas úteis"),
    ],
)
def test_key_facts_in_expected_section(policies, section, fact):
    assert fact in policies[section]


@pytest.mark.parametrize("name, limit, anchor, section, wording", DEADLINES)
def test_coded_deadlines_still_match_the_manual(policies, name, limit, anchor, section, wording):
    assert wording in policies[section], f"{name}: the manual no longer says '{wording}'"
    assert str(limit) in wording
