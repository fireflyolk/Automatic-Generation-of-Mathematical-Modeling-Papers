import json
import threading
from collections import deque
from pathlib import Path

import pytest

from mathmodel_paper_studio.agents.codex_app_server import (
    _APP_SERVER_ARGUMENTS,
    _decode_line,
    CodexAppServer,
    CodexAppServerError,
)
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.agents.providers.codex_cli import CODEX_DISABLED_FEATURES


_FEATURES = tuple(dict.fromkeys((
    *CODEX_DISABLED_FEATURES, 'memories', 'agent_message_board',
    'multi_agent_v2', 'guardianv2', 'token_budget',
)))


class _Installation:
    provider = 'codex'
    version = '0.159.3'

    def __init__(self, fake_root):
        self.executable = fake_root / 'codex.exe'
        self.native_home = fake_root / 'native-auth'
        self.revalidated = 0

    def revalidate(self):
        self.revalidated += 1


class _FakeProcess:
    def __init__(self, *, config=None, thread=None, turn=None, startup_events=(),
                 on_request=None, model_pages=None):
        self.config = config or {
            'model_provider': 'openai',
            'web_search': 'disabled',
            'mcp_servers': {},
            'approvals_reviewer': 'user',
            'apps': {'_default': {
                'enabled': False, 'open_world_enabled': False,
                'destructive_enabled': False,
            }},
            'features': {
                **{name: False for name in _FEATURES},
            },
            'cloud': {'skills': {'enabled': False}},
            'skills': {'include_instructions': False},
        }
        self.thread = thread or {
            'thread': {'id': 'thread-1', 'environments': []},
            'instructionSources': [], 'runtimeWorkspaceRoots': [],
            'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'},
            'modelProvider': 'openai',
        }
        self.turn = turn or {
            'turn': {'id': 'turn-1', 'status': 'completed', 'items': [
                {'id': 'message-1', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'FINAL ANSWER'},
            ]},
        }
        self.startup_events = list(startup_events)
        self.stdout = deque()
        self.stderr = deque()
        self.requests = []
        self.closed = False
        self.terminated = False
        self.input_closed = False
        self.on_request = on_request
        self.model_pages = model_pages or {}

    def write_stdin(self, data):
        request = json.loads(data.decode('utf-8'))
        self.requests.append(request)
        if callable(self.on_request):
            self.on_request(request)
        method = request.get('method')
        if method == 'initialize':
            result = {'codexHome': str(self.installation_home), 'platformOs': 'windows'}
        elif method == 'config/read':
            result = {'config': self.config}
        elif method == 'thread/start':
            result = self.thread
        elif method == 'turn/start':
            self.stdout.extend(_line(event) for event in self.startup_events)
            result = self.turn
        elif method == 'model/list':
            result = self.model_pages.get(request.get('params', {}).get('cursor'))
            if result is None:
                result = {'data': [], 'nextCursor': None}
        else:
            result = {}
        if method is not None and 'id' in request:
            self.stdout.append(_line({'id': request['id'], 'result': result}))

    def read(self, channel, size=4096):
        source = self.stdout if channel == 'stdout' else self.stderr
        if source:
            value = source.popleft()
            return value[:size]
        return None

    def poll(self):
        return None

    def finish_stdin(self):
        self.input_closed = True

    def terminate(self):
        self.terminated = True

    def cancel_writer(self, worker):
        return None

    def close(self):
        self.closed = True


def _line(value):
    return json.dumps(value, separators=(',', ':')).encode('utf-8') + b'\n'


def _notification(method, params):
    return {'method': method, 'params': params}


def _thread_started():
    return _notification('thread/started', {'thread': {'id': 'thread-1', 'environments': []}})


def _turn_completed(status='completed', text='FINAL ANSWER', items_view='full'):
    return _notification('turn/completed', {
        'threadId': 'thread-1',
        'turn': {'id': 'turn-1', 'status': status, 'items': [
            {'id': 'message-1', 'type': 'agentMessage', 'phase': 'final_answer', 'text': text},
        ], 'itemsView': items_view},
    })


