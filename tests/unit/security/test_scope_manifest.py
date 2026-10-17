from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE
from mathmodel_paper_studio.domain.models import ScopeManifest, Stage
from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.agents.protocol import AgentRequest, AgentEvent, EventKind
from mathmodel_paper_studio.security.scope_manifest import build_scope, EXTERNAL_PROCESSING_NOTICE


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task3' / uuid.uuid4().hex
        self.project = self.root / 'projects/demo'
        (self.project / 'inputs').mkdir(parents=True)
        (self.project / 'out').mkdir()
        (self.project / 'inputs/x.csv').write_text('x', encoding='utf-8')
        self.metadata = ScopeManifest('scope1', 'run1', Provider.CLAUDE, 'projects/demo',
                                      ('inputs/x.csv',), ('out',), (Stage.PAPER_DRAFTING,), True)
        self.scope = build_scope(self.root, self.metadata, Path(sys.executable), 'test-version', 'restricted_read_write')

    def test_scope_binds_executable_digest_selection_and_notice(self):
        self.assertEqual(len(self.scope.executable_sha256), 64)
        self.assertEqual(self.scope.project_root, self.project)
        self.assertEqual(self.scope.notice, EXTERNAL_PROCESSING_NOTICE)
        self.assertNotEqual(self.scope.digest, replace(self.scope, version='changed').digest)
        self.assertEqual(self.scope.readable_inputs, (self.project / 'inputs/x.csv',))
        with self.assertRaises(FrozenInstanceError):
            self.scope.version = 'changed'

    def test_scope_rejects_external_notice_omission_tool_expansion_missing_selection(self):
        for metadata, profile in [(replace(self.metadata, external_processing=False), 'restricted_read_write'),
                                  (self.metadata, 'shell'),
                                  (replace(self.metadata, readable_inputs=('inputs/missing.csv',)), 'restricted_read_write')]:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                build_scope(self.root, metadata, Path(sys.executable), 'test', profile)

    def test_request_and_event_are_strict_immutable_and_do_not_repr_prompt(self):
        request = AgentRequest(self.scope, Stage.PAPER_DRAFTING, 'private prompt')
        self.assertNotIn('private prompt', repr(request))
        with self.assertRaises(ValueError):
            AgentRequest(self.scope, Stage.CALCULATION, 'prompt')
        with self.assertRaises(ValueError):
            AgentEvent('run1', 'scope_granted', 'untrusted')
        with self.assertRaises(ValueError):
            AgentEvent('run1', EventKind.TEXT, 'x' * 65537)
        with self.assertRaises(FrozenInstanceError):
            request.prompt = 'changed'

    def test_launch_configuration_is_part_of_scope_and_expansion_is_blocked(self):
        changed = replace(self.scope, launch_arguments=('--restricted',))
        self.assertNotEqual(self.scope.digest, changed.digest)
        for arguments in [('--add-dir', 'outside'), ('--dangerously-skip-permissions',),
                          ('--tools', 'Read,Write,Bash'), ('--settings=x',)]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                replace(self.scope, launch_arguments=arguments)
