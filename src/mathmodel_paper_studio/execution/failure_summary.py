from dataclasses import dataclass
import re


_ERROR_TYPES = frozenset({
    'AssertionError', 'AttributeError', 'FileNotFoundError', 'ImportError',
    'IndexError', 'KeyError', 'MemoryError', 'ModuleNotFoundError', 'NameError',
    'OverflowError', 'PermissionError', 'RuntimeError', 'SyntaxError', 'TypeError',
    'UnboundLocalError', 'ValueError', 'ZeroDivisionError', 'OutputValidationError',
})
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')


@dataclass(frozen=True)
class ExecutionFailureSummary:
    """Allowlisted generated-script failure facts safe to send to a repair request."""

    error_type: str
    line: int | None
    name: str | None = None

    def __post_init__(self):
        if self.error_type not in _ERROR_TYPES:
            raise ValueError('计算错误类型不在允许的诊断集合中。')
        if self.line is not None and (type(self.line) is not int or not 1 <= self.line <= 10_000_000):
            raise ValueError('计算错误行号无效。')
        if self.name is not None and (not isinstance(self.name, str) or not _IDENTIFIER.fullmatch(self.name)):
            raise ValueError('计算错误名称无效。')

    def to_dict(self) -> dict:
        return {'error_type': self.error_type, 'line': self.line, 'name': self.name}

    @classmethod
    def from_value(cls, value):
        if not isinstance(value, dict) or set(value) != {'error_type', 'line', 'name'}:
            return None
        try:
            return cls(value['error_type'], value['line'], value['name'])
        except (TypeError, ValueError):
            return None

    def display_text(self) -> str:
        if self.error_type == 'OutputValidationError':
            return '声明的 CSV/PNG 输出未通过结构校验。'
        location = f'generated.py 第 {self.line} 行' if self.line is not None else 'generated.py'
        if self.name is not None and self.error_type in ('NameError', 'UnboundLocalError'):
            return f'{self.error_type}，{location}，未定义名称 {self.name}。'
        return f'{self.error_type}，{location}。'
