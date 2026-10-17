"""Provider choice and F:-only storage settings; never reads credentials."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QFrame, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, ComboBox, TitleLabel

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, UnavailableReason,
)
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient
from mathmodel_paper_studio.bootstrap.paths import AppPaths

_VISIBLE_PROVIDERS = (Provider.CODEX, Provider.DEEPSEEK)
_LABELS = {Provider.CODEX: 'Codex', Provider.DEEPSEEK: 'DeepSeek API'}


def _unverified_status(provider):
    if provider is Provider.DEEPSEEK:
        return DeepSeekApiClient().probe()
    return ProviderStatus(
        provider, None, None, False, UnavailableReason.CAPABILITIES_UNVERIFIED,
        'Codex 尚未完成本次完整能力及隔离预检；自动运行保持关闭。仅登录状态不代表预检通过。',
        AdapterCapabilities(), False,
    )


class SettingsView(QWidget):
    provider_changed = Signal(object)
    codex_connection_test_requested = Signal()

    def __init__(self, paths: AppPaths, provider_statuses=None, parent=None):
        super().__init__(parent)
        if not isinstance(paths, AppPaths):
            raise TypeError('设置页需要 AppPaths。')
        self.paths = paths
        self._codex_connection_text = '本机 CLI 检查尚未执行；模型请求未发起。'
        self._codex_probe_pending = False
        self._statuses = {provider: _unverified_status(provider) for provider in _VISIBLE_PROVIDERS}
        if provider_statuses is not None:
            for status in provider_statuses:
                if isinstance(status, ProviderStatus) and status.provider in _VISIBLE_PROVIDERS:
                    self._statuses[status.provider] = status
        self.setObjectName('settingsPage')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        scroll = QScrollArea(self)
        scroll.setObjectName('settingsScrollArea')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(0)
        content = QWidget(scroll)
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 10, 10)
        content_layout.setSpacing(12)

        data_card = CardWidget(content)
        data_card.setObjectName('dataRootSection')
        data_layout = QVBoxLayout(data_card)
        data_layout.addWidget(TitleLabel('数据目录', data_card))
        self.root_label = BodyLabel(str(paths.root), data_card)
        self.root_label.setObjectName('dataRootPath')
        self.root_label.setWordWrap(True)
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_layout.addWidget(CaptionLabel('应用项目、运行和审核文件固定保存在 F:。', data_card))
        data_layout.addWidget(self.root_label)
        content_layout.addWidget(data_card)

        provider_card = CardWidget(content)
        provider_card.setObjectName('providerSection')
        provider_layout = QVBoxLayout(provider_card)
        provider_layout.addWidget(TitleLabel('Agent 服务', provider_card))
        form = QFormLayout()
        self.provider_combo = ComboBox(provider_card)
        self.provider_combo.setObjectName('providerCombo')
        self.provider_combo.setMinimumWidth(0)
        for provider in _VISIBLE_PROVIDERS:
            self.provider_combo.addItem(_LABELS[provider], userData=provider)
        self.provider_combo.setCurrentIndex(0)
        self.connection_label = BodyLabel(provider_card)
        self.connection_label.setObjectName('providerConnectionStatus')
        self.connection_label.setWordWrap(True)
        self.codex_test_button = QPushButton('验证 Codex 文本连接', provider_card)
        self.codex_test_button.setObjectName('verifyCodexConnectionButton')
        self.codex_test_button.setToolTip(
            '发送一条固定 canary 提示验证本机 Codex 连接；不会发送题目或文件，可能使用账户额度。'
        )
        self.codex_test_button.clicked.connect(self.codex_connection_test_requested.emit)
        form.addRow('Agent', self.provider_combo)
        form.addRow('本机接入状态', self.connection_label)
        provider_layout.addLayout(form)
        provider_layout.addWidget(self.codex_test_button)
        content_layout.addWidget(provider_card)

        preflight_card = CardWidget(content)
        preflight_card.setObjectName('preflightSection')
        preflight_layout = QVBoxLayout(preflight_card)
        preflight_layout.addWidget(TitleLabel('运行预检', preflight_card))
        self.status_label = BodyLabel(preflight_card)
        self.status_label.setObjectName('automationPreflightStatus')
        self.status_label.setWordWrap(True)
        preflight_layout.addWidget(CaptionLabel('自动运行门槛', preflight_card))
        preflight_layout.addWidget(self.status_label)
        self.disclosure_label = BodyLabel(
            'Codex 使用本机 CLI 登录。DeepSeek 使用 API；本应用不保存 API 密钥，'
            '密钥只从 DEEPSEEK_API_KEY 环境变量读取，不显示也不写入项目数据。'
            'API 请求会发送审核页列出的题目、所选数据摘要和阶段结果，并按 token 用量计费。'
            '当前生成代码执行安全门未通过验收，运行会在计算阶段暂停，不会生成最终 Word。',
            preflight_card,
        )
        self.disclosure_label.setObjectName('providerDisclosure')
        self.disclosure_label.setWordWrap(True)
        preflight_layout.addWidget(self.disclosure_label)
        content_layout.addWidget(preflight_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        if self.provider_combo.currentIndex() < 0:
            self.provider_combo.setCurrentIndex(0)
        self.provider_combo.currentIndexChanged.connect(self._selection_changed)
        self._selection_changed()

    @property
    def selected_provider(self) -> Provider:
        return Provider(self.provider_combo.currentData())

    @property
    def selected_status(self) -> ProviderStatus:
        return self._statuses[self.selected_provider]

    def set_codex_probe_pending(self):
        self._codex_probe_pending = True
        self.codex_test_button.setEnabled(False)
        self._codex_connection_text = (
            '正在用固定提示验证 Codex 连接；不会发送题目或文件，可能使用账户额度。'
        )
        if self.selected_provider is Provider.CODEX:
            self.connection_label.setText(self._codex_connection_text)

    def set_codex_probe_status(self, status: ProviderStatus):
        if (not isinstance(status, ProviderStatus) or status.provider is not Provider.CODEX
                or status.available is not False or status.evidence_verified is not False):
            raise TypeError('Codex 连接检查状态无效。')
        self._codex_probe_pending = False
        self.codex_test_button.setEnabled(self.selected_provider is Provider.CODEX)
        self._statuses[Provider.CODEX] = status
        if status.connection_verified:
            self._codex_connection_text = (
                f'Codex CLI {status.version} 已收到固定文本连接验证回复；未发送题目或文件。'
                '这只证明文本连通，项目范围与执行隔离尚未通过，自动运行保持关闭。'
            )
        elif (status.reason is UnavailableReason.CAPABILITIES_UNVERIFIED and status.executable
              and status.version and '尚未发送固定文本连接请求' in status.message):
            self._codex_connection_text = (
                f'Codex CLI {status.version} 安装和本地登录状态已检查；尚未发送固定文本请求。'
            )
        elif status.reason is UnavailableReason.CAPABILITIES_UNVERIFIED and status.executable and status.version:
            self._codex_connection_text = (
                f'Codex CLI {status.version} 固定文本连通请求未通过；运行边界尚未验证，自动运行保持关闭。'
            )
        elif status.reason is UnavailableReason.LOGIN_REQUIRED:
            self._codex_connection_text = (
                '已找到本机 Codex CLI，但该独立登录环境尚未登录；模型请求未发起。'
            )
        elif status.reason is UnavailableReason.AUTH_UNVERIFIED:
            self._codex_connection_text = (
                '已找到本机 Codex CLI，但无法确认登录状态；模型请求未发起。'
            )
        elif status.reason is UnavailableReason.CLI_MISSING:
            self._codex_connection_text = '未找到应用固定位置中的本机 Codex CLI；模型请求未发起。'
        elif status.reason is UnavailableReason.VERSION_UNSUPPORTED:
            self._codex_connection_text = (
                '已找到本机 Codex CLI，但版本与应用固定版本不符；模型请求未发起。'
            )
        else:
            self._codex_connection_text = (
                '本机 Codex CLI 检查未完成；模型请求未发起，自动运行仍关闭。'
            )
        if self.selected_provider is Provider.CODEX:
            self._selection_changed()

    def set_codex_probe_failure(self):
        self._codex_probe_pending = False
        self.codex_test_button.setEnabled(self.selected_provider is Provider.CODEX)
        self._statuses[Provider.CODEX] = _unverified_status(Provider.CODEX)
        self._codex_connection_text = (
            '本机 Codex 连接验证失败；请检查登录状态或稍后重试，自动运行保持关闭。'
        )
        if self.selected_provider is Provider.CODEX:
            self._selection_changed()

    def set_provider_status(self, status: ProviderStatus):
        if not isinstance(status, ProviderStatus):
            raise TypeError('Agent 状态类型无效。')
        self._statuses[status.provider] = status
        if status.provider is self.selected_provider:
            self._selection_changed()

    def _selection_changed(self, *_):
        status = self.selected_status
        self.codex_test_button.setEnabled(
            self.selected_provider is Provider.CODEX and not self._codex_probe_pending
        )
        if self.selected_provider is Provider.CODEX:
            self.connection_label.setText(self._codex_connection_text)
        else:
            self.connection_label.setText(status.message)
        self.status_label.setText(status.message)
        self.provider_changed.emit(status)
