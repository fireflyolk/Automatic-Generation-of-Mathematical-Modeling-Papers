"""Canonical, versioned paper-writing standards shared by the workflow."""
from dataclasses import dataclass, replace
import hashlib
import json
from types import MappingProxyType
from typing import Mapping

from mathmodel_paper_studio.domain.models import Stage


def _immutable_text_map(value: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f'{field_name} must be a mapping.')
    entries = {}
    for key, item in value.items():
        if (not isinstance(key, str) or not key or len(key) > 80
                or not isinstance(item, str) or not item or len(item) > 4000):
            raise ValueError(f'{field_name} contains an invalid entry.')
        entries[key] = item
    return MappingProxyType(dict(sorted(entries.items())))


@dataclass(frozen=True)
class PaperStandard:
    """Immutable writing and review contract with a stable canonical digest."""

    standard_id: str
    version: str
    display_name: str
    target_pages: int
    required_sections: tuple[str, ...]
    optional_sections: tuple[str, ...]
    content_rules: Mapping[str, str]
    style_defaults: Mapping[str, str]
    stage_rules: Mapping[str, tuple[str, ...]]
    minimum_pages: int | None = None
    maximum_pages: int | None = None
    target_body_characters: int | None = None
    recommended_equation_count: int | None = None
    recommended_flowchart_count: int | None = None

    def __post_init__(self):
        for name in ('standard_id', 'version', 'display_name'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError(f'{name} is invalid.')
        if type(self.target_pages) is not int or not 1 <= self.target_pages <= 500:
            raise ValueError('target_pages must be between 1 and 500.')
        page_bounds = (self.minimum_pages, self.maximum_pages)
        if any(value is not None and (type(value) is not int or not 1 <= value <= 500)
               for value in page_bounds):
            raise ValueError('Page range bounds are invalid.')
        if (self.minimum_pages is not None and self.maximum_pages is not None
                and self.minimum_pages > self.maximum_pages):
            raise ValueError('Minimum pages must not exceed maximum pages.')
        bounded_targets = (
            (self.target_body_characters, 0, 1_000_000),
            (self.recommended_equation_count, 0, 256),
            (self.recommended_flowchart_count, 0, 64),
        )
        if any(value is not None and (type(value) is not int or not low <= value <= high)
               for value, low, high in bounded_targets):
            raise ValueError('Paper content targets are invalid.')
        for name in ('required_sections', 'optional_sections'):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or len(values) > 64:
                raise ValueError(f'{name} must be a bounded sequence.')
            normalized = tuple(values)
            if any(not isinstance(item, str) or not item or len(item) > 80 for item in normalized):
                raise ValueError(f'{name} contains an invalid section key.')
            if len(set(normalized)) != len(normalized):
                raise ValueError(f'{name} contains duplicate section keys.')
            object.__setattr__(self, name, normalized)
        if set(self.required_sections) & set(self.optional_sections):
            raise ValueError('Required and optional sections must be disjoint.')
        object.__setattr__(self, 'content_rules', _immutable_text_map(self.content_rules, 'content_rules'))
        object.__setattr__(self, 'style_defaults', _immutable_text_map(self.style_defaults, 'style_defaults'))
        if not isinstance(self.stage_rules, Mapping):
            raise TypeError('stage_rules must be a mapping.')
        stage_rules = {}
        for stage, keys in self.stage_rules.items():
            if not isinstance(stage, str) or not stage or len(stage) > 80:
                raise ValueError('stage_rules contains an invalid stage.')
            if not isinstance(keys, (tuple, list)) or not keys or len(keys) > 32:
                raise ValueError('stage_rules contains an invalid rule list.')
            selected = tuple(keys)
            if any(not isinstance(key, str) or key not in self.content_rules for key in selected):
                raise ValueError('stage_rules references an unknown content rule.')
            if len(set(selected)) != len(selected):
                raise ValueError('stage_rules contains duplicate rule keys.')
            stage_rules[stage] = selected
        object.__setattr__(self, 'stage_rules', MappingProxyType(dict(sorted(stage_rules.items()))))

    def guidance_for(self, stage: Stage | str) -> str:
        """Return only concise standard rules relevant to one workflow stage."""
        key = stage.value if isinstance(stage, Stage) else stage
        if not isinstance(key, str):
            raise TypeError('stage must be a workflow stage or its string value.')
        rule_keys = self.stage_rules.get(key, ())
        lines = [f'论文规范：{self.display_name}（{self.standard_id}，{self.version}）。']
        lines.extend(f'- {self.content_rules[rule_key]}' for rule_key in rule_keys)
        return '\n'.join(lines)

    def _canonical_object(self) -> dict:
        payload = {
            'standard_id': self.standard_id,
            'version': self.version,
            'display_name': self.display_name,
            'target_pages': self.target_pages,
            'required_sections': list(self.required_sections),
            'optional_sections': list(self.optional_sections),
            'content_rules': dict(self.content_rules),
            'style_defaults': dict(self.style_defaults),
            'stage_rules': {key: list(value) for key, value in self.stage_rules.items()},
        }
        extended = {
            'minimum_pages': self.minimum_pages,
            'maximum_pages': self.maximum_pages,
            'target_body_characters': self.target_body_characters,
            'recommended_equation_count': self.recommended_equation_count,
            'recommended_flowchart_count': self.recommended_flowchart_count,
        }
        payload.update({key: value for key, value in extended.items() if value is not None})
        return payload

    def canonical_payload(self) -> bytes:
        return json.dumps(self._canonical_object(), ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload()).hexdigest()

    def __hash__(self) -> int:
        return hash(self.sha256)


_LEGACY_STANDARD = PaperStandard(
    standard_id='b078-competition-v1',
    version='1.0.0',
    display_name='B078 数模论文规范',
    target_pages=30,
    required_sections=(
        'abstract', 'problem_restatement', 'problem_analysis', 'assumptions', 'notation',
        'model', 'solution', 'results', 'verification', 'conclusion', 'references',
    ),
    optional_sections=(
        'sensitivity_analysis', 'robustness_check', 'computational_complexity', 'appendix',
    ),
    content_rules={
        'structure': '按题目组织摘要、关键词、问题重述与分析、假设、符号、建模推导、求解、结果、检验、结论和参考文献；需要时使用十进制小节编号；可合并或调整章节，不写与题目无关的内容。',
        'mathematics': '说明变量、单位、假设、目标与约束；解释关键公式及推导关系；算法交代输入、步骤、停止条件和适用边界。',
        'evidence': '结果只能来自已选输入或本次运行的已验证产物；区分输入、计算、估算和未验证内容；不得虚构数值、实验、基线或引用。',
        'figures_tables': '图表与公式编号连续，标题靠近对象；正文解释单位、结果含义及其与结论的关系，不重复图表填充篇幅。',
        'reproducibility': '仅在项目实际拥有时纳入代码、数据字典、验证记录或复现步骤；不机械复制未经审核的代码或原始数据。',
        'references': '只列出有来源依据的参考资料；没有可验证来源时明确提示待补，不生成虚构引用。',
        'conclusion': '结论逐项回答子问题并说明模型作用、优点、不足和适用范围；未经验证的推测不得写成确定结果。',
        'length': '约 30 页仅作为内容规划目标；篇幅不足时报告原因和补充建议，不插入空白页、重复段落或无关内容。',
        'review': '规范检查不等于数学正确性验证；结论、模型假设和科学解释仍需人工复核。',
    },
    style_defaults={
        'page_size': 'A4',
        'margins': '2.54 cm',
        'body_font': '宋体',
        'body_size_pt': '10.5',
        'body_line_spacing': '1.5',
        'title_font': '黑体',
        'title_size_pt': '18',
        'heading_font': '黑体',
        'heading1_size_pt': '15',
        'heading2_size_pt': '13',
        'heading3_size_pt': '11',
        'caption_size_pt': '9',
        'heading_numbering': '中文主章节与十进制子章节',
        'front_page': '题目、摘要、关键词从首页开始；不添加空白封面',
        'captions': '表格标题置于表格前，图标题置于图后，编号连续',
        'footer': '居中页码域',
    },
    stage_rules={
        Stage.DATA_PROFILE.value: ('evidence',),
        Stage.PROBLEM_DECOMPOSITION.value: ('structure', 'evidence'),
        Stage.MODEL_PROPOSAL.value: ('structure', 'mathematics', 'evidence'),
        Stage.RESULT_VALIDATION.value: ('mathematics', 'evidence'),
        Stage.PAPER_DRAFTING.value: (
            'structure', 'mathematics', 'evidence', 'figures_tables',
            'reproducibility', 'references', 'conclusion', 'length', 'review',
        ),
        Stage.DOCX_QA.value: ('structure', 'figures_tables', 'references', 'conclusion', 'length', 'review'),
    },
)

_DEFAULT_STANDARD = replace(
    _LEGACY_STANDARD,
    version='1.1.0',
    target_pages=45,
    content_rules={
        **dict(_LEGACY_STANDARD.content_rules),
        'mathematics': (
            '给出模型变量、单位、假设、目标和约束；按逻辑顺序推导关键公式，逐一解释符号、假设来源和适用范围；'
            '用公式对象表达模型、算法和验证指标，建议至少 12 个有实际作用的可编辑公式，不拆分或重复表达式凑数。'
        ),
        'figures_tables': (
            '公式、图表连续编号且标题贴近对象；结果图必须来自已验证运行产物。按题目需要规划 3–5 张流程图，'
            '流程图仅表达可核对的研究步骤、模型关系、求解过程或验证路径；每张图后写清节点、分支条件和结论含义。'
            '不能重复图表来填充篇幅。'
        ),
        'length': (
            '论文目标 40–50 页，规划中心为 45 页，并力争约 50,000 个有实质内容的正文字符；'
            '通过题目分析、推导、求解、证据解释和复现说明形成篇幅，不插入空白页、重复段落、无关内容或虚构结果。'
            '题目或证据不足时如实报告缺口，不以内容数量作为工作流暂停条件。'
        ),
    },
    minimum_pages=40,
    maximum_pages=50,
    target_body_characters=50_000,
    recommended_equation_count=12,
    recommended_flowchart_count=3,
)

_STANDARD_REGISTRY = {
    (_LEGACY_STANDARD.standard_id, _LEGACY_STANDARD.version): _LEGACY_STANDARD,
    (_DEFAULT_STANDARD.standard_id, _DEFAULT_STANDARD.version): _DEFAULT_STANDARD,
}


def get_default_paper_standard() -> PaperStandard:
    """Return the current built-in profile; the frozen instance is safe to share."""
    return _DEFAULT_STANDARD


def get_paper_standard(standard_id: str, version: str, sha256: str) -> PaperStandard | None:
    """Resolve an archived profile only when its id, version, and digest agree."""
    if any(not isinstance(value, str) or not value for value in (standard_id, version, sha256)):
        return None
    selected = _STANDARD_REGISTRY.get((standard_id, version))
    return selected if selected is not None and selected.sha256 == sha256 else None


def canonical_payload(standard: PaperStandard | None = None) -> bytes:
    """Serialize a profile deterministically for manifests and audit records."""
    selected = get_default_paper_standard() if standard is None else standard
    if not isinstance(selected, PaperStandard):
        raise TypeError('standard must be a PaperStandard.')
    return selected.canonical_payload()
