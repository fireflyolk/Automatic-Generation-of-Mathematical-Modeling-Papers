"""New-project layout. Directory names never come from imported text."""
from pathlib import Path

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path

DIRECTORIES = ('00_inputs', '10_analysis', '20_model', '30_src', '40_results',
               '50_figures', '60_paper', '70_review', '90_runs')


def create_layout(root: Path) -> None:
    root = validate_managed_path(root)
    for name in DIRECTORIES:
        # Names are fixed constants; validating each child walks the same parent
        # chain repeatedly and can stall on Windows when creating a staged tree.
        (root / name).mkdir(exist_ok=False)
