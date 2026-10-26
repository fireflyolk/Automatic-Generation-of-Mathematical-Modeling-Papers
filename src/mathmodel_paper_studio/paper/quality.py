"""Content-focused writing checks for question-driven modeling papers."""
from __future__ import annotations


_CHINESE_NUMERALS = ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二')
COVERAGE_SECTION_MINIMUMS = {
    'problem_analysis': 220,
    'model': 240,
    'solution': 180,
    'results': 220,
    'conclusion': 120,
}
FINAL_COVERAGE_SECTION_MINIMUMS = {
    **COVERAGE_SECTION_MINIMUMS,
    # The model-core and model-derivation batches each contribute one part.
    'model': 480,
}
_BATCH_COVERAGE = {
    'analysis': 'problem_analysis',
    'model_core': 'model',
    'model_derivation': 'model',
    'solution': 'solution',
    'results': 'results',
    'closing': 'conclusion',
}


def problem_scopes(decomposition: dict) -> tuple[dict, ...]:
    """Build stable labels from the approved problem decomposition artifact."""
    if not isinstance(decomposition, dict) or set(decomposition) != {'subproblems'}:
        raise ValueError('题目分解结构无效，不能建立逐题写作范围。')
    subproblems = decomposition['subproblems']
    if not isinstance(subproblems, list) or not 1 <= len(subproblems) <= len(_CHINESE_NUMERALS):
        raise ValueError('题目分解数量无效，不能建立逐题写作范围。')
    if any(not isinstance(text, str) or not text.strip() or len(text) > 8000 or '\0' in text
           for text in subproblems):
        raise ValueError('题目分解包含无效子问题。')
    return tuple({
        'problem_id': f'problem_{index}',
        'heading': f'问题{_CHINESE_NUMERALS[index - 1]}',
        'description': text.strip(),
    } for index, text in enumerate(subproblems, start=1))


def batch_problem_coverage(batch_id: str, scopes: tuple[dict, ...]) -> dict | None:
    """Return the bounded coverage requirement for a problem-focused batch."""
    if not isinstance(batch_id, str) or not isinstance(scopes, tuple):
        raise TypeError('论文子问题覆盖输入无效。')
    target = _BATCH_COVERAGE.get(batch_id)
    if target is None or not scopes:
        return None
    section_key = target
    return {
        'section_key': section_key,
        'minimum_characters': COVERAGE_SECTION_MINIMUMS[section_key],
        'require_evidence': batch_id == 'results',
        'problems': [dict(item) for item in scopes],
    }


def validate_problem_coverage(sections: list[dict], requirement: dict | None) -> None:
    """Require a substantive subsection for every decomposed problem in a batch."""
    if requirement is None:
        return
    if (not isinstance(requirement, dict)
            or set(requirement) != {'section_key', 'minimum_characters', 'require_evidence', 'problems'}
            or not isinstance(requirement['section_key'], str)
            or type(requirement['minimum_characters']) is not int
            or type(requirement['require_evidence']) is not bool
            or not isinstance(requirement['problems'], list)):
        raise ValueError('子问题覆盖要求无效。')
    section = next((item for item in sections
                    if isinstance(item, dict) and item.get('section_key') == requirement['section_key']), None)
    if section is None:
        raise ValueError('子问题覆盖缺少指定章节。')
    subsections = section.get('subsections', ())
    if not isinstance(subsections, (tuple, list)):
        raise ValueError('子问题覆盖小节结构无效。')
    for problem in requirement['problems']:
        if (not isinstance(problem, dict)
                or set(problem) != {'problem_id', 'heading', 'description'}
                or any(not isinstance(problem.get(key), str) for key in ('problem_id', 'heading', 'description'))):
            raise ValueError('子问题覆盖项无效。')
        matching = [item for item in subsections
                    if isinstance(item, dict)
                    and isinstance(item.get('title'), str)
                    and item['title'].strip().startswith(problem['heading'])]
        if len(matching) != 1:
            raise ValueError(f"子问题覆盖不完整：{problem['heading']}。")
        paragraphs = matching[0].get('paragraphs')
        if (not isinstance(paragraphs, (tuple, list)) or len(paragraphs) < 2
                or any(not isinstance(text, str) for text in paragraphs)
                or sum(len(text.strip()) for text in paragraphs) < requirement['minimum_characters']):
            raise ValueError(f"子问题覆盖内容不足：{problem['heading']}。")
        evidence_ids = matching[0].get('evidence_ids', ())
        if (requirement['require_evidence']
                and (not isinstance(evidence_ids, (tuple, list)) or not evidence_ids
                     or any(not isinstance(item, str) or not item for item in evidence_ids))):
            raise ValueError(f"子问题覆盖缺少已验证结果证据：{problem['heading']}。")


def problem_coverage_evidence(sections, scopes: tuple[dict, ...]) -> tuple[dict, ...]:
    """Carry validated result-evidence links into the DOCX quality manifest."""
    if not isinstance(sections, (tuple, list)) or not isinstance(scopes, tuple):
        raise TypeError('逐题结果证据映射输入无效。')
    results = next((item for item in sections if getattr(item, 'section_key', None) == 'results'), None)
    subsections = () if results is None else getattr(results, 'subsections', ())
    output = []
    for problem in scopes:
        heading = problem['heading']
        matching = [item for item in subsections
                    if isinstance(getattr(item, 'title', None), str)
                    and item.title.strip().startswith(heading)]
        evidence_ids = tuple(dict.fromkeys(
            evidence_id
            for item in matching
            for evidence_id in getattr(item, 'evidence_ids', ())
            if isinstance(evidence_id, str)
        )) if len(matching) == 1 else ()
        output.append({
            'problem_id': problem['problem_id'],
            'section_key': 'results',
            'evidence_ids': evidence_ids,
        })
    return tuple(output)
