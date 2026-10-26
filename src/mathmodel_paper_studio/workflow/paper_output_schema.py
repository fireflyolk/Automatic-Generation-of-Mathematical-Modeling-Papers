"""Closed JSON shapes for application-owned paper response examples.

These schemas describe field placement and types only. Evidence references,
section keys, text limits and scientific claims still require local validation.
Never derive an example from provider text, imported files or a prompt.
"""

_MAX_SCHEMA_DEPTH = 16
_MAX_SCHEMA_NODES = 4096
_MAX_SCHEMA_CHARACTERS = 65536


def _check_size(nodes, characters):
    if nodes > _MAX_SCHEMA_NODES:
        raise ValueError('Paper schema example has too many nodes.')
    if characters > _MAX_SCHEMA_CHARACTERS:
        raise ValueError('Paper schema example has too many characters.')


def _validate_example(value, depth=0, ancestors=frozenset()):
    if depth > _MAX_SCHEMA_DEPTH:
        raise ValueError('Paper schema example exceeds the depth limit.')
    if isinstance(value, str):
        _check_size(1, len(value))
        return 1, len(value)
    if not isinstance(value, (dict, list)):
        raise ValueError('Unsupported paper schema example type.')
    if id(value) in ancestors:
        raise ValueError('Paper schema example contains a cycle.')
    ancestors = ancestors | {id(value)}
    if isinstance(value, list):
        if len(value) > 1:
            raise ValueError('Paper schema arrays require at most one example.')
        children = ((None, item) for item in value)
    else:
        children = value.items()
    nodes, characters = 1, 0
    for key, child in children:
        if isinstance(value, dict):
            if not isinstance(key, str):
                raise ValueError('Paper schema object keys must be strings.')
            characters += len(key)
            _check_size(nodes, characters)
        child_nodes, child_characters = _validate_example(child, depth + 1, ancestors)
        nodes, characters = nodes + child_nodes, characters + child_characters
        _check_size(nodes, characters)
    return nodes, characters


def _shape(value):
    if isinstance(value, str):
        return {'type': 'string'}
    if isinstance(value, dict):
        return {
            'type': 'object',
            'properties': {key: _shape(child) for key, child in value.items()},
            'required': list(value),
            'additionalProperties': False,
        }
    if value:
        return {'type': 'array', 'items': _shape(value[0])}
    return {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0}


def paper_output_schema(example):
    """Copy a bounded, trusted paper example into a strict JSON Schema.

    Every object key is required and extra keys are forbidden. A one-item list
    describes an array's item shape; an empty list permits only an empty array.
    Unsupported values, multiple list examples and oversized or cyclic inputs
    raise ValueError. The example and its shared child objects stay unchanged.
    """
    _validate_example(example)
    return _shape(example)
