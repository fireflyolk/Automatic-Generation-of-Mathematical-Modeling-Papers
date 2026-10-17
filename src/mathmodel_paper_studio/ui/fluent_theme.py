"""Initialize Fluent resources and follow the Windows system palette."""
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme


def apply_fluent_theme(app: QApplication) -> None:
    """Apply a nonpersistent system theme after QApplication is created."""
    if app is None or QApplication.instance() is not app:
        raise ValueError('Fluent 主题需要当前 QApplication。')
    setTheme(Theme.AUTO, save=False)
    available = set(QFontDatabase.families())
    for family in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Noto Sans CJK SC'):
        if family in available:
            font = QFont(app.font())
            font.setFamily(family)
            app.setFont(font)
            break
