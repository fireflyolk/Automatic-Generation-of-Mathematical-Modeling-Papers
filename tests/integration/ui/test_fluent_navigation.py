"""Run Fluent UI contracts without importing Qt into the collecting process."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\navigation')
_CHILD_FLAG = 'MATHMODEL_FLUENT_NAVIGATION_CHILD'


if os.environ.get(_CHILD_FLAG) == '1':
    from types import SimpleNamespace

    from PySide6.QtCore import QPoint, QRect, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from PySide6.QtTest import QTest
    from qfluentwidgets import FluentWindow, Theme, qconfig

    from mathmodel_paper_studio.agents.preflight import Provider
    from mathmodel_paper_studio.bootstrap.paths import AppPaths
    from mathmodel_paper_studio.domain.models import RunStatus
    from mathmodel_paper_studio.projects.project_service import ProjectService
    from mathmodel_paper_studio.storage.database import Database
    from mathmodel_paper_studio.storage.repositories import ProjectRepository
    from mathmodel_paper_studio.ui.fluent_theme import apply_fluent_theme
    from mathmodel_paper_studio.ui.main_window import MainWindow
    from mathmodel_paper_studio.ui.project_wizard import ProjectWizard

    APP = None

    class _Projects:
        def __init__(self, *projects):
            self.projects = tuple(projects)
            self.requested_ids = []

        def list_all(self):
            return self.projects

        def get(self, project_id):
            self.requested_ids.append(project_id)
            return next((project for project in self.projects
                         if project.project_id == project_id), None)

    class _DeferredPool:
        def __init__(self):
            self.workers = []

        def start(self, worker):
            self.workers.append(worker)

    class FluentNavigationTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            global APP
            ROOT.mkdir(parents=True, exist_ok=True)
            APP = QApplication.instance() or QApplication([])

        def test_theme_follows_system_without_storing_a_choice(self):
            apply_fluent_theme(APP)
            self.assertIs(qconfig.themeMode.value, Theme.AUTO)

        def test_four_persistent_destinations_keep_project_and_run(self):
            temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(temporary.cleanup)
            project = SimpleNamespace(project_id='a' * 32, title='示例项目',
                                      relative_path='projects/' + 'a' * 32)
            pool = _DeferredPool()
            window = MainWindow(AppPaths(Path(temporary.name) / 'app'),
                                project_repository=_Projects(project),
                                project_service=object(), workflow_engine=object(),
                                run_repository=object(), thread_pool=pool)
            self.addCleanup(window.close)

            self.assertIsInstance(window, FluentWindow)
            expected = ('项目与数据', 'Word 模板', '运行与审核', '设置')
            self.assertEqual(expected, tuple(window.navigation_destinations))
            self.assertEqual(4, len(window.navigationInterface.panel.items))
            settings_item = window.navigationInterface.panel.items['settingsPage'].widget
            self.assertGreaterEqual(window.navigationInterface.panel.topLayout.indexOf(settings_item), 0)
            self.assertEqual(expected, tuple(item.widget.text() for item in
                                             window.navigationInterface.panel.items.values()))
            pages = (window.project_page, window.template_view,
                     window.run_panel, window.settings_view)
            self.assertEqual(4, len({id(page) for page in pages}))
            self.assertIs(window.project_list.parentWidget(), window.project_page)
            self.assertEqual(project.project_id,
                             window.project_list.item(0).data(Qt.ItemDataRole.UserRole))
            self.assertEqual(project.project_id, window._project.project_id)

            active_run = SimpleNamespace(run_id='run_1')
            window._active_run = active_run
            window.run_panel.set_run_id(active_run.run_id)
            window.run_panel.set_run_state(RunStatus.PAUSED)
            for page in pages:
                window.workspace.setCurrentWidget(page)
                self.assertIs(window.workspace.currentWidget(), page)
                self.assertEqual(project.project_id, window._project.project_id)
                self.assertIs(window._active_run, active_run)
                self.assertEqual('run_1', window.run_panel.run_id)
                self.assertIs(window.run_panel.current_status, RunStatus.PAUSED)
            self.assertEqual(pages, tuple(window.workspace.widget(i) for i in range(4)))

            combo = window.settings_view.provider_combo
            self.assertEqual([Provider.CODEX, Provider.DEEPSEEK],
                             [combo.itemData(i) for i in range(combo.count())])

        def test_project_selection_looks_up_the_stored_project_id(self):
            temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(temporary.cleanup)
            first = SimpleNamespace(project_id='a' * 32, title='Alpha',
                                    relative_path='projects/' + 'a' * 32)
            second = SimpleNamespace(project_id='b' * 32, title='Beta',
                                     relative_path='projects/' + 'b' * 32)
            projects = _Projects(first, second)
            pool = _DeferredPool()
            window = MainWindow(AppPaths(Path(temporary.name) / 'app'),
                                project_repository=projects,
                                project_service=object(), workflow_engine=object(),
                                run_repository=object(), thread_pool=pool)
            self.addCleanup(window.close)

            item = window.project_list.item(1)
            self.assertEqual(second.project_id, item.data(Qt.ItemDataRole.UserRole))
            window.project_list.setCurrentRow(1)

            self.assertEqual(second.project_id, window._project.project_id)
            self.assertEqual(second.project_id, projects.requested_ids[-1])

        def test_project_actions_remain_reachable_in_a_narrow_window(self):
            temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(temporary.cleanup)
            project = SimpleNamespace(project_id='c' * 32, title='窄窗项目',
                                      relative_path='projects/' + 'c' * 32)
            window = MainWindow(
                AppPaths(Path(temporary.name) / 'app'),
                project_repository=_Projects(project), project_service=object(),
                workflow_engine=object(), run_repository=object(),
                thread_pool=_DeferredPool(),
            )
            self.addCleanup(window.close)
            # Isolate the real project page from the wider Settings page hint.
            host = QWidget()
            host_layout = QVBoxLayout(host)
            window.project_page.setParent(host)
            host_layout.addWidget(window.project_page)
            self.addCleanup(host.close)
            host.resize(620, 720)
            host.show()
            window.project_page.show()
            APP.processEvents()

            self.assertLess(host.width(), 1180)
            viewport = window.project_details_scroll.viewport()
            window.project_details_scroll.verticalScrollBar().setValue(
                window.project_details_scroll.verticalScrollBar().maximum()
            )
            APP.processEvents()

            for button in (window.run_button, window.open_word_button):
                self.assertTrue(button.isVisible())
                button_rect = QRect(button.mapTo(viewport, QPoint(0, 0)), button.size())
                self.assertTrue(
                    viewport.rect().contains(button_rect),
                    f'{button.accessibleName()} is outside the visible project area: '
                    f'{button_rect!r} vs {viewport.rect()!r}',
                )

        def test_empty_project_page_opens_the_new_project_wizard(self):
            temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(temporary.cleanup)
            paths = AppPaths(Path(temporary.name) / 'app')
            database = Database(paths)
            database.migrate()
            projects = ProjectRepository(database)
            service = ProjectService(paths, projects)
            window = MainWindow(paths, project_repository=projects,
                                project_service=service, workflow_engine=object(),
                                run_repository=object(), thread_pool=_DeferredPool())
            self.addCleanup(window.close)
            window.show()
            observed = []
            QTimer.singleShot(20, lambda: self._close_active_wizard(observed))

            self.assertEqual(0, window.project_list.count())
            self.assertTrue(window.empty_state_label.isVisible())
            self.assertTrue(window.new_project_button.isEnabled())
            QTest.mouseClick(window.new_project_button, Qt.MouseButton.LeftButton)
            self.assertEqual(1, len(observed))
            self.assertIsInstance(observed[0], ProjectWizard)
            self.assertIsNone(QApplication.activeModalWidget())

        @staticmethod
        def _close_active_wizard(observed):
            dialog = QApplication.activeModalWidget()
            if dialog is None:
                QTimer.singleShot(10, lambda: FluentNavigationTests._close_active_wizard(observed))
                return
            observed.append(dialog)
            dialog.reject()

else:
    class FluentNavigationProcessTests(unittest.TestCase):
        def test_fluent_contracts_in_isolated_qt_process(self):
            ROOT.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ)
            environment.update({
                _CHILD_FLAG: '1',
                'QT_QPA_PLATFORM': 'offscreen',
                'PYTHONPATH': os.pathsep.join((
                    str(Path(__file__).resolve().parents[3]),
                    str(Path(__file__).resolve().parents[3] / 'src'),
                )),
                'TEMP': str(ROOT), 'TMP': str(ROOT), 'TMPDIR': str(ROOT),
                'PYTHONPYCACHEPREFIX': str(ROOT / 'pycache'),
                'PYTHONIOENCODING': 'utf-8',
                'XDG_CACHE_HOME': str(ROOT / 'qt-cache'),
                'PIP_CACHE_DIR': str(ROOT / 'pip-cache'),
            })
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                cwd=Path(__file__).resolve().parents[3],
                env=environment, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('Ran 5 tests', result.stderr)


if __name__ == '__main__':
    unittest.main()
