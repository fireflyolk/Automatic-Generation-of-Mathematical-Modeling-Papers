"""Synthetic fake-CLI contracts. No live provider or credential store is used."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from mathmodel_paper_studio.agents.preflight import (
    Provider, UnavailableReason, assess_provider, observed_provider_statuses,
)


def fake_report(provider="codex", scenario="healthy"):
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "tests" / "fixtures" / "cli" / f"fake_{provider}.py"), scenario],
        capture_output=True, text=True, check=True, timeout=5, cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    ).stdout


def status(provider="codex", scenario="healthy", **overrides):
    args = dict(
        executable=str(ROOT / "tests" / "fixtures" / "cli" / f"fake_{provider}.py"),
        version="0.159.3" if provider == "codex" else "2.1.291",
        authenticated=True, capability_report=fake_report(provider, scenario),
        evidence_verified=True,
    )
    args.update(overrides)
    return assess_provider(Provider(provider), **args)


class ProviderPreflightTests(unittest.TestCase):
    def test_healthy_synthetic_contract_is_ready_and_immutable(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                result = status(provider)
                self.assertTrue(result.available)
                self.assertIsNone(result.reason)
                self.assertTrue(result.capabilities.filesystem_confinement)
                self.assertTrue(result.capabilities.streaming)
                self.assertTrue(result.capabilities.cancellation)
                self.assertEqual(result.capabilities.network_behavior, "provider_transport_only")
                self.assertEqual(result.capabilities.credential_inheritance, "cli_native_only")
                with self.assertRaises(FrozenInstanceError):
                    result.available = False

    def test_distinct_unavailable_reasons(self):
        cases = (
            ({"executable": None}, UnavailableReason.CLI_MISSING),
            ({"version": "999.0.0"}, UnavailableReason.VERSION_UNSUPPORTED),
            ({"version": None}, UnavailableReason.VERSION_UNSUPPORTED),
            ({"authenticated": False}, UnavailableReason.LOGIN_REQUIRED),
            ({"authenticated": None}, UnavailableReason.AUTH_UNVERIFIED),
            ({"capability_report": None}, UnavailableReason.CAPABILITIES_UNVERIFIED),
            ({"capability_report": "not JSON"}, UnavailableReason.CAPABILITY_REPORT_MALFORMED),
            ({"evidence_verified": False}, UnavailableReason.EVIDENCE_UNVERIFIED),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                result = status(**overrides)
                self.assertFalse(result.available)
                self.assertIs(result.reason, reason)
                self.assertTrue(result.message)

    def test_fake_cli_capability_failures_are_closed(self):
        cases = (
            ("unconfined", UnavailableReason.FILESYSTEM_UNCONFINED),
            ("no_cancel", UnavailableReason.CANCELLATION_UNVERIFIED),
            ("no_stream", UnavailableReason.STREAMING_UNVERIFIED),
            ("network_unknown", UnavailableReason.NETWORK_UNVERIFIED),
            ("credentials_inherited", UnavailableReason.CREDENTIAL_ISOLATION_UNVERIFIED),
            ("malformed", UnavailableReason.CAPABILITY_REPORT_MALFORMED),
        )
        for scenario, reason in cases:
            with self.subTest(scenario=scenario):
                result = status(scenario=scenario)
                self.assertFalse(result.available)
                self.assertIs(result.reason, reason)

    def test_malformed_or_mismatched_report_cannot_enable_provider(self):
        cases = (("filesystem_confinement", "true"), ("cancellation", 1),
                 ("schema_version", True), ("provider", "claude"),
                 ("unexpected", True), ("network_behavior", "anything"))
        for field, value in cases:
            with self.subTest(field=field):
                report = json.loads(fake_report())
                report[field] = value
                result = status(capability_report=json.dumps(report))
                self.assertIs(result.reason, UnavailableReason.CAPABILITY_REPORT_MALFORMED)
                self.assertFalse(result.available)

    def test_duplicate_keys_are_rejected(self):
        report = fake_report().strip()[:-1] + ', "filesystem_confinement": true}'
        self.assertIs(status(capability_report=report).reason,
                      UnavailableReason.CAPABILITY_REPORT_MALFORMED)

    def test_healthy_provider_does_not_enable_the_other(self):
        self.assertTrue(status("codex").available)
        self.assertFalse(status("claude", "unconfined").available)

    def test_real_observations_enable_only_the_verified_restricted_profile(self):
        results = observed_provider_statuses()
        self.assertEqual({entry.provider for entry in results}, {Provider.CODEX, Provider.DEEPSEEK})
        codex, deepseek = results
        self.assertFalse(codex.capabilities.filesystem_confinement)
        self.assertFalse(codex.available)
        self.assertFalse(deepseek.available)
        self.assertIs(deepseek.reason, UnavailableReason.API_KEY_REQUIRED)
        self.assertFalse(deepseek.evidence_verified)

    def test_generic_imported_observation_cannot_authorize_deepseek(self):
        result = assess_provider(
            Provider.DEEPSEEK, executable="fake", version="openai-compatible-v1",
            authenticated=True, capability_report=fake_report("codex"), evidence_verified=True,
        )
        self.assertFalse(result.available)
        self.assertIs(result.reason, UnavailableReason.API_KEY_REQUIRED)


if __name__ == "__main__":
    unittest.main()


