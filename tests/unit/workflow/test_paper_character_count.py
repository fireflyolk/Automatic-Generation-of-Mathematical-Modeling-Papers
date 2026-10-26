from mathmodel_paper_studio.paper.docx_writer import PaperDraft, TableDraft, TextSection
from mathmodel_paper_studio.workflow.live_engine import _paper_character_count, _paper_table_character_count
from mathmodel_paper_studio.paper.docx_writer import _generic_section_title, _numbered_section_title


def test_table_cells_do_not_count_as_substantive_paper_body():
    table = TableDraft('results_table', ('指标', '数值'), (('温度', '25'), ('湿度', '60')))
    draft = PaperDraft(
        sections=(TextSection('results', ('结果显示温度稳定。',)),),
        tables=(table,),
    )

    assert _paper_character_count(draft) == (
        len(draft.title) + len(_numbered_section_title('results')) + len('结果显示温度稳定。')
    )
    expected_table_chars = (
        len(_generic_section_title(table.section_key)) + sum(map(len, table.headers))
        + sum(len(cell) for row in table.rows for cell in row)
    )
    assert _paper_table_character_count(draft) == expected_table_chars