def _completed_item(item, *, thread_id='thread-1', turn_id='turn-1'):
    return _notification('item/completed', {
        'completedAtMs': 1, 'threadId': thread_id, 'turnId': turn_id, 'item': item,
    })


def _model(model_id, *, display_name=None, efforts=('low', 'medium', 'high'),
           default_effort='medium', is_default=False, hidden=False):
    return {
        'id': model_id,
        'model': model_id,
        'upgrade': None,
        'upgradeInfo': None,
        'availabilityNux': None,
        'displayName': display_name or model_id,
        'description': 'Test model',
        'modelSpecialty': None,
        'hidden': hidden,
        'supportedReasoningEfforts': [
            {'reasoningEffort': effort, 'description': f'{effort} effort'}
            for effort in efforts
        ],
        'defaultReasoningEffort': default_effort,
        'inputModalities': ['text'],
        'supportsPersonality': False,
        'multiAgentVersion': None,
        'additionalSpeedTiers': [],
        'serviceTiers': [],
        'defaultServiceTier': None,
        'availableAccessPrograms': None,
        'isDefault': is_default,
    }


def _make_server(tmp_path, process, monkeypatch):
    root = tmp_path / 'app-data'
    root.mkdir()
    paths = AppPaths(root)
    fake_root = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-start-gate-fix-20261018\fake-install')
    install = _Installation(fake_root)
    process.installation_home = str(install.native_home)
    created = []

    def process_factory(*args):
        created.append(process)
        return process

    return CodexAppServer(
        paths, install, process_factory=process_factory,
        environment_factory=lambda _paths, _exe, native_home, _provider: {
            'HOME': str(native_home), 'USERPROFILE': str(native_home),
            'APPDATA': str(native_home / 'AppData/Roaming'),
            'LOCALAPPDATA': str(native_home / 'AppData/Local'),
            'CODEX_HOME': str(native_home),
            'TEMP': str(_paths.temp), 'TMP': str(_paths.temp), 'TMPDIR': str(_paths.temp),
        },
    ), install, created


def test_verify_policy_is_local_only_and_checks_effective_thread(tmp_path, monkeypatch):
    process = _FakeProcess()
    server, installation, created = _make_server(tmp_path, process, monkeypatch)

    observation = server.verify_policy()

    assert observation.verified is True
    assert observation.cli_version == '0.159.3'
    assert observation.memories_disabled is True
    assert observation.history_notes_disabled is True
    assert observation.cloud_skills_disabled is True
    assert observation.skill_instructions_disabled is True
    assert observation.mcp_servers_empty is True
    assert observation.agent_message_board_disabled is True
    assert observation.multi_agent_v2_disabled is True
    assert observation.guardianv2_disabled is True
    assert observation.token_budget_disabled is True
    assert observation.approval_reviewer_user is True
    assert observation.git_attribution_settings_lookup_possible is True
    assert observation.host_skill_snapshot_may_be_consulted is True
    assert observation.skill_mentions_sanitized is True
    assert observation.os_process_confinement_verified is False
    assert observation.verified_scope == 'app-server-thread-no-project-tools'
    assert all(call['method'] != 'turn/start' for call in process.requests)
    thread_call = next(call for call in process.requests if call['method'] == 'thread/start')
    assert thread_call['params']['environments'] == []
    assert thread_call['params']['runtimeWorkspaceRoots'] == []
    assert len(created) == 1 and process.closed
    assert installation.revalidated >= 1


def test_generation_uses_only_text_and_returns_completed_agent_message(tmp_path, monkeypatch):
    process = _FakeProcess(startup_events=[_thread_started(), _turn_completed()])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    result = server.generate('题目和选定的文本数据')

    assert result.text == 'FINAL ANSWER'
    assert result.observation.verified is True
    turn = next(call for call in process.requests if call['method'] == 'turn/start')
    assert turn['params']['environments'] == []
    assert turn['params']['input'] == [{'type': 'text', 'text': '题目和选定的文本数据'}]
    assert set(turn['params']) == {'threadId', 'input', 'environments'}
    assert process.closed


