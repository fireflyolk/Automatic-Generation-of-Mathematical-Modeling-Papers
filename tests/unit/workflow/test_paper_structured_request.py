"""The real request seam opts into Codex structure without changing cached inputs."""
import copy
import json

import pytest

from mathmodel_paper_studio.agents.preflight import Provider
from tests.unit.workflow.test_paper_empty_nested_sections import nested_workflow


@pytest.mark.parametrize('provider,capability,expected', [
    (Provider.CODEX, True, 8),
    (Provider.CODEX, False, 0),
])
def test_paper_request_passes_separate_schema_only_to_capable_codex(
        tmp_path, provider, capability, expected):
    engine, context, run, calls, _ = nested_workflow(tmp_path, [])
    assert context.provider is provider
    client = engine.provider_client
    client.supports_output_schema = capability
    original_generate = client.generate
    schemas = []

    def generate(context, prompt, cancel_event=None, **kwargs):
        if kwargs:
            assert set(kwargs) == {'output_schema'}
            schemas.append(copy.deepcopy(kwargs['output_schema']))
        return original_generate(context, prompt, cancel_event)

    client.generate = generate
    combined = run()
    assert len(calls) == 8
    assert len(schemas) == expected
    for schema, prompt in zip(schemas, calls):
        example = json.loads(prompt.split('JSON schema example: ', 1)[1].splitlines()[0])
        assert set(schema['properties']) == set(example)
        section = schema['properties']['sections']['items']
        leaf = section['properties']['subsections']['items']
        assert set(leaf['properties']) == {'title', 'paragraphs', 'evidence_ids'}
        assert leaf['additionalProperties'] is False
        assert 'outputSchema' not in prompt
    assert run() == combined
    assert len(calls) == 8  # Schema negotiation does not invalidate cached batches.
