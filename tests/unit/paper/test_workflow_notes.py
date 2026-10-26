from mathmodel_paper_studio.paper.workflow_notes import publication_text, workflow_note_fragments


def test_publication_filter_preserves_legitimate_manual_process_science():
    sentence = '人工复核机制能够降低审批误差，并提升异常记录的可追溯性。'

    assert publication_text(sentence) == sentence
    assert workflow_note_fragments(sentence) == ()


def test_publication_filter_removes_an_explicit_manual_review_instruction():
    sentence = '该结果需人工复核后再提交。'

    assert publication_text(sentence) == ''
    assert workflow_note_fragments(sentence) == (sentence,)


def test_publication_filter_removes_explicit_placeholder_in_mixed_scientific_prose():
    text = '本模型满足质量守恒。待人工核验：请检查公式。边界条件采用题目给定范围。'

    assert publication_text(text) == '本模型满足质量守恒。边界条件采用题目给定范围。'


def test_publication_filter_catches_common_manual_and_expert_todo_variants():
    for text in ('需人工审核后提交。', '待专家确认模型参数。', '请手动补充真实数据。'):
        assert workflow_note_fragments(text) == (text,)
        assert publication_text(text) == ''