def test_generation_omits_optional_model_and_effort_when_using_codex_defaults(tmp_path, monkeypatch):
    process = _FakeProcess(startup_events=[_thread_started(), _turn_completed()])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    server.generate('use Codex defaults')

    turn = next(call for call in process.requests if call['method'] == 'turn/start')
    assert 'model' not in turn['params']
    assert 'effort' not in turn['params']


def test_generation_passes_selected_model_and_effort_to_turn_start(tmp_path, monkeypatch):
    process = _FakeProcess(startup_events=[_thread_started(), _turn_completed()])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    server.generate('selected settings', model='gpt-5.5', effort='high')

    turn = next(call for call in process.requests if call['method'] == 'turn/start')
    assert turn['params']['model'] == 'gpt-5.5'
    assert turn['params']['effort'] == 'high'


def test_model_list_paginates_after_app_server_initialization_and_excludes_hidden_models(
    tmp_path, monkeypatch,
):
    process = _FakeProcess(model_pages={
        None: {'data': [
            _model('gpt-5.5', display_name='GPT 5.5', is_default=True),
            _model('hidden-model', hidden=True),
        ], 'nextCursor': 'page-2'},
        'page-2': {'data': [
            _model('gpt-5.4', display_name='GPT 5.4', efforts=('low', 'high'),
                   default_effort='low'),
        ], 'nextCursor': None},
    })
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    models = server.list_models()

    methods = [call.get('method') for call in process.requests]
    assert methods.index('initialized') > methods.index('initialize')
    model_calls = [call for call in process.requests if call.get('method') == 'model/list']
    assert len(model_calls) == 2
    assert model_calls[0]['params']['includeHidden'] is False
    assert model_calls[1]['params']['cursor'] == 'page-2'
    assert all(method not in ('thread/start', 'turn/start') for method in methods)
    assert [model.id for model in models] == ['gpt-5.5', 'gpt-5.4']
    assert models[0].display_name == 'GPT 5.5'
    assert models[0].supported_efforts == ('low', 'medium', 'high')
    assert models[0].default_effort == 'medium'
    assert models[0].is_default is True
    assert models[1].supported_efforts == ('low', 'high')
    assert process.closed


@pytest.mark.parametrize('response', [
    {'data': 'not-an-array', 'nextCursor': None},
    {'data': [_model('gpt-5.5')], 'nextCursor': 3},
    {'data': [{**_model('gpt-5.5'), 'displayName': ''}], 'nextCursor': None},
    {'data': [_model('gpt 5.5')], 'nextCursor': None},
    {'data': [{**_model('gpt-5.5'), 'supportedReasoningEfforts': []}], 'nextCursor': None},
    {'data': [{**_model('gpt-5.5'), 'supportedReasoningEfforts': [
        {'reasoningEffort': '_high'},
    ], 'defaultReasoningEffort': '_high'}], 'nextCursor': None},
    {'data': [{**_model('gpt-5.5'), 'defaultReasoningEffort': 'max'}], 'nextCursor': None},
])
def test_model_list_rejects_malformed_catalog_records_fail_closed(tmp_path, monkeypatch, response):
    process = _FakeProcess(model_pages={None: response})
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.list_models()

    assert process.closed
    assert all(call.get('method') != 'turn/start' for call in process.requests)


def test_model_list_rejects_repeated_cursor_and_duplicate_model_ids(tmp_path, monkeypatch):
    repeated_cursor_process = _FakeProcess(model_pages={
        None: {'data': [_model('gpt-5.5')], 'nextCursor': 'same'},
        'same': {'data': [_model('gpt-5.4')], 'nextCursor': 'same'},
    })
    repeated_cursor_server, _installation, _created = _make_server(
        tmp_path, repeated_cursor_process, monkeypatch,
    )

    with pytest.raises(CodexAppServerError):
        repeated_cursor_server.list_models()

    assert repeated_cursor_process.closed

    duplicate_process = _FakeProcess(model_pages={
        None: {'data': [_model('gpt-5.5')], 'nextCursor': 'page-2'},
        'page-2': {'data': [_model('gpt-5.5')], 'nextCursor': None},
    })
    duplicate_temp_path = tmp_path / 'duplicate-server'
    duplicate_temp_path.mkdir()
    duplicate_server, _installation, _created = _make_server(
        duplicate_temp_path, duplicate_process, monkeypatch,
    )

    with pytest.raises(CodexAppServerError):
        duplicate_server.list_models()

    assert duplicate_process.closed


