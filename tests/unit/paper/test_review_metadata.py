import pytest

from mathmodel_paper_studio.paper.review_metadata import (
    PaperReviewMetadata, decode_review_metadata, encode_review_metadata,
)


def test_review_metadata_round_trip_deduplicates_without_losing_pending_state():
    metadata = PaperReviewMetadata(
        ('ev_example', 'ev_example'), ('检查边界',), ('核对来源',),
        body_character_count=7439,
        problem_result_evidence=(('problem_1', ('ev_example',)),),
    )
    assert decode_review_metadata(encode_review_metadata(metadata)) == metadata
    assert metadata.evidence_ids == ('ev_example',)


def test_legacy_review_metadata_remains_readable():
    payload = (b'<mmps:review xmlns:mmps="urn:mathmodel-paper-studio:paper-review:1" '
               b'version="1"><mmps:evidence>ev_example</mmps:evidence></mmps:review>')
    metadata = decode_review_metadata(payload)
    assert metadata.evidence_ids == ('ev_example',)
    assert metadata.body_character_count is None


@pytest.mark.parametrize('payload', (
    b'', b'<bad/>', b'<!DOCTYPE review [<!ENTITY x SYSTEM "file:///not-readable">]><review>&x;</review>',
    b'<mmps:review xmlns:mmps="urn:mathmodel-paper-studio:paper-review:1" version="3"/>',
    b'<mmps:review xmlns:mmps="urn:mathmodel-paper-studio:paper-review:1" version="1"><mmps:unknown>x</mmps:unknown></mmps:review>',
    b'<mmps:review xmlns:mmps="urn:mathmodel-paper-studio:paper-review:1" version="1"><mmps:evidence>../bad</mmps:evidence></mmps:review>',
), ids=('empty', 'wrong-root', 'xxe', 'wrong-version', 'unknown-field', 'bad-evidence'))
def test_untrusted_review_metadata_fails_closed(payload):
    with pytest.raises(ValueError):
        decode_review_metadata(payload)


def test_oversized_review_metadata_fails_closed():
    with pytest.raises(ValueError):
        decode_review_metadata(b'x' * (1024 * 1024 + 1))


@pytest.mark.parametrize('values', (('BAD ID',), (None,), ('',)))
def test_metadata_rejects_invalid_evidence(values):
    with pytest.raises(ValueError):
        PaperReviewMetadata(values)


def test_problem_result_metadata_rejects_unhashable_evidence_as_validation_error():
    with pytest.raises(ValueError):
        PaperReviewMetadata(problem_result_evidence=(('problem_1', ([],)),))
