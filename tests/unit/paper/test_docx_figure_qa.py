from mathmodel_paper_studio.paper.docx_qa import _missing_figure_keys


def test_qa_matches_figures_named_with_their_section_key_and_unique_id():
    missing = _missing_figure_keys(
        ("results_figure_1",),
        {"figure-results_figure_1-result-figure-001"},
    )

    assert missing == ()


def test_qa_accepts_legacy_numbered_result_figures_for_existing_documents():
    missing = _missing_figure_keys(
        ("results_figure_1", "results_figure_2", "results_figure_3"),
        {"figure-result-figure-001", "figure-result-figure-002", "figure-result-figure-003"},
    )

    assert missing == ()


def test_qa_does_not_let_flowcharts_or_too_few_legacy_figures_cover_results():
    missing = _missing_figure_keys(
        ("results_figure_1", "results_figure_2"),
        {"figure-result-figure-001", "figure-flowchart-01"},
    )

    assert missing == ("results_figure_2",)
