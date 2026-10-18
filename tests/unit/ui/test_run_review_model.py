import os
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtTest import QSignalSpy, QTest

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, UnavailableReason,
)
from mathmodel_paper_studio.agents.deepseek_model_preferences import DeepSeekModelPreference
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient, ENDPOINT, MODEL
from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.security.scope_manifest import EXTERNAL_PROCESSING_NOTICE
from mathmodel_paper_studio.ui.run_review import RunReviewDialog, RunReviewModel

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task5-tests')
APP = None


def ready_status():
    return ProviderStatus(
        Provider.CODEX, 'codex.exe', '0.159.3', True, None, '能力检查通过。',
        AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only',
                            'text_only_no_environment'), True,
    )


def unavailable_status():
    return ProviderStatus(
        Provider.CLAUDE, None, None, False, UnavailableReason.EVIDENCE_UNVERIFIED,
        '尚未完成可信隔离检查，暂不能自动运行。', AdapterCapabilities(), False,
    )


def codex_capabilities_unverified_status():
    return ProviderStatus(
        Provider.CODEX, 'codex.exe', '0.159.3', False, UnavailableReason.CAPABILITIES_UNVERIFIED,
        'Codex 能力及隔离预检未通过。', AdapterCapabilities(), False,
    )


def deepseek_local_key_status():
    return DeepSeekApiClient(key_resolver=lambda: 'unit-test-only-key').probe()


class RunReviewModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        ROOT.mkdir(parents=True, exist_ok=True)
        APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'projects' / 'demo1'
        (self.project / '00_inputs').mkdir(parents=True)
        (self.project / '90_runs').mkdir()
        self.source = self.project / '00_inputs' / 'data.csv'
        self.source.write_text('x,y\n1,2\n', encoding='utf-8')
        self.docx_source = self.project / '00_inputs' / 'reading.docx'
        self.docx_source.write_bytes(b'fixture')
        self.output = self.project / '90_runs' / 'run1'

    def model(self, status=None, scope_verified=True, docx_data_files=(),
              deepseek_model_preference=None):
        return RunReviewModel(
            provider_status=status or ready_status(), project_root=self.project,
            readable_files=(self.source, *docx_data_files), writable_directories=(self.output,),
            planned_stages=(Stage.PREFLIGHT, Stage.DATA_PROFILE, Stage.DOCX_QA),
            scope_verified=scope_verified,
            scope_message='文件范围已由可信检查确认。' if scope_verified else '文件隔离尚未验证。',
            docx_data_files=docx_data_files,
            deepseek_model_preference=deepseek_model_preference,
        )

    def test_pre_run_summary_shows_exact_scope_provider_stages_and_disclosure(self):
        dialog = RunReviewDialog(self.model())
        self.addCleanup(dialog.close)
        dialog.show()
        QTest.qWait(10)
        text = dialog.summary_view.toPlainText()

        self.assertIn('Codex', text)
        self.assertNotIn('Claude', text)
        self.assertIn(str(self.project), text)
        self.assertIn(str(self.source), text)
        self.assertIn(str(self.output), text)
        self.assertIn(Stage.PREFLIGHT.value, text)
        self.assertIn(EXTERNAL_PROCESSING_NOTICE, text)
        self.assertIn('Codex CLI 本身不是 Windows 操作系统级进程沙箱', text)
        self.assertIn('关闭 memory、history notes、cloud skills 和 skill instructions', text)
        self.assertIn('不提供项目工作区文件读取、写入、命令或代码执行工具', text)
        self.assertIn('本机 CLI 自身配置和登录凭据', text)
        self.assertIn('附带的账号设置请求', text)
        self.assertTrue(dialog.start_button.isEnabled())

    def test_start_stays_disabled_when_provider_or_scope_preflight_fails(self):
        for model in (self.model(unavailable_status()), self.model(scope_verified=False)):
            with self.subTest(scope=model.scope_verified, provider=model.provider_status.available):
                dialog = RunReviewDialog(model)
                dialog.show()
                QTest.qWait(5)
                self.assertFalse(dialog.start_button.isEnabled())
                dialog.close()

    def test_codex_ready_requires_the_verified_no_environment_tool_profile(self):
        legacy = ProviderStatus(
            Provider.CODEX, 'codex.exe', '0.159.3', True, None, 'legacy profile',
            AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only',
                                'text_only'), True,
        )
        self.assertFalse(self.model(status=legacy).provider_ready)
        self.assertTrue(self.model().provider_ready)

    def test_disabled_start_button_explains_codex_and_file_scope_gates(self):
        models = (
            self.model(codex_capabilities_unverified_status()),
            self.model(scope_verified=False),
        )
        for model in models:
            with self.subTest(scope=model.scope_verified):
                dialog = RunReviewDialog(model)
                dialog.show()
                QTest.qWait(5)
                self.assertFalse(dialog.start_button.isEnabled())
                self.assertTrue(dialog.start_block_reason_label.isVisible())
                self.assertIn('不能开始', dialog.start_block_reason_label.text())
                self.assertTrue(dialog.start_button.toolTip())
                if not model.scope_verified:
                    self.assertIn('文件范围', dialog.start_block_reason_label.text())
                else:
                    self.assertIn('Codex 能力及隔离预检未通过', dialog.start_block_reason_label.text())
                dialog.close()

    def test_edit_scope_closes_review_so_the_user_can_change_selected_files(self):
        dialog = RunReviewDialog(self.model())
        self.addCleanup(dialog.close)
        dialog.show()
        edited = QSignalSpy(dialog.edit_scope_requested)

        QTest.mouseClick(dialog.edit_scope_button, Qt.MouseButton.LeftButton)

        self.assertEqual(1, edited.count())
        self.assertFalse(dialog.isVisible())

    def test_blocked_codex_review_can_change_agent_without_authorizing_a_run(self):
        dialog = RunReviewDialog(self.model(codex_capabilities_unverified_status(),
                                            scope_verified=False))
        self.addCleanup(dialog.close)
        dialog.show()
        started = QSignalSpy(dialog.start_requested)
        button = dialog.findChild(QPushButton, 'changeRunAgentButton')
        self.assertIsNotNone(button, 'A blocked provider must offer a way to change Agent.')
        changed = QSignalSpy(dialog.change_agent_requested)

        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

        self.assertEqual(1, changed.count())
        self.assertEqual(0, started.count())
        self.assertFalse(dialog.start_confirmed)
        self.assertFalse(dialog.isVisible())

    def test_start_requires_deliberate_click_and_cancel_is_available(self):
        dialog = RunReviewDialog(self.model())
        self.addCleanup(dialog.close)
        dialog.show()
        start_spy = QSignalSpy(dialog.start_requested)
        self.assertEqual(0, start_spy.count())
        QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)

        self.assertEqual(1, start_spy.count())
        self.assertTrue(dialog.start_confirmed)
        self.assertTrue(dialog.cancel_button.isEnabled())

    def test_deepseek_review_shows_api_scope_and_requires_click_consent(self):
        preference = DeepSeekModelPreference('deepseek-v4-pro', 'max')
        model = self.model(status=deepseek_local_key_status(), docx_data_files=(self.docx_source,),
                           deepseek_model_preference=preference)
        self.assertTrue(model.provider_ready)
        self.assertTrue(model.can_start)
        self.assertIn('DeepSeek API', model.summary_text)
        self.assertIn(ENDPOINT, model.summary_text)
        self.assertIn('deepseek-v4-pro', model.summary_text)
        self.assertIn('推理强度：max', model.summary_text)
        self.assertIn('按 token 用量计费', model.summary_text)
        self.assertIn('所选输入的名称和数据摘要', model.summary_text)
        self.assertIn('不作为文件附件上传', model.summary_text)
        self.assertIn('DOCX 数据处理：会提取正文段落和表格文本', model.summary_text)
        self.assertIn(str(self.docx_source), model.summary_text)
        self.assertIn('无工具调用', model.summary_text)
        self.assertNotIn('对应 CLI 发送', model.summary_text)

        dialog = RunReviewDialog(model)
        self.addCleanup(dialog.close)
        self.assertIn('按量计费', dialog.start_button.text())
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertFalse(dialog.start_confirmed)

    def test_legacy_claude_status_cannot_be_presented_or_started(self):
        legacy_status = ProviderStatus(
            Provider.CLAUDE, 'claude.exe', '2.1.291', True, None, '能力检查通过。',
            AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only',
                                'restricted_read_write'), True,
        )
        model = self.model(status=legacy_status)

        self.assertFalse(model.provider_ready)
        self.assertFalse(model.can_start)
        self.assertIn('未支持的 Agent', model.summary_text)
        self.assertNotIn('Claude', model.summary_text)
        dialog = RunReviewDialog(model)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.start_button.isEnabled())

    def test_deepseek_review_without_local_key_keeps_start_disabled(self):
        status = DeepSeekApiClient(key_resolver=lambda: None).probe()
        model = self.model(status=status)

        self.assertFalse(model.provider_ready)
        self.assertFalse(model.can_start)
        dialog = RunReviewDialog(model)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.start_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
