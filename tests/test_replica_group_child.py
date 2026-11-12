"""The replica child accepts only the configured isolated test subtree."""

import os
from pathlib import Path

import pytest

from tests.helpers import replica_group_child as child


def test_current_pytest_child_accepts_its_real_temporary_directory(tmp_path):
    assert child.admitted_root(str(tmp_path)) == tmp_path


@pytest.mark.parametrize("selection", ["missing_qa", "relative_qa", "qa_itself", "outside_qa", "missing_child"])
def test_current_pytest_child_rejects_unowned_roots(tmp_path, monkeypatch, selection):
    qa = Path(os.environ["RENO_QA_ROOT"])
    root = tmp_path
    if selection == "missing_qa":
        monkeypatch.delenv("RENO_QA_ROOT")
    elif selection == "relative_qa":
        monkeypatch.setenv("RENO_QA_ROOT", ".")
    elif selection == "qa_itself":
        root = qa
    elif selection == "outside_qa":
        root = qa.parent
    else:
        root = tmp_path / "missing"
    with pytest.raises((ValueError, FileNotFoundError)):
        child.admitted_root(str(root))


def test_retained_runner_keeps_its_original_pytest_subtree(tmp_path, monkeypatch):
    repository = tmp_path / "retained-repository"
    qa = repository / "data/t16-h02-replica-group-r748/run-01/isolated"
    root = qa / "pytest/case"
    root.mkdir(parents=True)
    monkeypatch.setattr(child, "REPOSITORY", repository)
    monkeypatch.setenv("RENO_QA_ROOT", str(qa))
    assert child.admitted_root(str(root)) == root
    outside = qa / "outside"
    outside.mkdir()
    with pytest.raises(ValueError):
        child.admitted_root(str(outside))