def test_generation_uses_completed_item_events_when_turn_completion_is_summary(tmp_path, monkeypatch):
    final_item = {
        'id': 'message-1', 'type': 'agentMessage', 'phase': 'final_answer',
        'text': 'AUTHORITATIVE ANSWER',
    }
    process = _FakeProcess(startup_events=[
        _thread_started(),
        _completed_item({'id': 'user-1', 'type': 'userMessage', 'content': []}),
        _completed_item(final_item),
        _turn_completed(text='SUMMARY VIEW', items_view='summary'),
    ])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    result = server.generate('text')

    assert result.text == 'AUTHORITATIVE ANSWER'
    assert all(call['method'] != 'thread/items/list' for call in process.requests)
    assert process.closed


def test_generation_rejects_summary_without_completed_authoritative_items(tmp_path, monkeypatch):
    process = _FakeProcess(startup_events=[
        _thread_started(), _turn_completed(text='SUMMARY VIEW', items_view='summary'),
    ])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.generate('text')

    assert process.closed


def test_generation_rejects_turn_completion_without_required_items_view(tmp_path, monkeypatch):
    completed = _turn_completed()
    del completed['params']['turn']['itemsView']
    process = _FakeProcess(startup_events=[_thread_started(), completed])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.generate('text')

    assert process.closed


def test_untrusted_prompt_at_mentions_are_sanitized_before_turn_start(tmp_path, monkeypatch):
    process = _FakeProcess(startup_events=[_thread_started(), _turn_completed()])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    result = server.generate('contact: user@example.com; data: @skill')

    turn = next(call for call in process.requests if call['method'] == 'turn/start')
    assert turn['params']['input'] == [{
        'type': 'text', 'text': 'contact: user＠example.com; data: ＠skill',
    }]
    assert result.observation.skill_mentions_sanitized is True


def test_fixed_app_server_policy_arguments_include_local_extension_gates():
    for feature in (*CODEX_DISABLED_FEATURES, 'agent_message_board', 'multi_agent_v2', 'guardianv2', 'token_budget'):
        assert any(
            _APP_SERVER_ARGUMENTS[index:index + 2] == ('--disable', feature)
            for index in range(len(_APP_SERVER_ARGUMENTS) - 1)
        )
    assert any(
        _APP_SERVER_ARGUMENTS[index:index + 2] == ('-c', 'approvals_reviewer=user')
        for index in range(len(_APP_SERVER_ARGUMENTS) - 1)
    )
    assert any(
        _APP_SERVER_ARGUMENTS[index:index + 2] == (
            '-c', 'features.token_budget.use_history_notes_extension=false',
        )
        for index in range(len(_APP_SERVER_ARGUMENTS) - 1)
    )


def test_effective_feature_or_mcp_policy_mismatch_blocks_before_turn(tmp_path, monkeypatch):
    config = {
        'model_provider': 'openai',
        'web_search': 'disabled', 'mcp_servers': {'untrusted': {}},
        'apps': {'_default': {
            'enabled': False, 'open_world_enabled': False,
            'destructive_enabled': False,
        }},
        'features': {
            **{name: False for name in _FEATURES},
        },
        'cloud': {'skills': {'enabled': False}},
        'skills': {'include_instructions': False},
    }
    process = _FakeProcess(config=config)
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.generate('must not be sent')

    assert all(call['method'] != 'turn/start' for call in process.requests)
    assert process.closed


