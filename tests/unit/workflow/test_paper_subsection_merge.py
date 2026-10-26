from mathmodel_paper_studio.workflow.live_engine import (
    _merge_paper_sections, _remap_paper_equations,
)


def _section(subsections):
    return {
        'section_key': 'model', 'paragraphs': [], 'evidence_ids': [],
        'subsections': [
            {'title': title, 'paragraphs': [title], 'evidence_ids': []}
            for title in subsections
        ],
    }


def test_equation_anchor_maps_to_merged_subsection_when_later_batch_adds_new_section():
    sections, index = [], {}
    first_maps = _merge_paper_sections(sections, index, [_section(['变量定义'])])
    second_maps = _merge_paper_sections(sections, index, [_section(['边界条件'])])

    remapped = _remap_paper_equations(
        [{'section_key': 'model', 'subsection_index': '1', 'expression': 'x = 1'}],
        second_maps,
    )

    assert first_maps == {'model': (1,)}
    assert second_maps == {'model': (2,)}
    assert remapped[0]['subsection_index'] == '2'


def test_equation_anchor_reuses_existing_subsection_when_titles_match():
    sections, index = [], {}
    _merge_paper_sections(sections, index, [_section(['变量定义'])])
    maps = _merge_paper_sections(sections, index, [_section(['新方程', '变量定义'])])

    remapped = _remap_paper_equations(
        [{'section_key': 'model', 'subsection_index': '2', 'expression': 'x = 1'}],
        maps,
    )

    assert maps == {'model': (2, 1)}
    assert remapped[0]['subsection_index'] == '1'
