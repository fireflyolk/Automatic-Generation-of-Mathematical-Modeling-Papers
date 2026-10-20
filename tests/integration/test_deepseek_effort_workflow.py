"""The real API adapter passes complete planning responses to the workflow offline."""
import json

from mathmodel_paper_studio.agents.deepseek_model_preferences import DeepSeekModelPreference
from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient
from mathmodel_paper_studio.domain.models import Run, RunStatus, Stage
from tests.integration import test_live_workflow as workflow_fixtures
from tests.unit.agents.providers.test_deepseek_api import (
    API_KEY, FakeConnection, FakeResponse, chat_response,
)


def test_max_effort_planning_reaches_code_generation_with_reviewed_budget():
    fixture = workflow_fixtures.LiveWorkflowTests(
        'test_composes_nine_real_host_stages_and_keeps_originals')
    fixture.setUp()
    try:
        fixture.run = Run('runbudget1', fixture.project.project_id, Provider.DEEPSEEK,
                          RunStatus.CREATED, '90_runs/runbudget1')
        fixture.runs.add(fixture.run)
        values = [
            {'subproblems': ['Fit observations and evaluate error.']},
            {'assumptions': ['Independent observations.'], 'variables': ['x, y'],
             'equations': ['y = a*x'], 'validation_criteria': ['Finite residual.']},
        ]
        connections = [FakeConnection(FakeResponse(chat_response(json.dumps(value))))
                       for value in values]
        # Stop before code execution. No remote service or generated code runs.
        connections.append(FakeConnection(FakeResponse('{}', status=500)))
        seen = []

        def transport(*args):
            connection = connections.pop(0)
            seen.append(connection)
            return connection

        client = DeepSeekApiClient(
            key_resolver=lambda: API_KEY, transport_factory=transport,
            preference_resolver=lambda: DeepSeekModelPreference('deepseek-flash', 'max', 131072),
        )
        client.authorize_chargeable_requests(fixture.run.run_id)
        state = fixture.start(fixture.engine(provider_client=client, provider_status=client.probe()))

        assert state.status is RunStatus.PAUSED
        assert state.next_stage is Stage.CODE_GENERATION
        assert len(seen) == 3
        assert [json.loads(connection.request_args[2])['max_tokens']
                for connection in seen] == [131072, 131072, 131072]
        assert not fixture.runner.calls
        assert all(path.read_bytes() == content for path, content in fixture.originals.items())
    finally:
        fixture.doCleanups()
