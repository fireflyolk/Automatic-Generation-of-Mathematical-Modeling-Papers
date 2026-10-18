"""Pre-run scope and provider disclosure; starting always needs an explicit click."""
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout,
)
from qfluentwidgets import BodyLabel, CardWidget, FluentIcon, PrimaryPushButton, PushButton, SubtitleLabel

from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus
from mathmodel_paper_studio.agents.deepseek_model_preferences import (
    DEFAULT_DEEPSEEK_EFFORT,
    DeepSeekModelPreference,
)
from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.security.path_guard import selected_path
from mathmodel_paper_studio.security.scope_manifest import EXTERNAL_PROCESSING_NOTICE

_PROVIDER_LABELS = {
    Provider.CODEX: 'Codex', Provider.DEEPSEEK: 'DeepSeek API',
}


@dataclass(frozen=True)
class RunReviewModel:
    provider_status: ProviderStatus
    project_root: Path
    readable_files: tuple[Path, ...]
    writable_directories: tuple[Path, ...]
    planned_stages: tuple[Stage, ...]
    scope_verified: bool
    scope_message: str
    external_processing_notice: str = EXTERNAL_PROCESSING_NOTICE
    scope_errors: tuple[str, ...] = ()
    docx_data_files: tuple[Path, ...] = ()
    docx_template_files: tuple[Path, ...] = ()
    deepseek_model_preference: DeepSeekModelPreference | None = None

    def __post_init__(self):
        if not isinstance(self.provider_status, ProviderStatus):
            raise TypeError('预运行摘要缺少 Agent 状态。')
        if (self.deepseek_model_preference is not None
                and (self.provider_status.provider is not Provider.DEEPSEEK
                     or not isinstance(self.deepseek_model_preference, DeepSeekModelPreference))):
            raise TypeError('DeepSeek 模型选择与 Agent 不匹配。')
        if not isinstance(self.project_root, Path):
            raise TypeError('项目根目录必须是 pathlib.Path。')
        if not isinstance(self.readable_files, (tuple, list)) or not isinstance(self.writable_directories, (tuple, list)):
            raise TypeError('运行范围必须是路径列表。')
        if not isinstance(self.planned_stages, (tuple, list)) or not self.planned_stages:
            raise ValueError('运行阶段不能为空。')
        if type(self.scope_verified) is not bool or not isinstance(self.scope_message, str):
            raise TypeError('范围检查状态无效。')
        if self.external_processing_notice != EXTERNAL_PROCESSING_NOTICE:
            raise ValueError('必须展示完整的外部处理说明。')
        errors = list(self.scope_errors)
        try:
            root = validate_managed_path(self.project_root)
            if not root.is_dir():
                errors.append('项目根目录不存在。')
            reads = tuple(validate_managed_path(Path(item)) for item in self.readable_files)
            writes = tuple(validate_managed_path(Path(item)) for item in self.writable_directories)
            docx_data = tuple(validate_managed_path(Path(item)) for item in self.docx_data_files)
            docx_templates = tuple(validate_managed_path(Path(item)) for item in self.docx_template_files)
            if any(path.suffix.casefold() != '.docx' for path in (*docx_data, *docx_templates)):
                errors.append('DOCX 数据或模板清单包含非 DOCX 文件。')
            if not set((*docx_data, *docx_templates)).issubset(reads):
                errors.append('DOCX 数据或模板必须包含在本次可读文件范围内。')
            if not reads:
                errors.append('本次没有明确选择可读文件。')
            if not writes:
                errors.append('本次没有明确选择可写目录。')
            if root.is_dir():
                for path in reads:
                    selected_path(path, root, reads)
                for path in writes:
                    if not path.is_relative_to(root) or path == root:
                        raise ValueError('可写目录必须是项目根目录中的明确子目录。')
                    if any(source.is_relative_to(path) for source in reads):
                        raise ValueError('可写目录不能包含只读输入。')
            object.__setattr__(self, 'project_root', root)
            object.__setattr__(self, 'readable_files', reads)
            object.__setattr__(self, 'writable_directories', writes)
            object.__setattr__(self, 'docx_data_files', docx_data)
            object.__setattr__(self, 'docx_template_files', docx_templates)
        except (OSError, TypeError, ValueError) as exc:
            errors.append(str(exc) or '文件范围无效。')
        object.__setattr__(self, 'planned_stages', tuple(Stage(item) for item in self.planned_stages))
        object.__setattr__(self, 'scope_errors', tuple(dict.fromkeys(errors)))

    @property
    def provider_ready(self) -> bool:
        status = self.provider_status
        caps = status.capabilities
        if status.provider is Provider.DEEPSEEK:
            return bool(
                status.available and (status.evidence_verified or status.provider is Provider.DEEPSEEK)
                and caps.cancellation and caps.filesystem_confinement
                and caps.network_behavior == 'fixed_https_api_only'
                and caps.credential_inheritance == 'process_memory_only'
                and caps.tool_profile == 'api_no_tools'
            )
        if status.provider is not Provider.CODEX:
            return False
        return bool(
            status.available and status.evidence_verified and caps.streaming and caps.cancellation
            and caps.filesystem_confinement and caps.network_behavior == 'provider_transport_only'
            and caps.credential_inheritance == 'cli_native_only'
            and caps.tool_profile == 'text_only_no_environment'
            and status.version == '0.159.3'
        )

    @property
    def can_start(self) -> bool:
        return self.provider_ready and self.scope_verified and not self.scope_errors

    @property
    def start_block_reasons(self) -> tuple[str, ...]:
        reasons = []
        if not self.provider_ready:
            detail = (self.provider_status.message.strip() or 'Agent 能力和隔离预检尚未通过。')[:500]
            reasons.append(f'Agent 运行预检未通过：{detail}')
        if not self.scope_verified:
            detail = (self.scope_message.strip() or '本次范围验证尚未完成。')[:500]
            reasons.append(f'本次文件范围未通过可信验证：{detail}')
        reasons.extend(f'文件范围错误：{error}' for error in self.scope_errors)
        return tuple(reasons)

    @property
    def summary_text(self) -> str:
        status = self.provider_status
        provider = _PROVIDER_LABELS.get(status.provider, '未支持的 Agent')
        if self.provider_ready and status.provider is Provider.DEEPSEEK:
            provider_state = '本机 API Key 已检测；远端连接与额度将在授权后的首次请求时确认。'
        elif self.provider_ready and status.provider is Provider.CODEX:
            provider_state = '本机无执行环境策略已验证；正式运行仍会再次检查文件范围。'
        else:
            provider_state = '本次可信预检通过' if self.provider_ready else (status.message or '本次预检未通过')
        lines = [
            f'Agent：{provider}', f'Agent 状态：{provider_state}', f'项目目录：{self.project_root}',
            '本次可读取文件：',
        ]
        if status.provider is Provider.DEEPSEEK:
            preference = self.deepseek_model_preference
            selected_model = preference.model if preference is not None else status.version
            selected_effort = preference.effort if preference is not None else DEFAULT_DEEPSEEK_EFFORT
            lines[2:2] = [
                f'API 地址与模型：{status.executable or "DeepSeek HTTPS API"} / {selected_model or "未确认"}',
                f'推理强度：{selected_effort}',
                '外发数据类别：题目文本、阶段提示词、所选输入的名称和数据摘要（包括已选 DOCX 正文及表格提取文本）、已验证上游阶段摘要、应用规则。',
                '文件传输：CSV/XLSX/DOCX 不作为文件附件上传；本适配器只发送工作流生成的文本请求。',
                '工具与本机权限：无工具调用、文件访问、代码执行或浏览功能。',
                '费用与外发：点击同意后，请求会发送到 DeepSeek API 并按 token 用量计费；费用取决于实际输入和输出。',
                '远端连接、API Key 有效性和账户额度尚未验证；首次请求可能失败。',
                '当前生成代码执行安全门未通过验收；运行会在计算阶段暂停，不会生成最终 Word。',
            ]
        elif status.provider is Provider.CODEX:
            lines[2:2] = [
                '外发数据类别：同意后，题目文本、所选文件提取出的文本摘要及阶段结果会通过 Codex CLI 发送；不会以文件附件上传。',
                '工具边界：本次 Codex 会话不提供项目工作区文件读取、写入、命令或代码执行工具。',
                '本机策略：每次会话验证空执行环境，并关闭 memory、history notes、cloud skills 和 skill instructions。',
                'Codex CLI 本身不是 Windows 操作系统级进程沙箱；它仍由当前 Windows 用户运行，可访问本机 CLI 自身配置和登录凭据。',
                '应用会将导入文本中的 @ 转为全角，避免触发显式本机 Skill 名称；Codex 仍可能内部检查本机 Skill 清单，不宣称已禁用所有主机文件读取。',
                'Codex CLI 还可能向所选服务发送附带的账号设置请求；这类请求由 CLI 服务处理，不属于项目文本内容。',
            ]
        if self.docx_data_files:
            lines.append('DOCX 数据处理：会提取正文段落和表格文本，作为未信任文本发送给所选 Agent；DOCX 不作为附件上传。')
        if self.docx_template_files:
            lines.append('Word 模板只在本机用于生成和校验文档，不会上传。')
        for path in self.readable_files:
            if path in self.docx_data_files:
                label = 'DOCX 数据：'
            elif path in self.docx_template_files:
                label = 'Word 模板：'
            else:
                label = ''
            lines.append(f'  • {label}{path}')
        lines.append('本次可写目录：')
        lines.extend(f'  • {path}' for path in self.writable_directories)
        lines.append('计划阶段：')
        lines.extend(f'  • {stage.value}' for stage in self.planned_stages)
        lines.append('文件范围状态：' + (self.scope_message if self.scope_verified else '尚未通过本次可信范围检查。'))
        lines.extend(f'范围问题：{item}' for item in self.scope_errors)
        if status.provider is Provider.DEEPSEEK:
            lines.append('服务商说明：DeepSeek API 会按其服务规则处理收到的文本请求。')
        else:
            lines.append(self.external_processing_notice)
        if status.provider is Provider.CODEX:
            lines.append('服务商说明：Codex 请求使用本机 CLI 登录的账户和对应服务商。')
        return '\n'.join(lines)


