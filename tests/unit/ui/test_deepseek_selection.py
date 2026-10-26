import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, UnavailableReason,
)
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.ui.settings_view import SettingsView

ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-fluent-20261017\task5-tests')
APP = None


class DeepSeekSelectionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''})
        environment.start()
        self.addCleanup(environment.stop)

    @classmethod
    def setUpClass(cls):
        global APP
        ROOT.mkdir(parents=True, exist_ok=True)
        APP = QApplication.instance() or QApplication([])

    def test_selector_offers_codex_and_deepseek_without_claude(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)
            providers = [view.provider_combo.itemData(index)
                         for index in range(view.provider_combo.count())]
            self.assertEqual(providers, [Provider.CODEX, Provider.DEEPSEEK])

    def test_deepseek_defaults_to_missing_key_and_discloses_token_billing(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)
            view.provider_combo.setCurrentIndex(1)
            self.assertIs(view.selected_provider, Provider.DEEPSEEK)
            self.assertIs(view.selected_status.reason, UnavailableReason.API_KEY_REQUIRED)
            disclosure = '\n'.join(label.text() for label in view.findChildren(type(view.status_label)))
            self.assertIn('按 token 用量计费', disclosure)
            self.assertIn('不保存 API 密钥', disclosure)

    def test_deepseek_reports_local_key_presence_without_claiming_remote_readiness(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary, \
                patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'unit-test-only-key'}):
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)
            view.provider_combo.setCurrentIndex(1)

            status = view.selected_status

            self.assertTrue(status.available)
            self.assertFalse(status.evidence_verified)
            self.assertIn('未验证', status.message)
            self.assertIn('远端连接', status.message)
            self.assertIn('本机环境检测到 API Key', view.connection_label.text())
            self.assertIn('未验证远端连接', view.connection_label.text())

    def test_settings_groups_data_root_provider_and_preflight_without_wide_minimum(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)

            self.assertIsNotNone(view.findChild(QWidget, 'dataRootSection'))
            self.assertIsNotNone(view.findChild(QWidget, 'providerSection'))
            self.assertIsNotNone(view.findChild(QWidget, 'preflightSection'))
            self.assertLessEqual(view.minimumSizeHint().width(), 1024)

    def test_deepseek_environment_secret_is_never_rendered_or_persisted(self):
        secret = 'deepseek-test-secret-never-display'
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary, \
                patch.dict(os.environ, {'DEEPSEEK_API_KEY': secret}):
            app_root = Path(temporary) / 'app-data'
            app_root.mkdir()
            view = SettingsView(AppPaths(app_root))
            self.addCleanup(view.close)
            view.provider_combo.setCurrentIndex(1)

            rendered = '\n'.join(
                widget.text() for widget in view.findChildren(QLabel)
            )

            self.assertTrue(view.selected_status.available)
            self.assertNotIn(secret, rendered)
            stored = b''.join(path.read_bytes() for path in app_root.rglob('*') if path.is_file())
            self.assertNotIn(secret.encode(), stored)

    def test_codex_adapter_connection_is_shown_separately_from_automation_gate(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)

            self.assertIs(view.selected_provider, Provider.CODEX)
            self.assertIn('本机 CLI 检查尚未执行', view.connection_label.text())
            self.assertIn('模型请求未发起', view.connection_label.text())
            self.assertFalse(view.selected_status.available)
            self.assertIn('完整能力及隔离预检', view.status_label.text())
            self.assertIn('自动运行', view.status_label.text())

    def test_live_codex_local_status_does_not_claim_model_connection_or_open_run_gate(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)
            status = ProviderStatus(
                Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', False,
                UnavailableReason.CAPABILITIES_UNVERIFIED,
                'Codex 本地版本和登录状态已检查；尚未发送固定文本连接请求。',
                AdapterCapabilities(tool_profile='unverified'), False,
            )

            initial_status = view.selected_status
            view.set_codex_probe_status(status)

            self.assertIn('0.159.3', view.connection_label.text())
            self.assertIn('安装和本地登录状态已检查', view.connection_label.text())
            self.assertIn('尚未发送固定文本请求', view.connection_label.text())
            self.assertIs(view.selected_status, status)
            self.assertIsNot(view.selected_status, initial_status)
            self.assertFalse(view.selected_status.available)
            self.assertIn('尚未发送固定文本连接请求', view.status_label.text())
            self.assertEqual(status.message, view.status_label.text())
            self.assertTrue(view.codex_test_button.isEnabled())

    def test_codex_settings_accepts_verified_no_environment_policy_but_rejects_legacy_profile(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            view = SettingsView(AppPaths(Path(temporary) / 'app-data'))
            self.addCleanup(view.close)
            initial_status = view.selected_status
            legacy_ready_status = ProviderStatus(
                Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', True, None,
                'synthetic ready status',
                AdapterCapabilities(True, True, True, 'provider_transport_only',
                                    'cli_native_only', 'text_only'), True,
            )

            with self.assertRaises(TypeError):
                view.set_codex_probe_status(legacy_ready_status)

            self.assertIs(view.selected_status, initial_status)
            self.assertFalse(view.selected_status.available)

            verified_status = ProviderStatus(
                Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', True, None,
                'empty execution environment verified',
                AdapterCapabilities(True, True, True, 'provider_transport_only',
                                    'cli_native_only', 'text_only_no_environment'), True,
            )
            view.set_codex_probe_status(verified_status)

            self.assertIs(view.selected_status, verified_status)
            self.assertTrue(view.selected_status.available)
            self.assertIn('无执行环境策略已验证', view.connection_label.text())


if __name__ == '__main__':
    unittest.main()
