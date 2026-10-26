"""Non-rendered audit data for formal Word papers.

Evidence bookkeeping and pending checks belong in the application, not in the
paper's paragraphs. A bounded custom XML part lets standalone QA retain that
information without printing it or mistaking its absence for verification.
"""
from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException


REVIEW_PART = 'customXml/mmps-paper-review.xml'
REVIEW_RELATIONSHIP = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml'
REVIEW_TARGET = '../' + REVIEW_PART
_NS = 'urn:mathmodel-paper-studio:paper-review:1'
_TAG = '{' + _NS + '}'
_MAX_BYTES = 1024 * 1024
_MAX_ITEMS = 4096
_EVIDENCE_ID = re.compile(r'^[a-z][a-z0-9_-]{2,63}$')
_PROBLEM_ID = re.compile(r'^problem_[1-9][0-9]?$')
ET.register_namespace('mmps', _NS)


def _unique(values):
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class PaperReviewMetadata:
    evidence_ids: tuple[str, ...] = ()
    pending_reviews: tuple[str, ...] = ()
    pending_citations: tuple[str, ...] = ()
    body_character_count: int | None = None
    problem_result_evidence: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self):
        for key in ('evidence_ids', 'pending_reviews', 'pending_citations'):
            values = getattr(self, key)
            if not isinstance(values, (tuple, list)) or len(values) > _MAX_ITEMS:
                raise ValueError('Paper audit metadata collection is invalid.')
            if any(not isinstance(v, str) or not v.strip() or len(v) > 100000 for v in values):
                raise ValueError('Paper audit metadata text is invalid.')
            object.__setattr__(self, key, _unique(values))
        if any(not _EVIDENCE_ID.fullmatch(v) for v in self.evidence_ids):
            raise ValueError('Paper audit evidence identifier is invalid.')
        if (self.body_character_count is not None
                and (type(self.body_character_count) is not int
                     or not 0 <= self.body_character_count <= 1_000_000)):
            raise ValueError('Paper audit body character count is invalid.')
        if (not isinstance(self.problem_result_evidence, (tuple, list))
                or len(self.problem_result_evidence) > 12):
            raise ValueError('Paper problem-result evidence map is invalid.')
        problem_ids = set()
        normalized = []
        for item in self.problem_result_evidence:
            if (not isinstance(item, (tuple, list)) or len(item) != 2
                    or not isinstance(item[0], str) or _PROBLEM_ID.fullmatch(item[0]) is None
                    or item[0] in problem_ids or not isinstance(item[1], (tuple, list))
                    or len(item[1]) > 64):
                raise ValueError('Paper problem-result evidence entry is invalid.')
            evidence_ids = tuple(item[1])
            if any(not isinstance(value, str) or _EVIDENCE_ID.fullmatch(value) is None
                   for value in evidence_ids):
                raise ValueError('Paper problem-result evidence identifier is invalid.')
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ValueError('Paper problem-result evidence identifier is invalid.')
            problem_ids.add(item[0])
            normalized.append((item[0], evidence_ids))
        object.__setattr__(self, 'problem_result_evidence', tuple(normalized))


def encode_review_metadata(metadata: PaperReviewMetadata) -> bytes:
    if not isinstance(metadata, PaperReviewMetadata):
        raise TypeError('Paper audit metadata is required.')
    version = '2' if metadata.body_character_count is not None or metadata.problem_result_evidence else '1'
    attributes = {'version': version}
    if metadata.body_character_count is not None:
        attributes['bodyCharacters'] = str(metadata.body_character_count)
    root = ET.Element(_TAG + 'review', attributes)
    for tag, values in (
        ('evidence', metadata.evidence_ids), ('pendingReview', metadata.pending_reviews),
        ('pendingCitation', metadata.pending_citations),
    ):
        for value in values:
            ET.SubElement(root, _TAG + tag).text = value
    for problem_id, evidence_ids in metadata.problem_result_evidence:
        problem = ET.SubElement(root, _TAG + 'problemResultEvidence', {'problemId': problem_id})
        for evidence_id in evidence_ids:
            ET.SubElement(problem, _TAG + 'evidence').text = evidence_id
    payload = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if len(payload) > _MAX_BYTES:
        raise ValueError('Paper audit metadata exceeds its size limit.')
    return payload


def decode_review_metadata(payload: bytes) -> PaperReviewMetadata:
    if not isinstance(payload, bytes) or not payload or len(payload) > _MAX_BYTES:
        raise ValueError('Paper audit metadata payload is invalid.')
    try:
        root = SafeET.fromstring(payload, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ValueError, ET.ParseError, DefusedXmlException) as exc:
        raise ValueError('Paper audit metadata XML is invalid.') from exc
    if root.tag != _TAG + 'review':
        raise ValueError('Paper audit metadata schema is unsupported.')
    values = {'evidence': [], 'pendingReview': [], 'pendingCitation': []}
    problem_results = []
    for child in root:
        key = child.tag.removeprefix(_TAG)
        if child.tag != _TAG + key:
            raise ValueError('Paper audit metadata contains an unknown field.')
        if key in values and not child.attrib and not len(child):
            values[key].append(child.text or '')
            continue
        if (key == 'problemResultEvidence' and set(child.attrib) == {'problemId'}
                and all(item.tag == _TAG + 'evidence' and not item.attrib and not len(item)
                        for item in child)):
            problem_results.append((child.get('problemId'), tuple(item.text or '' for item in child)))
            continue
        raise ValueError('Paper audit metadata contains an unknown field.')
    version = root.get('version')
    body_count = None
    if version == '1':
        if root.attrib != {'version': '1'} or problem_results:
            raise ValueError('Paper audit metadata schema is unsupported.')
    elif version == '2':
        allowed_attributes = {'version', 'bodyCharacters'}
        if set(root.attrib) - allowed_attributes or not (problem_results or 'bodyCharacters' in root.attrib):
            raise ValueError('Paper audit metadata schema is unsupported.')
        raw_count = root.get('bodyCharacters')
        if raw_count is not None:
            if re.fullmatch(r'(?:0|[1-9][0-9]{0,6})', raw_count) is None:
                raise ValueError('Paper audit body character count is invalid.')
            body_count = int(raw_count)
    else:
        raise ValueError('Paper audit metadata schema is unsupported.')
    return PaperReviewMetadata(
        tuple(values['evidence']), tuple(values['pendingReview']), tuple(values['pendingCitation']),
        body_count, tuple(problem_results),
    )