class RunReviewDialog(QDialog):
    start_requested = Signal(object)
    edit_scope_requested = Signal()
    change_agent_requested = Signal()

    def __init__(self, model: RunReviewModel, parent=None):
        super().__init__(parent)
        if not isinstance(model, RunReviewModel):
            raise TypeError('预运行审核窗口需要 RunReviewModel。')
        self.model = model
        self.start_confirmed = False
        self.setWindowTitle('运行前范围确认')
        self.setMinimumSize(580, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        review_card = CardWidget(self)
        review_layout = QVBoxLayout(review_card)
        heading = SubtitleLabel('开始前请检查 Agent、文件范围和处理说明', review_card)
        heading.setObjectName('runReviewHeading')
        heading.setWordWrap(True)
        review_layout.addWidget(heading)
        review_layout.addWidget(BodyLabel('本次运行将读取以下文件，并仅在列出的目录中写入产物。', review_card))
        self.summary_view = QPlainTextEdit()
        self.summary_view.setObjectName('runScopeDisclosure')
        self.summary_view.setAccessibleName('本次运行范围和数据处理说明')
        self.summary_view.setReadOnly(True)
        self.summary_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.summary_view.setMinimumWidth(0)
        self.summary_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.summary_view.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.summary_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.summary_view.setPlainText(model.summary_text)
        review_layout.addWidget(self.summary_view, 1)
        layout.addWidget(review_card, 1)
        self.start_block_reason_label = BodyLabel(self)
        self.start_block_reason_label.setObjectName('startRunDisabledReason')
        self.start_block_reason_label.setAccessibleName('开始按钮不可用原因')
        self.start_block_reason_label.setTextFormat(Qt.TextFormat.PlainText)
        self.start_block_reason_label.setWordWrap(True)
        if model.start_block_reasons:
            reason_text = '\n'.join(f'• {reason}' for reason in model.start_block_reasons)
            next_step = ('请点击“更换 Agent”选择可用服务，然后重新确认本次运行。'
                         if not model.provider_ready else
                         '请编辑文件范围或完成本次范围检查后重新确认。')
            self.start_block_reason_label.setText(
                f'暂不能开始：\n{reason_text}\n{next_step}')
            self.start_block_reason_label.show()
        else:
            self.start_block_reason_label.hide()
        layout.addWidget(self.start_block_reason_label)
        actions = QHBoxLayout()
        self.edit_scope_button = PushButton('编辑范围', self)
        self.edit_scope_button.setIcon(FluentIcon.EDIT)
        self.edit_scope_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.edit_scope_button.setAccessibleName('编辑本次运行文件范围')
        self.change_agent_button = PushButton('更换 Agent', self)
        self.change_agent_button.setObjectName('changeRunAgentButton')
        self.change_agent_button.setIcon(FluentIcon.SETTING)
        self.change_agent_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.change_agent_button.setAccessibleName('更换本次运行的 Agent 服务')
        self.change_agent_button.setVisible(not model.provider_ready)
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.setIcon(FluentIcon.CANCEL)
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_button.setAccessibleName('取消运行审核')
        start_label = ('同意数据外发、按量计费并开始'
                       if model.provider_status.provider is Provider.DEEPSEEK else '同意范围并开始')
        self.start_button = PrimaryPushButton(start_label, self)
        self.start_button.setIcon(FluentIcon.PLAY)
        self.start_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.start_button.setAccessibleName(start_label)
        self.start_button.setObjectName('startRunButton')
        self.start_button.setEnabled(model.can_start)
        if model.start_block_reasons:
            self.start_button.setToolTip(self.start_block_reason_label.text())
            self.start_button.setAccessibleDescription(self.start_block_reason_label.text())
        actions.addWidget(self.edit_scope_button)
        actions.addWidget(self.change_agent_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        self.edit_scope_button.clicked.connect(self._edit_scope)
        self.change_agent_button.clicked.connect(self._change_agent)
        self.cancel_button.clicked.connect(self.reject)
        self.start_button.clicked.connect(self._start)
        self.setTabOrder(self.edit_scope_button, self.change_agent_button)
        self.setTabOrder(self.change_agent_button, self.cancel_button)
        self.setTabOrder(self.cancel_button, self.start_button)

    def _edit_scope(self):
        self.edit_scope_requested.emit()
        self.reject()

    def _change_agent(self):
        self.change_agent_requested.emit()
        self.reject()

    def _start(self):
        if not self.model.can_start:
            return
        self.start_confirmed = True
        self.start_requested.emit(self.model)
        self.accept()