@pytest.mark.parametrize('path', [
    ('features', 'memories'),
    ('features', 'token_budget'),
    ('cloud', 'skills', 'enabled'),
    ('skills', 'include_instructions'),
])
def test_extension_policy_must_be_explicitly_disabled_before_thread_start(tmp_path, monkeypatch, path):
    config = _FakeProcess().config
    target = config
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = True
    process = _FakeProcess(config=config)
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.verify_policy()

    assert all(call['method'] != 'thread/start' for call in process.requests)
    assert all(call['method'] != 'turn/start' for call in process.requests)
    assert process.closed


@pytest.mark.parametrize(('path', 'value'), [
    (('features', 'agent_message_board'), True),
    (('features', 'agent_message_board'), None),
    (('features', 'multi_agent_v2'), True),
    (('features', 'multi_agent_v2'), None),
    (('features', 'guardianv2'), True),
    (('features', 'guardianv2'), None),
    (('features', 'token_budget'), True),
    (('features', 'token_budget'), None),
    (('approvals_reviewer',), 'auto'),
    (('approvals_reviewer',), None),
])
def test_additional_extension_gate_must_be_explicit_before_thread_start(
    tmp_path, monkeypatch, path, value,
):
    config = _FakeProcess().config
    target = config
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    process = _FakeProcess(config=config)
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.verify_policy()

    assert all(call['method'] != 'thread/start' for call in process.requests)
    assert all(call['method'] != 'turn/start' for call in process.requests)
    assert process.closed


@pytest.mark.parametrize('thread', [
    {'thread': {'id': 'thread-1', 'environments': None}, 'instructionSources': [],
     'runtimeWorkspaceRoots': [], 'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'}},
    {'thread': {'id': 'thread-1', 'environments': [{'id': 'shell'}]}, 'instructionSources': [],
     'runtimeWorkspaceRoots': [], 'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'}},
    {'thread': {'id': 'thread-1', 'environments': []}, 'instructionSources': ['AGENTS.md'],
     'runtimeWorkspaceRoots': [], 'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'}},
    {'thread': {'id': 'thread-1', 'environments': []}, 'instructionSources': [],
     'runtimeWorkspaceRoots': ['F:/project'], 'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'}},
])
def test_thread_response_must_prove_no_environment_and_no_instructions(tmp_path, monkeypatch, thread):
    process = _FakeProcess(thread=thread)
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.verify_policy()

    assert all(call['method'] != 'turn/start' for call in process.requests)
    assert process.closed


def test_server_request_is_denied_and_never_approved(tmp_path, monkeypatch):
    process = _FakeProcess()

    def server_request(data):
        request = json.loads(data.decode('utf-8'))
        process.requests.append(request)
        if request.get('method') == 'initialize':
            process.stdout.append(_line({'id': request['id'], 'result': {
                'codexHome': process.installation_home, 'platformOs': 'windows',
            }}))
        elif request.get('method') == 'config/read':
            process.stdout.append(_line({'id': 'server-permission-1', 'method': 'item/commandExecution/requestApproval',
                                         'params': {}}))
        return None

    process.write_stdin = server_request
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.verify_policy()

    denied = [item for item in process.requests if item.get('id') == 'server-permission-1' and 'error' in item]
    assert denied and denied[0]['error']['code'] == -32601
    assert process.closed


def test_host_effect_item_aborts_turn_and_discards_partial_text(tmp_path, monkeypatch):
    process = _FakeProcess(startup_events=[
        _notification('item/started', {
            'threadId': 'thread-1', 'turnId': 'turn-1', 'startedAtMs': 1,
            'item': {'id': 'exec-1', 'type': 'commandExecution'},
        }),
    ])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.generate('data')

    assert process.closed


def test_failed_turn_does_not_return_partial_or_delta_text(tmp_path, monkeypatch):
    failed_turn = {'turn': {'id': 'turn-1', 'status': 'failed', 'items': [
        {'id': 'message-1', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'partial'},
    ]}}
    process = _FakeProcess(turn=failed_turn, startup_events=[_turn_completed('failed')])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.generate('data')

    assert process.closed


