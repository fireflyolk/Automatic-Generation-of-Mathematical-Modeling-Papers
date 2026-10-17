"""Evidence references for paper claims; hashes prove identity, never mathematics."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from types import MappingProxyType

from mathmodel_paper_studio.domain.models import (
    ArtifactEvidence, identifier, relative_path, sha256_digest,
)
from mathmodel_paper_studio.execution.python_runner import (
    RunArtifactLayout, _atomic_create, _evidence,
)


class ClaimEvidenceStatus(str, Enum):
    EVIDENCE_LINKED = 'evidence_linked'
    UNVERIFIED = 'unverified'


@dataclass(frozen=True)
class EvidenceCitation:
    evidence_id: str
    relative_path: str
    sha256: str
    kind: str

    def __post_init__(self):
        identifier(self.evidence_id)
        relative_path(self.relative_path)
        sha256_digest(self.sha256)
        if self.kind not in ('script', 'table', 'figure', 'validation', 'paper', 'profile'):
            raise ValueError('证据类型无效。')


@dataclass(frozen=True)
class NumericClaim:
    claim_id: str
    statement: str
    numeric_value: str
    citations: tuple[EvidenceCitation, ...]
    unresolved_evidence_ids: tuple[str, ...]
    status: ClaimEvidenceStatus
    human_review_required: bool = True
    mathematical_correctness_verified: bool = False

    def __post_init__(self):
        identifier(self.claim_id)
        if not isinstance(self.statement, str) or not self.statement.strip() or len(self.statement) > 2000:
            raise ValueError('数值声明必须包含不超过 2000 字的说明。')
        if not isinstance(self.numeric_value, str) or len(self.numeric_value) > 128:
            raise ValueError('数值声明必须使用有限的短文本表示。')
        try:
            value = Decimal(self.numeric_value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError('数值声明不是有效数字。') from exc
        if not value.is_finite():
            raise ValueError('数值声明不能是 NaN 或 Inf。')
        if not isinstance(self.citations, (tuple, list)) or not isinstance(self.unresolved_evidence_ids, (tuple, list)):
            raise ValueError('数值声明证据必须为列表或元组。')
        citations = tuple(self.citations)
        unresolved = tuple(self.unresolved_evidence_ids)
        if any(not isinstance(item, EvidenceCitation) for item in citations):
            raise ValueError('证据引用类型无效。')
        for item in unresolved:
            identifier(item)
        if len({item.evidence_id for item in citations}) != len(citations) or len(set(unresolved)) != len(unresolved):
            raise ValueError('同一数值声明不能重复引用证据。')
        if type(self.human_review_required) is not bool or self.human_review_required is not True:
            raise ValueError('数值声明始终需要人工复核。')
        if type(self.mathematical_correctness_verified) is not bool or self.mathematical_correctness_verified is not False:
            raise ValueError('工件哈希不能证明数学正确性。')
        object.__setattr__(self, 'citations', citations)
        object.__setattr__(self, 'unresolved_evidence_ids', unresolved)
        object.__setattr__(self, 'status', ClaimEvidenceStatus(self.status))
        expected_status = (ClaimEvidenceStatus.EVIDENCE_LINKED
                           if citations and not unresolved else ClaimEvidenceStatus.UNVERIFIED)
        if self.status is not expected_status:
            raise ValueError('数值声明证据状态与引用不一致。')


class EvidenceIndex:
    """Indexes verified artifact identities and links them to reviewable claims."""

    def __init__(self, evidence):
        if not isinstance(evidence, (tuple, list)) or len(evidence) > 4096:
            raise ValueError('证据集合类型无效或超过上限。')
        indexed = {}
        for item in evidence:
            if not isinstance(item, ArtifactEvidence):
                raise TypeError('证据集合包含无效类型。')
            if item.evidence_id in indexed:
                raise ValueError('证据标识符重复。')
            indexed[item.evidence_id] = item
        self._evidence = MappingProxyType(indexed)

    @property
    def evidence(self) -> tuple[ArtifactEvidence, ...]:
        return tuple(self._evidence.values())

    def numeric_claim(self, claim_id: str, statement: str, numeric_value,
                      evidence_ids=()) -> NumericClaim:
        if isinstance(numeric_value, bool) or not isinstance(numeric_value, (str, int, float, Decimal)):
            raise ValueError('数值声明必须是数字。')
        value_text = str(numeric_value)
        if len(value_text) > 128:
            raise ValueError('数值声明过长。')
        try:
            value = Decimal(value_text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError('数值声明不是有效数字。') from exc
        if not value.is_finite():
            raise ValueError('数值声明不能是 NaN 或 Inf。')
        if not isinstance(evidence_ids, (tuple, list)) or len(evidence_ids) > 64:
            raise ValueError('证据标识符集合无效或超过上限。')
        requested = tuple(evidence_ids)
        for item in requested:
            identifier(item)
        if len(set(requested)) != len(requested):
            raise ValueError('证据标识符不能重复。')

        citations = []
        unresolved = []
        for evidence_id in requested:
            item = self._evidence.get(evidence_id)
            if item is None or not item.verified:
                unresolved.append(evidence_id)
            else:
                citations.append(EvidenceCitation(
                    item.evidence_id, item.relative_path, item.sha256, item.kind
                ))
        status = (ClaimEvidenceStatus.EVIDENCE_LINKED
                  if citations and not unresolved else ClaimEvidenceStatus.UNVERIFIED)
        return NumericClaim(claim_id, statement, value_text, tuple(citations), tuple(unresolved),
                            status, True, False)

    def write_claim_manifest(self, layout: RunArtifactLayout, claims):
        if not isinstance(layout, RunArtifactLayout):
            raise ValueError('运行证据目录无效。')
        if not isinstance(claims, (tuple, list)) or len(claims) > 1024:
            raise ValueError('数值声明集合无效或超过上限。')
        values = tuple(claims)
        if any(not isinstance(item, NumericClaim) for item in values):
            raise TypeError('数值声明集合包含无效类型。')
        if len({item.claim_id for item in values}) != len(values):
            raise ValueError('声明标识符不能重复。')

        run_prefix = layout.run.relative_path + '/'
        for item in self.evidence:
            if item.run_id != layout.run.run_id or not item.relative_path.startswith(run_prefix):
                raise ValueError('不能把其他运行的证据放入本次运行清单。')
        for claim in values:
            for citation in claim.citations:
                source = self._evidence.get(citation.evidence_id)
                if (source is None or source.verified is not True
                        or source.run_id != layout.run.run_id
                        or not source.relative_path.startswith(run_prefix)
                        or source.sha256 != citation.sha256
                        or source.relative_path != citation.relative_path
                        or source.kind != citation.kind):
                    raise ValueError('数值声明引用的证据不在本次运行索引中。')

        destination = None
        for version in range(1, 10000):
            candidate = layout.path_for(f'40_results/evidence-v{version:04d}.json')
            if not candidate.exists():
                destination = candidate
                break
        if destination is None:
            raise ValueError('证据清单版本数量超过上限。')
        manifest = {
            'schema_version': 1,
            'run_id': layout.run.run_id,
            'mathematical_correctness_verified': False,
            'human_review_required': True,
            'evidence': [
                {'evidence_id': item.evidence_id, 'relative_path': item.relative_path,
                 'sha256': item.sha256, 'kind': item.kind, 'structurally_verified': item.verified}
                for item in self.evidence
            ],
            'numeric_claims': [
                {'claim_id': item.claim_id, 'statement': item.statement,
                 'numeric_value': item.numeric_value, 'status': item.status.value,
                 'evidence': [
                     {'evidence_id': ref.evidence_id, 'relative_path': ref.relative_path,
                      'sha256': ref.sha256, 'kind': ref.kind}
                     for ref in item.citations
                 ],
                 'unresolved_evidence_ids': list(item.unresolved_evidence_ids),
                 'human_review_required': True,
                 'mathematical_correctness_verified': False}
                for item in values
            ],
        }
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')
        if len(encoded) > 4 * 1024 * 1024:
            raise ValueError('证据清单超过 4 MiB 上限。')
        _atomic_create(destination, encoded)
        relative = destination.relative_to(layout.root).as_posix()
        artifact = _evidence(layout, relative, 'validation', destination)
        return destination, artifact
