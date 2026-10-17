from dataclasses import replace
from pathlib import Path
import sys
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE
from mathmodel_paper_studio.domain.models import ScopeManifest, Stage
from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus, AdapterCapabilities, observed_provider_statuses
from mathmodel_paper_studio.security.scope_manifest import build_scope
from mathmodel_paper_studio.security.sandbox_gate import ScopeGate, ProbeResult, GateBlocked


class ScopeGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task3' / uuid.uuid4().hex
        self.project = self.root / 'projects/demo'
        (self.project / 'out').mkdir(parents=True)
        (self.project / 'input.txt').write_text('selected', encoding='utf-8')
        self.scope = build_scope(self.root, ScopeManifest('s1', 'r1', Provider.CLAUDE, 'projects/demo',
                                 ('input.txt',), ('out',), (Stage.PAPER_DRAFTING,), True),
                                 Path(sys.executable), 'fake1', 'restricted_read_write')
        caps = AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only', 'restricted_read_write')
        self.status = ProviderStatus(Provider.CLAUDE, str(self.scope.executable), 'fake1', True, None, 'fake', caps, True)

    def test_historical_status_imported_claims_and_missing_probe_never_authorize(self):
        for value in [self.status, observed_provider_statuses()[1], {'verified': True}, None]:
            with self.subTest(claim=type(value).__name__), self.assertRaises(GateBlocked):
                ScopeGate().authorize(self.scope, value)

    def test_trusted_fake_probe_grant_is_ephemeral_scope_bound_and_nontransferable(self):
        gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(self.status, True, True, True, True))
        grant = gate.authorize(self.scope)
        gate.require(grant, self.scope)
        for target in [replace(self.scope, version='different'), replace(self.scope, tool_profile='workspace_exec')]:
            with self.assertRaises(GateBlocked):
                gate.require(grant, target)
        with self.assertRaises(GateBlocked):
            ScopeGate().require(grant, self.scope)
        gate.revoke(grant)
        with self.assertRaises(GateBlocked):
            gate.require(grant, self.scope)

    def test_scope_guard_denies_outside_read_write_without_touching_markers(self):
        outside = self.root / 'outside.txt'
        outside.write_text('outside marker', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.scope.check_read(outside)
        with self.assertRaises(ValueError):
            self.scope.check_write(self.root / 'forbidden.txt')
        self.assertEqual(outside.read_text(encoding='utf-8'), 'outside marker')
        self.assertFalse((self.root / 'forbidden.txt').exists())

    def test_failed_fresh_capability_and_generated_python_are_blocked(self):
        for checks in [(False, True, True, True), (True, False, True, True), (True, True, False, True), (True, True, True, False)]:
            gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(self.status, *checks))
            with self.subTest(checks=checks), self.assertRaises(GateBlocked):
                gate.authorize(self.scope)
        gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(self.status, True, True, True, True))
        with self.assertRaisesRegex(GateBlocked, 'Python'):
            gate.authorize(self.scope, purpose='generated_python')

    def test_expired_evidence_is_blocked(self):
        import time
        gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(self.status, True, True, True, True), lifetime_seconds=1)
        grant = gate.authorize(self.scope)
        from unittest.mock import patch
        with patch('mathmodel_paper_studio.security.sandbox_gate.time.monotonic', return_value=time.monotonic() + 2):
            with self.assertRaises(GateBlocked):
                gate.require(grant, self.scope)

