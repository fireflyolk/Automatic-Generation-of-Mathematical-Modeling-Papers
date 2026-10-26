"""Remove generation-process commentary from manuscript text."""
import re


_MARKERS = (
    re.compile(
        r'(?:待(?:人工|手动)(?:核验|核查|复核|审核|补充|确认)|(?:人工|手动)(?:核验|核查|复核|审核|补充)\s*[:：]|'
        r'(?:仍需|需要|需|必须|应当|建议|请).{0,24}(?:人工|手动)(?:核验|核查|复核|审核|补充|确认)|'
        r'(?:待专家(?:确认|复核|审核|核查)|待(?:人工|手动)(?:填写|检查)|待补(?:充|文献|数据)|待确认|待填写))',
        re.I,
    ),
    re.compile(r'\b(?:TODO|TBD|FIXME|review_required)\b', re.I),
    re.compile(r'\b(?:needs? manual review|manual review required|to be completed)\b', re.I),
    # Internal application evidence and run provenance stay in the audit record.
    re.compile(
        r'(?:数值|数字|结果|诊断量|定量(?:结果|诊断|结论)|指标).{0,32}'
        r'(?:来自|取自|依据|根据).{0,40}'
        r'(?:所提供|提供的|本次运行|验证表|诊断表|计算表|验证产物|运行产物|应用生成|系统生成|模型输出)',
        re.I,
    ),
    re.compile(
        r'(?:已提供的|所提供的?|本次|当前|现有|同一|模型)?'
        r'(?:计算表|验证表|诊断表|验证产物|运行产物|诊断计算).{0,32}'
        r'(?:所列|以|给出|记录|显示|表明|支持|来自|提供|仅限|范围|口径|中的)',
        re.I,
    ),
    re.compile(r'(?:按|根据|引用|取自).{0,12}(?:诊断表|验证表|验证产物|本次计算表)', re.I),
    # Explicit absence of simulator/formal-test material is a run status, not paper prose.
    re.compile(
        r'(?:当前|现有|本次|所选材料|问题文本|材料中|现有产物|本文|本研究).{0,44}'
        r'(?:未提供|没有提供|没有|缺少|尚无|暂无|不包含).{0,48}'
        r'(?:模拟器|正式测试|演练|实测|测试成绩|测试日志|案例编码|测试统计|检测点|读数|观测数据|测试记录)',
        re.I,
    ),
    re.compile(
        r'(?:未提供|没有提供|缺少|尚无|暂无).{0,36}'
        r'(?:模拟器|正式测试|演练|实测|测试成绩|测试日志|案例编码|测试统计|观测数据|测试记录)',
        re.I,
    ),
    re.compile(
        r'(?:题面|问题文本|所选材料|输入材料).{0,24}(?:未提供|未给出|缺少).{0,36}'
        r'(?:具体检测点|检测点坐标|测点坐标|具体测点读数|具体读数|案例数据|观测值)',
        re.I,
    ),
    re.compile(
        r'(?:没有|尚无|未提供|未给出).{0,16}'
        r'(?:具体测点读数|具体检测点坐标|实际坐标和读数|具体读数)',
        re.I,
    ),
    re.compile(
        r'(?:不是|并非|并不是|而非|不代表|不构成).{0,40}'
        r'(?:模拟器观测|模拟器测试|正式测试|正式测试成绩|正式测试结果|现场观测|实测成绩|演练结果)',
        re.I,
    ),
    re.compile(
        r'(?:结果|数值|结论|模型|数学正确性).{0,32}'
        r'(?:尚未验证|尚未独立验证|未经独立验证|未被验证|未独立验证|尚未核验|待验证|待核验)|'
        r'(?:尚未验证|尚未独立验证|未经独立验证|未被验证|未独立验证|尚未核验|待验证|待核验).{0,32}'
        r'(?:结果|数值|结论|模型|数学正确性)',
        re.I,
    ),
    re.compile(r'未将.{0,32}标记为已独立验证|没有将.{0,32}标记为已独立验证', re.I),
    re.compile(r'(?:不验证|不构成验证|未验证).{0,40}(?:模拟器|路径|响应|完成率|测试)', re.I),
    re.compile(
        r'(?:其|它|本文|本研究).{0,28}(?:不含|未包含|没有).{0,44}'
        r'(?:实际|真实|现场).{0,24}(?:数据|样本|日志|路径).{0,32}'
        r'(?:因此|因而|不能|无法).{0,32}(?:量化|报告|评价|给出)',
        re.I,
    ),
    re.compile(
        r'(?:具体实例|实际定位区域).{0,40}(?:须由|需要).{0,20}'
        r'(?:坐标|读数|观测数据).{0,20}(?:计算|确定)',
        re.I,
    ),
    re.compile(
        r'(?:本文|本研究|本节).{0,36}(?:不报告|不提供|不填报|无法报告|不能给出|不作定量结论).{0,48}'
        r'(?:虚构|清除率|测试成绩|测试结果|案例编码|平均时间|运行时间|实测性能)',
        re.I,
    ),
    re.compile(
        r'(?:不把|不能把|不可将|不应将).{0,36}(?:模型诊断|计算结果|方法设计|几何结果).{0,20}'
        r'(?:当成|作为|解释为|写成).{0,28}(?:模拟器|演练|正式测试|实测|测试成绩|测试结果)',
        re.I,
    ),
    re.compile(
        r'\b(?:values|numbers|results|diagnostics).{0,50}'
        r'(?:from|based on|derived from).{0,50}'
        r'(?:the supplied|provided|validated|application|run|workflow).{0,32}'
        r'(?:table|artifact|output|evidence|record)',
        re.I,
    ),
    re.compile(
        r'\b(?:not|rather than|instead of)\s+(?:(?:a|an|the)\s+)?'
        r'(?:simulator observation|simulator test result|formal test score|formal test result|observed test result)\b',
        re.I,
    ),
    re.compile(
        r'\b(?:unverified|not\s+(?:yet\s+)?(?:verified|validated|tested|confirmed))\b',
        re.I,
    ),
    re.compile(
        r'\b(?:cannot|do not|does not).{0,28}(?:claim|report|present|describe).{0,32}'
        r'(?:formal test|simulator result|test score|test result)\b',
        re.I,
    ),
)
_SENTENCE = re.compile(r'(?<=[。！？；;\n])|(?<=[.!?])(?=\s|$)')
_PROCESS_ANNOTATION = re.compile(
    r'[（(][^()（）]{0,160}(?:模拟器观测|模拟器测试|正式测试成绩|正式测试结果|演练成绩|'
    r'尚未验证|未经验证|待(?:人工|手动)(?:核验|核查|复核|审核)|AI生成|Agent生成)[^()（）]{0,80}[）)]',
    re.I,
)
_PROVENANCE_REWRITES = (
    (re.compile(r'已提供的诊断表以([^。！？；]{1,100})给出反例'), r'\1构成反例'),
    (re.compile(r'(?:同一|现有|当前)?诊断表(?:显示|表明)'), '计算结果显示'),
    (re.compile(r'(?:模型)?诊断表记录的|诊断计算中的|诊断计算给出的构造'), ''),
    (re.compile(r'(?:诊断表|验证表)(?:所列的?|列出的?|给出的?|记录的?|中的?)'), ''),
    (re.compile(r'按诊断表中的|按验证表中的'), '按'),
    (re.compile(r'(?:同一|现有|当前)?诊断表支持'), '几何计算表明'),
    (re.compile(r'经验证的诊断值为[：:]'), '诊断结果为：'),
    (re.compile(r'所给合成等边三角形'), '合成等边三角形'),
)


def _has_marker(value: str) -> bool:
    return bool(_PROCESS_ANNOTATION.search(value)) or any(pattern.search(value) for pattern in _MARKERS)


def workflow_note_fragments(value: str) -> tuple[str, ...]:
    """Return clauses that expose internal review, test, or evidence state."""
    if not isinstance(value, str) or not value:
        return ()
    return tuple(segment.strip() for segment in _SENTENCE.split(value)
                 if segment.strip() and _has_marker(segment))


def publication_text(value: str) -> str:
    """Remove process commentary while preserving neighboring scientific prose."""
    if not isinstance(value, str) or not value:
        return ''
    cleaned = _PROCESS_ANNOTATION.sub('', value)
    for pattern, replacement in _PROVENANCE_REWRITES:
        cleaned = pattern.sub(replacement, cleaned)
    return ''.join(segment for segment in _SENTENCE.split(cleaned)
                   if segment.strip() and not _has_marker(segment)).strip()


def contains_workflow_note(value: str) -> bool:
    return bool(workflow_note_fragments(value))
