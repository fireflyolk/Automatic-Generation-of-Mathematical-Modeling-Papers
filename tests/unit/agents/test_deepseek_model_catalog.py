import json

import pytest

from mathmodel_paper_studio.agents.deepseek_model_catalog import (
    DeepSeekModelCatalogError,
    parse_model_catalog,
)


def _row(model_id='deepseek-flash', *, modalities=('text',), efforts=('low', 'high', 'max'),
         default='high'):
    row = {
        'id': model_id,
        'object': 'model',
        'owned_by': 'deepseek',
        'name': 'DeepSeek model',
        'input_modalities': list(modalities),
        'output_modalities': ['text'],
    }
    if efforts is not None:
        row['effort'] = {'supported_levels': list(efforts), 'default_level': default}
    return row


def test_parser_filters_non_text_models_and_preserves_supported_effort_order():
    payload = {
        'object': 'list',
        'data': [
            _row(),
            _row('vision-only', modalities=('image',)),
            dict(_row('other-owner'), owned_by='other'),
            _row('no-effort', efforts=None),
        ],
    }

    models = parse_model_catalog(json.dumps(payload).encode('utf-8'))

    assert [item.id for item in models] == ['deepseek-flash', 'no-effort']
    assert models[0].supported_efforts == ('low', 'high', 'max')
    assert models[0].default_effort == 'high'
    assert models[1].supported_efforts == ()
    assert models[1].default_effort == 'none'


@pytest.mark.parametrize('raw', [
    b'{"object":"list","data":[],"data":[]}',
    json.dumps({'object': 'list', 'data': [_row(), _row()]}).encode('utf-8'),
    json.dumps({'object': 'list', 'data': [_row('../unsafe')]}).encode('utf-8'),
    json.dumps({'object': 'list', 'data': [_row(efforts=('low', 'low'))]}).encode('utf-8'),
    b'{"object":"not-list","data":[]}',
    b'x' * (512 * 1024 + 1),
], ids=['duplicate-key', 'duplicate-model', 'unsafe-model-id', 'duplicate-effort',
        'wrong-envelope', 'oversized'])
def test_parser_rejects_malformed_ambiguous_or_oversized_catalogs(raw):
    with pytest.raises(DeepSeekModelCatalogError):
        parse_model_catalog(raw)
