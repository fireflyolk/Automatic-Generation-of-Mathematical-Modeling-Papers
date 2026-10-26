"""Normalize recoverable paper metadata before strict validation."""


def result_artifact_anchor_maps(evidence):
    """Assign stable built-in anchors from validated, kind-specific evidence."""
    return {
        field: {item.evidence_id: f'results_{kind}_{index}'
                for index, item in enumerate(
                    (item for item in evidence if item.kind == kind), start=1)}
        for field, kind in (('tables', 'table'), ('figures', 'figure'))
    }


def normalize_result_anchors(value, anchors, *, builtin_tables, builtin_figures):
    """Resolve only the generic results parent heading to an artifact anchor.

    A confirmed custom mapping, an explicit artifact anchor, or an unverified
    evidence reference is never repaired. Raw provider responses and cached
    digests stay unchanged; subsequent validators still reject collisions.
    """
    if not isinstance(value, dict):
        return value
    result = dict(value)
    for field, builtin in (('tables', builtin_tables), ('figures', builtin_figures)):
        if not builtin or not isinstance(value.get(field), list):
            continue
        items = []
        for item in value[field]:
            if (isinstance(item, dict) and item.get('section_key') == 'results'
                    and isinstance(item.get('evidence_id'), str)
                    and item['evidence_id'] in anchors[field]):
                item = {**item, 'section_key': anchors[field][item['evidence_id']]}
            items.append(item)
        result = {**result, field: items}
    return result


def _with_optional_evidence(value):
    if not isinstance(value, dict):
        return value
    if 'evidence_ids' in value:
        return dict(value)
    return {**value, 'evidence_ids': []}


def _normalize_subsection(value):
    result = _with_optional_evidence(value)
    if (isinstance(result, dict) and isinstance(result.get('subsections'), list)
            and not result['subsections']):
        return {key: item for key, item in result.items() if key != 'subsections'}
    return result


def normalize_optional_evidence(value):
    """Default omitted citation lists without repairing supplied invalid data.

    Explanatory sections, subsections and equations may have no result evidence.
    Artifact references still require their singular evidence_id. Unknown keys,
    explicit nulls and invalid lists remain available to the strict validator.
    An empty child-subsection list is a redundant leaf marker, so omit it only
    on subsection leaves. Nonempty children and invalid types remain invalid.
    The provider response is never mutated, including when replaying a cache.
    """
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(value.get('sections'), list):
        sections = []
        for section in value['sections']:
            normalized = _with_optional_evidence(section)
            if isinstance(section, dict) and isinstance(section.get('subsections'), list):
                normalized = {
                    **normalized,
                    'subsections': [_normalize_subsection(item) for item in section['subsections']],
                }
            sections.append(normalized)
        result = {**result, 'sections': sections}
    if isinstance(value.get('equations'), list):
        result = {**result, 'equations': [_with_optional_evidence(item) for item in value['equations']]}
    return result
