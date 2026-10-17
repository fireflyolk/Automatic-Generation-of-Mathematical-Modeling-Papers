"""Stage a complete new project, rename without replacement, then index it."""
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import Project
from mathmodel_paper_studio.inputs.import_service import ImportService
from mathmodel_paper_studio.inputs.profiling import ImportLimits
from mathmodel_paper_studio.projects.layout import create_layout
from mathmodel_paper_studio.storage.repositories import ProjectRepository


class ProjectService:
    def __init__(self, paths: AppPaths, repository: ProjectRepository, *, limits: ImportLimits = ImportLimits()):
        if not isinstance(paths, AppPaths) or not isinstance(repository, ProjectRepository):
            raise TypeError('项目服务需要 AppPaths 和 ProjectRepository。')
        if paths.root != repository.database.paths.root:
            raise ValueError('项目与数据库必须使用同一 AppPaths。')
        self.paths = paths
        self.repository = repository
        self.imports = ImportService(limits)

    def create(self, title: str, problem: str, sources, *, template=None, sheets=None) -> Project:
        self.paths.ensure_directories()
        project = self._project(title)
        destination = validate_managed_path(self.paths.root / project.relative_path)
        if destination.exists():
            raise ValueError('项目目标已存在，请创建新项目。')
        selected = self.imports.validate_sources(sources, template)
        stage = Path(tempfile.mkdtemp(prefix='project-', dir=self.paths.temp))
        finalized = False
        try:
            create_layout(stage)
            self.imports.populate(stage, project.project_id, problem, selected, sheets=sheets)
            self.paths.validate()
            validate_managed_path(stage)
            validate_managed_path(destination)
            if destination.exists():
                raise ValueError('项目目标已存在，请创建新项目。')
            # Windows rename refuses an existing destination, including a race.
            stage.rename(destination)
            finalized = True
            self.repository.add(project)
            return project
        except BaseException:
            owned = destination if finalized else stage
            validate_managed_path(owned)
            if owned.exists():
                shutil.rmtree(owned)
            raise

    @staticmethod
    def _project(title: str) -> Project:
        project_id = uuid4().hex
        return Project(project_id, title, f'projects/{project_id}')