def test_duplicate_json_fields_are_rejected(tmp_path, monkeypatch):
    process = _FakeProcess()
    process.stdout.append(b'{"id":1,"id":1,"result":{}}\n')
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.verify_policy()

    assert process.closed


@pytest.mark.parametrize('line', [
    b'{"value":9223372036854775808}',
    b'{"value":' + b'9' * 5000 + b'}',
])
def test_json_integer_values_are_bounded_and_fail_closed(line):
    with pytest.raises(CodexAppServerError):
        _decode_line(line)


def test_cancel_before_launch_does_not_spawn_or_send_prompt(tmp_path, monkeypatch):
    process = _FakeProcess()
    server, _installation, created = _make_server(tmp_path, process, monkeypatch)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(CodexAppServerError):
        server.generate('private text', cancel_event=cancel)

    assert created == []


def test_cancel_after_turn_start_discards_result_and_closes_process(tmp_path, monkeypatch):
    cancel = threading.Event()
    process = _FakeProcess(
        startup_events=[_thread_started(), _turn_completed()],
        on_request=lambda request: cancel.set() if request.get('method') == 'turn/start' else None,
    )
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    with pytest.raises(CodexAppServerError):
        server.generate('text', cancel_event=cancel)

    assert process.closed


def test_connection_canary_sends_only_its_fixed_text_and_checks_exact_reply(tmp_path, monkeypatch):
    process = _FakeProcess(
        turn={'turn': {'id': 'turn-1', 'status': 'completed', 'items': [
            {'id': 'message-1', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'CODEX_CONNECTION_OK'},
        ]}},
        startup_events=[_thread_started(), _turn_completed(text='CODEX_CONNECTION_OK')],
    )
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)

    result = server.verify_connection()

    assert result.text == 'CODEX_CONNECTION_OK'
    turn = next(call for call in process.requests if call['method'] == 'turn/start')
    assert turn['params']['input'] == [{
        'type': 'text',
        'text': 'Reply with exactly CODEX_CONNECTION_OK. Do not call any tools, run commands, or read or write files.',
    }]
    assert process.closed

def test_agent_text_observer_forwards_only_message_deltas_and_never_runs_tools(tmp_path, monkeypatch):
    def updated(item):
        return _notification('item/updated', {
            'threadId': 'thread-1', 'turnId': 'turn-1', 'item': item,
        })

    process = _FakeProcess(startup_events=[
        updated({'id': 'reason-1', 'type': 'reasoning', 'text': 'hidden reasoning'}),
        updated({'id': 'user-1', 'type': 'userMessage', 'text': 'source text'}),
        _notification('item/agentMessage/delta', {
            'threadId': 'thread-1', 'turnId': 'turn-1',
            'itemId': 'message-1', 'delta': 'Visible',
        }),
        _notification('item/agentMessage/delta', {
            'threadId': 'thread-1', 'turnId': 'turn-1',
            'itemId': 'message-1', 'delta': ' answer',
        }),
        _turn_completed(text='Visible answer'),
    ])
    server, _installation, _created = _make_server(tmp_path, process, monkeypatch)
    deltas = []

    result = server.generate('data', on_agent_text=lambda item_id, delta: deltas.append((item_id, delta)))

    assert result.text == 'Visible answer'
    assert deltas == [('message-1', 'Visible'), ('message-1', ' answer')]
    assert 'hidden reasoning' not in repr(deltas)
    assert 'source text' not in repr(deltas)

    blocked_process = _FakeProcess(startup_events=[_notification('item/started', {
        'threadId': 'thread-1', 'turnId': 'turn-1', 'startedAtMs': 1,
        'item': {'id': 'exec-1', 'type': 'commandExecution'},
    })])
    blocked_case = tmp_path / 'blocked-case'
    blocked_case.mkdir()
    blocked_server, _installation, _created = _make_server(blocked_case, blocked_process, monkeypatch)
    attempted = []
    with pytest.raises(CodexAppServerError):
        blocked_server.generate('data', on_agent_text=lambda item_id, delta: attempted.append((item_id, delta)))
    assert attempted == []
    assert blocked_process.closed




