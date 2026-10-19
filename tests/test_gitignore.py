from __future__ import annotations

import shutil
import subprocess

from clipbench.config import REPO_ROOT
from clipbench.report.publish import gitignore_covers_data


def test_gitignore_lists_data():
    assert gitignore_covers_data(REPO_ROOT)


def test_git_check_ignore_data():
    git = shutil.which("git")
    if git is None:
        return
    try:
        inside = subprocess.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return  # not a git checkout (or git unusable here): the file check above still applies
    res = subprocess.run(
        [git, "check-ignore", "-q", "data/audio/x.wav"], cwd=REPO_ROOT, capture_output=True, timeout=10
    )
    assert res.returncode == 0, "data/ is not ignored by git"
