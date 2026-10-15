"""Tests for timewalk: the git layer, the notes and slides readers, and the web application's guards."""

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import timewalk

TOKEN = "test-token"
PORT = 8765
HOST = {"host": f"127.0.0.1:{PORT}"}

STEPS = [
    ("step-00", "Start", "Only a README.", {"README.md": "# sample\n", ".gitignore": "runs/\n"}),
    ("step-01", "A file", "greet.py arrives.", {"src/greet.py": "def greet(name):\n    return 'Hello, ' + name\n", "justfile": "run:\n    echo run\n"}),
    ("step-02", "Its tests", "", {"tests/test_greet.py": "def test_it():\n    assert True\n", "src/greet.py": "def greet(name: str) -> str:\n    return 'Hello, ' + name\n"}),
    ("step-03", "Tidy", "A file goes away.", {"justfile": None}),
]


def run_git(
    cwd: Path,  # Repository
    *args: str,  # Arguments to git
) -> str:  # What git printed
    "Run git in a test repository."
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def sample(
    tmp_path: Path,  # pytest's temporary directory
) -> Path:  # A repository with four tagged steps
    "Build a small repository whose history is four tagged steps. step-02 has a lightweight tag, the others annotated."
    repo = tmp_path / "sample"
    repo.mkdir()
    run_git(repo, "init", "--quiet", "--initial-branch", "main")
    run_git(repo, "config", "user.name", "test")
    run_git(repo, "config", "user.email", "test@example.invalid")
    for tag, subject, note, files in STEPS:
        for path, text in files.items():
            target = repo / path
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        run_git(repo, "add", "--all")
        run_git(repo, "commit", "--quiet", "--message", subject)
        if note:
            run_git(repo, "tag", "--annotate", tag, "--message", note)
        else:
            run_git(repo, "tag", tag)
    return repo


@pytest.fixture
def repo(
    sample: Path,  # The sample repository
) -> timewalk.Repo:  # It, opened for stepping in a second working copy
    "Open the sample repository the way the command line does by default."
    return timewalk.Repo(sample)


# ---------- steps ----------


def test_steps_come_from_tags_in_order(repo: timewalk.Repo) -> None:
    "Steps are the matching tags in name order, with the commit subject and the tag's message."
    assert [s.name for s in repo.steps] == ["step-00", "step-01", "step-02", "step-03"]
    assert [s.subject for s in repo.steps] == ["Start", "A file", "Its tests", "Tidy"]
    assert repo.steps[1].note == "greet.py arrives."
    assert repo.steps[2].note == "", "a lightweight tag has no note"


def test_an_annotated_tag_resolves_to_its_commit(repo: timewalk.Repo, sample: Path) -> None:
    "The step's sha is the commit, not the tag object, or `current` would never match."
    assert repo.steps[1].sha == run_git(sample, "rev-parse", "step-01^{commit}")


def test_other_tag_patterns_and_no_match(sample: Path) -> None:
    "A different glob selects different tags, and no match is an error that says what to do."
    run_git(sample, "tag", "v1", "step-01")
    assert [s.name for s in timewalk.Repo(sample, tags="v*", in_place=True).steps] == ["v1"]
    with pytest.raises(timewalk.GitError, match="--commits"):
        timewalk.Repo(sample, tags="nothing-*", in_place=True)


def test_commits_mode_walks_the_branch(sample: Path) -> None:
    "With commits=True every commit is a step, oldest first."
    walked = timewalk.Repo(sample, commits=True, in_place=True)
    assert [s.subject for s in walked.steps] == ["Start", "A file", "Its tests", "Tidy"]


# ---------- the working copy ----------


def test_stepping_happens_in_a_second_working_copy(repo: timewalk.Repo, sample: Path) -> None:
    "By default a `<repo>-replay` copy is made at the first step and the repository itself is not moved."
    assert repo.work == sample.parent / "sample-replay"
    assert repo.current() == 0
    repo.move(2)
    assert repo.current() == 2
    assert run_git(sample, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (sample / "tests" / "test_greet.py").exists(), "the main repository still has every file"
    assert not (sample / "justfile").exists()


def test_the_replay_copy_is_reused(repo: timewalk.Repo, sample: Path) -> None:
    "Opening the repository again finds the same working copy, where it was left."
    repo.move(1)
    again = timewalk.Repo(sample)
    assert again.work == repo.work
    assert again.current() == 1


def test_a_foreign_folder_in_the_way_is_refused(sample: Path) -> None:
    "A folder named like the replay copy that is not one is never taken over."
    (sample.parent / "sample-replay").mkdir()
    with pytest.raises(timewalk.GitError, match="not a working copy"):
        timewalk.Repo(sample)


def test_the_replay_copy_can_go_where_asked(sample: Path, tmp_path: Path) -> None:
    "--replay puts the replay copy in a folder you name, made if missing, and reuses it the next time."
    target = tmp_path / "kit" / "worktree"
    repo = timewalk.Repo(sample, replay=target)
    assert repo.work == target and repo.current() == 0
    assert timewalk.Repo(sample, replay=target).work == target
    with pytest.raises(timewalk.GitError, match="inside the repository"):
        timewalk.Repo(sample, replay=sample / "replay")
    with pytest.raises(timewalk.GitError, match="cannot be used with --in-place"):
        timewalk.Repo(sample, replay=target, in_place=True)


def test_in_place_moves_the_repository_itself(sample: Path) -> None:
    "With in_place=True there is no second copy."
    walked = timewalk.Repo(sample, in_place=True)
    assert walked.work == walked.main
    walked.move(0)
    assert not (sample / "src").exists()


def test_move_changes_the_files(repo: timewalk.Repo) -> None:
    "Moving forward and back puts each step's files in place."
    assert not (repo.work / "src" / "greet.py").exists()
    repo.move(1)
    assert "def greet(name):" in (repo.work / "src" / "greet.py").read_text()
    repo.move(2)
    assert "name: str" in (repo.work / "src" / "greet.py").read_text()
    repo.move(0)
    assert not (repo.work / "src").exists()


def test_move_rejects_a_step_that_does_not_exist(repo: timewalk.Repo) -> None:
    "Indices outside the list are refused."
    for index in (-1, 4):
        with pytest.raises(timewalk.GitError, match="no step"):
            repo.move(index)


def test_between_steps_is_reported_as_none(repo: timewalk.Repo, sample: Path) -> None:
    "A working copy on a commit that is not a step says so: here, a commit made in the replay copy."
    run_git(repo.work, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "--message", "after the steps")
    assert repo.current() is None
    assert repo.tree()["files"], "the tree is still listed"


# ---------- what a step changed ----------


def test_changes_per_step(repo: timewalk.Repo) -> None:
    "The first step adds everything; later steps report added, modified and deleted files."
    assert repo.changes(0) == {"README.md": "A", ".gitignore": "A"}
    assert repo.changes(1) == {"src/greet.py": "A", "justfile": "A"}
    assert repo.changes(2) == {"tests/test_greet.py": "A", "src/greet.py": "M"}
    assert repo.changes(3) == {"justfile": "D"}


def test_tree_marks_changed_files(repo: timewalk.Repo) -> None:
    "The tree lists tracked files with what this step did to each, and names files the step deleted."
    repo.move(2)
    status = {f["path"]: f["status"] for f in repo.tree()["files"]}
    assert status == {".gitignore": "", "README.md": "", "justfile": "", "src/greet.py": "M", "tests/test_greet.py": "A"}
    repo.move(3)
    assert repo.tree()["deleted"] == ["justfile"]


def test_diff_is_against_the_previous_step(repo: timewalk.Repo) -> None:
    "A file's diff shows what this step did, and the first step has none."
    assert repo.diff("README.md") == ""
    repo.move(2)
    diff = repo.diff("src/greet.py")
    assert "-def greet(name):" in diff and "+def greet(name: str) -> str:" in diff


# ---------- reading files ----------


def test_read_returns_text(repo: timewalk.Repo) -> None:
    "A text file is returned as text."
    assert repo.read("README.md") == {"path": "README.md", "text": "# sample\n"}


@pytest.mark.parametrize("path", ["../sample/README.md", "/etc/passwd", "../../etc/passwd", "no/such/file", "src"])
def test_read_refuses_paths_outside_or_missing(repo: timewalk.Repo, path: str) -> None:
    "Paths that leave the working copy, do not exist, or are folders are reported missing and never read."
    assert repo.read(path) == {"path": path, "missing": True}


def test_read_refuses_a_link_that_leaves_the_working_copy(repo: timewalk.Repo, tmp_path: Path) -> None:
    "A symbolic link pointing outside is not followed."
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (repo.work / "link").symlink_to(secret)
    assert repo.read("link") == {"path": "link", "missing": True}


def test_read_skips_binary_and_large_files(repo: timewalk.Repo) -> None:
    "Binary and very large files are named, not shown."
    (repo.work / "blob.bin").write_bytes(b"\x00\x01\x02")
    (repo.work / "big.txt").write_text("x" * (timewalk.MAX_FILE_BYTES + 1))
    assert repo.read("blob.bin")["skipped"] == "a binary file"
    assert "too large" in repo.read("big.txt")["skipped"]


# ---------- edits and run outputs are never lost ----------


def test_edits_block_a_move_in_place(sample: Path) -> None:
    "In place, uncommitted edits to tracked files stop a move, and the working copy stays where it was."
    repo = timewalk.Repo(sample, in_place=True)
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    assert repo.edits() == ["src/greet.py"]
    with pytest.raises(timewalk.GitError, match="uncommitted edits"):
        repo.move(2)
    assert repo.current() == 1
    assert (repo.work / "src" / "greet.py").read_text() == "edited\n"


def test_edits_are_set_aside_in_place_not_discarded(sample: Path) -> None:
    "In place, asked to, a move stashes the edits under a name that says where they were made."
    repo = timewalk.Repo(sample, in_place=True)
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    repo.move(2, set_aside=True)
    assert repo.current() == 2
    assert repo.edits() == []
    assert "timewalk: edits made at step-01" in run_git(repo.work, "stash", "list")
    assert "edited" in run_git(repo.work, "stash", "show", "--patch", "stash@{0}")


def test_a_move_keeps_edits_and_a_file_in_the_way_on_a_saved_branch(repo: timewalk.Repo) -> None:
    "A replay clone never asks: the edits and a new file the step has go on timewalk/saved/<place>, then the move goes on."
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    (repo.work / "tests").mkdir()
    (repo.work / "tests" / "test_greet.py").write_text("mine\n")  # step-02 has this file
    (repo.work / "scratch.txt").write_text("keep\n")  # no step has this one
    repo.move(2)
    assert repo.current() == 2 and repo.edits() == []
    assert (repo.work / "tests" / "test_greet.py").read_text() == "def test_it():\n    assert True\n"
    assert (repo.work / "scratch.txt").read_text() == "keep\n", "an untracked file that nothing replaces stays"
    assert repo.kept == ["timewalk/saved/step-01"]
    assert run_git(repo.work, "show", "timewalk/saved/step-01:src/greet.py") == "edited"
    assert run_git(repo.work, "show", "timewalk/saved/step-01:tests/test_greet.py") == "mine"
    assert "scratch.txt" not in run_git(repo.work, "ls-tree", "-r", "--name-only", "timewalk/saved/step-01"), "a file nothing replaces stays on disk"
    assert run_git(repo.work, "rev-parse", "timewalk/saved/step-01^") == repo.steps[1].sha, "on top of the step it was made at"
    assert "your edits, kept by timewalk before the move to step-02" in run_git(repo.work, "log", "-1", "--format=%s", "timewalk/saved/step-01")
    assert run_git(repo.work, "stash", "list") == ""


def two_steps(
    tmp_path: Path,  # pytest's temporary directory
    before: dict[str, str],  # The files of step-00, by path
    after: dict[str, str],  # The files of step-01, by path; a path missing here is deleted
) -> timewalk.Repo:  # The repository, opened in a replay clone at step-00
    "Build a repository of two tagged steps with the given files, for the cases of a file in the way."
    main = tmp_path / "two"
    main.mkdir()
    run_git(main, "init", "--quiet", "--initial-branch", "main")
    run_git(main, "config", "user.name", "test")
    run_git(main, "config", "user.email", "test@example.invalid")
    for tag, files in (("step-00", before), ("step-01", after)):
        for old in run_git(main, "ls-files").splitlines():
            if old not in files:
                run_git(main, "rm", "--quiet", old)
        for path, text in files.items():
            (main / path).parent.mkdir(parents=True, exist_ok=True)
            (main / path).write_text(text)
        run_git(main, "add", "--all")
        run_git(main, "add", "--force", "--", *files)   # a step may track a file that .gitignore names
        run_git(main, "commit", "--quiet", "-m", f"{tag}: files")
        run_git(main, "tag", "-a", tag, "-m", tag)
    repo = timewalk.Repo(main)
    repo.move(0)
    return repo


def kept_file(repo: timewalk.Repo, path: str) -> str:
    "The text of a file on the branch that the last move kept the work on."
    return run_git(repo.work, "show", f"{repo.kept[0]}:{path}")


def test_a_folder_where_the_step_has_a_file_is_kept(tmp_path: Path) -> None:
    "The learner's untracked tests/x.py, where step-01 has a file named tests: kept on the branch, then replaced."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "tests": "a file\n"})
    (repo.work / "tests").mkdir()
    (repo.work / "tests" / "x.py").write_text("mine\n")
    repo.move(1)
    assert (repo.work / "tests").read_text() == "a file\n" and kept_file(repo, "tests/x.py") == "mine"


def test_a_file_where_the_step_has_a_folder_is_kept(tmp_path: Path) -> None:
    "The learner's untracked file data, where step-01 has data/x.csv: kept, then replaced by the folder."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "data/x.csv": "1,2\n"})
    (repo.work / "data").write_text("mine\n")
    repo.move(1)
    assert (repo.work / "data" / "x.csv").read_text() == "1,2\n" and kept_file(repo, "data") == "mine"


def test_an_ignored_folder_where_the_step_has_a_file_is_kept(tmp_path: Path) -> None:
    "An ignored runs/ folder, where step-01 has a file runs: git would delete it without a word; it is kept first."
    repo = two_steps(tmp_path, {".gitignore": "runs\n"}, {".gitignore": "runs\n", "runs": "a file\n"})
    (repo.work / "runs").mkdir()
    (repo.work / "runs" / "out.bin").write_text("weights\n")
    repo.move(1)
    assert kept_file(repo, "runs/out.bin") == "weights"


def test_a_name_that_differs_only_in_case_is_kept_where_git_ignores_case(tmp_path: Path) -> None:
    "On a Mac, README.MD and readme.md are one file: the learner's untracked README.MD is kept before step-01's readme.md lands."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "readme.md": "the step's\n"})
    run_git(repo.work, "config", "core.ignorecase", "true")
    (repo.work / "README.MD").write_text("mine\n")
    repo.move(1)
    assert repo.kept and "mine" in kept_file(repo, "README.MD")


def test_a_repository_of_its_own_in_the_way_stops_the_move(tmp_path: Path) -> None:
    "A git repository the learner made inside the copy cannot be kept on a branch: where the step has files, the move refuses."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "lib/x.py": "the step's\n"})
    (repo.work / "lib").mkdir()
    run_git(repo.work / "lib", "init", "--quiet")
    (repo.work / "lib" / "x.py").write_text("mine\n")
    with pytest.raises(timewalk.GitError, match="lib is a git repository of its own"):
        repo.move(1)
    assert (repo.work / "lib" / "x.py").read_text() == "mine\n" and repo.current() == 0


def test_what_was_staged_is_kept_under_the_edits(tmp_path: Path) -> None:
    "A file staged, then edited again: the staged text is one commit, the edited text another on top of it."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "b\n"})
    (repo.work / "a.txt").write_text("staged\n")
    run_git(repo.work, "add", "a.txt")
    (repo.work / "a.txt").write_text("edited\n")
    repo.move(1)
    assert kept_file(repo, "a.txt") == "edited"
    assert run_git(repo.work, "show", f"{repo.kept[0]}~1:a.txt") == "staged"


def test_a_name_with_brackets_is_a_name_not_a_pattern(tmp_path: Path) -> None:
    "An ignored file x[1].out in the way is kept; the unrelated ignored x1.out is not, though x[1] would match it as a pattern."
    repo = two_steps(tmp_path, {".gitignore": "*.out\n"}, {".gitignore": "*.out\n", "x[1].out": "the step's\n"})
    (repo.work / "x[1].out").write_text("mine\n")
    (repo.work / "x1.out").write_text("other\n")
    repo.move(1)
    names = run_git(repo.work, "ls-tree", "-r", "--name-only", repo.kept[0]).splitlines()
    assert "x[1].out" in names and "x1.out" not in names


def test_an_unreadable_file_that_nothing_replaces_does_not_stop_a_move(tmp_path: Path) -> None:
    "A file no one can read, which no step has, is not kept and does not stop a move with edits."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "b\n"})
    secret = repo.work / "secret.txt"
    secret.write_text("x\n")
    secret.chmod(0)
    (repo.work / "a.txt").write_text("edited\n")
    try:
        repo.move(1)
        assert kept_file(repo, "a.txt") == "edited"
    finally:
        secret.chmod(0o644)


def test_a_rebase_left_half_done_is_ended_once_the_work_is_kept(tmp_path: Path) -> None:
    "A learner's rebase stopped on a conflict: the move keeps the work, and git no longer thinks the rebase goes on."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "b\n"})
    run_git(repo.work, "-c", "user.name=t", "-c", "user.email=t@e", "switch", "--quiet", "-c", "side")
    (repo.work / "a.txt").write_text("side\n")
    run_git(repo.work, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "-am", "side")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "rebase", "--quiet", "step-01"], cwd=repo.work, capture_output=True)
    git_dir = Path(run_git(repo.work, "rev-parse", "--absolute-git-dir"))
    assert (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
    repo.move(1)
    assert not (git_dir / "rebase-merge").exists() and not (git_dir / "rebase-apply").exists()
    assert repo.current() == 1 and repo.edits() == []


def test_a_file_untracked_by_the_learner_is_kept_when_the_move_goes_back(tmp_path: Path) -> None:
    "git rm --cached notes.txt, an edit, then back to a step without it: reset --hard would have deleted it; it is kept."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "notes.txt": "the step's\n"})
    repo.move(1)
    run_git(repo.work, "rm", "--cached", "--quiet", "notes.txt")
    (repo.work / "notes.txt").write_text("mine\n")
    repo.move(0)
    assert repo.current() == 0 and kept_file(repo, "notes.txt") == "mine"


def test_a_folder_of_the_step_left_replaced_by_a_file_is_kept(tmp_path: Path) -> None:
    "The step left has a folder data/; the learner deleted it and made a file data. The file is kept, whatever the step entered."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "data/x.csv": "1\n"})
    repo.move(1)
    run_git(repo.work, "rm", "-r", "--quiet", "data")
    (repo.work / "data").write_text("mine\n")
    repo.move(0)
    assert repo.current() == 0 and kept_file(repo, "data") == "mine"


def test_an_edit_hidden_by_assume_unchanged_is_kept(tmp_path: Path) -> None:
    "An edit to a file marked assume-unchanged, which git status does not show, is kept all the same."
    repo = two_steps(tmp_path, {"a.txt": "a\n", "b.txt": "b\n"}, {"a.txt": "a\n", "b.txt": "two\n"})
    run_git(repo.work, "update-index", "--assume-unchanged", "a.txt")
    (repo.work / "a.txt").write_text("hidden edit\n")
    repo.move(1)
    assert repo.current() == 1 and kept_file(repo, "a.txt") == "hidden edit"


def test_staged_work_in_a_merge_stopped_on_a_conflict_is_kept(tmp_path: Path) -> None:
    "In a merge stopped on a conflict, a file staged and then edited again: both its texts are kept, and the conflicted file too."
    repo = two_steps(tmp_path, {"a.txt": "a\n", "b.txt": "b\n"}, {"a.txt": "theirs\n", "b.txt": "b\n"})
    who = ["-c", "user.name=t", "-c", "user.email=t@e"]
    run_git(repo.work, *who, "switch", "--quiet", "-c", "side")
    (repo.work / "a.txt").write_text("ours\n")
    run_git(repo.work, *who, "commit", "--quiet", "-am", "ours")
    subprocess.run(["git", *who, "merge", "--quiet", "step-01"], cwd=repo.work, capture_output=True)
    (repo.work / "b.txt").write_text("staged\n")
    run_git(repo.work, "add", "b.txt")
    (repo.work / "b.txt").write_text("edited\n")
    repo.move(1)
    assert kept_file(repo, "b.txt") == "edited" and "<<<<<<<" in kept_file(repo, "a.txt")
    assert run_git(repo.work, "show", f"{repo.kept[0]}~1:b.txt") == "staged"


def test_a_file_the_check_misses_stops_the_move_and_the_work_comes_back(tmp_path: Path, monkeypatch) -> None:
    "The checkout is never forced: if the check of what is in the way missed a file, git refuses, the file stays, the edits come back."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "b\n", "new.txt": "the step's\n"})
    (repo.work / "new.txt").write_text("mine\n")
    (repo.work / "a.txt").write_text("edited\n")
    monkeypatch.setattr(repo, "in_the_way", lambda *commits: ([], []))   # as if the check had missed new.txt
    with pytest.raises(timewalk.GitError, match="The move stopped; your work is kept on timewalk/saved/step-00, and is back in your files"):
        repo.move(1)
    assert (repo.work / "new.txt").read_text() == "mine\n" and (repo.work / "a.txt").read_text() == "edited\n"


def test_a_locked_index_stops_the_move_before_anything_is_saved(tmp_path: Path) -> None:
    "An index.lock that stays: the move refuses at once, saves nothing, and touches nothing."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "b\n"})
    (repo.work / "a.txt").write_text("edited\n")
    lock = Path(run_git(repo.work, "rev-parse", "--absolute-git-dir")) / "index.lock"
    lock.write_text("")
    with pytest.raises(timewalk.GitError, match="another git is using the replay copy"):
        repo.move(1)
    lock.unlink()
    assert run_git(repo.work, "branch", "--list", "timewalk/saved/*") == "" and (repo.work / "a.txt").read_text() == "edited\n"


def test_an_edit_hidden_by_skip_worktree_is_kept(tmp_path: Path) -> None:
    "An edit to a file marked skip-worktree is kept, and the move goes on instead of failing on it."
    repo = two_steps(tmp_path, {"a.txt": "a\n", "b.txt": "b\n"}, {"a.txt": "a\n", "b.txt": "two\n"})
    run_git(repo.work, "update-index", "--skip-worktree", "a.txt")
    (repo.work / "a.txt").write_text("hidden edit\n")
    repo.move(1)
    assert repo.current() == 1 and kept_file(repo, "a.txt") == "hidden edit"


def test_a_repository_made_where_a_tracked_file_was_stops_the_move(tmp_path: Path) -> None:
    "The learner deletes the tracked file tool and makes a git repository there: git lists nothing in it, yet the move refuses."
    repo = two_steps(tmp_path, {"a.txt": "a\n", "tool": "a file\n"}, {"a.txt": "b\n", "tool": "a file\n"})
    (repo.work / "tool").unlink()
    (repo.work / "tool").mkdir()
    run_git(repo.work / "tool", "init", "--quiet")
    (repo.work / "tool" / "work.py").write_text("mine\n")
    with pytest.raises(timewalk.GitError, match="tool is a git repository of its own"):
        repo.move(1)
    assert (repo.work / "tool" / "work.py").read_text() == "mine\n"


def test_in_place_a_folder_where_the_step_left_has_a_file_stops_the_move(sample: Path) -> None:
    "In place, a stash would put the step's file back and delete the learner's folder in its place: the move refuses."
    repo = timewalk.Repo(sample, in_place=True)
    repo.move(1)
    (repo.work / "justfile").unlink()
    (repo.work / "justfile").mkdir()
    (repo.work / "justfile" / "mine.txt").write_text("mine\n")
    with pytest.raises(timewalk.GitError, match="will not be overwritten: justfile"):
        repo.move(0, set_aside=True)
    assert (repo.work / "justfile" / "mine.txt").read_text() == "mine\n"


def test_a_file_that_cannot_be_removed_stops_the_move_with_a_message(tmp_path: Path) -> None:
    "An ignored folder in the way with a read-only folder inside: the move stops with a message that names the kept branch."
    repo = two_steps(tmp_path, {".gitignore": "out\n"}, {".gitignore": "out\n", "out": "a file\n"})
    (repo.work / "out" / "locked").mkdir(parents=True)
    (repo.work / "out" / "locked" / "z").write_text("z\n")
    (repo.work / "out" / "locked").chmod(0o555)
    try:
        with pytest.raises(timewalk.GitError, match="The move stopped; your work is kept on timewalk/saved/step-00"):
            repo.move(1)
        assert (repo.work / "out" / "locked" / "z").read_text() == "z\n"
    finally:
        (repo.work / "out" / "locked").chmod(0o755)


def test_a_path_with_a_space_and_an_accent_comes_as_it_is(tmp_path: Path) -> None:
    "The file tree and a step's changes name a file with a space and an accent as it is, not quoted by git."
    repo = two_steps(tmp_path, {"a.txt": "a\n"}, {"a.txt": "a\n", "notes/thé café.md": "hi\n"})
    repo.move(1)
    assert repo.changes(1) == {"notes/thé café.md": "A"}
    assert "notes/thé café.md" in [file["path"] for file in repo.tree()["files"]]


def test_a_move_with_nothing_to_keep_makes_no_branch(repo: timewalk.Repo) -> None:
    "No edits, and no file in the way: nothing is saved, even with an untracked file that stays."
    repo.move(1)
    (repo.work / "scratch.txt").write_text("keep\n")
    repo.move(2)
    assert repo.kept == [] and run_git(repo.work, "branch", "--list", "timewalk/saved/*") == ""


def test_an_ignored_file_is_saved_only_when_the_move_replaces_it(repo: timewalk.Repo) -> None:
    "An ignored file in the way of the step's file is kept on the branch; other ignored files are neither saved nor touched."
    repo.move(1)
    exclude = Path(run_git(repo.work, "rev-parse", "--git-path", "info/exclude"))
    exclude = exclude if exclude.is_absolute() else repo.work / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("tests/\n*.db\n")
    (repo.work / "tests").mkdir()
    (repo.work / "tests" / "test_greet.py").write_text("mine, ignored\n")  # step-02 has this file
    (repo.work / "runs.db").write_text("a store")
    repo.move(2)
    assert run_git(repo.work, "show", "timewalk/saved/step-01:tests/test_greet.py") == "mine, ignored"
    assert "runs.db" not in run_git(repo.work, "ls-tree", "-r", "--name-only", "timewalk/saved/step-01")
    assert (repo.work / "runs.db").read_text() == "a store"


def test_a_learners_commits_and_edits_go_on_one_branch(repo: timewalk.Repo) -> None:
    "A commit the learner made, then an edit: one saved branch holds both, the edit on top."
    repo.move(1)
    (repo.work / "README.md").write_text("committed\n")
    run_git(repo.work, "commit", "-qam", "mine")
    (repo.work / "src" / "greet.py").write_text("edited\n")
    repo.move(3)
    assert repo.kept == ["timewalk/saved/step-01"]
    assert run_git(repo.work, "log", "--format=%s", "-2", "timewalk/saved/step-01").splitlines()[1] == "mine"
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("again\n")
    repo.move(2)
    assert repo.kept == ["timewalk/saved/step-01-2"], "a second save at the same place gets a name of its own"


def test_discard_throws_edits_away_in_a_worktree_of_an_older_version(sample: Path, tmp_path: Path) -> None:
    "Only a replay worktree made by an older version still throws edits away with --discard-edits."
    worktree = tmp_path / "sample-replay"
    run_git(sample, "worktree", "add", "--detach", "--quiet", str(worktree), "step-00")
    repo = timewalk.Repo(sample, discard=True)
    assert repo.branch is None
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    repo.move(2)
    assert repo.edits() == [] and repo.kept == []
    assert run_git(repo.work, "stash", "list") == ""


def test_discarding_is_refused_in_place(sample: Path) -> None:
    "Discarding edits is only for a replay copy: in place it would throw away work in the real repository."
    with pytest.raises(timewalk.GitError, match="cannot be used with --in-place"):
        timewalk.Repo(sample, in_place=True, discard=True)


def test_untracked_files_survive_every_move(repo: timewalk.Repo) -> None:
    "What a command wrote (a database, a run folder, an environment) is left alone, ignored or not."
    (repo.work / "runs").mkdir()
    (repo.work / "runs" / "checkpoint.bin").write_bytes(b"weights")
    (repo.work / "mlflow.db").write_text("a tracking store")
    for index in (3, 1, 0, 2):
        repo.move(index)
        assert (repo.work / "runs" / "checkpoint.bin").read_bytes() == b"weights"
        assert (repo.work / "mlflow.db").read_text() == "a tracking store"
    assert repo.edits() == [], "untracked files are not edits"


def test_edits_are_shown_against_the_step(repo: timewalk.Repo) -> None:
    "What a command changed in a tracked file is shown as a diff against the step's commit; other files show none."
    repo.move(2)
    (repo.work / "src" / "greet.py").write_text("def greet(name: str) -> str:\n    return f'Hello, {name}'\n")
    edits = repo.edit_diff("src/greet.py")
    assert "-    return 'Hello, ' + name" in edits and "+    return f'Hello, {name}'" in edits
    assert repo.edit_diff("README.md") == ""
    assert repo.edit_diff("../sample/README.md") == ""
    assert "return 'Hello, ' + name" in repo.diff("src/greet.py"), "the step's own changes are still against the step before"


def test_edit_marks_notice_a_second_edit(repo: timewalk.Repo) -> None:
    "Editing an already edited file changes the fingerprint, so the pages hear of it."
    repo.move(1)
    assert repo.edit_marks() == ()
    target = repo.work / "src" / "greet.py"
    target.write_text("one\n")
    first = repo.edit_marks()
    target.write_text("two, longer\n")
    assert first and repo.edit_marks() != first


def test_the_watcher_announces_edits(repo: timewalk.Repo) -> None:
    "The watcher tells the pages when the working copy's edits change, and stays quiet otherwise."
    import asyncio

    class Listener(timewalk.Hub):
        def __init__(self) -> None:
            super().__init__()
            self.heard: list[dict] = []

        async def tell(self, event: dict) -> None:
            self.heard.append(event)

    async def scenario() -> list[dict]:
        hub = Listener()
        watcher = asyncio.create_task(timewalk.watch_edits(repo, hub, every=0.05))
        await asyncio.sleep(0.2)
        assert hub.heard == [], "nothing changed yet"
        (repo.work / "README.md").write_text("edited\n")
        for _ in range(60):
            if hub.heard:
                break
            await asyncio.sleep(0.05)
        watcher.cancel()
        return hub.heard

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=10)) == [{"type": "edits"}]


def test_an_untracked_file_is_never_overwritten_in_place(sample: Path) -> None:
    "In place, if a later step has a file where an untracked one sits, the move is refused and the file kept."
    repo = timewalk.Repo(sample, in_place=True)
    repo.move(0)
    (repo.work / "justfile").write_text("mine\n")
    with pytest.raises(timewalk.GitError, match="will not be overwritten: justfile"):
        repo.move(1)
    assert repo.current() == 0
    assert (repo.work / "justfile").read_text() == "mine\n"



def test_a_move_refused_for_an_untracked_file_leaves_the_edits_in_place(sample: Path) -> None:
    "In place, the untracked file in the way is found before the edits are stashed, so a refused move does not hide the edits."
    repo = timewalk.Repo(sample, in_place=True)
    (repo.work / "justfile").write_text("mine\n")
    (repo.work / "README.md").write_text("an edit\n")
    with pytest.raises(timewalk.GitError, match="will not be overwritten"):
        repo.move(1, set_aside=True)
    assert (repo.work / "README.md").read_text() == "an edit\n" and run_git(repo.work, "stash", "list") == ""


def test_saving_a_section_skips_headings_inside_fenced_code() -> None:
    "A line that starts with ## inside fenced code is code: it neither ends a section nor starts one."
    text = "## step-01 One\n\n```\n## step-02 in code\n```\n\nafter\n\n## step-02 Two\n\nkept\n"
    out = timewalk.replace_section(text, "step-01", "new\n\n```\n## inside\n```")
    assert out == "## step-01 One\nnew\n\n```\n## inside\n```\n\n## step-02 Two\n\nkept\n"
    assert timewalk.parse_notes(out)["step-02"]["text"] == "kept"
    assert timewalk.replace_section(text, "step-02", "two") == "## step-01 One\n\n```\n## step-02 in code\n```\n\nafter\n\n## step-02 Two\ntwo\n"


# ---------- recipes ----------


def test_recipes_follow_the_step(repo: timewalk.Repo) -> None:
    "The recipe list is read from the justfile at the current step, and is empty where there is none."
    if not timewalk.shutil.which("just"):
        pytest.skip("just is not installed")
    assert timewalk.recipes(repo.work) == []
    repo.move(1)
    assert [r["name"] for r in timewalk.recipes(repo.work)] == ["run"]


# ---------- notes and slides ----------

NOTES = """# Notes

Text before the first step is ignored.

## step-00 Where it starts
time: 0:00

Say **hello**.

$ ls -la
runs$ just train modal configs/helpdesk.yaml
runs2$ just train modal configs/ablation.yaml
main$ just mlflow

## step-01
time: 12:30
- one
- two
"""


def test_notes_are_read_per_step() -> None:
    "Each `## step` section gives a title, a planned time in seconds, commands with their track, and prose."
    notes = timewalk.parse_notes(NOTES)
    assert list(notes) == ["step-00", "step-01"]
    first = notes["step-00"]
    assert first["title"] == "Where it starts"
    assert first["time"] == 0
    assert first["commands"] == [
        {"track": "replay", "text": "ls -la"},
        {"track": "runs", "text": "just train modal configs/helpdesk.yaml"},
        {"track": "runs2", "text": "just train modal configs/ablation.yaml"},
        {"track": "main", "text": "just mlflow"},
    ]
    assert first["text"] == "Say **hello**."
    assert notes["step-01"]["time"] == 750
    assert "title" not in notes["step-01"]
    assert notes["step-01"]["text"] == "- one\n- two"


def test_notes_keep_prose_and_commands_in_order() -> None:
    "The parts of a step keep the order of the file, so each command shows where it is written."
    notes = timewalk.parse_notes("## step-00\ntime: 0:00\n\nFirst read this.\n\n$ ls\nruns$ just train\n\nThen this.\n\nmain$ git log\n")
    assert [(p["kind"], p["text"]) for p in notes["step-00"]["parts"]] == [
        ("text", "First read this."), ("command", "ls"), ("command", "just train"), ("text", "Then this."), ("command", "git log")]
    assert [p.get("track") for p in notes["step-00"]["parts"] if p["kind"] == "command"] == ["replay", "runs", "main"]


def test_a_dollar_line_in_a_code_block_is_code_not_a_command() -> None:
    "Inside a fenced code block, a `$ ` line is code to read, and a `## ` line does not start a step."
    notes = timewalk.parse_notes("## step-00\n\n```\n$ not a button\n## not a step\n```\n\n$ ls\n")
    assert list(notes) == ["step-00"]
    assert notes["step-00"]["commands"] == [{"track": "replay", "text": "ls"}]
    assert notes["step-00"]["parts"][0] == {"kind": "text", "text": "```\n$ not a button\n## not a step\n```"}


def test_the_pdf_keeps_a_code_block_of_the_notes_as_written() -> None:
    "The PDF wraps command lines in code blocks, but leaves a code block that the notes already have."
    from timewalk import slides_pdf

    raw = "time: 0:01\nRead.\n\n```\n$ shown as code\n```\n\n$ ls\nMore."
    assert slides_pdf.notes_markdown(raw) == "Read.\n\n```\n$ shown as code\n```\n\n```\n$ ls\n```\nMore."


def test_slides_manifest(tmp_path: Path) -> None:
    "The manifest maps a step to its slides, in order; a missing manifest means no slides."
    manifest = tmp_path / "slides.toml"
    manifest.write_text('[slides]\nstep-00 = ["pic.svg", "deck.pdf#page=2"]\nstep-02 = ["deck.html#/3"]\nbroken = "not a list"\n')
    assert timewalk.load_slides(manifest) == {"step-00": ["pic.svg", "deck.pdf#page=2"], "step-02": ["deck.html#/3"]}
    assert timewalk.load_slides(tmp_path / "absent.toml") == {}
    assert timewalk.load_slides(None) == {}


DECK = """# One

text

---

## Two

```
---
a rule inside code is not a new slide
```

---
## Three
"""


def test_a_markdown_deck_splits_at_rules() -> None:
    "A line that is exactly --- starts a new slide, except inside fenced code; empty slides are dropped."
    slides = timewalk.split_slides(DECK)
    assert [s.splitlines()[0] for s in slides] == ["# One", "## Two", "## Three"]
    assert "a rule inside code" in slides[1]
    assert timewalk.split_slides("# Only\n") == ["# Only"]
    assert timewalk.split_slides("---\n# After a leading rule\n---\n") == ["# After a leading rule"]


def test_manifest_entries_expand_to_single_slides(tmp_path: Path) -> None:
    "A Markdown file stands for all its slides; #n and #a-b pick some; other kinds of slide pass through."
    (tmp_path / "talk.md").write_text(DECK)
    manifest = tmp_path / "slides.toml"
    manifest.write_text(
        '[slides]\n'
        'step-00 = ["talk.md"]\n'
        'step-01 = ["talk.md#2", "pic.svg"]\n'
        'step-02 = ["talk.md#2-3", "deck.pdf#page=4", "missing.md"]\n'
    )
    assert timewalk.load_slides(manifest) == {
        "step-00": ["talk.md#1", "talk.md#2", "talk.md#3"],
        "step-01": ["talk.md#2", "pic.svg"],
        "step-02": ["talk.md#2", "talk.md#3", "deck.pdf#page=4", "missing.md#1"],
    }


def test_bare_numbers_refer_to_the_default_deck(tmp_path: Path) -> None:
    "With `deck` set, a number or a range is a slide of that deck: a Markdown slide, or a page of a PDF."
    manifest = tmp_path / "slides.toml"
    manifest.write_text('deck = "talk.md"\n[slides]\nstep-00 = [1, "2-3"]\nstep-01 = [4, "pic.svg"]\n')
    assert timewalk.load_slides(manifest) == {"step-00": ["talk.md#1", "talk.md#2", "talk.md#3"], "step-01": ["talk.md#4", "pic.svg"]}
    manifest.write_text('deck = "handout.pdf"\n[slides]\nstep-00 = [1, "2-3"]\n')
    assert timewalk.load_slides(manifest) == {"step-00": ["handout.pdf#page=1", "handout.pdf#page=2", "handout.pdf#page=3"]}



def test_a_step_can_show_a_document_instead_of_slides(tmp_path: Path) -> None:
    "A step under [docs] is one whole document, not split at ---, and it replaces any slides that step had."
    manifest = tmp_path / "slides.toml"
    (tmp_path / "talk.md").write_text("# One\n\n---\n\n# Two\n")
    (tmp_path / "guide.md").write_text("# Guide\n\nA paragraph.\n\n---\n\nMore after a rule.\n")
    manifest.write_text('[slides]\nstep-00 = ["talk.md"]\nstep-01 = ["talk.md"]\n[docs]\nstep-01 = "guide.md"\nstep-02 = "guide.md"\n')
    assert timewalk.load_slides(manifest) == {"step-00": ["talk.md#1", "talk.md#2"], "step-01": ["guide.md#doc"], "step-02": ["guide.md#doc"]}


def test_a_document_entry_passes_through_slides(tmp_path: Path) -> None:
    "Written in [slides] as `file.md#doc`, a document stays one entry beside ordinary slides."
    manifest = tmp_path / "slides.toml"
    (tmp_path / "talk.md").write_text("# One\n\n---\n\n# Two\n")
    manifest.write_text('[slides]\nstep-00 = ["talk.md#1", "talk.md#doc"]\n')
    assert timewalk.load_slides(manifest) == {"step-00": ["talk.md#1", "talk.md#doc"]}

# ---------- the web application ----------


@pytest.fixture
def served(
    repo: timewalk.Repo,  # The sample repository
    tmp_path: Path,  # pytest's temporary directory
) -> TestClient:  # A client for the application, with notes and slides
    "Serve the sample repository with a notes file and a two-step slides manifest."
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES)
    deck = tmp_path / "deck"
    deck.mkdir()
    (deck / "slides.toml").write_text('[slides]\nstep-00 = ["one.md"]\nstep-01 = ["two.svg", "three.svg"]\n')
    (deck / "one.md").write_text("# One\n")
    app = timewalk.make_app(repo, TOKEN, PORT, notes_path=notes, assistant="", slides_path=deck / "slides.toml")
    return TestClient(app, headers=HOST)


def test_every_route_needs_the_token(served: TestClient) -> None:
    "Pages, the API, the notes, static files and slides are all refused without the token."
    for path in ("/", "/presenter", "/api/state", "/api/tree", "/api/notes", "/api/file?path=README.md", "/static/app.js", "/slides/one.md"):
        assert served.get(path).status_code == 403, path
        assert served.get(path, params={"t": "wrong"}).status_code == 403, path
    assert served.post("/api/move", json={"to": 1}).status_code == 403
    assert served.post("/api/type", json={"text": "echo no"}).status_code == 403


def test_listening_beyond_this_machine_accepts_any_host_name_but_still_needs_the_token(repo: timewalk.Repo) -> None:
    "With --host, people reach timewalk by an IP or a DNS name, so the host check is off, and the token is the guard."
    app = TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", any_host=True), headers={"host": f"10.0.0.5:{PORT}"})
    assert app.get("/api/state", params={"t": TOKEN}).status_code == 200
    assert app.get("/api/state", params={"t": "wrong"}).status_code == 403


def test_requests_to_another_host_are_refused(served: TestClient) -> None:
    "The right token sent to a different host name is refused, which stops a hostile page that rebinds a name to localhost."
    assert served.get("/api/state", params={"t": TOKEN}, headers={"host": "evil.example:8765"}).status_code == 403
    assert served.get("/api/state", params={"t": TOKEN}).status_code == 200


def test_sockets_need_the_token(served: TestClient) -> None:
    "A terminal or event socket without the token is closed at once with code 4403, which a page can read; no shell starts."
    from starlette.websockets import WebSocketDisconnect

    for path in ("/ws/term/replay", "/ws/events", "/ws/term/replay?t=wrong"):
        with served.websocket_connect(path) as socket, pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
        assert refused.value.code == 4403
    state = served.get("/api/state", params={"t": TOKEN})
    assert state.status_code == 200


def test_a_page_load_lets_its_assets_through(served: TestClient) -> None:
    "Opening a page with the token sets a cookie, and the page's scripts and slides then load without the address."
    assert served.get("/", params={"t": TOKEN}).status_code == 200
    assert served.get("/static/app.js").status_code == 200
    assert served.get("/slides/one.md").text == "# One\n"


def test_state_move_and_slides(served: TestClient) -> None:
    "The state reports the step and its slides; moving resets the slide; the slide index is clamped."
    auth = {"t": TOKEN}
    state = served.get("/api/state", params=auth).json()
    assert state["current"] == 0 and state["slides"] == ["one.md#1"] and state["has_slides"] is True
    assert served.post("/api/move", params=auth, json={"to": 1}).json()["current"] == 1
    assert served.post("/api/slide", params=auth, json={"to": 9}).json() == {"slide": 1, "slides": ["two.svg", "three.svg"]}
    assert served.get("/api/state", params=auth).json()["slide"] == 1
    served.post("/api/move", params=auth, json={"to": 2})
    state = served.get("/api/state", params=auth).json()
    assert state["slides"] == [] and state["slide"] == 0


def test_api_moves_over_edits_and_tells_every_window_where_they_are_kept(served: TestClient, repo: timewalk.Repo) -> None:
    "A move over uncommitted edits does not ask: it keeps them on a saved branch, and the moved event names it."
    auth = {"t": TOKEN}
    served.post("/api/move", params=auth, json={"to": 1})
    (repo.work / "justfile").write_text("edited\n")
    with served.websocket_connect(f"/ws/events?t={TOKEN}") as window:
        assert next_event(window) == {"type": "hello"}
        assert served.post("/api/move", params=auth, json={"to": 2}).json()["current"] == 2
        assert next_event(window) == {"type": "moved", "kept": ["timewalk/saved/step-01"]}
    assert run_git(repo.work, "show", "timewalk/saved/step-01:justfile") == "edited"


def test_scripts_are_revalidated_on_every_load(served: TestClient) -> None:
    "Scripts and slides are sent with no-cache, so a browser never runs a new page against an old cached script."
    response = served.get("/static/common.js", params={"t": TOKEN})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    assert served.get("/slides/one.md", params={"t": TOKEN}).headers["cache-control"] == "no-cache"


def test_file_api_is_read_only_and_stays_inside(served: TestClient) -> None:
    "The file route returns text and the step's diff, reports paths outside as missing, and has no way to write."
    auth = {"t": TOKEN}
    served.post("/api/move", params=auth, json={"to": 2})
    shown = served.get("/api/file", params={**auth, "path": "src/greet.py"}).json()
    assert "name: str" in shown["text"] and "+def greet(name: str) -> str:" in shown["diff"]
    assert served.get("/api/file", params={**auth, "path": "../sample/README.md"}).json() == {"path": "../sample/README.md", "missing": True, "diff": "", "edits": ""}
    for method in (served.post, served.put, served.delete):
        assert method("/api/file", params={**auth, "path": "README.md"}).status_code == 405


def test_file_api_shows_edits(served: TestClient, repo: timewalk.Repo) -> None:
    "The file route returns an edited file's new text and its edits, and the tree names it as edited."
    auth = {"t": TOKEN}
    served.post("/api/move", params=auth, json={"to": 1})
    (repo.work / "src" / "greet.py").write_text("def greet(name):\n    return 'Hi, ' + name\n")
    shown = served.get("/api/file", params={**auth, "path": "src/greet.py"}).json()
    assert "'Hi, '" in shown["text"] and "+    return 'Hi, ' + name" in shown["edits"]
    assert served.get("/api/tree", params=auth).json()["edits"] == ["src/greet.py"]


def test_both_pages_share_what_is_shown(served: TestClient) -> None:
    "Layout, open file, view and terminal tab are kept by the server, so the projector and the presenter page agree."
    auth = {"t": TOKEN}
    state = served.get("/api/state", params=auth).json()
    assert (state["layout"], state["path"], state["track"]) == ("split", None, "replay")
    served.post("/api/show", params=auth, json={"layout": "code"})
    served.post("/api/show", params=auth, json={"path": "README.md", "view": "diff", "track": "main"})
    state = served.get("/api/state", params=auth).json()
    assert (state["layout"], state["path"], state["view"], state["track"]) == ("code", "README.md", "diff", "main")
    served.post("/api/show", params=auth, json={"layout": "slides"})
    served.post("/api/show", params=auth, json={"path": "README.md"})
    assert served.get("/api/state", params=auth).json()["layout"] == "split", "a file asked for is never hidden behind the slides"
    served.post("/api/show", params=auth, json={"layout": "sideways", "track": "nowhere"})
    state = served.get("/api/state", params=auth).json()
    assert (state["layout"], state["track"]) == ("split", "main"), "unknown layouts and tabs are ignored"


def test_the_clock_band_is_off_unless_asked_for(served: TestClient, repo: timewalk.Repo) -> None:
    "The state says whether the presenter page shows the clock band: not by default, and yes when make_app is given --clock."
    auth = {"t": TOKEN}
    assert served.get("/api/state", params=auth).json()["show_clock"] is False
    with_clock = TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", show_clock=True), headers=HOST)
    assert with_clock.get("/api/state", params=auth).json()["show_clock"] is True


def test_the_old_presenter_address_goes_to_the_one_page(served: TestClient) -> None:
    "There is one page now. /presenter sends the browser there, with the token, so old links still work."
    response = served.get("/presenter", params={"t": TOKEN}, follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == f"/?t={TOKEN}"


def test_state_says_if_there_are_notes(served: TestClient, repo: timewalk.Repo) -> None:
    "The page shows the notes column, and the Notes toggle only when timewalk has a notes file."
    assert served.get("/api/state", params={"t": TOKEN}).json()["has_notes"] is True
    bare = TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant=""), headers=HOST)
    assert bare.get("/api/state", params={"t": TOKEN}).json()["has_notes"] is False


def test_a_section_of_the_notes_is_saved_and_the_rest_kept(served: TestClient, tmp_path: Path) -> None:
    "Saving writes one step's section; a stale page is refused, so two writers never overwrite each other."
    auth = {"t": TOKEN}
    notes = tmp_path / "notes.md"
    before = notes.read_text()
    base = served.get("/api/notes", params=auth).json()["notes"]["step-01"]["raw"]
    assert served.post("/api/notes", params=auth, json={"step": "step-01", "base": base, "text": "> Say: hello\n\n$ ls"}).json() == {"ok": True}
    after = timewalk.parse_notes(notes.read_text())
    assert after["step-01"]["raw"] == "> Say: hello\n\n$ ls" and after["step-01"]["commands"] == [{"track": "replay", "text": "ls"}]
    assert after["step-00"] == timewalk.parse_notes(before)["step-00"], "other steps are untouched"
    stale = served.post("/api/notes", params=auth, json={"step": "step-01", "base": base, "text": "mine"})
    assert stale.status_code == 409 and "changed" in stale.json()["error"]
    served.post("/api/notes", params=auth, json={"step": "step-03", "base": "", "text": "New notes."})
    assert timewalk.parse_notes(notes.read_text())["step-03"]["text"] == "New notes.", "a step without a section gets one"
    assert served.post("/api/notes", params=auth, json={"step": "step-09", "base": "", "text": "x"}).status_code != 200, "not a step"


def test_replace_section_keeps_the_layout_of_the_file() -> None:
    "Only the lines of one section change; the headings and the other sections stay as written."
    text = "# Notes\n\n## step-00 Start\ntime: 0:00\n\nOld.\n\n## step-01\n\nKeep me.\n"
    out = timewalk.replace_section(text, "step-00", "time: 0:00\n\nNew.")
    assert out == "# Notes\n\n## step-00 Start\ntime: 0:00\n\nNew.\n\n## step-01\n\nKeep me.\n"


def test_notes_inside_the_repository_are_refused(repo: timewalk.Repo, tmp_path: Path) -> None:
    "Notes belong outside the repository the class walks through, and outside its replay copy."
    assert timewalk.notes_inside(repo, repo.main / "notes.md")
    assert timewalk.notes_inside(repo, repo.work / "docs" / "notes.md")
    assert not timewalk.notes_inside(repo, tmp_path / "class" / "notes.md")


def test_recipes_come_only_from_the_folders_own_justfile(tmp_path: Path) -> None:
    "A justfile in a parent folder is some other project's, so its recipes are not listed."
    if not shutil.which("just"):
        pytest.skip("just is not installed")
    (tmp_path / "justfile").write_text("# The parent's recipe\nparent:\n    echo parent\n")
    child = tmp_path / "child"
    child.mkdir()
    assert timewalk.recipes(child) == []
    (child / "justfile").write_text("# The child's recipe\nmine:\n    echo mine\n")
    assert [r["name"] for r in timewalk.recipes(child)] == ["mine"]


def test_the_pdf_has_each_steps_notes_after_its_slides(tmp_path: Path) -> None:
    "With notes, a step's notes follow its slides, commands in code blocks and the planned time left out."
    from timewalk import slides_pdf

    notes = tmp_path / "notes.md"
    notes.write_text("## step-00 Start\ntime: 0:00\n\n> Say: hello\n\nRead this.\n\n$ ls\nruns$ just train\n\n## step-02 Later\n\nOnly notes.\n")
    manifest = tmp_path / "slides.toml"
    manifest.write_text('[slides]\nstep-00 = ["a.svg"]\nstep-01 = ["b.svg"]\n')
    deck = slides_pdf.deck_of(manifest, notes, "T", with_notes=True)
    assert [(s["name"], s["slides"]) for s in deck["steps"]] == [("step-00", ["a.svg"]), ("step-01", ["b.svg"]), ("step-02", [])]
    assert deck["steps"][0]["notes"] == "> Say: hello\n\nRead this.\n\n```\n$ ls\nruns$ just train\n```"
    assert deck["steps"][1]["notes"] == "" and deck["steps"][2]["notes"] == "Only notes."
    assert all(s["notes"] == "" for s in slides_pdf.deck_of(manifest, notes, "T")["steps"]), "without with_notes, no notes are printed"


# A picture of one pixel, for a brand's logo.
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                      "1f15c4890000000d49444154789c6360f8cf000000030101005d8c0ad80000000049454e44ae426082")


def a_font() -> Path | None:  # A font file of this machine, for a brand that loads its font from its folder
    for folder in ("/System/Library/Fonts/Supplemental", "/System/Library/Fonts", "/usr/share/fonts", "C:/Windows/Fonts"):
        found = sorted(Path(folder).rglob("*.ttf")) if Path(folder).is_dir() else []
        if found:
            return found[0]
    return None


def full_brand(folder: Path) -> Path:  # A brand with every key
    folder.mkdir()
    (folder / "logo.png").write_bytes(PIXEL)
    font = a_font()
    if font:
        shutil.copy(font, folder / "face.ttf")
    (folder / "brand.toml").write_text(
        "[brand]\n" + ('font = "face.ttf"\n' if font else "") +
        'text_color = "#404040"\ntitle_color = "#1F497D"\naccent = "#951026"\nfooter = "A Teacher"\nlogo = "logo.png"\nlogo_on = "every"\n'
        '[cover]\ntitle = "A course"\nbox = "CS 101"\nlines = ["A Teacher", "A school"]\n'
        '[divider]\ncolor = "#951026"\ntitle = true\n')
    return folder


def test_a_brand_is_read_from_its_folder(tmp_path: Path) -> None:
    "A brand's files become addresses under /brand/, a brand may set one key only, and the toml may be named in place of the folder."
    from timewalk import slides_pdf

    brand = slides_pdf.load_brand(full_brand(tmp_path / "full"))
    assert brand["folder"] == tmp_path / "full"
    assert brand["brand"]["logo"] == "/brand/logo.png" and brand["cover"]["lines"] == ["A Teacher", "A school"]
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "brand.toml").write_text('[divider]\ncolor = "#2a6f4e"\n')
    assert slides_pdf.load_brand(tmp_path / "plain" / "brand.toml") == {"folder": tmp_path / "plain", "divider": {"color": "#2a6f4e"}}
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit, match="there is no brand"):
        slides_pdf.load_brand(tmp_path / "empty")


@pytest.mark.parametrize("toml, says", [
    ('[brand]\nlogo = "gone.png"\n', "brand.logo names gone.png, and there is no such file"),
    ('[brand]\naccent = "maroon"\n', "brand.accent = 'maroon' is not a colour"),
    ('[brand]\ncolour = "#fff"\n', "a key colour in [brand] that a brand does not have"),
    ('[footer]\ntext = "x"\n', "a table [footer] that a brand does not have"),
    ('[divider]\ntitle = "yes"\n', "divider.title must be true or false"),
    ('[brand]\nlogo_on = "some"\n', "brand.logo_on = 'some' must be one of 'cover', 'every'"),
    ('[cover]\nlines = "one"\n', "cover.lines must be a list of lines"),
    ('[brand\n', "is not valid TOML"),
])
def test_a_brand_with_a_mistake_says_which_key(tmp_path: Path, toml: str, says: str) -> None:
    "A brand that is wrong stops the PDF with a message that names the key and the file."
    from timewalk import slides_pdf

    (tmp_path / "brand.toml").write_text(toml)
    with pytest.raises(SystemExit, match=re.escape(says)):
        slides_pdf.load_brand(tmp_path)


def test_a_brand_adds_a_cover_and_a_divider_before_each_step_with_slides(tmp_path: Path) -> None:
    "Pages: one per slide, one per divider and the cover. Without a brand the PDF is as before, even after a branded one."
    from timewalk import slides_pdf

    if shutil.which("pdftotext") is None:
        pytest.skip("needs pdftotext")
    (tmp_path / "talk.md").write_text("## One\n\nfirst\n\n---\n\n## Two\n\nsecond\n\n---\n\n## Three\n\nthird\n")
    manifest = tmp_path / "slides.toml"
    manifest.write_text('[slides]\nstep-00 = ["talk.md#1"]\nstep-01 = ["talk.md#2-3"]\n')
    notes = tmp_path / "notes.md"
    notes.write_text("## step-00 Start\n\nSay hi.\n\n## step-01 Then\n\nMore.\n\n## step-02 Notes only\n\nNo slides here.\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "brand.toml").write_text('[divider]\ncolor = "#2a6f4e"\n')

    def made(brand: Path | None) -> tuple[int, str, set[str]]:  # The pages, the text and the page sizes of a PDF
        try:
            data = slides_pdf.make_pdf(manifest, notes, "Deck", brand=brand)
        except SystemExit as error:
            if "no Chrome" in str(error):
                pytest.skip(str(error))
            raise
        out = tmp_path / "out.pdf"
        out.write_bytes(data)
        text = subprocess.run(["pdftotext", "-raw", str(out), "-"], capture_output=True, text=True, check=True).stdout
        pages = slides_pdf.PdfReader(out).pages
        return len(pages), text, {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in pages}

    before = made(None)
    assert before[0] == 3 and "Deck" in before[1] and before[2] == {(960, 540)}
    full = made(full_brand(tmp_path / "full"))
    assert full[0] == 3 + 2 + 1, "three slides, a divider before each of the two steps with slides, and the cover"
    assert "CS 101" in full[1] and "Start" in full[1] and "Notes only" not in full[1] and "A Teacher" in full[1]
    assert made(plain)[0] == 3 + 2, "a brand with only a divider colour adds the dividers alone"
    assert made(None) == before, "no leftover of a brand in the next PDF"




def test_notes_are_served_only_by_their_own_route(served: TestClient) -> None:
    "The presenter's notes come from /api/notes and are not part of the state the audience page reads."
    auth = {"t": TOKEN}
    assert served.get("/api/notes", params=auth).json()["notes"]["step-00"]["title"] == "Where it starts"
    assert "notes" not in served.get("/api/state", params=auth).json()


def test_the_terminal_runs_commands_in_the_working_copy(served: TestClient, repo: timewalk.Repo) -> None:
    "A terminal socket starts a shell in the replay copy; what is typed runs there and its output comes back."
    with served.websocket_connect(f"/ws/term/replay?t={TOKEN}") as socket:
        socket.send_json({"type": "resize", "rows": 24, "cols": 100})
        socket.send_json({"type": "input", "data": "echo marker-$((6*7)) && pwd\r"})
        seen, deadline = b"", time.time() + 20
        while time.time() < deadline and not (b"marker-42" in seen and repo.work.name.encode() in seen):
            seen += socket.receive_bytes()
    assert b"marker-42" in seen
    assert repo.work.name.encode() in seen


def test_shells_do_not_inherit_this_tools_python() -> None:
    "The environment uv run made for timewalk is taken off PATH, so `python` in a class shell is the project's."
    tool = "/cache/uv/environments-v2/timewalk-abc"
    env = timewalk.shell_environment({"PATH": f"{tool}/bin:{sys.prefix}/bin:/opt/homebrew/bin:/usr/bin", "VIRTUAL_ENV": tool,
                                      "UV_RUN_RECURSION_DEPTH": "1", "HOME": "/home/me"})
    assert env["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert "VIRTUAL_ENV" not in env and "UV_RUN_RECURSION_DEPTH" not in env
    assert env["HOME"] == "/home/me" and env["TIMEWALK"] == "1"


def next_event(socket) -> dict:
    "The next event on a window's socket, past the edits watcher's: it tells every window when the edits change, at any time."
    while True:
        message = socket.receive_json()
        if message.get("type") != "edits":
            return message


def test_a_scroll_in_one_window_reaches_the_other_windows(served: TestClient) -> None:
    "A window that scrolls says so on its event socket; the server passes it on to the other windows, not back."
    auth = f"?t={TOKEN}"
    with served.websocket_connect("/ws/events" + auth) as mine, served.websocket_connect("/ws/events" + auth) as theirs:
        assert next_event(mine) == {"type": "hello"}   # on the list of windows: no event can be missed
        assert next_event(theirs) == {"type": "hello"}   # on the list of windows: no event can be missed
        mine.send_json({"type": "scroll", "pane": "notes", "at": 0.4})
        assert next_event(theirs) == {"type": "scroll", "pane": "notes", "at": 0.4}
        mine.send_json({"type": "scroll", "pane": "nowhere", "at": 0.4})   # not a pane: ignored
        mine.send_json({"type": "scroll", "pane": "slide", "at": 7})       # kept between 0 and 1
        assert next_event(theirs) == {"type": "scroll", "pane": "slide", "at": 1.0}


def test_the_shell_toggle_is_shared_by_every_window(served: TestClient) -> None:
    "Shell is kept by the server, like the layout: the state says so, and every window hears of a change."
    auth = {"t": TOKEN}
    assert served.get("/api/state", params=auth).json()["shell"] is False
    with served.websocket_connect(f"/ws/events?t={TOKEN}") as window:
        assert next_event(window) == {"type": "hello"}   # on the list of windows: no event can be missed
        served.post("/api/show", params=auth, json={"shell": True})
        event = next_event(window)
        assert event["type"] == "show" and event["shell"] is True
    assert served.get("/api/state", params=auth).json()["shell"] is True
    served.post("/api/show", params=auth, json={"shell": "yes"})  # not a yes or no: ignored
    assert served.get("/api/state", params=auth).json()["shell"] is True
    served.post("/api/show", params=auth, json={"layout": "code"})
    assert served.get("/api/state", params=auth).json()["shell"] is True, "a layout leaves the Shell toggle as it is"


def test_a_terminal_scroll_reaches_the_other_windows(served: TestClient) -> None:
    "A terminal scrolled back by some lines: the other windows hear the shell and the lines; a bad count is ignored."
    auth = f"?t={TOKEN}"
    with served.websocket_connect("/ws/events" + auth) as mine, served.websocket_connect("/ws/events" + auth) as theirs:
        assert next_event(mine) == {"type": "hello"}   # on the list of windows: no event can be missed
        assert next_event(theirs) == {"type": "hello"}   # on the list of windows: no event can be missed
        mine.send_json({"type": "scroll", "pane": "term", "track": "replay", "lines": "many"})
        mine.send_json({"type": "scroll", "pane": "term", "track": "replay", "lines": -3})
        assert next_event(theirs) == {"type": "scroll", "pane": "term", "track": "replay", "lines": 0}
        mine.send_json({"type": "scroll", "pane": "term", "track": "runs", "lines": 12})
        assert next_event(theirs) == {"type": "scroll", "pane": "term", "track": "runs", "lines": 12}


def test_the_room_window_sizes_a_shell_and_tells_the_others(served: TestClient) -> None:
    "In Shell mode the Room's size stands: another window's resize is answered with it. Off, the Room's size is forgotten."
    auth = {"t": TOKEN}
    with served.websocket_connect(f"/ws/events?t={TOKEN}") as window, served.websocket_connect(f"/ws/term/runs?t={TOKEN}") as shell:
        assert next_event(window) == {"type": "hello"}   # on the list of windows: no event can be missed
        shell.send_json({"type": "resize", "rows": 30, "cols": 100, "room": True})   # not in Shell mode: an ordinary resize
        served.post("/api/show", params=auth, json={"shell": True})
        assert next_event(window)["shell"] is True
        shell.send_json({"type": "resize", "rows": 40, "cols": 160, "room": True})
        assert next_event(window) == {"type": "size", "track": "runs", "rows": 40, "cols": 160}
        shell.send_json({"type": "resize", "rows": 20, "cols": 80})
        assert next_event(window) == {"type": "size", "track": "runs", "rows": 40, "cols": 160}, "told the Room's size again"
        assert served.get("/api/state", params=auth).json()["room_sizes"] == {"runs": [40, 160]}
        served.post("/api/show", params=auth, json={"shell": False})
        assert next_event(window)["shell"] is False
        shell.send_json({"type": "resize", "rows": 20, "cols": 80})
        shell.send_json({"type": "resize", "rows": float("inf"), "cols": 80})   # ignored, and the socket stays
    assert served.get("/api/state", params=auth).json()["room_sizes"] == {}


def test_the_command_line_is_followed_from_what_is_typed() -> None:
    "timewalk knows the command line is empty after Enter, Ctrl-C or Ctrl-U, and is unsure after an arrow key."
    assert timewalk.typed_line(0, "ls") == 2
    assert timewalk.typed_line(2, "\x7f\x7f") == 0
    assert timewalk.typed_line(0, "ls -la\r") == 0
    assert timewalk.typed_line(0, "ech\x03") == 0
    assert timewalk.typed_line(0, "\x1b[A") == -1, "an arrow key may bring back a command"
    assert timewalk.typed_line(-1, "x\x15") == 0


def test_an_idle_shell_draws_its_prompt_again_and_a_busy_one_does_not(tmp_path: Path) -> None:
    "After a move, only a shell that waits at an empty command line gets an Enter."
    import asyncio

    async def scenario() -> list[bool]:
        term = timewalk.Terminal(tmp_path)
        term.start()
        await asyncio.sleep(1.5)
        seen = [term.refresh_prompt()]           # idle, empty line: yes
        term.write("ech")
        seen.append(term.refresh_prompt())       # half a command typed: no
        term.write("\x15")
        await asyncio.sleep(0.3)
        seen.append(term.refresh_prompt())       # line cleared: yes
        term.write("sleep 3\r")
        await asyncio.sleep(1.0)
        seen.append(term.refresh_prompt())       # a program runs in front: no
        term.process.kill()
        return seen

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=20)) == [True, False, True, False]


def test_the_content_watcher_announces_an_edited_slide(tmp_path: Path) -> None:
    "An edit to a slide file named in the manifest, or to the notes, tells the pages to draw them again."
    import asyncio

    (tmp_path / "talk.md").write_text("# One\n")
    manifest = tmp_path / "slides.toml"
    manifest.write_text('[slides]\nstep-00 = ["talk.md"]\n')
    notes = tmp_path / "notes.md"
    notes.write_text("## step-00\n\nHello.\n")
    assert len(timewalk.content_marks(notes, manifest)) == 3

    class Listener(timewalk.Hub):
        def __init__(self) -> None:
            super().__init__()
            self.heard: list[dict] = []

        async def tell(self, event: dict) -> None:
            self.heard.append(event)

    async def scenario() -> list[dict]:
        hub = Listener()
        watcher = asyncio.create_task(timewalk.watch_content(lambda: (notes, manifest, None), hub, every=0.05))
        await asyncio.sleep(0.2)
        assert hub.heard == []
        (tmp_path / "talk.md").write_text("# One, edited at length\n")
        for _ in range(60):
            if hub.heard:
                break
            await asyncio.sleep(0.05)
        watcher.cancel()
        return hub.heard

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=10)) == [{"type": "content"}]


def test_a_step_is_back_where_you_left_it(served: TestClient) -> None:
    "Move away from a step and back: its slide, and the scroll of its slide and notes, come back."
    auth = {"t": TOKEN}
    served.post("/api/move", params=auth, json={"to": 1})
    served.post("/api/slide", params=auth, json={"to": 1})
    served.get("/api/state", params=auth)
    with served.websocket_connect(f"/ws/events?t={TOKEN}") as window, served.websocket_connect(f"/ws/events?t={TOKEN}") as other:
        assert next_event(window) == {"type": "hello"}   # on the list of windows: no event can be missed
        assert next_event(other) == {"type": "hello"}   # on the list of windows: no event can be missed
        window.send_json({"type": "scroll", "pane": "notes", "at": 0.6})
        assert next_event(other)["at"] == 0.6
    served.post("/api/move", params=auth, json={"to": 2})
    later = served.get("/api/state", params=auth).json()
    assert (later["slide"], later["restore"]) == (0, {}), "a step not visited yet starts at the top"
    served.post("/api/move", params=auth, json={"to": 1})
    back = served.get("/api/state", params=auth).json()
    assert back["slide"] == 1 and back["restore"]["notes"] == 0.6


def test_unknown_terminal_names_are_refused(served: TestClient) -> None:
    "Only the known tab names get a shell."
    assert served.post("/api/type", params={"t": TOKEN}, json={"track": "../../bin", "text": "echo no"}).status_code == 409


def test_a_second_runs_tab_has_a_shell_of_its_own(served: TestClient, repo: timewalk.Repo) -> None:
    "`runs2` is a further shell in the replay copy, so two long commands can go at once."
    with served.websocket_connect(f"/ws/term/runs2?t={TOKEN}") as socket:
        socket.send_json({"type": "resize", "rows": 24, "cols": 100})
        socket.send_json({"type": "input", "data": "echo second-$((6*7)) && pwd\r"})
        seen, deadline = b"", time.time() + 20
        while time.time() < deadline and not (b"second-42" in seen and repo.work.name.encode() in seen):
            seen += socket.receive_bytes()
    assert b"second-42" in seen
    assert repo.work.name.encode() in seen


def test_runs_tabs_stop_at_nine(served: TestClient) -> None:
    "`runs2` to `runs9` exist; `runs1` and `runs10` are not names of anything."
    for name in ("runs1", "runs10", "runs0"):
        assert served.post("/api/type", params={"t": TOKEN}, json={"track": name, "text": "echo no"}).status_code == 409


# ---------- several walks ----------


@pytest.fixture
def class_folder(tmp_path: Path) -> Path:
    "A class folder with two walks: the narrative of every step, and a walk on step-01 and step-03 that borrows a slide."
    root = tmp_path / "class"
    (root / "slides").mkdir(parents=True)
    (root / "slides" / "talk.md").write_text("# One\n\n---\n\n# Two\n\n---\n\n# Three\n\n---\n\n# Four\n")
    (root / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["talk.md#1"]\nstep-01 = ["talk.md#2"]\nstep-02 = ["talk.md#3"]\nstep-03 = ["talk.md#4"]\n')
    (root / "notes.md").write_text("## step-00 Start\nSay hi.\n\n## step-01 A file\n\n## step-02 Tests\n\n## step-03 Tidy\n")
    part = root / "walks" / "part"
    (part / "slides").mkdir(parents=True)
    (part / "slides" / "own.md").write_text("# Only here\n")
    (part / "slides" / "slides.toml").write_text('[slides]\nstep-01 = ["../../../slides/talk.md#2", "own.md"]\nstep-03 = ["own.md"]\n')
    (part / "notes.md").write_text("## step-01 The file, again\nThe part walk.\n\n## step-03 Tidy, again\n")
    (root / "toc.toml").write_text('[[walk]]\nid = "narrative"\ntitle = "Every step"\ndescription = "  The whole story. "\nnotes = "notes.md"\nslides = "slides/slides.toml"\n\n'
                                   '[[walk]]\nid = "part"\ntitle = "A part"\nfolder = "walks/part"\nsteps = ["step-01", "step-03"]\n')
    return root


def test_a_table_of_contents_lists_walks_with_their_files(class_folder: Path) -> None:
    "Paths come from the table's folder; a walk with a folder has notes.md and slides/slides.toml in it."
    from timewalk.walks import load_toc

    narrative, part = load_toc(class_folder / "toc.toml")
    assert (narrative.id, narrative.kind, narrative.notes, narrative.steps) == ("narrative", "narrative", class_folder / "notes.md", None)
    assert part.slides == class_folder / "walks" / "part" / "slides" / "slides.toml" and part.steps == ["step-01", "step-03"]
    assert (narrative.description, part.description) == ("The whole story.", "")


@pytest.mark.parametrize("toc, says", [
    ('[[walk]]\nid = "a"\ncolour = "red"\n', "has the key colour, which a walk does not have"),
    ('[[walk]]\nid = "a"\n\n[[walk]]\nid = "a"\n', "the id a is used twice"),
    ('[[walk]]\nid = "a b"\n', "needs an id of letters, digits"),
    ('[[walk]]\nid = "a"\nkind = "lecture"\n', "kind = 'lecture'"),
    ('[[walk]]\nid = "a"\nsteps = ["step-01"]\ntags = "x-*"\n', "give steps or tags, not both"),
    ('title = "x"\n[[walk]]\nid = "a"\n', "has title, which a table of contents does not have"),
    ('', "lists no walk"),
    ('[[walk]]\nid = "a"\ndescription = 3\n', "description must be text in quotes"),
])
def test_a_table_of_contents_with_a_mistake_says_which(tmp_path: Path, toc: str, says: str) -> None:
    "A mistake in toc.toml stops with a sentence that names the file and the key."
    from timewalk.walks import TocError, load_toc

    (tmp_path / "toc.toml").write_text(toc)
    with pytest.raises(TocError, match=re.escape(says)):
        load_toc(tmp_path / "toc.toml")


def test_a_walk_names_slides_from_the_class_folder(class_folder: Path) -> None:
    "With the class folder as root, a borrowed slide and a walk's own slide are both named from the root; one outside is left out."
    manifest = class_folder / "walks" / "part" / "slides" / "slides.toml"
    assert timewalk.load_slides(manifest, class_folder)["step-01"] == ["slides/talk.md#2", "walks/part/slides/own.md#1"]
    assert timewalk.rebase_entry("../../../../elsewhere.md#1", manifest.parent, class_folder) is None
    assert timewalk.load_slides(class_folder / "slides" / "slides.toml")["step-00"] == ["talk.md#1"], "without a root, as before"


def test_the_checks_pass_a_good_class_and_name_each_mistake(class_folder: Path, sample: Path) -> None:
    "A good class has no errors. Each mistake gives one error that names the walk, the step and the file."
    from timewalk.walks import check, load_toc

    assert check(load_toc(class_folder / "toc.toml"), sample, class_folder) == ([], [])
    part = class_folder / "walks" / "part"
    (part / "notes.md").write_text("## step-01 x\n\n## step-02 not in this walk\n")
    (part / "slides" / "slides.toml").write_text('[slides]\nstep-01 = ["../../../slides/talk.md#9", "gone.md"]\nstep-02 = ["own.md"]\n')
    errors, warnings = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert "walk part: notes.md has a section ## step-02, which is not a step of the walk" in errors
    assert "walk part: slides.toml has an entry for step-02, which is not a step of the walk" in errors
    assert "walk part: step-03 has no entry in slides.toml; give it at least one slide" in errors
    assert "walk part: step-01: ../../../slides/talk.md has 4 slides; the entry asks for slide 9" in errors
    assert "walk part: step-01: gone.md does not exist" in errors
    assert "walk part: step-03 has no section in notes.md" in warnings
    (part / "slides" / "slides.toml").write_text('[slides]\nstep-01 = ["../../../../outside.md"]\nstep-03 = ["own.md"]\n')
    errors, _ = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert any("outside.md is outside the class folder" in e for e in errors)


def test_a_picture_inside_a_markdown_slide_counts_as_used(class_folder: Path, sample: Path) -> None:
    "A picture that a used Markdown slide shows is used; one that nothing shows is a warning."
    from timewalk.walks import check, load_toc

    (class_folder / "slides" / "shown.svg").write_text("<svg/>")
    (class_folder / "slides" / "spare.svg").write_text("<svg/>")
    (class_folder / "slides" / "talk.md").write_text("# One\n\n![a picture](shown.svg)\n\n---\n\n# Two\n\n---\n\n# Three\n\n---\n\n# Four\n")
    _, warnings = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert warnings == ["slides/spare.svg is in a slides folder, and no walk uses it"]


@pytest.fixture
def walked(repo: timewalk.Repo, class_folder: Path) -> TestClient:
    "Serve the sample repository with the two walks of the class folder."
    from timewalk.walks import load_toc

    app = timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(class_folder / "toc.toml"), root=class_folder)
    return TestClient(app, headers=HOST)


def test_a_change_of_walk_changes_the_steps_notes_and_slides(walked: TestClient, repo: timewalk.Repo) -> None:
    "The state lists the walks; a change of walk gives its steps, notes and slides, and goes back where the other walk was left."
    auth = {"t": TOKEN}
    state = walked.get("/api/state", params=auth).json()
    assert [w["id"] for w in state["walks"]] == ["narrative", "part"] and state["walk"]["id"] == "narrative"
    walked.post("/api/move", params=auth, json={"to": 2})
    assert walked.get("/slides/slides/talk.md", params=auth).status_code == 200, "slides are served from the class folder"
    with walked.websocket_connect(f"/ws/events?t={TOKEN}") as window:
        assert next_event(window) == {"type": "hello"}   # on the list of windows: no event can be missed
        walked.post("/api/walk", params=auth, json={"id": "part"})
        assert next_event(window) == {"type": "walk"}
    state = walked.get("/api/state", params=auth).json()
    assert [s["name"] for s in state["steps"]] == ["step-01", "step-03"] and state["current"] == 0
    assert state["slides"] == ["slides/talk.md#2", "walks/part/slides/own.md#1"]
    assert walked.get("/api/notes", params=auth).json()["notes"]["step-01"]["title"] == "The file, again"
    walked.post("/api/walk", params=auth, json={"id": "narrative"})
    state = walked.get("/api/state", params=auth).json()
    assert state["current"] == 2 and state["steps"][2]["name"] == "step-02", "the narrative comes back at the step it was left at"
    assert walked.post("/api/walk", params=auth, json={"id": "nowhere"}).status_code == 409


def test_a_change_of_walk_with_edits_keeps_them_on_a_branch(walked: TestClient, repo: timewalk.Repo) -> None:
    "A change of walk over an edit does not ask, as a move does not: the edit goes on a saved branch named for the step."
    auth = {"t": TOKEN}
    walked.post("/api/move", params=auth, json={"to": 1})
    (repo.work / "src" / "greet.py").write_text("# an edit\n")
    assert walked.post("/api/walk", params=auth, json={"id": "part"}).status_code == 200
    assert walked.get("/api/state", params=auth).json()["walk"]["id"] == "part"
    assert run_git(repo.work, "show", "timewalk/saved/step-01:src/greet.py") == "# an edit"


TUTORIAL = [
    (None, "step-00: the start", {"README.md": "# t\n"}),
    (None, "step-01.1: a", {"a.txt": "a\n"}),
    (None, "step-01.2: b", {"b.txt": "b\n", "README.md": "# t, with b\n"}),
    ("step-01", "step-01: c", {"c.txt": "c\n"}),
    ("step-02", "step-02: d", {"d.txt": "d\n"}),
]


@pytest.fixture
def tutorial(
    tmp_path: Path,  # pytest's temporary directory
) -> timewalk.Repo:  # A repository whose step-01 is made of three moves, as a tutorial walks it
    "Build a history with small commits before step-01, and open it as a tutorial."
    main = tmp_path / "tut"
    main.mkdir()
    run_git(main, "init", "--quiet", "--initial-branch", "main")
    run_git(main, "config", "user.name", "test")
    run_git(main, "config", "user.email", "test@example.invalid")
    for number, (tag, subject, files) in enumerate(TUTORIAL):
        for path, text in files.items():
            (main / path).write_text(text)
        run_git(main, "add", "--all")
        run_git(main, "commit", "--quiet", "--message", subject)
        if number == 0:
            run_git(main, "tag", "--annotate", "step-00", "--message", "The start.")
        if tag:
            run_git(main, "tag", "--annotate", tag, "--message", subject)
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    return repo


def test_a_tutorial_finds_the_moves_between_the_steps(tutorial: timewalk.Repo) -> None:
    "A step's moves are the commits after the step before, its own included; a step of one commit, and the first, have none."
    assert [[m.name for m in moves] for moves in tutorial.moves] == [[], ["step-01.1", "step-01.2", "step-01.3"], []]
    assert tutorial.moves[1][-1].sha == tutorial.steps[1].sha, "the last move is the step's own commit"
    assert [m.subject for m in tutorial.moves[1]] == ["step-01.1: a", "step-01.2: b", "step-01: c"]


def test_a_tutorial_reaches_any_move(tutorial: timewalk.Repo) -> None:
    "A step starts before its first move; any move of it can be reached, forward or back, each at its own commit."
    tutorial.move(1)
    assert tutorial.position() == (1, 0) and tutorial.current() == 1
    assert run_git(tutorial.work, "rev-parse", "HEAD") == tutorial.steps[0].sha, "move 0 is the step before"
    assert tutorial.tree()["summary"] == "" and tutorial.span()[0] == tutorial.span()[1], "nothing of the step is made yet"
    tutorial.move(1, move=2)   # past move 1: each move is a commit of its own
    assert tutorial.position() == (1, 2)
    assert {f["path"]: f["status"] for f in tutorial.tree()["files"] if f["status"]} == {"b.txt": "A", "README.md": "M"}, "what this move changed"
    assert "+# t, with b" in tutorial.diff("README.md")
    tutorial.move(1, move=3)
    assert tutorial.position() == (1, 3) and run_git(tutorial.work, "rev-parse", "HEAD") == tutorial.steps[1].sha
    tutorial.move(1, move=1)
    assert tutorial.position() == (1, 1), "back to an earlier move"
    with pytest.raises(timewalk.GitError, match="has no move 4"):
        tutorial.move(1, move=4)
    tutorial.move(2)
    assert tutorial.position() == (2, None), "a step of one commit has no moves, and stands on its tag"


def test_the_state_of_a_tutorial_lists_the_moves_and_their_files(tutorial: timewalk.Repo) -> None:
    "At a step with moves, the state gives the move made and each move with the files its commit added or changed."
    tutorial.move(1)
    tutorial.move(1, move=1)
    state = tutorial.state()
    assert state["current"] == 1 and state["move"] == 1
    assert [(m["name"], [f["path"] for f in m["files"]]) for m in state["moves"]] == [
        ("step-01.1", ["a.txt"]), ("step-01.2", ["README.md", "b.txt"]), ("step-01.3", ["c.txt"])]


def test_a_tutorial_after_a_restart_finds_its_move_from_the_commit(tutorial: timewalk.Repo) -> None:
    "Without a remembered place, a move's commit says which move it is, and a step's commit is its last move."
    tutorial.move(1)
    tutorial.move(1, move=1)
    tutorial.at = None
    assert tutorial.position() == (1, 1)
    run_git(tutorial.work, "checkout", "--quiet", "--detach", tutorial.steps[1].sha)
    assert tutorial.position() == (1, 3)


def test_notes_give_each_move_a_part_and_a_files_line() -> None:
    "A ### heading with the step's name and a number starts a move; files: is a part; another ### is prose."
    parts = timewalk.parse_notes("## step-01 S\nIntro.\n### step-01.1 A\nfiles:\n$ ls\n### Aside\ntext\n### step-01.2\n")["step-01"]["parts"]
    assert parts == [{"kind": "text", "text": "Intro."}, {"kind": "move", "name": "step-01.1", "title": "A"}, {"kind": "files", "items": []},
                     {"kind": "command", "track": "replay", "text": "ls"}, {"kind": "text", "text": "### Aside\ntext"},
                     {"kind": "move", "name": "step-01.2", "title": ""}]


@pytest.fixture
def tutorial_class(tmp_path: Path) -> Path:
    "A class folder with one tutorial walk, its notes and slides."
    folder = tmp_path / "tclass"
    (folder / "slides").mkdir(parents=True)
    (folder / "toc.toml").write_text('[[walk]]\nid = "tut"\nkind = "tutorial"\nnotes = "notes.md"\nslides = "slides/slides.toml"\n')
    (folder / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\n$ ls\n### step-01.2 B\n$ ls\n### step-01.3 C\n$ ls\n\n## step-02\n")
    (folder / "slides" / "deck.md").write_text("# zero\n---\n# one\n---\n# m1\n---\n# m2\n---\n# m3\n---\n# two\n")
    (folder / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\n"step-01.1" = ["deck.md#3"]\n'
                                                  '"step-01.2" = ["deck.md#4"]\n"step-01.3" = ["deck.md#5"]\nstep-02 = ["deck.md#6"]\n')
    return folder


def unsourced(
    warnings: list[str],  # Warnings of timewalk-check
) -> list[str]:  # The same, without those of a file whose patch no item names, for a test of something else
    "Leave out the warnings about where a file comes from: the notes of most tests have no patch$ lines."
    return [w for w in warnings if "no patch$ line names it" not in w]


def test_the_checks_of_a_tutorial(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each move needs a ### section, in the order of the commits."
    from timewalk.walks import check, load_toc

    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert (errors, unsourced(warnings)) == ([], [])
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.2 B\n### step-01.1 A\n### step-01.4 D\n\n## step-02\n")
    (tutorial_class / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\n"step-01.9" = ["deck.md#3"]\nstep-02 = ["deck.md#6"]\n')
    errors, _ = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert "walk tut: notes.md has a section ### step-01.4, which is not a move of step-01" in errors
    assert "walk tut: step-01.3 has no section ### step-01.3 in notes.md" in errors
    assert "walk tut: the ### sections of step-01 in notes.md are not in the order of the commits: step-01.2, step-01.1" in errors
    assert "walk tut: slides.toml has an entry for step-01.9, which is not a step or a move of the walk" in errors


def test_a_move_needs_no_name_in_its_subject(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A move is named by its place, so its commit can have any subject, or none, as jj new leaves it; its title comes from the notes."
    from timewalk.facts import move_title
    from timewalk.walks import check, load_toc

    run_git(tutorial.main, "checkout", "--quiet", "--detach", tutorial.steps[0].sha)
    run_git(tutorial.main, "commit", "--quiet", "--allow-empty", "--allow-empty-message", "--message", "")
    run_git(tutorial.main, "commit", "--quiet", "--allow-empty", "--message", "step-01: the end")
    run_git(tutorial.main, "tag", "--force", "--annotate", "step-01", "--message", "again")
    run_git(tutorial.main, "tag", "--delete", "step-02")
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\n### step-01.2 B\n")
    (tutorial_class / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\n')
    errors, _ = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert errors == []
    first = timewalk.step_moves(tutorial.main, timewalk.tag_steps(tutorial.main, "step-*"))[1][0]
    assert first.subject == "" and move_title(first, "A") == "A"


@pytest.fixture
def tutored(tutorial: timewalk.Repo, tutorial_class: Path) -> TestClient:
    "Serve the tutorial with its class folder."
    from timewalk.walks import load_toc

    app = timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(tutorial_class / "toc.toml"), root=tutorial_class)
    return TestClient(app, headers=HOST)


def test_slides_and_moves_follow_each_other_with_sync(tutored: TestClient, tutorial_class: Path) -> None:
    "In watch mode, a step's deck is its slides, then each move's; a move shows its first slide, and a move's slide makes that move."
    auth = {"t": TOKEN}
    tutored.post("/api/show", params=auth, json={"mode": "watch"})
    tutored.post("/api/move", params=auth, json={"to": 1})
    state = tutored.get("/api/state", params=auth).json()
    assert state["slides"] == [f"slides/deck.md#{n}" for n in (2, 3, 4, 5)] and state["slide_moves"] == [0, 1, 2, 3] and state["sync"]
    assert state["move"] == 0 and state["walk"]["kind"] == "tutorial"
    assert tutored.post("/api/move", params=auth, json={"to": 1, "move": 1}).status_code == 200
    assert tutored.get("/api/state", params=auth).json()["slide"] == 1, "the move's first slide"
    assert tutored.post("/api/slide", params=auth, json={"to": 2}).status_code == 200
    assert tutored.get("/api/state", params=auth).json()["move"] == 2, "a slide of the next move makes that move"
    refused = tutored.post("/api/move", params=auth, json={"to": 1, "move": 3 + 1})
    assert refused.status_code == 409
    tutored.post("/api/slide", params=auth, json={"to": 0})
    assert tutored.get("/api/state", params=auth).json()["move"] == 2, "the step's own slide makes no move"
    tutored.post("/api/move", params=auth, json={"to": 1, "move": 0})
    assert tutored.post("/api/slide", params=auth, json={"to": 3}).status_code == 200
    state = tutored.get("/api/state", params=auth).json()
    assert (state["move"], state["slide"]) == (1, 1), "a slide two moves ahead makes the next move only, and shows its slide"
    assert tutored.post("/api/move", params=auth, json={"to": 1, "name": "step-00", "move": 1}).status_code == 409, "the name must be the step's"
    tutored.post("/api/move", params=auth, json={"to": 2})
    tutored.post("/api/move", params=auth, json={"to": 1})
    state = tutored.get("/api/state", params=auth).json()
    assert (state["move"], state["slide"]) == (0, 0), "back at a step, its own first slide, not a later move's"
    manifest = tutorial_class / "slides" / "slides.toml"
    manifest.write_text("sync = false\n" + manifest.read_text())   # before [slides], or it would be a key of that table
    assert tutored.post("/api/slide", params=auth, json={"to": 3}).status_code == 200
    assert tutored.get("/api/state", params=auth).json()["move"] == 0, "without sync, slides and moves are apart"


def test_the_page_serves_slides_but_not_the_notes_of_the_class_folder(walked: TestClient) -> None:
    "Slides come from the walks' slides folders only: the notes, with their private cues, and the table stay unserved."
    auth = {"t": TOKEN}
    assert walked.get("/slides/slides/talk.md", params=auth).status_code == 200
    assert walked.get("/slides/walks/part/slides/own.md", params=auth).status_code == 200
    for path in ("notes.md", "toc.toml", "walks/part/notes.md", "slides/../notes.md", "slides/%2e%2e/notes.md"):
        assert walked.get(f"/slides/{path}", params=auth).status_code == 404, path


def test_save_names_its_walk_and_is_refused_in_another(walked: TestClient, class_folder: Path) -> None:
    "An edit begun in one walk is not saved into another walk's notes after a window changed the walk."
    auth = {"t": TOKEN}
    walked.post("/api/walk", params=auth, json={"id": "part"})
    refused = walked.post("/api/notes", params=auth, json={"walk": "narrative", "step": "step-03", "base": "Tidy, again", "text": "mine"})
    assert refused.status_code == 409 and "the walk changed" in refused.json()["error"]
    assert "mine" not in (class_folder / "walks" / "part" / "notes.md").read_text()
    assert walked.post("/api/notes", params=auth, json={"walk": "part", "step": "step-03", "base": "", "text": "ours"}).status_code == 200


def test_a_walk_folder_without_notes_or_slides_is_allowed(class_folder: Path, sample: Path) -> None:
    "In a folder, notes.md and slides/slides.toml are each optional; a missing folder is a mistake."
    from timewalk.walks import TocError, check, load_toc

    (class_folder / "walks" / "part" / "notes.md").unlink()
    part = load_toc(class_folder / "toc.toml")[1]
    assert part.notes is None and part.slides is not None
    errors, _ = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert errors == []
    (class_folder / "toc.toml").write_text('[[walk]]\nid = "x"\nfolder = "walks/none"\n')
    with pytest.raises(TocError, match="its folder walks/none does not exist"):
        load_toc(class_folder / "toc.toml")


@pytest.mark.parametrize("manifest, says", [
    ('slides = ["talk.md"]\n', "has slides that is not a table"),
    ('[slides]\nstep-00 = "talk.md#1"\n', "step-00 in slides.toml must be a list in brackets"),
])
def test_a_manifest_of_the_wrong_shape_is_a_sentence_not_a_traceback(class_folder: Path, sample: Path, manifest: str, says: str) -> None:
    "The checks name the mistake in a manifest whose tables or lists have the wrong shape."
    from timewalk.walks import check, load_toc

    (class_folder / "slides" / "slides.toml").write_text(manifest)
    errors, _ = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert any(says in e for e in errors), errors


def test_a_tutorial_takes_every_step_and_a_files_line_outside_a_move_is_prose(tmp_path: Path) -> None:
    "A tutorial with steps = [...] is refused; files: before any move is an ordinary line of the notes."
    from timewalk.walks import TocError, load_toc

    (tmp_path / "toc.toml").write_text('[[walk]]\nid = "t"\nkind = "tutorial"\nsteps = ["step-01"]\n')
    with pytest.raises(TocError, match="a tutorial takes every step"):
        load_toc(tmp_path / "toc.toml")
    parts = timewalk.parse_notes("## step-01\nfiles:\n- one\n")["step-01"]["parts"]
    assert parts == [{"kind": "text", "text": "files:\n- one"}]


def test_a_tutorial_keeps_its_place_across_a_restart(tutorial: timewalk.Repo) -> None:
    "Move 0 of a step and the step before are one commit; the place written in the replay copy's git folder tells them apart."
    tutorial.move(1)
    assert tutorial.position() == (1, 0)
    again = timewalk.Repo(tutorial.main)
    again.select("step-*", None, tutorial=True)
    assert again.position() == (1, 0), "not step-00, which stands on the same commit"
    assert not (again.work / "timewalk-place.json").exists(), "the place is not a file of the replay copy"


def test_a_failed_change_of_walk_keeps_the_place_in_a_tutorial(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A change of walk that fails (here, git's index is locked) leaves the tutorial at the same move."
    from timewalk.walks import load_toc

    (tutorial_class / "toc.toml").write_text((tutorial_class / "toc.toml").read_text() + '\n[[walk]]\nid = "plain"\n')
    walks = load_toc(tutorial_class / "toc.toml")
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=walks, root=tutorial_class), headers=HOST)
    auth = {"t": TOKEN}
    client.post("/api/move", params=auth, json={"to": 1})
    tutorial.place_file().unlink()   # so that only the restore of the place in memory can keep it
    lock = Path(run_git(tutorial.work, "rev-parse", "--absolute-git-dir")) / "index.lock"
    lock.write_text("")   # another git holds the index, and does not let go: the checkout fails
    assert client.post("/api/walk", params=auth, json={"id": "plain"}).status_code == 409
    lock.unlink()
    state = client.get("/api/state", params=auth).json()
    assert (state["current"], state["move"]) == (1, 0)


def test_a_window_that_only_opens_a_shell_keeps_its_size(served: TestClient) -> None:
    "A size sent on opening goes to a new shell only; the Room's size is forgotten when its socket closes."
    auth = {"t": TOKEN}
    with served.websocket_connect(f"/ws/term/runs?t={TOKEN}") as first:
        first.send_json({"type": "resize", "rows": 30, "cols": 100, "opening": True})
        first.send_json({"type": "input", "data": "echo one-$((6*7)) $(stty size)\r"})
        seen = ""
        while "one-42 30 100" not in seen:
            data = first.receive()
            seen += data.get("text") or (data.get("bytes") or b"").decode("utf-8", "replace")
        with served.websocket_connect(f"/ws/term/runs?t={TOKEN}") as second:
            second.send_json({"type": "resize", "rows": 11, "cols": 50, "opening": True})
            second.send_json({"type": "input", "data": "echo two-$((6*7)) $(stty size)\r"})
            while "two-42 30 100" not in seen:
                data = first.receive()
                seen += data.get("text") or (data.get("bytes") or b"").decode("utf-8", "replace")
    served.post("/api/show", params=auth, json={"shell": True})
    with served.websocket_connect(f"/ws/term/runs?t={TOKEN}") as room:
        room.send_json({"type": "resize", "rows": 50, "cols": 160, "room": True})
        for _ in range(50):
            if served.get("/api/state", params=auth).json()["room_sizes"]:
                break
            time.sleep(0.05)
        assert served.get("/api/state", params=auth).json()["room_sizes"] == {"runs": [50, 160]}
    for _ in range(50):
        if not served.get("/api/state", params=auth).json()["room_sizes"]:
            break
        time.sleep(0.05)
    assert served.get("/api/state", params=auth).json()["room_sizes"] == {}, "the Room window went away"



def test_the_checks_keep_the_notes_out_of_what_the_page_serves(class_folder: Path, sample: Path) -> None:
    "A manifest at the top of the class folder, notes in a slides folder, and a picture outside every slides folder are errors."
    from timewalk.walks import check, load_toc

    (class_folder / "slides" / "talk.md").write_text("# One\n\n![p](../pictures/p.png)\n\n---\n\n# Two\n\n---\n\n# Three\n\n---\n\n# Four\n")
    (class_folder / "pictures").mkdir()
    (class_folder / "pictures" / "p.png").write_bytes(b"png")
    errors, _ = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert any("shows ../pictures/p.png, which is not in a slides folder" in e for e in errors), errors
    (class_folder / "slides.toml").write_text('[slides]\nstep-00 = ["slides/talk.md#1"]\n')
    (class_folder / "walks" / "part" / "slides" / "notes.md").write_text("## step-01\n")
    (class_folder / "toc.toml").write_text('[[walk]]\nid = "top"\nnotes = "notes.md"\nslides = "slides.toml"\n\n'
                                           '[[walk]]\nid = "part"\nnotes = "walks/part/slides/notes.md"\nslides = "walks/part/slides/slides.toml"\n'
                                           'steps = ["step-01", "step-03"]\n')
    errors, _ = check(load_toc(class_folder / "toc.toml"), sample, class_folder)
    assert any("a slides manifest is at the top of the class folder" in e for e in errors), errors
    assert any("walk part: its notes" in e and "are in a slides folder" in e for e in errors), errors


def test_a_link_in_a_slides_folder_to_the_notes_is_not_served(walked: TestClient, class_folder: Path) -> None:
    "A link inside a slides folder that leads to the notes is refused like the notes themselves."
    (class_folder / "slides" / "link.md").symlink_to(class_folder / "notes.md")
    assert walked.get("/slides/slides/link.md", params={"t": TOKEN}).status_code == 404


def test_a_fence_before_the_first_step_is_read_as_save_reads_it() -> None:
    "A fenced example of a heading before the first step is code for the parser too."
    text = "# Notes\n\n```\n## step-01 an example\n```\n\n## step-01 Real\none\n\n## step-02 Two\ntwo\n"
    notes = timewalk.parse_notes(text)
    assert notes["step-01"]["text"] == "one" and notes["step-02"]["text"] == "two"


def test_a_move_without_slides_keeps_the_slide_before_it_and_down_goes_on(tutored: TestClient, tutorial_class: Path) -> None:
    "In watch mode, move 2 has no slides: it shows move 1's slide, and Down then makes move 3, never undoing move 2."
    auth = {"t": TOKEN}
    tutored.post("/api/show", params=auth, json={"mode": "watch"})
    (tutorial_class / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\n"step-01.1" = ["deck.md#3"]\n'
                                                          '"step-01.3" = ["deck.md#5"]\nstep-02 = ["deck.md#6"]\n')
    tutored.post("/api/move", params=auth, json={"to": 1})
    tutored.post("/api/move", params=auth, json={"to": 1, "move": 1})
    tutored.post("/api/move", params=auth, json={"to": 1, "move": 2})
    state = tutored.get("/api/state", params=auth).json()
    assert (state["move"], state["slide"]) == (2, 1)
    tutored.post("/api/slide", params=auth, json={"to": 2, "step": "step-01"})
    state = tutored.get("/api/state", params=auth).json()
    assert (state["move"], state["slide"]) == (3, 2)
    refused = tutored.post("/api/slide", params=auth, json={"to": 0, "step": "step-02"})
    assert refused.status_code == 409 and "the step changed" in refused.json()["error"]


def test_a_step_without_moves_forgets_the_place_written_before(tutorial: timewalk.Repo) -> None:
    "Go to step-01's start, then back to step-00: after a restart, the copy is at step-00, not at step-01's start."
    tutorial.move(1)
    tutorial.move(0)
    again = timewalk.Repo(tutorial.main)
    again.select("step-*", None, tutorial=True)
    assert again.position() == (0, None)


def test_a_return_to_a_tutorial_comes_back_to_its_move(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Leave a tutorial at a move, visit another walk, come back: the same move, and the saved branch names the move."
    from timewalk.walks import load_toc

    (tutorial_class / "toc.toml").write_text((tutorial_class / "toc.toml").read_text() + '\n[[walk]]\nid = "plain"\n')
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(tutorial_class / "toc.toml"),
                                          root=tutorial_class), headers=HOST)
    auth = {"t": TOKEN}
    client.post("/api/move", params=auth, json={"to": 1})
    client.post("/api/move", params=auth, json={"to": 1, "move": 1})
    client.post("/api/move", params=auth, json={"to": 1, "move": 2})
    (tutorial.work / "a.txt").write_text("an edit\n")
    assert client.post("/api/walk", params=auth, json={"id": "plain"}).status_code == 200
    assert run_git(tutorial.work, "show", "timewalk/saved/step-01.2:a.txt") == "an edit"
    client.post("/api/walk", params=auth, json={"id": "tut"})
    state = client.get("/api/state", params=auth).json()
    assert (state["current"], state["move"]) == (1, 2)


def test_two_walks_that_share_a_notes_file_can_both_save(repo: timewalk.Repo, class_folder: Path) -> None:
    "An edit begun in one walk saves after a change to another walk with the same notes file; with another file, it is refused."
    from timewalk.walks import load_toc

    (class_folder / "toc.toml").write_text((class_folder / "toc.toml").read_text() +
                                           '\n[[walk]]\nid = "again"\nnotes = "notes.md"\nslides = "slides/slides.toml"\n')
    client = TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(class_folder / "toc.toml"), root=class_folder),
                        headers=HOST)
    auth = {"t": TOKEN}
    client.post("/api/walk", params=auth, json={"id": "again"})
    saved = client.post("/api/notes", params=auth, json={"walk": "narrative", "step": "step-02", "base": "", "text": "shared"})
    assert saved.status_code == 200 and "shared" in (class_folder / "notes.md").read_text()
    client.post("/api/walk", params=auth, json={"id": "part"})
    refused = client.post("/api/notes", params=auth, json={"walk": "narrative", "step": "step-03", "base": "Tidy, again", "text": "x"})
    assert refused.status_code == 409


def test_a_class_folder_given_through_a_link_still_serves_its_slides(repo: timewalk.Repo, class_folder: Path, tmp_path: Path) -> None:
    "The class folder named through a symbolic link: the walks' paths are resolved, and so is the folder, so the slides stay in."
    from timewalk.walks import load_toc

    link = tmp_path / "linked"
    link.symlink_to(class_folder)
    client = TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(link / "toc.toml"), root=link), headers=HOST)
    auth = {"t": TOKEN}
    client.post("/api/move", params=auth, json={"to": 1})
    assert client.get("/api/state", params=auth).json()["slides"] == ["slides/talk.md#2"]


def test_do_mode_marks_moves_done_without_moving_the_code(tutored: TestClient, tutorial: timewalk.Repo) -> None:
    "In do mode, the default, Done counts the moves made by hand; the code stays where the learner has it, and slides follow."
    auth = {"t": TOKEN}
    state = tutored.get("/api/state", params=auth).json()
    assert state["mode"] == "do" and state["done"] == 0
    tutored.post("/api/move", params=auth, json={"to": 1})
    head = run_git(tutorial.work, "rev-parse", "HEAD")
    assert tutored.post("/api/done", params=auth, json={"step": "step-01", "done": 1}).status_code == 200
    state = tutored.get("/api/state", params=auth).json()
    assert (state["done"], state["move"], state["slide"]) == (1, 0, 2), "move 2's slide, as move 2 is the one to do now"
    assert run_git(tutorial.work, "rev-parse", "HEAD") == head, "Done does not move the code"
    tutored.post("/api/slide", params=auth, json={"to": 3, "step": "step-01"})
    assert tutored.get("/api/state", params=auth).json()["move"] == 0, "in do mode a slide makes no move"
    assert tutored.post("/api/done", params=auth, json={"step": "step-02", "done": 1}).status_code == 409


def test_catch_me_up_sets_the_code_to_the_end_of_a_move(tutored: TestClient, tutorial: timewalk.Repo) -> None:
    "Catch me up is a move to a move: the code is that move's commit, and that many moves are done. The learner's try is kept."
    auth = {"t": TOKEN}
    tutored.post("/api/move", params=auth, json={"to": 1})
    (tutorial.work / "README.md").write_text("my own try\n")
    assert tutored.post("/api/move", params=auth, json={"to": 1, "name": "step-01", "move": 1}).status_code == 200
    state = tutored.get("/api/state", params=auth).json()
    assert (state["move"], state["done"]) == (1, 1)
    assert run_git(tutorial.work, "show", "timewalk/saved/step-01:README.md") == "my own try"


def test_the_reader_shows_one_moves_change_whatever_the_code(tutored: TestClient) -> None:
    "With of, the file API gives what that move changed, even while the code is at the start of the step."
    auth = {"t": TOKEN}
    tutored.post("/api/move", params=auth, json={"to": 1})
    answer = tutored.get("/api/file", params={**auth, "path": "README.md", "of": "step-01.2"}).json()
    assert answer["of"] == "step-01.2" and "+# t, with b" in answer["diff"]
    assert "of" not in tutored.get("/api/file", params={**auth, "path": "README.md"}).json()


def test_the_mode_starts_from_the_table_and_is_shared(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "moves = watch in toc.toml starts the walk in watch mode; the page can switch it, for every window; a wrong value is a mistake."
    from timewalk.walks import TocError, load_toc

    toc = tutorial_class / "toc.toml"
    toc.write_text(toc.read_text() + 'moves = "watch"\n')
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    auth = {"t": TOKEN}
    assert client.get("/api/state", params=auth).json()["mode"] == "watch"
    with client.websocket_connect(f"/ws/events?t={TOKEN}") as window:
        assert next_event(window) == {"type": "hello"}   # on the list of windows: no event can be missed
        client.post("/api/show", params=auth, json={"mode": "do"})
        assert next_event(window)["mode"] == "do"
    toc.write_text(toc.read_text().replace('"watch"', '"look"'))
    with pytest.raises(TocError, match='write "do" or "watch"'):
        load_toc(toc)


def test_timewalk_notes_drafts_the_missing_moves(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each move without a section gets a draft with what its commit changed; a section already written stays as it is."
    from timewalk.facts import changed_names
    from timewalk.walks import add_move_sections, check, load_toc

    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01 S\n\n### step-01.1 Mine\nmy words\n\n$ ls\n\n## step-02\n")
    walk = load_toc(tutorial_class / "toc.toml")[0]
    text, added, drafts = add_move_sections(walk, tutorial.main)
    assert [d.split("\n", 1)[0] for d in drafts] == ["### step-01.2 b", "### step-01.3 c"]
    assert added == ["step-01.2", "step-01.3"]
    assert "### step-01.1 Mine\nmy words\n\n$ ls" in text
    assert "### step-01.2 b\n" in text and "### step-01.3 c\n" in text, "titles without the move's or the step's prefix"
    sha = tutorial.moves[1][1].sha
    assert changed_names(tutorial.main, f"{sha}^", sha) == {"README.md": {"added": [], "removed": [], "changed": []},
                                                          "b.txt": {"added": [], "removed": [], "changed": []}}
    (tutorial_class / "notes.md").write_text(text)
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("step-01.2 has no command" in w for w in warnings) and not any("step-01.1 has no command" in w for w in warnings)


def test_the_summary_names_python_functions_and_recipes(tmp_path: Path) -> None:
    "A function added, a function changed in its signature and in its body, a class added, a recipe added: each is named."
    main = tmp_path / "s"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "m.py").write_text("def a():\n    return 1\n\n\ndef b(x):\n    return x\n")
    (main / "justfile").write_text("test:\n    echo t\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "one")
    (main / "m.py").write_text("def a():\n    return 2\n\n\ndef b(x, y):\n    return x\n\n\nclass C:\n    pass\n")
    (main / "justfile").write_text("test:\n    echo t\n\ncheck:\n    echo c\n")
    run_git(main, "commit", "-qam", "two")
    from timewalk.facts import changed_names

    assert changed_names(main, "HEAD^", "HEAD") == {"justfile": {"added": ["recipe check"], "removed": [], "changed": []},
                                                    "m.py": {"added": ["class C"], "removed": [], "changed": ["a", "b"]}}


# ---------- the replay copy: a clone on a branch ----------


def test_the_replay_copy_is_a_clone_on_a_branch_of_its_own(repo: timewalk.Repo, sample: Path) -> None:
    "The replay copy is a repository of its own, cloned from yours, on the branch timewalk/replay, which moves with the steps."
    assert (repo.work / ".git").is_dir() and not repo.legacy
    assert Path(run_git(repo.work, "remote", "get-url", timewalk.HOME)).resolve() == sample.resolve()
    repo.move(2)
    assert run_git(repo.work, "branch", "--show-current") == timewalk.BRANCH
    assert run_git(repo.work, "rev-parse", "HEAD") == repo.steps[2].sha


def test_your_repository_is_never_written_to(repo: timewalk.Repo, sample: Path) -> None:
    "Moves, kept edits, a commit and a change to git's config in the replay copy leave your repository's config, refs and stash alone."
    before = ((sample / ".git" / "config").read_text(), run_git(sample, "for-each-ref"), run_git(sample, "stash", "list"))
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("# an edit\n")
    repo.move(2)
    run_git(repo.work, "config", "core.hooksPath", "hooks")
    run_git(repo.work, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", "a learner's commit")
    repo.move(3)
    after = ((sample / ".git" / "config").read_text(), run_git(sample, "for-each-ref"), run_git(sample, "stash", "list"))
    assert after == before
    assert run_git(repo.work, "show", "timewalk/saved/step-01:src/greet.py") == "# an edit", "the kept edits are in the replay copy"


def test_a_learners_commits_are_kept_when_a_move_resets_the_branch(repo: timewalk.Repo) -> None:
    "Commits made at a step are kept on timewalk/saved/<step> when the class moves on; a second visit gets -2; no commit, no branch."
    def commit(message: str) -> str:
        run_git(repo.work, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", message)
        return run_git(repo.work, "rev-parse", "HEAD")

    repo.move(1)
    repo.move(2)
    assert run_git(repo.work, "branch", "--list", "timewalk/saved/*") == "", "no commits: nothing saved"
    mine = commit("my move at step-02")
    repo.move(3)
    assert run_git(repo.work, "rev-parse", "timewalk/saved/step-02") == mine
    repo.move(2)
    again = commit("my second try")
    repo.move(1)
    assert run_git(repo.work, "rev-parse", "timewalk/saved/step-02-2") == again


def test_a_step_tagged_again_is_seen_at_the_next_start(repo: timewalk.Repo, sample: Path) -> None:
    "Tag a step on another commit in your repository; the next start fetches it into the replay copy, and moves there."
    run_git(sample, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", "a fix for step-03")
    run_git(sample, "tag", "--force", "--annotate", "step-03", "-m", "Tidy, fixed")
    again = timewalk.Repo(sample)
    assert again.work == repo.work and again.steps[3].sha == run_git(sample, "rev-parse", "HEAD")
    again.move(3)
    assert run_git(again.work, "rev-parse", "HEAD") == again.steps[3].sha


def test_a_replay_worktree_of_an_older_version_still_works(sample: Path) -> None:
    "A worktree at the replay copy's place, as older versions made it, is used as it is, on a detached HEAD."
    first = run_git(sample, "rev-list", "--max-parents=0", "HEAD")
    run_git(sample, "worktree", "add", "--quiet", "--detach", str(sample.parent / "sample-replay"), first)
    old = timewalk.Repo(sample)
    assert old.legacy and old.branch is None
    old.move(2)
    assert run_git(old.work, "rev-parse", "HEAD") == old.steps[2].sha and run_git(old.work, "branch", "--show-current") == ""


def learner_commit(work: Path, message: str) -> str:
    "Make an empty commit in the replay copy, as a learner would, and return it."
    run_git(work, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", message)
    return run_git(work, "rev-parse", "HEAD")


def test_commits_on_the_branch_are_kept_even_when_head_is_elsewhere(repo: timewalk.Repo) -> None:
    "A learner commits on the branch, then checks out a step by hand; the move still keeps the branch's commit."
    repo.move(1)
    mine = learner_commit(repo.work, "on the branch")
    run_git(repo.work, "checkout", "--quiet", "--detach", repo.steps[2].sha)
    repo.move(3)
    assert run_git(repo.work, "branch", "--list", "timewalk/saved/*", "--format=%(objectname)") == mine


def test_no_branch_is_saved_without_a_learners_commit_even_after_a_retag(repo: timewalk.Repo, sample: Path) -> None:
    "History rewritten and tagged again in your repository: the old step commits are not taken for a learner's work."
    repo.move(3)
    run_git(sample, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--amend", "-m", "Tidy, rewritten")
    run_git(sample, "tag", "--force", "--annotate", "step-03", "-m", "Tidy")
    again = timewalk.Repo(sample)
    again.move(1)
    assert run_git(again.work, "branch", "--list", "timewalk/*") == f"* {timewalk.BRANCH}", "only the replay branch"


def test_a_learners_commit_after_a_restart_is_saved_under_its_step(repo: timewalk.Repo, sample: Path) -> None:
    "Without a remembered place, a commit made at step-01 is saved as step-01, named from where timewalk put the branch."
    repo.move(1)
    mine = learner_commit(repo.work, "after the restart")
    again = timewalk.Repo(sample)
    again.move(2)
    assert run_git(again.work, "rev-parse", "timewalk/saved/step-01") == mine


def test_a_step_tagged_during_the_session_can_be_reached(repo: timewalk.Repo, sample: Path) -> None:
    "A step tagged in your repository while timewalk runs: its commit is fetched into the clone when it is needed."
    run_git(sample, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", "a new step")
    run_git(sample, "tag", "--annotate", "step-04", "-m", "New")
    repo.select("step-*")
    assert [s.name for s in repo.steps][-1] == "step-04"
    repo.move(4)
    assert run_git(repo.work, "rev-parse", "HEAD") == run_git(sample, "rev-parse", "step-04^{commit}")


def test_a_deleted_step_tag_goes_from_the_clone_and_a_learners_tag_stays(repo: timewalk.Repo, sample: Path) -> None:
    "Delete a step's tag in your repository: the next start drops it from the replay copy, and keeps a tag the learner made."
    run_git(repo.work, "tag", "mine")
    run_git(sample, "tag", "--delete", "step-03")
    timewalk.Repo(sample)
    tags = run_git(repo.work, "tag", "--list").splitlines()
    assert "step-03" not in tags and "mine" in tags


def test_commits_mode_on_a_detached_repository_saves_nothing(sample: Path) -> None:
    "Steps are the commits of a detached HEAD, as in a submodule: moving between them is not a learner's work."
    run_git(sample, "checkout", "--quiet", "--detach")
    repo = timewalk.Repo(sample, commits=True)
    repo.move(len(repo.steps) - 1)
    repo.move(0)
    assert run_git(repo.work, "branch", "--list", "timewalk/saved/*") == ""


def test_a_learners_tags_are_never_moved_or_deleted(repo: timewalk.Repo, sample: Path) -> None:
    "A learner's tag that matches the step pattern, or has the name of a step you tag later, survives every fetch."
    run_git(repo.work, "tag", "step-try")
    learner_commit(repo.work, "my try")
    run_git(repo.work, "tag", "step-04")
    mine = run_git(repo.work, "rev-parse", "step-04")
    run_git(sample, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", "a new step")
    run_git(sample, "tag", "--annotate", "step-04", "-m", "New")
    repo.select("step-*")
    timewalk.Repo(sample)
    assert "step-try" in run_git(repo.work, "tag", "--list").splitlines()
    assert run_git(repo.work, "rev-parse", "step-04") == mine, "the learner's own step-04 stays"


def test_a_start_that_stopped_halfway_leaves_no_replay_copy(sample: Path) -> None:
    "A clone is made in a folder of its own and renamed when done: a leftover folder from a stopped start is made again."
    making = sample.parent / ".sample-replay.making"
    making.mkdir()
    (making / "junk").write_text("from a start that stopped\n")
    repo = timewalk.Repo(sample)
    assert not making.exists() and repo.branch == timewalk.BRANCH
    assert repo.edits() == [], "a whole working copy, not an empty index"


def test_a_home_branch_named_like_the_replay_branch_is_no_trouble(sample: Path) -> None:
    "Your repository has a branch timewalk/replay, checked out: the replay copy is still made, on its own branch of that name."
    run_git(sample, "switch", "--quiet", "-c", timewalk.BRANCH)
    repo = timewalk.Repo(sample)
    assert run_git(repo.work, "rev-parse", "HEAD") == repo.steps[0].sha and run_git(repo.work, "status", "--porcelain") == ""


def test_commits_mode_reaches_new_commits_of_a_detached_repository(sample: Path) -> None:
    "Your repository on a detached HEAD gains a commit; the next start with --commits can move to it."
    run_git(sample, "checkout", "--quiet", "--detach")
    timewalk.Repo(sample, commits=True)
    run_git(sample, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "--allow-empty", "-m", "after")
    again = timewalk.Repo(sample, commits=True)
    again.move(len(again.steps) - 1)
    assert run_git(again.work, "rev-parse", "HEAD") == run_git(sample, "rev-parse", "HEAD")


def test_files_items_with_words_and_show_lines() -> None:
    "Items under files:, words over two lines, show: lines, a named move, and items in a narrative step."
    notes = timewalk.parse_notes("## step-02\n### step-02.1 A\nfiles:\n- diff `src/x.py`: one word\n  answers it.\n  show: `x = 1`\n"
                                 "- file step-02.1 `t.py`: read it.\n\n$ ls\n## step-03\nfiles:\n- diff `a.py`: in a narrative.\nprose\n")
    items = next(p for p in notes["step-02"]["parts"] if p["kind"] == "files")["items"]
    assert items == [{"kind": "diff", "move": None, "path": "src/x.py", "text": "one word answers it.", "show": ["x = 1"]},
                     {"kind": "file", "move": "step-02.1", "path": "t.py", "text": "read it.", "show": []}]
    assert notes["step-03"]["parts"][0]["items"][0]["path"] == "a.py"
    assert "- diff `src/x.py`: one word" in notes["step-02"]["text"], "the PDF prints an item as written"


def test_the_checks_verify_items_and_show_lines(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A diff item names a file its move changes, a file item a file of its commit, and a show: line a line of the change."
    from timewalk.walks import check, load_toc

    (tutorial_class / "notes.md").write_text(
        "## step-00\n\n## step-01\n### step-01.1 A\nfiles:\n- file `a.txt`: new.\n- diff `zzz.txt`: wrong.\n$ ls\n"
        "### step-01.2 B\nfiles:\n- diff `README.md`: changed.\n  show: `with b`\n  show: `not there`\n$ ls\n### step-01.3 C\n$ ls\n\n## step-02\n")
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert "walk tut: step-01.1: the diff item zzz.txt is not changed by the move" in warnings
    assert "walk tut: step-01.2: the line `not there` is no longer in the change of README.md" in warnings
    assert not any("with b" in w or "a.txt" in w for w in unsourced(warnings))


def test_timewalk_notes_starts_the_notes_of_a_walk_folder_without_them(tutorial: timewalk.Repo, tmp_path: Path) -> None:
    "A tutorial whose folder has no notes.md yet: --write makes it, with a titled section for each step and a draft for each move."
    folder = tmp_path / "kit" / "walks" / "tut"
    folder.mkdir(parents=True)
    (tmp_path / "kit" / "toc.toml").write_text('[[walk]]\nid = "tut"\nkind = "tutorial"\nfolder = "walks/tut"\n')
    done = subprocess.run([sys.executable, "-c", "from timewalk.walks import notes_main; notes_main()", str(tutorial.main),
                           "--toc", str(tmp_path / "kit" / "toc.toml"), "--walk", "tut", "--write"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    notes = timewalk.parse_notes((folder / "notes.md").read_text())
    assert list(notes) == [step.name for step in tutorial.steps]
    assert all(notes[step.name]["title"] for step in tutorial.steps), "each heading has the first line of its tag's message"
    assert [part["name"] for part in notes["step-01"]["parts"] if part["kind"] == "move"] == ["step-01.1", "step-01.2", "step-01.3"]


def test_the_summary_names_functions_in_files_with_odd_names(tmp_path: Path) -> None:
    "A function added in a file with a space, or with an accent, is named: git's tab and quotes do not hide the file."
    from timewalk.facts import changed_names
    main = tmp_path / "odd"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "a.txt").write_text("a\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "one")
    for name in ("sp ace.py", "café.py"):
        (main / name).write_text("def b():\n    return 1\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "two")
    names = changed_names(main, "HEAD^", "HEAD")
    assert names["sp ace.py"]["added"] == ["b"] and names["café.py"]["added"] == ["b"]


def test_timewalk_notes_keeps_the_order_the_line_ends_and_the_titles(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A missing step goes before the next step written; a lightweight tag's title loses its prefix; Windows line ends stay."
    from timewalk.walks import add_move_sections, load_toc

    run_git(tutorial.main, "tag", "-d", "step-01")
    run_git(tutorial.main, "tag", "step-01", tutorial.steps[1].sha)   # lightweight: no message, the commit's subject is the title
    (tutorial_class / "notes.md").write_bytes(b"## step-00\r\n\r\n## step-02 Two\r\n")
    text, _, _ = add_move_sections(load_toc(tutorial_class / "toc.toml")[0], tutorial.main)
    assert text.index("## step-01") < text.index("## step-02 Two"), "step-01 goes before step-02, which is written"
    heading = next(line for line in text.split("\n") if line.startswith("## step-01"))
    assert not heading.startswith("## step-01 step-01:") and len(heading) > len("## step-01 "), heading
    done = subprocess.run([sys.executable, "-c", "from timewalk.walks import notes_main; notes_main()", str(tutorial.main),
                           "--toc", str(tutorial_class / "toc.toml"), "--walk", "tut", "--write"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    raw = (tutorial_class / "notes.md").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), "every line ends as the file's did"


def test_timewalk_notes_drafts_items_for_each_file(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A drafted move ends with an item for every file of its commit: file for a new one, diff for a changed one, with the words left to write, and no counted list."
    from timewalk.walks import add_move_sections, load_toc

    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n\n## step-02\n")
    _, _, drafts = add_move_sections(load_toc(tutorial_class / "toc.toml")[0], tutorial.main)
    assert drafts[1].endswith("files:\n- diff `README.md`: \n- file `b.txt`: \n")
    assert drafts[0].endswith("files:\n- file `a.txt`: \n")
    assert not any("The move changes" in d for d in drafts)


def test_the_two_tabs_and_a_file_as_a_move_leaves_it(tutored: TestClient) -> None:
    "Do mode at the start of step-01: Next change is move 1, Last change is none, and a new file of move 1 can be read as it leaves it."
    auth = {"t": TOKEN}
    tutored.post("/api/move", params=auth, json={"to": 1})
    changes = tutored.get("/api/state", params=auth).json()["changes"]
    assert changes["last"] is None and changes["next"]["name"] == "step-01.1"
    answer = tutored.get("/api/file", params={**auth, "path": "a.txt", "at": "step-01.1"}).json()
    assert answer["missing"] and answer["next"].endswith("+a") and answer["at_text"]["text"] == "a\n"
    tutored.post("/api/done", params=auth, json={"step": "step-01", "done": 1})
    changes = tutored.get("/api/state", params=auth).json()["changes"]
    assert (changes["last"]["name"], changes["next"]["name"]) == ("step-01.1", "step-01.2")
    tutored.post("/api/show", params=auth, json={"mode": "watch"})
    for number in (1, 2, 3):   # watch mode follows the code, which do mode left at the Start
        assert tutored.post("/api/move", params=auth, json={"to": 1, "move": number}).status_code == 200
    changes = tutored.get("/api/state", params=auth).json()["changes"]
    assert changes["last"]["name"] == "step-01.3" and changes["next"] is None, "the last move of a step: nothing next"


def test_your_files_match_the_move_being_worked_on(tutored: TestClient, tutorial: timewalk.Repo) -> None:
    "Do mode: a file made by hand that equals the move's makes the files match; a different one is listed."
    auth = {"t": TOKEN}
    tutored.post("/api/move", params=auth, json={"to": 1})
    first = tutored.get("/api/match", params=auth).json()
    assert first["move"] == "step-01.1" and first["differ"] == ["a.txt"]
    (tutorial.work / "a.txt").write_text("not quite\n")
    assert tutored.get("/api/match", params=auth).json()["differ"] == ["a.txt"]
    (tutorial.work / "a.txt").write_text("a\n")
    assert tutored.get("/api/match", params=auth).json()["match"] is True
    tutored.post("/api/show", params=auth, json={"mode": "watch"})
    assert tutored.get("/api/match", params=auth).json()["move"] is None, "not in watch mode"


def test_files_match_with_spaces_and_accents_in_names(tutorial: timewalk.Repo, tmp_path: Path) -> None:
    "A move that adds files named with a space and an accent: they differ until the learner makes them, then match."
    work = tutorial.work
    base = run_git(work, "rev-parse", "HEAD")
    (work / "sp ace.txt").write_text("one\n")
    (work / "données.py").write_text("two\n")
    run_git(work, "add", "-A")
    run_git(work, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "-m", "names")
    target = run_git(work, "rev-parse", "HEAD")
    run_git(work, "checkout", "--quiet", base)
    assert tutorial.differ_from(base, target) == ["données.py", "sp ace.txt"]
    (work / "sp ace.txt").write_text("one\n")
    (work / "données.py").write_text("two\n")
    assert tutorial.differ_from(base, target) == []
    (work / "sp ace.txt").unlink()   # made by hand, untracked: in the way of the commit that has them
    (work / "données.py").unlink()
    run_git(work, "checkout", "--quiet", target)
    (work / "sp ace.txt").write_text("edited\n")
    assert tutorial.edits() == ["sp ace.txt"], "a path with a space, as it is, not quoted"


def test_items_end_at_a_heading_and_at_a_command() -> None:
    "Items do not run into the next step, an indented command under an item stays a command, and a blank show: is dropped."
    notes = timewalk.parse_notes("## step-01\nfiles:\n- diff `a.py`: why\n  show: `  `\n  $ just test\n## step-02\n- diff `b.py`: not an item here\n")
    items = notes["step-01"]["parts"][0]["items"]
    assert items == [{"kind": "diff", "move": None, "path": "a.py", "text": "why", "show": []}]
    assert {"kind": "command", "track": "replay", "text": "just test"} in notes["step-01"]["parts"]
    assert notes["step-02"]["parts"] == [{"kind": "text", "text": "- diff `b.py`: not an item here"}]


def test_an_item_naming_another_moves_name_is_checked(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "An item may name an earlier move of the walk; a name that is no move at all is a warning."
    from timewalk.walks import check, load_toc

    (tutorial_class / "notes.md").write_text(
        "## step-00\n\n## step-01\n### step-01.1 A\n$ ls\n### step-01.2 B\nfiles:\n- diff step-01.1 `a.txt`: the first file.\n"
        "- diff step-09.9 `x.py`: a typo.\n$ ls\n### step-01.3 C\n$ ls\n\n## step-02\n")
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("names step-09.9, which is not a move of the walk" in w for w in warnings)
    assert not any("a.txt" in w for w in unsourced(warnings))


def test_moves_go_in_order_and_back_is_the_start(tutored: TestClient, tutorial: timewalk.Repo) -> None:
    "A move has no just setup of its own: forward one move at a time, in both modes; back only to the step's Start."
    auth = {"t": TOKEN}
    tutored.post("/api/move", params=auth, json={"to": 1})
    skip = tutored.post("/api/move", params=auth, json={"to": 1, "move": 2})
    assert skip.status_code == 409 and "the next is step-01.1" in skip.json()["error"]
    assert tutored.post("/api/done", params=auth, json={"step": "step-01", "done": 2}).status_code == 409, "Done skips nothing"
    assert tutored.post("/api/done", params=auth, json={"step": "step-01", "done": 1}).status_code == 200
    assert tutored.post("/api/done", params=auth, json={"step": "step-01", "done": 0}).status_code == 409, "back is the Start"
    tutored.post("/api/show", params=auth, json={"mode": "watch"})
    tutored.post("/api/move", params=auth, json={"to": 1, "move": 1})
    tutored.post("/api/move", params=auth, json={"to": 1, "move": 2})
    assert tutored.post("/api/move", params=auth, json={"to": 1, "move": 1}).status_code == 409, "not back one move"
    assert tutored.post("/api/move", params=auth, json={"to": 1, "move": 0}).status_code == 200, "back to the Start"
    state = tutored.get("/api/state", params=auth).json()
    assert (state["move"], state["done"]) == (0, 0)
    assert run_git(tutorial.work, "rev-parse", "HEAD") == tutorial.steps[0].sha


BLOCK_NOTES = """# t, one move at a time

The opening.

## step-00 Start

time: 0:00

$ just setup

## step-01 Three moves

### step-01.1 A file a

::: do
Make `a.txt` with one line, `a`.

```text
:::
a line of code, kept as it is

the blank line above stays
```

$ cat a.txt
:::

::: watch
The move adds `a.txt`.
:::

> Say: the presenter's line,
which goes on here.

files:
- file `a.txt`: the first file.

### step-01.2 Files b

patch$ README.md
patch$ b.txt

files:
- diff `README.md`: the title.
  show: `# t, with b`

$ ls

### step-01.3 File c

patch$ c.txt

It is short.

$ ls

## step-02 D
"""


def test_blocks_for_one_mode_are_read_with_their_rules() -> None:
    "::: do and ::: watch open a block and ::: closes it; a heading or a block inside one is an error, and fenced code is code."
    text = "## step-01\n::: do\na\n```\n:::\n```\n:::\n::: {.watch}\nb\n:::\n::: callout-note\nc\n:::\n:::\n"
    blocks = timewalk.note_blocks(text)
    assert [blocks.mode[i] for i in (2, 4, 8, 11)] == ["do", "do", "watch", None]
    assert blocks.fences == {1, 6, 7, 9}, "a ::: in fenced code closes nothing"
    assert blocks.errors == []
    assert [line for line, _ in blocks.warnings] == [11, 14], "a div of another name, and a ::: that closes nothing"
    assert blocks.mode[5] == "do", "the fence of the code closes the code, and the block goes on"
    broken = timewalk.note_blocks("## step-01\n::: do\na\n::: watch\nb\n### step-01.1 x\n::: do\nc\n")
    assert [line for line, _ in broken.errors] == [4, 2, 7], "a block in a block, a heading in a block, a block not closed"
    assert "holds no other block" in broken.errors[0][1] and "not closed before the heading at line 6" in broken.errors[1][1]


def test_the_parts_of_a_block_carry_its_mode() -> None:
    "Text, commands and files: items inside a block are for its mode only; the lines ::: are not text."
    parts = timewalk.parse_notes(BLOCK_NOTES)["step-01"]["parts"]
    do = [p for p in parts if p.get("mode") == "do"]
    assert [p["kind"] for p in do] == ["text", "command"]
    assert do[0]["text"].startswith("Make `a.txt`") and ":::\na line of code" in do[0]["text"], "fenced code in a block is kept whole"
    assert [p["text"] for p in parts if p.get("mode") == "watch"] == ["The move adds `a.txt`."]
    assert not any(line == ":::" for p in parts if p["kind"] == "text" for line in p["text"].split("\n") if p.get("mode") is None)
    assert timewalk.parse_notes(BLOCK_NOTES)["step-01"]["commands"][0] == {"track": "replay", "text": "cat a.txt", "mode": "do"}
    shared = timewalk.parse_notes("## step-01\n### step-01.1 A\nfiles:\n- file `a.txt`: both\n::: do\n- file `b.txt`: do\n:::\n")
    files = [p for p in shared["step-01"]["parts"] if p["kind"] == "files"]
    assert len(files) == 1 and [(i["path"], i.get("mode")) for i in files[0]["items"]] == [("a.txt", None), ("b.txt", "do")], \
        "the items of one files: line go on into a block, each for its mode"
    inner = timewalk.parse_notes("## step-01\n::: do\nA\n::: callout-tip\nB\n:::\nC\n$ just do-only\n:::\nBOTH\n")["step-01"]
    assert inner["commands"] == [{"track": "replay", "text": "just do-only", "mode": "do"}], "a div inside a block does not end it"
    assert [p["text"] for p in inner["parts"] if p["kind"] == "text" and not p.get("mode")] == ["BOTH"]


def test_the_checks_of_blocks_and_of_each_mode(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "timewalk-check reports a block not closed, and checks each mode's items and commands as the reader of that mode sees them."
    from timewalk.walks import check, load_toc

    (tutorial_class / "notes.md").write_text(BLOCK_NOTES)
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert errors == []
    assert "walk tut: step-01.1 has no command in watch mode in notes.md; add one that shows what the move did, for example $ just test" in warnings
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES.replace("::: watch\nThe move adds `a.txt`.\n:::",
                                                                 "::: watch\nThe move adds `a.txt`.\nfiles:\n- file `z.txt`: no such file.\n"))
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("notes.md:" in e and "::: watch is not closed before the heading" in e for e in errors), errors
    assert "walk tut: step-01.1: the file item z.txt is not in the move's commit" in warnings, "an item in a block is checked in its mode"
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES.replace("::: watch", "::: show"))
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("::: show is not a mode" in w for w in warnings)


def test_timewalk_notes_drafts_a_block_for_each_mode(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A drafted move has, in order: the problem and the change for both modes, a ::: do and a ::: watch block, the check, and files: last."
    from timewalk.walks import add_move_sections, load_toc

    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n\n## step-02\n")
    text, _, drafts = add_move_sections(load_toc(tutorial_class / "toc.toml")[0], tutorial.main)
    for draft in drafts:
        order = [draft.index(mark) for mark in ("For both modes", "patch$ ", "::: do\n", "::: watch\n", "A command that checks", "files:")]
        assert order == sorted(order), draft
    assert "\npatch$ a.txt\n" in drafts[0], "a patch$ line for each file of the commit, in the text for both modes"
    assert [p["path"] for p in timewalk.parse_notes(text)["step-01"]["parts"] if p["kind"] == "patch"] == ["a.txt", "README.md", "b.txt", "c.txt"]
    assert timewalk.note_blocks(text).errors == []
    assert timewalk.parse_notes(text)["step-01"]["commands"] == [], "the example command is in a comment, not a command"


def test_the_pdf_prints_both_blocks_under_their_mode() -> None:
    "The PDF of the notes has no mode: it prints each block under a line that names it."
    from timewalk.slides_pdf import notes_markdown

    out = notes_markdown("both\n::: do\ntype\n$ just a\n:::\n::: watch\nsee\n:::\nend")
    assert "*In do mode:*\n\ntype\n```\n$ just a\n```" in out and "*In watch mode:*\n\nsee" in out and ":::" not in out


def run_export(
    main: Path,  # The project
    *args: str,  # The options of timewalk-export
) -> subprocess.CompletedProcess:  # What it did
    "Run timewalk-export as a command."
    return subprocess.run([sys.executable, "-c", "from timewalk.export import main; main()", str(main), *args], capture_output=True, text=True)


def test_timewalk_export_writes_a_markdown_file_for_each_mode(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each file keeps its mode's blocks, drops the other's and the private lines, and links each step, move and item to GitHub."
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES)
    out = tutorial_class / "out"
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--markdown", str(out), "--github", "https://github.com/o/t")
    assert done.returncode == 0, done.stderr
    do, watch = (out / "TUTORIAL-do.md").read_text(), (out / "TUTORIAL-watch.md").read_text()
    url, shas = "https://github.com/o/t", {m.name: m.sha for found in tutorial.moves for m in found}
    assert "Make `a.txt`" in do and "The move adds `a.txt`." not in do
    assert "The move adds `a.txt`." in watch and "Make `a.txt`" not in watch and "cat a.txt" not in watch
    assert "a line of code, kept as it is\n\nthe blank line above stays" in do, "fenced code keeps its blank lines"
    for text in (do, watch):
        assert "Say:" not in text and "which goes on here" not in text and "time:" not in text, "the private lines go"
        assert ":::" not in text.replace(":::\na line", "")
        assert text.startswith("<!-- Made by timewalk-export") and "# t, one move at a time" in text and "The opening." in text
        assert f"# step-00 Start\n\n[The code at step-00]({url}/tree/step-00)" in text, "the first step is where the walk starts"
        start, end = tutorial.steps[0].sha, tutorial.steps[1].sha
        assert (f"[The code at its Start]({url}/tree/{start}), the commit before step-01.1. "
                f"[The code at step-01]({url}/tree/step-01) is the code after its moves.") in text, "do mode starts a whole step from its Start"
        assert f"# step-02 D\n\n[The code at step-02]({url}/tree/step-02)\n" in text, "the page keeps a step of one commit at its tag"
        assert f"tree/{end}" not in text
        assert f"### step-01.2 Files b\n\n[The commit of this move]({url}/commit/{shas['step-01.2']}) shows what the move changes." in text
        assert f"- [`a.txt`]({url}/blob/{shas['step-01.1']}/a.txt): the first file." in text
        digest = hashlib.sha256(b"README.md").hexdigest()
        assert f"- [`README.md`]({url}/blob/{shas['step-01.2']}/README.md), [its change]({url}/commit/{shas['step-01.2']}#diff-{digest})" in text
        assert "  - look at: `# t, with b`" in text
        assert "```bash\nls\n```" in text and "$ ls" not in text
        change = text.index("The change of `README.md`:\n\n```diff\n@@")
        assert text.index("  - look at: `# t, with b`") < change < text.index("```bash\nls\n```"), "the diff comes after its list, before the command"
        assert "+# t, with b" in text[change:] and "diff --git" not in text and "index " not in text[change:change + 200]
        assert "The change of `a.txt`" not in text, "a file item shows no change"
        patched = f"[`c.txt`]({url}/commit/{shas['step-01.3']}#diff-{hashlib.sha256(b'c.txt').hexdigest()})"
        if text is do:
            assert (f"Apply the patch to {patched}, from the top of the project:\n\n```bash\n"
                    f"curl -sL {url}/commit/{shas['step-01.3']}.diff | git apply --include=c.txt\n```") in text
        else:
            assert f"A patch to {patched}." in text, "watch mode: where the diff is"
        assert "patch$" not in text
    assert "[The same tutorial in watch mode](TUTORIAL-watch.md)" in do and "[The same tutorial in do mode](TUTORIAL-do.md)" in watch
    assert "```bash\ncat a.txt\n```" in do


def test_timewalk_export_writes_a_website_with_a_section_for_each_mode(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A page for each step in each mode, a sidebar whose move links use the heading's id, and only its own pages rendered."
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES)
    site = tutorial_class / "site"
    args = ["--toc", str(tutorial_class / "toc.toml"), "--site", str(site), "--github", "https://github.com/o/t", "--title", "T"]
    assert run_export(tutorial.main, *args).returncode == 0
    for mode, other in (("do", "watch"), ("watch", "do")):
        page = (site / mode / "step-01.qmd").read_text()
        assert page.startswith('---\ntitle: "step-01: Three moves"\n---\n')
        assert f"[The same step in {other} mode](../{other}/step-01.qmd)" in page
        assert "\n## step-01.1 A file a {#step-01.1}\n" in page, "a move is a section of its step's page, with the move as its id"
    assert "Make `a.txt`" in (site / "do/step-01.qmd").read_text() and "Make `a.txt`" not in (site / "watch/step-01.qmd").read_text()
    config = (site / "_smalldocs.yml").read_text()
    assert 'render: ["index.qmd", "do/*.qmd", "watch/*.qmd"]' in config and "theme: cosmo" in config
    assert "href: do/step-01.qmd#step-01.1" in config and "href: watch/step-02.qmd" in config
    assert "- [step-01: Three moves](do/step-01.qmd)" in (site / "index.qmd").read_text()
    (site / "do" / "step-09.qmd").write_text("<!-- Made by timewalk-export from the notes of the walk. Do not edit it by hand: run timewalk-export again. -->\n")
    (site / "do" / "mine.qmd").write_text("a page of mine\n")
    assert run_export(tutorial.main, *args).returncode == 0
    assert not (site / "do" / "step-09.qmd").exists() and (site / "do" / "mine.qmd").exists(), "only its own old pages go"
    assert run_export(tutorial.main, *args, "--quarto").returncode == 0
    assert "format:\n  html:\n    theme: cosmo" in (site / "_quarto.yml").read_text()


def test_timewalk_export_never_writes_over_files_it_did_not_make(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A file of the same name that it did not write, or a folder inside the project, stops it with nothing written."
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES)
    out = tutorial_class / "out"
    out.mkdir()
    (out / "TUTORIAL-do.md").write_text("my own tutorial\n")
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--markdown", str(out), "--github", "https://github.com/o/t")
    assert done.returncode != 0 and "did not make it" in done.stderr
    assert (out / "TUTORIAL-do.md").read_text() == "my own tutorial\n" and not (out / "TUTORIAL-watch.md").exists()
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--markdown", str(tutorial.main / "docs"), "--github", "https://github.com/o/t")
    assert done.returncode != 0 and "inside the project" in done.stderr and not (tutorial.main / "docs").exists()
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--markdown", str(out))
    assert done.returncode != 0 and "--github" in done.stderr, "with no origin on GitHub, it asks for the address"
    (out / "TUTORIAL-do.md").write_text("<!-- Made by timewalk-tutorial from the notes of the walk. Do not edit it by hand: run timewalk-tutorial again. -->\n")
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--markdown", str(out), "--github", "https://github.com/o/t")
    assert done.returncode == 0 and "timewalk-export" in (out / "TUTORIAL-do.md").read_text(), "a file made under its old name is its own"


def test_timewalk_export_drops_every_form_of_private_line_and_keeps_code(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "The cue rule is the walk skill's: fences as CommonMark reads them, the whole blockquote, a comment kept closed; code as written."
    from timewalk.export import for_mode

    cases = {
        "a fence of four holding three": "````\n```\n````\n\n> Say: LEAK-A\n",
        "backticks in prose": "```not a fence``` here.\n\n> Say: LEAK-B\n",
        "the start of the blockquote": "> Intro LEAK-C\nlazy LEAK-C\n> Say: x\n",
        "an indented fence is not one": "    ```\n\n> Say: LEAK-E\n",
        "a line that goes on": "> Say: x\n#hashtag LEAK-F\n",
        "a cue in a comment": "<!-- > Say: x\nLEAK-I\n-->\n",
        "a cue in a list and bold": "- [ ] > **Note** : LEAK-J\n",
        "a planned time": "  time: 1:30\n",
    }
    for what, text in cases.items():
        out = for_mode("## step-01\n\n" + text, "do")
        assert "LEAK" not in out and "Say" not in out and "Note" not in out and "time:" not in out, (what, out)
    kept = for_mode("## step-01\n\nBefore.\n\n<!--\n> Say: x\n-->\n\nVisible after.\n", "do")
    assert "<!--\n-->" in kept and "Visible after." in kept, "a comment that a kept line opened is closed again"
    closed = for_mode("## step-01\n\n::: do\nDo this.\n\n> Say: x\n:::\nShared after.\n\n$ just test\n", "do")
    assert "Shared after." in closed and "Say" not in closed, "a block's fence ends a cue's blockquote, so the text after it stays"
    code = "## step-01\n\n```python\ndef f():\n\n\n    return 1\n> Say: in code, kept\n```\n"
    assert for_mode(code, "watch").endswith("def f():\n\n\n    return 1\n> Say: in code, kept\n```"), "fenced code is kept exactly"


def test_timewalk_export_links_items_to_the_right_change(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "An item naming another move links there; a diff item of a step links to the step's whole change; paths are quoted in links."
    from timewalk.export import build, read_sections

    steps = {s.name: s for s in tutorial.steps}
    moves = {m.name: m for found in tutorial.moves for m in found}
    url = "https://github.com/o/t"
    text = ("## step-01 S\n\nfiles:\n- diff `README.md`: the step's change\n\n### step-01.2 B\n\nfiles:\n- file step-01.1 `a.txt`: from move 1\n"
            "- file `my file (1).py`: odd\n\npatch$ `my file (1).py`\n\n### step-01.3 C\n\nPress **Apply the whole move**, then run `just lint`.\n\n"
            "## step-07 Not a tag\n\n$ rm -rf ghost\n")
    warned: set[str] = set()
    _, _, sources = read_sections(text)
    found = build(sources, tutorial.steps, tutorial.moves, url, 3, warned)
    body = found[1].body
    digest = hashlib.sha256(b"README.md").hexdigest()
    assert f"[its change]({url}/compare/{steps['step-00'].sha}...{steps['step-01'].sha}#diff-{digest})" in body
    assert f"[`a.txt`]({url}/blob/{moves['step-01.1'].sha}/a.txt)" in body
    assert f"({url}/blob/{moves['step-01.2'].sha}/my%20file%20%281%29.py)" in body
    assert "Press **Apply the whole move**, then run `just lint`." in body, "a sentence goes as written: no Apply is read in it"
    assert "| git apply --include='my file (1).py'" in body, "a path with a space, quoted for the shell"
    assert "ghost" not in "".join(s.body for s in found) and "step-07" not in [s.name for s in found], "a section of no step is left out"


def test_timewalk_export_starts_a_selected_whole_step_on_its_start(tutorial: timewalk.Repo) -> None:
    "A walk that selects a whole step with moves first links that step's Start, as the page stands it there; a step of one commit, its tag."
    from timewalk.export import build, read_sections

    url, every = "https://github.com/o/t", tutorial.steps
    _, _, sources = read_sections("## step-01 S\n\none\n\n### step-01.1 A\n\n### step-01.2 B\n\n### step-01.3 C\n\n## step-02 T\n\ntwo\n")
    steps, moves = timewalk.select_steps(tutorial.main, "step-*", ["step-01", "step-02"], True)
    first, second = build(sources, steps, moves, url, 3, set(), None, None, tutorial=True)
    assert first.body.startswith(f"[The code at its Start]({url}/tree/{every[0].sha}), the commit before step-01.1. "
                                 f"[The code at step-01]({url}/tree/step-01) is the code after its moves.\n"), "the first stop too"
    assert second.body.startswith(f"[The code at step-02]({url}/tree/step-02)\n"), "no moves: the page stays at the tag"
    steps, moves = timewalk.select_steps(tutorial.main, "step-*", ["step-01"], False)
    story = build(sources, steps, moves, url, 3, set(), None, None)
    assert story[0].body.startswith(f"[The code at step-01]({url}/tree/step-01)\n"), "a narrative shows a step at its tag"


def test_timewalk_export_writes_nothing_when_one_file_is_not_its_own(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Every file is checked first: a foreign TUTORIAL-watch.md, a folder in a file's place, a link into the project or the replay copy."
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES)
    out, site = tutorial_class / "out", tutorial_class / "site"
    args = ["--toc", str(tutorial_class / "toc.toml"), "--markdown", str(out), "--site", str(site), "--github", "https://github.com/o/t"]
    out.mkdir()
    (out / "TUTORIAL-watch.md").write_text("mine\n")
    done = run_export(tutorial.main, *args)
    assert done.returncode != 0 and not (out / "TUTORIAL-do.md").exists() and not site.exists()
    (out / "TUTORIAL-watch.md").unlink()
    (out / "TUTORIAL-watch.md").mkdir()
    assert run_export(tutorial.main, *args).returncode != 0 and not (out / "TUTORIAL-do.md").exists()
    (out / "TUTORIAL-watch.md").rmdir()
    site.mkdir()
    (site / "do").symlink_to(tutorial.main)
    done = run_export(tutorial.main, *args)
    assert done.returncode != 0 and "inside the project" in done.stderr and not list(tutorial.main.glob("*.qmd"))
    (site / "do").unlink()
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--markdown", str(tutorial.work / "out"), "--github", "x")
    assert done.returncode != 0 and not (tutorial.work / "out").exists(), "nor the replay copy"
    assert run_export(tutorial.main, *args).returncode == 0


def test_timewalk_export_finds_the_github_address_of_the_origin(tutorial: timewalk.Repo) -> None:
    "Every form of a GitHub remote gives the same address."
    from timewalk.export import github_url

    for origin in ("git@github.com:o/p.git", "https://github.com/o/p", "https://user@github.com/o/p.git", "ssh://git@github.com:22/o/p.git",
                   "ssh://git@github.com/o/p", "https://github.com/o/p/"):
        run_git(tutorial.main, "remote", "remove", "origin") if "origin" in run_git(tutorial.main, "remote") else None
        run_git(tutorial.main, "remote", "add", "origin", origin)
        assert github_url(tutorial.main, None) == "https://github.com/o/p", origin


def test_a_narrative_with_a_do_block_is_told_it_never_shows(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A narrative shows the watch blocks; its do blocks never show, and timewalk-check says so."
    from timewalk.walks import check, load_toc

    (tutorial_class / "toc.toml").write_text('[[walk]]\nid = "n"\nkind = "narrative"\nnotes = "notes.md"\nslides = "slides/slides.toml"\n')
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n::: do\nnever\n:::\n\n## step-02\n")
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("a narrative has no do mode" in w for w in warnings)


def test_a_selection_takes_steps_moves_and_ranges_in_order(tutorial: timewalk.Repo) -> None:
    "A whole step keeps its moves and its Start is the tag before; a move or a range is a small step, from the commit before it."
    shas = {m.name: m.sha for found in tutorial.moves for m in found}
    tags = {s.name: s.sha for s in tutorial.steps}
    steps, moves = timewalk.select_steps(tutorial.main, "step-*", ["step-01.1", "step-01.2..step-01.3", "step-02"], True)
    assert [s.name for s in steps] == ["step-01.1", "step-01.2 to step-01.3", "step-02"]
    assert [[m.name for m in found] for found in moves] == [["step-01.1"], ["step-01.2", "step-01.3"], []]
    assert [s.start for s in steps] == [tags["step-00"], shas["step-01.1"], tags["step-01"]]
    assert [s.section for s in steps] == ["step-01", "step-01", ""] and steps[1].sha == tags["step-01"]
    whole, _ = timewalk.select_steps(tutorial.main, "step-*", ["step-01"], True)
    assert whole[0].start == tags["step-00"] and whole[0].section == ""
    story, story_moves = timewalk.select_steps(tutorial.main, "step-*", ["step-00", "step-01.2", "step-01"], False)
    assert [s.name for s in story] == ["step-00", "step-01.2", "step-01"] and story_moves == [[], [], []] and story[1].sha == shas["step-01.2"]
    with pytest.raises(timewalk.GitError, match="out of order"):
        timewalk.select_steps(tutorial.main, "step-*", ["step-01", "step-01.2"], False)   # a narrative's step is its tag, after its moves
    for wrong, said in ((["step-01.2", "step-01.1"], "out of order"), (["step-01", "step-01.2"], "out of order"),
                        (["step-01.1..step-01.2", "step-01.2"], "out of order"), (["step-09"], "not a step or a move"),
                        (["step-01.3..step-01.1"], "goes forward inside one step"), (["step-01.1..step-02"], "from a move to a move")):
        with pytest.raises(timewalk.GitError, match=said):
            timewalk.select_steps(tutorial.main, "step-*", wrong, True)
    with pytest.raises(timewalk.GitError, match="a narrative takes steps and single moves"):
        timewalk.select_steps(tutorial.main, "step-*", ["step-01.1..step-01.2"], False)


def test_a_small_step_starts_at_the_commit_before_its_first_move(tutorial: timewalk.Repo) -> None:
    "Move 0 of a small step is the commit before its first move; Last change and the reader's span start there too."
    tutorial.select("step-*", tutorial=True, select=["step-01.2..step-01.3"])
    shas = {m.name: m.sha for found in tutorial.moves for m in found}
    assert tutorial.commit_at(0, 0) == tutorial.steps[0].start
    tutorial.move(0, move=0)
    assert run_git(tutorial.work, "rev-parse", "HEAD") == tutorial.steps[0].start
    tutorial.move(0, move=1)
    assert run_git(tutorial.work, "rev-parse", "HEAD") == shas["step-01.2"] and tutorial.span() == (tutorial.steps[0].start, shas["step-01.2"])
    assert tutorial.changes(0) == {"README.md": "M", "b.txt": "A", "c.txt": "A"}, "a small step's change is from its Start"


def test_the_saved_branch_of_a_small_step_is_a_name_git_takes(tutorial: timewalk.Repo) -> None:
    "A small step's name has spaces; the branch that keeps a learner's work there is still a name that git takes."
    tutorial.select("step-*", tutorial=True, select=["step-01.1..step-01.2", "step-02"])
    tutorial.move(0, move=0)
    (tutorial.work / "README.md").write_text("my own\n")
    tutorial.move(1)
    assert tutorial.kept == ["timewalk/saved/step-01.1-to-step-01.2"]


def test_a_walk_reads_its_small_steps_from_the_notes_of_the_whole() -> None:
    "A small step shows the commands at the top of its step and its moves' sections; a narrative shows a step before its moves."
    notes = timewalk.parse_notes("## step-01 S\n\n$ just setup\n\nAbout the whole step.\n\n### step-01.1 A\nmove a\n$ cat a\n\n"
                                 "### step-01.2 B\nmove b\n\n### step-01.3 C\nmove c\n")
    a, b = timewalk.Move("step-01.1", "1" * 40, ""), timewalk.Move("step-01.2", "2" * 40, "")
    small = timewalk.Step(0, "step-01.1 to step-01.2", "2" * 40, "", "", "0" * 40, "step-01")
    view = timewalk.walk_notes(notes, [small], [[a, b]], True)["step-01.1 to step-01.2"]
    assert view["derived"] and view["raw"] == ""
    assert [p.get("name") or p["text"] for p in view["parts"]] == ["just setup", "step-01.1", "move a", "cat a", "step-01.2", "move b"]
    stop = timewalk.Step(0, "step-01.2", "2" * 40, "", "", "1" * 40, "step-01")
    whole = timewalk.Step(1, "step-01", "3" * 40, "", "")
    story = timewalk.walk_notes(notes, [stop, whole], [[], []], False, True)
    assert [p.get("text") for p in story["step-01.2"]["parts"]] == ["just setup", "move b"] and story["step-01.2"]["title"] == "B"
    assert [p["kind"] for p in story["step-01"]["parts"]] == ["command", "text"], "a narrative's step stops before its moves"
    assert story["step-01"]["derived"], "so its notes are edited in the whole tutorial"
    assert timewalk.walk_notes(notes, [stop, whole], [[], []], False)["step-01"] is notes["step-01"], "a narrative that does not select shows all"
    bare = timewalk.parse_notes("## step-01 S\n\n$ just setup\n")
    assert timewalk.walk_notes(bare, [stop], [[]], False, True)["step-01.2"]["parts"] == [bare["step-01"]["parts"][0]], \
        "a section with no moves yet gives its setup, and no crash"


def test_the_checks_of_a_walk_that_selects(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A walk that selects reads the notes of the whole: a section it takes and the notes lack is an error; the rest is not its own."
    from timewalk.walks import TocError, check, load_toc

    toc = tutorial_class / "toc.toml"
    toc.write_text(toc.read_text() + '\n[[walk]]\nid = "part"\nkind = "tutorial"\nnotes = "notes.md"\nslides = "slides/slides.toml"\n'
                   'select = ["step-01.2..step-01.3"]\n')
    errors, warnings = check(load_toc(toc), tutorial.main, tutorial_class)
    assert (errors, [w for w in unsourced(warnings) if "part" in w]) == ([], [])
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\n$ ls\n### step-01.3 C\n$ ls\n\n## step-02\n")
    errors, _ = check(load_toc(toc), tutorial.main, tutorial_class)
    assert "walk part: step-01.2 to step-01.3 takes the section ### step-01.2, and notes.md has none" in errors
    for bad, said in (('select = "step-01"', "select must be a list"), ('steps = ["step-01"]\nselect = ["step-01"]', "steps or select"),
                      ('select = ["step-01"]\nintro = "nope.md"', "its intro nope.md does not exist")):
        toc.write_text(f'[[walk]]\nid = "x"\nkind = "narrative"\nnotes = "notes.md"\n{bad}\n')
        with pytest.raises(TocError, match=said):
            load_toc(toc)


def test_timewalk_notes_drafts_what_a_small_walk_takes_into_the_notes_of_the_whole(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A walk that selects drafts the sections of its steps and moves only, in the order of the whole."
    from timewalk.walks import add_move_sections, load_toc

    toc = tutorial_class / "toc.toml"
    toc.write_text('[[walk]]\nid = "part"\nkind = "tutorial"\nnotes = "notes.md"\nselect = ["step-01.2"]\n')
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-02\n")
    text, added, _ = add_move_sections(load_toc(toc)[0], tutorial.main)
    assert added == ["step-01", "step-01.2"] and text.index("## step-01") < text.index("### step-01.2") < text.index("## step-02")
    assert "### step-01.1" not in text


def test_timewalk_export_writes_a_small_walk_and_a_narrative(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A small step starts at the commit before its first move, with its step's setup; a narrative has one file and one site."
    shas = {m.name: m.sha for found in tutorial.moves for m in found}
    (tutorial_class / "notes.md").write_text("# The whole\n\nThe opening.\n\n## step-00 Start\n\n## step-01 Three moves\n\n$ just setup\n\n"
                                             "About all three.\n\n### step-01.1 A\nmove a\n\n### step-01.2 B\nmove b\n$ ls\n\n"
                                             "### step-01.3 C\nmove c\n$ ls\n\n## step-02 D\n\nThe last.\n")
    (tutorial_class / "intro.md").write_text("Only two moves.\n")
    toc = tutorial_class / "toc.toml"
    toc.write_text('[[walk]]\nid = "part"\ntitle = "Part"\nkind = "tutorial"\nnotes = "notes.md"\nintro = "intro.md"\n'
                   'select = ["step-01.2..step-01.3"]\n\n[[walk]]\nid = "story"\ntitle = "Story"\nkind = "narrative"\nnotes = "notes.md"\n'
                   'select = ["step-01.2", "step-01"]\n')
    url = "https://github.com/o/t"
    done = run_export(tutorial.main, "--toc", str(toc), "--walk", "part", "--markdown", str(tutorial_class / "part"),
                        "--site", str(tutorial_class / "part-site"), "--github", url)
    assert done.returncode == 0, done.stderr
    do = (tutorial_class / "part" / "TUTORIAL-do.md").read_text()
    assert do.index("# Part") < do.index("Only two moves.") and "The opening." not in do, "the walk's intro takes the place of the opening"
    assert f"## step-01.2 to step-01.3\n\n[The code at its Start]({url}/tree/{shas['step-01.1']}), the commit before step-01.2." in do
    assert "```bash\njust setup\n```" in do and "About all three." not in do and "move a" not in do and "move b" in do
    assert (tutorial_class / "part-site" / "do" / "step-01.2-to-step-01.3.qmd").exists()
    done = run_export(tutorial.main, "--toc", str(toc), "--walk", "story", "--markdown", str(tutorial_class / "story"),
                        "--site", str(tutorial_class / "story-site"), "--github", url)
    assert done.returncode == 0, done.stderr
    story = (tutorial_class / "story" / "TUTORIAL.md").read_text()
    assert not (tutorial_class / "story" / "TUTORIAL-do.md").exists() and "do mode" not in story
    assert "## step-01 Three moves" in story and "About all three." in story and "move a" not in story, "a step stops before its moves"
    assert f"## step-01.2 B\n\n[The code at step-01.2]({url}/tree/{shas['step-01.2']})" in story and "move b" in story
    assert sorted(p.name for p in (tutorial_class / "story-site").glob("*.qmd")) == ["index.qmd", "step-01.2.qmd", "step-01.qmd"]


def test_a_selecting_walk_on_the_page_its_stops_tabs_and_slides(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Next change of a stop goes from the next stop's Start; a stop at a move with no slides shows its step's; an item may name any move."
    from timewalk.walks import load_toc

    toc = tutorial_class / "toc.toml"
    toc.write_text(toc.read_text() + '\n[[walk]]\nid = "story"\nkind = "narrative"\nnotes = "notes.md"\nslides = "slides/slides.toml"\n'
                   'select = ["step-01.1", "step-02"]\n')
    shas = {m.name: m.sha for found in tutorial.moves for m in found}
    tags = {s.name: s.sha for s in tutorial.steps}
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    auth = {"t": TOKEN}
    assert client.post("/api/walk", params=auth, json={"id": "story"}).status_code == 200
    state = client.get("/api/state", params=auth).json()
    assert [s["name"] for s in state["steps"]] == ["step-01.1", "step-02"] and state["current"] == 0
    assert state["changes"]["next"] == {"name": "step-02", "before": tags["step-01"], "after": tags["step-02"]}, "from the next stop's Start"
    assert state["changes"]["last"]["before"] == tags["step-00"], "a stop at a move shows what the move changed"
    assert tutorial.span() == (tags["step-00"], shas["step-01.1"]), "the reader's span too, from the stop's Start"
    assert state["slides"] == ["slides/deck.md#3"], "the move's own slide"
    tutorial.select("step-*", tutorial=False, select=["step-01.2"])
    assert tutorial.move_named("step-01.1").sha == shas["step-01.1"], "an item may name a move that the walk does not take"


def test_a_stop_with_no_slides_of_its_own_shows_its_steps(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A narrative's stop at a move that the slides do not name shows the slides of its step."
    from timewalk.walks import load_toc

    (tutorial_class / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\nstep-02 = ["deck.md#6"]\n')
    toc = tutorial_class / "toc.toml"
    toc.write_text('[[walk]]\nid = "story"\nkind = "narrative"\nnotes = "notes.md"\nslides = "slides/slides.toml"\nselect = ["step-01.1", "step-02"]\n')
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    tutorial.select("step-*", tutorial=False, select=["step-01.1", "step-02"])
    tutorial.move(0)
    assert client.get("/api/state", params={"t": TOKEN}).json()["slides"] == ["slides/deck.md#2"]


def test_the_start_of_a_stop_is_a_place_after_a_restart(tutorial: timewalk.Repo) -> None:
    "A copy at the Start of a small step, with no place remembered, is at move 0 of that step, and not at no step."
    tutorial.select("step-*", tutorial=True, select=["step-01.2..step-01.3"])
    run_git(tutorial.work, "checkout", "--quiet", "--detach", tutorial.steps[0].start)
    tutorial.at = None
    assert tutorial.position() == (0, 0)
    tutorial.move(0, move=1)
    assert tutorial.at == (0, 1)
    tutorial.select("step-*", tutorial=True, select=["step-01.1"])
    assert tutorial.at is None, "a new selection forgets the place of the last"


def test_a_walk_that_selects_never_moves_a_repository_in_place(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Started in place, or with walks that have errors, timewalk does not move the copy to the first stop."
    toc = tutorial_class / "toc.toml"
    toc.write_text('[[walk]]\nid = "part"\nkind = "tutorial"\nnotes = "nope.md"\nselect = ["step-01.2"]\n')
    head = run_git(tutorial.main, "rev-parse", "HEAD")
    done = subprocess.run([sys.executable, "-m", "timewalk", str(tutorial.main), "--in-place", "--toc", str(toc), "--no-open", "--port", "8791"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and "errors" in done.stderr
    assert run_git(tutorial.main, "rev-parse", "HEAD") == head and run_git(tutorial.main, "symbolic-ref", "--short", "HEAD") == "main"


def test_drafted_moves_go_in_the_order_of_the_commits() -> None:
    "A new move section goes before the first section written after it; the sections already written stay as they are."
    from timewalk.walks import place_moves

    raw = "\n$ just setup\n\n### step-05.4 D\nd\n\n### step-05.6 F\n```\n### step-05.5 in code\n```\n"
    out = place_moves(raw, "step-05", {"step-05.5": "### step-05.5 E\ne\n", "step-05.1": "### step-05.1 A\na\n", "step-05.9": "### step-05.9 Z\n"})
    assert [line for line in out.split("\n") if line.startswith("### ")] == ["### step-05.1 A", "### step-05.4 D", "### step-05.5 E",
                                                                             "### step-05.6 F", "### step-05.5 in code", "### step-05.9 Z"]


def test_the_pdf_of_a_walk_that_selects_has_its_stops() -> None:
    "A small step has the slides of its moves, the setup of its step and its moves' notes; a whole step has its moves' slides."
    from timewalk.slides_pdf import selected_stops

    parsed = timewalk.parse_notes("## step-05 S\n\n$ just setup\n\nAbout.\n\n### step-05.1 A\na\n\n### step-05.2 B\nb\n\n### step-05.3 C\nc\n")
    slides = {"step-05": ["s.md#1"], "step-05.2": ["m.md#2"], "step-05.3": ["m.md#3"]}
    stops = selected_stops(["step-05.2..step-05.3"], True, parsed, slides, True)
    assert [(s["name"], s["slides"]) for s in stops] == [("step-05.2 to step-05.3", ["m.md#2", "m.md#3"])]
    assert "just setup" in stops[0]["notes"] and "### step-05.2 B" in stops[0]["notes"] and "About." not in stops[0]["notes"]
    whole = selected_stops(["step-05"], True, parsed, slides, False)[0]
    assert whole["slides"] == ["s.md#1", "m.md#2", "m.md#3"] and whole["title"] == "S"
    story = selected_stops(["step-05.1"], False, parsed, slides, True)[0]
    assert story["slides"] == ["s.md#1"] and story["title"] == "A" and "### step-05.1" not in story["notes"], "a narrative's stop: its step's slide"


def test_a_narrative_site_names_its_pages_whatever_the_tags() -> None:
    "A narrative on tags of its own has its pages, by name, in the list of pages to build."
    from timewalk.export import Section, site_files

    files = site_files("T", "", {None: [Section("parts-01", "One", "a\n", []), Section("parts-02", "", "b\n", [])]}, False)
    assert 'render: ["index.qmd", "parts-01.qmd", "parts-02.qmd"]' in files["_smalldocs.yml"]


def test_timewalk_export_fences_a_change_that_holds_backticks() -> None:
    "A change with a fence of its own in it gets a longer fence, so the diff block is not cut short."
    from timewalk.export import render

    seen = []

    def diff(base: str, sha: str, path: str) -> str:
        seen.append((base, sha, path))
        return "diff --git a/n.md b/n.md\nindex 1..2 100644\n--- a/n.md\n+++ b/n.md\n@@ -1 +1,3 @@\n-old\n+```\n+new\n+```"

    lines = ["files:", "- diff `n.md`: the notes.", "", "Then more."]
    body = "\n".join(line for line, _ in render(lines, "step-01.1", "https://github.com/o/t", {"step-01.1": "abc"}, {}, diff))
    assert seen == [("abc^", "abc", "n.md")], "a move's change is from its parent"
    assert "````diff\n@@ -1 +1,3 @@\n-old\n+```\n+new\n+```\n````" in body and "index 1..2" not in body and "+++ b/n.md" not in body
    assert body.index("````diff") < body.index("Then more.")


def test_timewalk_export_shows_changes_after_their_list_and_only_what_can_be_read() -> None:
    "Fenced code ends a files: list; a binary change has no block; a long change is cut; an item naming a step changes from its Start."
    from timewalk.export import DIFF_LINES, render

    changes = {"x.py": "@@ -1 +1 @@\n-a\n+b", "pic.png": "Binary files /dev/null and b/pic.png differ",
               "long.txt": "@@ -0,0 +1,500 @@\n" + "\n".join(f"+{i}" for i in range(500))}
    seen = []

    def diff(base: str, sha: str, path: str) -> str:
        seen.append((base, path))
        return changes[path]

    lines = ["files:", "- diff `x.py`: x.", "- diff `pic.png`: a picture.", "- diff step-02 `long.txt`: long.", "```python", "code()", "```"]
    body = "\n".join(line for line, _ in render(lines, "step-02.1", "https://github.com/o/t",
                                                 {"step-02.1": "m1", "step-02": "t2"}, {"step-02": "t1"}, diff))
    assert body.index("The change of `x.py`") < body.index("```python"), "the changes come before the code that ends the list"
    assert "The change of `pic.png`" not in body, "a binary file has nothing to read"
    assert ("t1", "long.txt") in seen, "an item that names a step changes from the step's Start"
    assert f"+{DIFF_LINES - 2}\n" in body and f"+{DIFF_LINES + 5}\n" not in body and "more lines: see its change on GitHub" in body


def test_timewalk_export_keeps_cues_out_whatever_the_line_breaks_and_in_the_intro() -> None:
    "Line breaks that splitlines knows do not shift the private lines; a line that only starts with ::: does not end a cue; an intro loses its cues."
    from timewalk.export import for_mode, public_text

    for brk in ["\x0c", "\r", "\u2028", "\x85"]:
        out = for_mode(f"## step-01 A\n\nx{brk}y\n\n> Say: SECRET\n\nPublic.\n", "do")
        assert "SECRET" not in out and "Public." in out, repr(brk)
    out = for_mode("## step-01 A\n\n> Say: the answer is\n  :::42 SECRET\n\nPublic.\n", "do")
    assert "SECRET" not in out and "Public." in out
    intro = public_text("An intro.\n\n> Say: INTROSECRET\n\ntime: 1:00\n\nThe end.\n")
    assert "INTROSECRET" not in intro and "time:" not in intro and "An intro." in intro and "The end." in intro


def test_timewalk_export_and_check_agree_on_notes_that_end_with_blank_lines(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Notes that end with a blank line, or with odd line breaks, export; a cue that export cannot drop is an error of timewalk-check too."
    from timewalk.export import NotesError, for_mode, public_text
    from timewalk.walks import check, load_toc

    for ending in ["\n\n", "\n\n\n", "\r\n\r\n", "\n\x0c\n"]:
        for mode in (None, "do", "watch"):
            assert "text" in for_mode(f"## step-01 A\n\n::: do\ntext\n:::\n\ntext{ending}", mode)
        assert "intro" in public_text(f"intro{ending}")
    mixed = "## step-00\n\n## step-01\n\n> Say: a secret\n> Try: this\n\n## step-02\n"
    with pytest.raises(NotesError) as raised:
        for_mode(mixed, "do")
    assert raised.value.line == 6
    (tutorial_class / "notes.md").write_text(mixed)
    errors, _ = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("notes.md:6: '> Try: this' is a public cue in the blockquote" in e for e in errors), errors


def test_a_show_line_with_backticks_is_a_double_backtick_span(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "show: ``a `b` c`` quotes the line without its outer backticks; the copies write it back so; check warns of one backtick around one."
    from timewalk.export import render
    from timewalk.walks import check, load_toc

    assert timewalk.shown_line("  show: ``The command `wikiapp` uses it.``") == ("  ", "The command `wikiapp` uses it.")
    assert timewalk.shown_line("  show: `` `x` ``") == ("  ", "`x`"), "a space inside each end goes, as in CommonMark"
    assert timewalk.shown_line("  show: `a `b` c`") == ("  ", "a `b` c"), "one backtick around a backtick still finds the line"
    assert timewalk.shown_line("  show: `x = 1`") == ("  ", "x = 1")
    for text in ["x = 1", "a `b` c", "`x`", "a ``b`` c", " x "]:
        assert timewalk.shown_line("  show: " + timewalk.code_span(text)) == ("  ", text)
    notes = timewalk.parse_notes("## step-02\n### step-02.1 A\nfiles:\n- diff `x.py`: x.\n  show: ``say `hi` now``\n$ ls\n")
    assert next(p for p in notes["step-02"]["parts"] if p["kind"] == "files")["items"][0]["show"] == ["say `hi` now"]
    body = "\n".join(line for line, _ in render(["files:", "- diff `x.py`: x.", "  show: `say `hi` now`"], "step-02.1",
                                                 "https://github.com/o/t", {"step-02.1": "m1"}, {}, lambda *_: ""))
    assert "  - look at: ``say `hi` now``" in body
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\n$ ls\n### step-01.2 B\nfiles:\n"
                                             "- diff `README.md`: changed.\n  show: `with `b``\n$ ls\n### step-01.3 C\n$ ls\n\n## step-02\n")
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("notes.md:9: this show: line is not a quote as GitHub reads it" in w and "show: `` with `b` ``" in w for w in warnings), warnings


def test_show_lines_are_code_spans_as_github_reads_them() -> None:
    "The closing run of backticks is as long as the opening one; check warns only of an item's show: line that GitHub ends early."
    from timewalk.walks import show_problems

    assert [timewalk.shown_line("  show: " + s) for s in ["````", "`a``", "``a`"]] == [None, None, None]
    assert timewalk.shown_line("  show: ` `` `") == ("  ", "``")
    notes = ("## step-01\n### step-01.1 A\nfiles:\n- diff `a.py`: a.\n  show: ` `` `\n  show: ``a``b``\n  show: `a `b` c`\n"
             "  show: ``a `b` c``\n\nProse.\n  show: `a`b`\n```\n- diff `b.py`: b.\n  show: `x`y`\n```\n$ ls\n")
    assert show_problems(notes) == [
        (6, "GitHub ends this show: line's quote at a backtick inside it; write it as show: ```a``b```"),
        (7, "GitHub ends this show: line's quote at a backtick inside it; write it as show: ``a `b` c``")]


def test_saving_a_section_keeps_the_others_whatever_the_line_breaks() -> None:
    "A line break that parse_notes knows, inside a line, does not hide the next heading from the save; other lines keep their ends."
    text = "## step-01\n\nintro\u2028## step-02 Second\nkept words\n\n## step-03\n\nend\n"
    assert list(timewalk.parse_notes(timewalk.replace_section(text, "step-01", "\nnew intro\n"))) == ["step-01", "step-02", "step-03"]
    assert timewalk.replace_section("## step-01\r\nold\r\n## step-02\r\nx\r\n", "step-01", "new\n") == "## step-01\r\nnew\n\n## step-02\r\nx\r\n"
    assert timewalk.replace_section("## a\nx\n## step-01", "step-01", "\nbody\n") == "## a\nx\n## step-01\n\nbody\n"


def test_package_files_match_by_the_names_of_their_packages(tmp_path: Path) -> None:
    "A pyproject.toml or uv.lock with newer versions matches loosely; another package, or another setting, does not."
    made = b'[project]\nname = "w"\nrequires-python = ">=3.11"\ndependencies = ["httpx>=0.27"]\n\n[dependency-groups]\ntest = ["pytest>=8.3.4"]\n'
    mine = tmp_path / "pyproject.toml"
    mine.write_bytes(made.replace(b"8.3.4", b"9.0.1").replace(b"httpx>=0.27", b"HTTPX >= 0.28"))
    assert timewalk.loosely_same("pyproject.toml", mine, made) == "names"
    mine.write_bytes(made.replace(b"pytest>=8.3.4", b"pytest>=8.3.4\", \"ruff"))
    assert timewalk.loosely_same("pyproject.toml", mine, made) == "", "another package"
    mine.write_bytes(made.replace(b">=3.11", b">=3.12"))
    assert timewalk.loosely_same("pyproject.toml", mine, made) == "", "a setting that is not a package"
    lock = ('version = 1\nrequires-python = ">=3.11"\n\n[[package]]\nname = "pytest"\nversion = "{v}"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
            'dependencies = [{{ name = "pluggy" }}{extra}]\n\n[[package]]\nname = "w"\nversion = "0.1.0"\nsource = {{ virtual = "." }}\n\n'
            '[package.dev-dependencies]\ntest = [{{ name = "pytest" }}]\n\n[package.metadata.requires-dev]\ntest = [{{ name = "pytest", specifier = ">={v}" }}]\n')
    (tmp_path / "uv.lock").write_text(lock.format(v="9.0.1", extra=', { name = "iniconfig" }'))
    assert timewalk.loosely_same("uv.lock", tmp_path / "uv.lock", lock.format(v="8.3.4", extra="").encode()) == "names", \
        "what the project asks for, not what its packages need"
    (tmp_path / "uv.lock").write_text(lock.format(v="9.0.1", extra="").replace('test = [{ name = "pytest" }]', 'test = [{ name = "ruff" }]'))
    assert timewalk.loosely_same("uv.lock", tmp_path / "uv.lock", lock.format(v="8.3.4", extra="").encode()) == ""
    (tmp_path / "notes.txt").write_text("anything")
    assert timewalk.loosely_same("notes.txt", tmp_path / "notes.txt", b"else") == "skipped"
    assert timewalk.loosely_same("new.txt", tmp_path / "new.txt", b"made by the move") == "", "a file not compared must still be made"
    assert timewalk.loosely_same("notes.txt", tmp_path / "notes.txt", None) == "", "and one the move deletes, deleted"
    assert timewalk.loosely_same("pyproject.toml", tmp_path / "gone" / "pyproject.toml", made) == "", "a file not made yet does not match"


def test_a_walks_loose_files_do_not_stop_the_match(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "With loose = [\"a.txt\"], a different a.txt still matches, and the match says how; check warns of a glob no move changes."
    from timewalk.walks import TocError, check, load_toc

    toc = tutorial_class / "toc.toml"
    toc.write_text(toc.read_text() + 'loose = ["a.txt", "nothing/*.toml"]\n')
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    auth = {"t": TOKEN}
    client.post("/api/move", params=auth, json={"to": 1})
    (tutorial.work / "a.txt").write_text("not quite\n")
    found = client.get("/api/match", params=auth).json()
    assert found["match"] is True and found["loose"] == {"a.txt": "skipped"}
    _, warnings = check(load_toc(toc), tutorial.main, tutorial_class)
    assert "walk tut: loose names nothing/*.toml, which no move of the walk changes" in warnings
    assert not any("loose names a.txt" in w for w in warnings)
    toc.write_text(toc.read_text().replace('loose = ["a.txt", "nothing/*.toml"]', "loose = \"a.txt\""))
    with pytest.raises(TocError, match="loose must be a list"):
        load_toc(toc)


def test_a_selection_can_title_its_entries(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "{ range = ..., title = ... } titles a stop in the steps, the notes, the PDF and the copies; plain names still work."
    from timewalk.export import Source, build
    from timewalk.slides_pdf import selected_stops
    from timewalk.walks import TocError, load_toc

    select = ["step-00", {"range": "step-01.1..step-01.2", "title": "The first two"}, "step-01.3"]
    steps, moves = timewalk.select_steps(tutorial.main, "step-*", select, True)
    assert [(s.name, s.title) for s in steps] == [("step-00", ""), ("step-01.1 to step-01.2", "The first two"), ("step-01.3", "")]
    notes = timewalk.parse_notes("## step-00 Zero\n\n## step-01 One\n### step-01.1 A\n$ ls\n### step-01.2 B\n$ ls\n### step-01.3 C\n$ ls\n")
    shown = timewalk.walk_notes(notes, steps, moves, True, True)
    assert [shown[s.name]["title"] for s in steps] == ["Zero", "The first two", "C"]
    deck = selected_stops(select, True, notes, {}, False)
    assert [stop["title"] for stop in deck] == ["Zero", "The first two", "C"]
    sources = {"step-00": Source("Zero", [], {}), "step-01": Source("One", [], {n: (t, ["$ ls"]) for n, t in
                                                                                  [("step-01.1", "A"), ("step-01.2", "B"), ("step-01.3", "C")]})}
    shas = {s.name: s.sha for s in steps} | {m.name: m.sha for found in moves for m in found}
    sections = build(sources, steps, moves, "https://github.com/o/t", 2, set(), shas, {s.name: s.start for s in steps})
    assert [(s.name, s.title) for s in sections] == [("step-00", "Zero"), ("step-01.1 to step-01.2", "The first two"), ("step-01.3", "C")]
    toc = tutorial_class / "toc.toml"
    toc.write_text('[[walk]]\nid = "small"\nkind = "tutorial"\nnotes = "notes.md"\nselect = [{ range = "step-01.1..step-01.2", title = "Two" }, "step-02"]\n')
    assert load_toc(toc)[0].select[0] == {"range": "step-01.1..step-01.2", "title": "Two"}
    toc.write_text('[[walk]]\nid = "small"\nkind = "tutorial"\nnotes = "notes.md"\nselect = [{ moves = "step-01.1", title = "Two" }]\n')
    with pytest.raises(TocError, match="has a range and a title"):
        load_toc(toc)


def test_the_page_gets_a_walks_intro(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A walk's intro goes to the page with the walk, to show at its first stop; a walk without one sends nothing."
    from timewalk.walks import load_toc

    toc = tutorial_class / "toc.toml"
    (tutorial_class / "intro.md").write_text("This walk leaves out step-01.2.\n\n> Say: tell them why\n")
    toc.write_text(toc.read_text() + 'intro = "intro.md"\n')
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    assert client.get("/api/state", params={"t": TOKEN}).json()["walk"]["intro"]["watch"].startswith("This walk leaves out step-01.2.")
    toc.write_text(toc.read_text().replace('intro = "intro.md"\n', ""))
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    assert client.get("/api/state", params={"t": TOKEN}).json()["walk"]["intro"] == {}


def test_loose_keeps_addresses_and_markers_and_warns_of_a_move_it_would_make_alone(tmp_path: Path) -> None:
    "A package from another address, or with another marker, differs; check warns of a move whose only change is a version."
    from timewalk.walks import check, load_toc

    made = b'[project]\nname = "w"\ndependencies = ["httpx>=0.27"]\n'
    for other in (b'"httpx @ git+https://example.invalid/httpx"', b"\"httpx>=0.27; sys_platform != 'win32'\""):
        assert timewalk.loose_match("pyproject.toml", made.replace(b'"httpx>=0.27"', other), made) == ""
    assert timewalk.loose_match("pyproject.toml", made.replace(b">=0.27", b">=0.28,<1"), made) == "names"
    main = tmp_path / "p"
    main.mkdir()
    for args in (["init", "--quiet", "-b", "main"], ["config", "user.name", "t"], ["config", "user.email", "t@e"]):
        run_git(main, *args)
    for subject, text, tag in [("step-00: start", made, "step-00"), ("step-01.1: pin", made.replace(b">=0.27", b">=0.27,<1"), None),
                               ("step-01: more", made.replace(b'"httpx>=0.27"', b'"httpx>=0.27,<1", "rich"'), "step-01")]:
        (main / "pyproject.toml").write_bytes(text)
        run_git(main, "add", "-A")
        run_git(main, "commit", "--quiet", "-m", subject)
        if tag:
            run_git(main, "tag", "-a", tag, "-m", subject)
    (tmp_path / "toc.toml").write_text('[[walk]]\nid = "t"\nkind = "tutorial"\nnotes = "notes.md"\nloose = ["pyproject.toml"]\n')
    (tmp_path / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\n$ ls\n### step-01.2 B\n$ ls\n")
    _, warnings = check(load_toc(tmp_path / "toc.toml"), main, tmp_path)
    assert any("step-01.1 changes only what loose does not compare" in w for w in warnings), warnings
    assert not any("step-01.2 changes only" in w for w in warnings)


def test_the_intro_keeps_its_modes_blocks_and_drops_its_times(tmp_path: Path) -> None:
    "The page gets the intro once for each mode, with that mode's blocks, without the fences and the planned times."
    intro = tmp_path / "intro.md"
    intro.write_text("Both.\n\n::: do\nDO ONLY\n:::\n\n::: watch\nWATCH ONLY\n:::\n\ntime: 1:30\n\n```\ntime: 2:00\n```\n> Say: a cue\n")
    found = timewalk.intro_of(intro.read_text())
    assert "DO ONLY" in found["do"] and "WATCH ONLY" not in found["do"] and "WATCH ONLY" in found["watch"]
    assert ":::" not in found["do"] + found["watch"] and "time: 1:30" not in found["do"]
    assert "time: 2:00" in found["do"] and "> Say: a cue" in found["do"], "code stays, and cues are hidden by the page"
    assert timewalk.intro_of("") == {} and timewalk.intro_of("\n  \n") == {}


def test_a_titled_whole_step_without_notes_and_titles_in_the_copies(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A whole step that select titles keeps the title without a section, and can be written; a title is one line; a bracket ends no link."
    from timewalk.export import Section, site_files
    from timewalk.walks import TocError, load_toc

    steps, moves = timewalk.select_steps(tutorial.main, "step-*", [{"range": "step-00", "title": "T"}, "step-01.2"], True)
    shown = timewalk.walk_notes({}, steps, moves, True, True)
    assert shown["step-00"]["title"] == "T" and not shown["step-00"].get("derived"), "the step's notes can be written"
    files = site_files("W", "", {None: [Section("step-00", "a [b] c", "x\n", [])]}, False)
    assert "- [step-00: a \\[b\\] c](" in files["index.qmd"]
    toc = tutorial_class / "toc.toml"
    toc.write_text('[[walk]]\nid = "s"\nkind = "tutorial"\nnotes = "notes.md"\nselect = [{ range = "step-01.1", title = "a\\nb" }]\n')
    with pytest.raises(TocError, match="on one line"):
        load_toc(toc)


def test_a_save_keeps_crlf_and_refuses_a_section_that_is_not_one(served: TestClient, tmp_path: Path) -> None:
    "A notes file with Windows line ends keeps them through a save; a step that is not a section of the file is refused."
    auth = {"t": TOKEN}
    notes = tmp_path / "notes.md"
    notes.write_bytes(notes.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    base = served.get("/api/notes", params=auth).json()["notes"]["step-01"]["raw"]
    assert served.post("/api/notes", params=auth, json={"step": "step-01", "base": base, "text": "new\nlines"}).json() == {"ok": True}
    data = notes.read_bytes()
    assert b"new\r\nlines\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), "every line ends as the file's did"
    refused = served.post("/api/notes", params=auth, json={"step": "step-01.2 to step-01.3", "base": "", "text": "HACK"})
    assert refused.status_code != 200 and b"HACK" not in notes.read_bytes()


def test_a_derived_section_is_not_saved_and_a_restart_counts_the_moves_made(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A small step's notes come from its step's, and are not saved as their own; after a restart, the moves already made count as done."
    from timewalk.walks import load_toc

    toc = tutorial_class / "toc.toml"
    toc.write_text(toc.read_text() + '\n[[walk]]\nid = "small"\nkind = "tutorial"\nnotes = "notes.md"\nselect = ["step-01.2..step-01.3"]\n')
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc), root=tutorial_class), headers=HOST)
    auth = {"t": TOKEN}
    client.post("/api/walk", params=auth, json={"walk": "small"})
    before = (tutorial_class / "notes.md").read_text()
    refused = client.post("/api/notes", params=auth, json={"step": "step-01.2 to step-01.3", "base": "", "text": "HACK", "walk": "small"})
    assert refused.status_code != 200 and (tutorial_class / "notes.md").read_text() == before
    tutorial.select("step-*", None, tutorial=True)
    tutorial.move(1, move=2)
    again = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=load_toc(toc)[:1], root=tutorial_class), headers=HOST)
    state = again.get("/api/state", params=auth).json()
    assert (state["move"], state["done"]) == (2, 2)


def test_nothing_is_written_into_a_replay_copy_made_elsewhere(sample: Path, tmp_path: Path) -> None:
    "A replay copy in a folder of its own is known by its remote; timewalk-notes and timewalk-export do not write there."
    from timewalk.walks import in_a_copy

    elsewhere = tmp_path / "far" / "copy"
    timewalk.Repo(sample, replay=elsewhere)
    assert in_a_copy(elsewhere / "notes.md", sample) and in_a_copy(sample / "sub" / "x.md", sample)
    assert not in_a_copy(tmp_path / "class" / "notes.md", sample)


def test_a_saved_branch_is_named_by_a_tag_that_the_walk_leaves_out(tutorial: timewalk.Repo) -> None:
    "Work kept from a commit that is no stop of the walk on show, but has a tag, is kept under the tag's name."
    tutorial.move(2)
    (tutorial.work / "d.txt").write_text("mine\n")
    tutorial.select("step-*", None, tutorial=True, select=["step-01.1"])
    tutorial.at = None
    assert tutorial.place_name() == "step-02"


def test_a_do_line_makes_the_move_runs_in_do_mode_and_is_shown_in_watch_mode(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "do$ is a command that makes the move: a run of its own and no anchor; in a block, a warning; the copies show it by mode."
    from timewalk.export import for_mode, render
    from timewalk.walks import check, load_toc, moves_without_commands

    notes = timewalk.parse_notes("## step-01\n### step-01.1 A\ndo$ uv add ruff\n### step-01.2 B\ndo$ git rm x\n$ just check\n")
    parts = notes["step-01"]["parts"]
    assert parts[1] == {"kind": "command", "track": "replay", "text": "uv add ruff", "makes": True}
    assert moves_without_commands(parts) == ["step-01.1"], "a do$ line is no anchor"
    lines = ["Now we add ruff.", "do$ uv add --dev ruff", "$ just check"]
    shas = {"step-01.1": "m1"}
    watched = "\n".join(line for line, _ in render(lines, "step-01.1", "https://github.com/o/t", shas, {}, None, True))
    done = "\n".join(line for line, _ in render(lines, "step-01.1", "https://github.com/o/t", shas, {}, None, False))
    assert "The move ran: `uv add --dev ruff`" in watched and "```bash\njust check\n```" in watched
    assert "```bash\nuv add --dev ruff\n```" in done and "```bash\njust check\n```" in done and "The move ran" not in done, \
        "what makes the move and what checks it are two blocks"
    assert "SECRET" not in for_mode("## step-01\n\n> Say: SECRET\ndo$ uv add ruff\n", "watch"), "a do$ line ends a cue as a $ line does"
    assert "uv add ruff" in for_mode("## step-01\n\n> Say: SECRET\ndo$ uv add ruff\n", "watch")
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\ndo$ touch early\n$ just setup\n### step-01.1 A\n::: do\ndo$ uv add ruff\n"
                                             "```\ndo$ in code\n```\n:::\n$ ls\n### step-01.2 B\n$ ls\ndo$ late\n### step-01.3 C\ndo$ fine\n$ ls\n\n## step-02\n"
                                             "patch$ d.txt\n$ ls\n")
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    found = [w for w in warnings if "do$" in w]
    assert any("step-01: the do$ line 'touch early' is before the first move" in w for w in found), found
    assert any("step-01.1: the do$ line 'uv add ruff' is in the block ::: do" in w for w in found), found
    assert any("step-01.2: the do$ line 'late' comes after the commands that check the move" in w for w in found), found
    assert len(found) == 3, "code is not a do$ line, and step-01.3 is right"
    steps, moves = timewalk.select_steps(tutorial.main, "step-*", ["step-01.2..step-01.3"], True)
    parsed = timewalk.parse_notes((tutorial_class / "notes.md").read_text())
    start = timewalk.walk_notes(parsed, steps, moves, True, True)["step-01.2 to step-01.3"]["parts"]
    assert [p["text"] for p in start if p["kind"] == "command"][:1] == ["just setup"], "a do$ line before the moves joins no Start"


SHARED_NOTES = ("# The whole\n\nThe opening.\n\n## step-00 Start\n\n## step-01 Three moves\n\n$ just setup\n\nAbout all three.\n\n"
                "### step-01.1 A\nmove a\n$ ls\n\n### step-01.2 B\nmove b\n$ ls\n\n### step-01.3 C\nmove c\n$ ls\n\n## step-02 D\n\nThe last.\n")
OWN_NOTES = ("This walk leaves out step-01.1.\n\n> Say: a cue in the intro\n\n## The middle two\n\n$ just setup\n\n"
             "We skipped step-01.1, which makes a.\n\n{{include step-01.2}}\n\nBetween the two.\n\n::: do\nDO ONLY\n:::\n\n"
             "{{include step-01.3}}\n\nSo far: b and c.\n\n## The end\n\n{{include step-02}}\n\nRecap of D.\n")


def own_walk(
    folder: Path,  # A class folder
    own: str = OWN_NOTES,  # The walk's own notes
    extra: str = "",  # More lines for the walk's table in toc.toml
) -> Path:  # Its toc.toml, with a walk "own" whose notes include sections of the shared notes.md
    "Write a class folder with shared notes and a walk with notes of its own."
    (folder / "notes.md").write_text(SHARED_NOTES)
    (folder / "walks" / "own").mkdir(parents=True, exist_ok=True)
    (folder / "walks" / "own" / "notes.md").write_text(own)
    toc = folder / "toc.toml"
    toc.write_text(f'[[walk]]\nid = "own"\ntitle = "Own"\nkind = "tutorial"\nnotes = "walks/own/notes.md"\nshared = "notes.md"\n{extra}')
    return toc


def test_a_walk_with_its_own_notes_makes_its_stops_from_them(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each ## stop is a place of the walk under its title; its text stays around the move sections that it includes."
    from timewalk.walks import load_toc

    walk = load_toc(own_walk(tutorial_class))[0]
    assert walk.select == [{"range": "step-01.2..step-01.3", "title": "The middle two"}, {"range": "step-02", "title": "The end"}]
    assert walk.intro_text().startswith("This walk leaves out step-01.1.") and "## " not in walk.intro_text()
    first, second = walk.own_sections()
    order = ["We skipped step-01.1", "### step-01.2 B", "move b", "Between the two.", "DO ONLY", "### step-01.3 C", "move c", "So far: b and c."]
    assert [first.index(text) for text in order] == sorted(first.index(text) for text in order)
    assert "move a" not in first and "About all three." not in first and "{{include" not in first
    assert "The last." in second and second.index("The last.") < second.index("Recap of D.")
    tutorial.select("step-*", None, tutorial=True, select=walk.select)
    shown = timewalk.walk_notes({}, tutorial.steps, tutorial.moves, True, True, walk.own_sections())
    stop = shown["step-01.2 to step-01.3"]
    assert stop["title"] == "The middle two" and stop["derived"] and [c["text"] for c in stop["commands"]] == ["just setup", "ls", "ls"]
    assert [p["name"] for p in stop["parts"] if p["kind"] == "move"] == ["step-01.2", "step-01.3"]
    assert shown["step-02"]["title"] == "The end" and "Recap of D." in shown["step-02"]["text"]


def test_a_stops_includes_make_one_place(tutorial_class: Path) -> None:
    "Includes follow on inside one step; a stop with none is left out; the forms and their mistakes are read as written."
    stop = timewalk.OwnStop("x", 1, [], [(2, "step-01.1"), (4, "step-01.2..step-01.3")])
    assert timewalk.stop_range(stop) == ("step-01.1..step-01.3", "")
    assert timewalk.stop_range(timewalk.OwnStop("x", 1, [], [(2, "step-01.2")])) == ("step-01.2", "")
    assert "leaves out a move" in timewalk.stop_range(timewalk.OwnStop("x", 1, [], [(2, "step-01.1"), (3, "step-01.3")]))[1]
    assert "goes back" in timewalk.stop_range(timewalk.OwnStop("x", 1, [], [(2, "step-01.2"), (3, "step-01.2")]))[1]
    assert "more than one step" in timewalk.stop_range(timewalk.OwnStop("x", 1, [], [(2, "step-01.3"), (3, "step-02.1")]))[1]
    assert "whole step and something more" in timewalk.stop_range(timewalk.OwnStop("x", 1, [], [(2, "step-01"), (3, "step-02.1")]))[1]
    assert "is not a step, a move" in timewalk.stop_range(timewalk.OwnStop("x", 1, [], [(2, "step-01.3..step-01.1")]))[1]
    assert timewalk.stop_range(timewalk.OwnStop("x", 1, [], []))[0] == ""
    intro, stops = timewalk.own_stops("Intro\n\n## A\n\n```\n{{include step-01.1}}\n## not a stop\n```\n\n{{include step-01.2}}\n### Why\n")
    assert intro == "Intro" and [s.title for s in stops] == ["A"] and stops[0].includes == [(10, "step-01.2")], "fenced code holds neither"


@pytest.mark.parametrize("own, extra, says", [
    (OWN_NOTES, 'select = ["step-01"]\n', "leave out select"),
    (OWN_NOTES, 'intro = "notes.md"\n', "leave out intro"),
    ("Only an intro.\n## A\nNo include.\n", "", "has no ## stop whose"),
    ("## A\n{{include step-01.3..step-01.1}}\n", "", "has no ## stop whose"),
])
def test_a_walk_with_its_own_notes_and_a_mistake_says_which(tutorial_class: Path, own: str, extra: str, says: str) -> None:
    "A walk with no place to walk, or keys that its own notes replace, stops timewalk at the start."
    from timewalk.walks import TocError, load_toc

    with pytest.raises(TocError, match=says):
        load_toc(own_walk(tutorial_class, own, extra))


def test_shared_is_for_a_tutorial_with_both_files(tutorial_class: Path) -> None:
    "A narrative cannot include moves, and both files must be there."
    from timewalk.walks import TocError, load_toc

    toc = own_walk(tutorial_class)
    toc.write_text(toc.read_text().replace('kind = "tutorial"', 'kind = "narrative"'))
    with pytest.raises(TocError, match="shared is for a tutorial"):
        load_toc(toc)
    toc = own_walk(tutorial_class)
    (tutorial_class / "notes.md").unlink()
    with pytest.raises(TocError, match=r"its shared notes .* do not exist"):
        load_toc(toc)


def test_the_checks_of_a_walk_with_its_own_notes(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Includes name moves of the history; the walk's own text has no files:, do$ or move heading; a stop needs an include and the setup."
    from timewalk.walks import check, load_toc

    errors, warnings = check(load_toc(own_walk(tutorial_class)), tutorial.main, tutorial_class)
    assert errors == [] and not any("own/notes.md" in w for w in warnings), (errors, warnings)
    own = ("## A\n\n{{include step-01.2}}\n\nfiles:\n- file `a.txt`: a\n\ndo$ touch b\n\n### step-01.3 C\n\n## Recap\n\nNo include.\n\n"
           "## Gone\n\n{{include step-01.9}}\n")
    errors, warnings = check(load_toc(own_walk(tutorial_class, own)), tutorial.main, tutorial_class)
    assert any("notes.md:18: {{include step-01.9}} names step-01.9, which is not a move of the history" in e for e in errors), errors
    assert any("notes.md:5: a files: list" in e for e in errors) and any("notes.md:8: a do$ line" in e for e in errors)
    assert any("notes.md:10: a move's heading" in e for e in errors)
    assert any("notes.md:12: the stop ## Recap has no {{include" in w for w in warnings), warnings
    assert any("the stop ## A starts at a Start of step-01, and does not run just setup" in w for w in warnings), warnings


def test_the_page_shows_a_walk_with_its_own_notes(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "The page gets its intro and each stop's section; a save there is refused, since its sections are made from two files."
    from timewalk.walks import load_toc

    walks = load_toc(own_walk(tutorial_class))
    tutorial.select("step-*", None, tutorial=True, select=walks[0].select)
    client = TestClient(timewalk.make_app(tutorial, TOKEN, PORT, assistant="", walks=walks, root=tutorial_class), headers=HOST)
    state = client.get("/api/state", params={"t": TOKEN}).json()
    assert state["walk"]["intro"]["do"].startswith("This walk leaves out step-01.1.") and state["walk"]["own"], "the page hides Edit for it"
    notes = client.get("/api/notes", params={"t": TOKEN}).json()["notes"]
    assert notes["step-01.2 to step-01.3"]["title"] == "The middle two" and "We skipped step-01.1" in notes["step-01.2 to step-01.3"]["text"]
    done = client.post("/api/notes", params={"t": TOKEN}, json={"walk": "own", "step": "step-02", "text": "x", "base": ""})
    assert "edit them in your editor" in done.json()["error"]
    assert "Recap of D." in (tutorial_class / "walks" / "own" / "notes.md").read_text()
    assert "walks/own/notes.md" not in client.get("/static/../walks/own/notes.md", params={"t": TOKEN}).text


def test_timewalk_export_writes_a_walk_with_its_own_notes(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each copy has the walk's intro, then each stop under its title with the walk's own text around the moves it includes."
    toc, url = own_walk(tutorial_class), "https://github.com/o/t"
    shas = {m.name: m.sha for found in tutorial.moves for m in found}
    done = run_export(tutorial.main, "--toc", str(toc), "--walk", "own", "--markdown", str(tutorial_class / "out"),
                      "--site", str(tutorial_class / "site"), "--github", url)
    assert done.returncode == 0, done.stderr
    do, watch = ((tutorial_class / "out" / name).read_text() for name in ("TUTORIAL-do.md", "TUTORIAL-watch.md"))
    for text in (do, watch):
        assert text.index("# Own") < text.index("This walk leaves out step-01.1.") and "Say:" not in text and "The opening." not in text
        assert f"## step-01.2 to step-01.3 The middle two\n\n[The code at its Start]({url}/tree/{shas['step-01.1']})" in text
        order = ["We skipped step-01.1", "### step-01.2 B", "move b", "Between the two.", "### step-01.3 C", "So far: b and c.", "Recap of D."]
        assert [text.index(t) for t in order] == sorted(text.index(t) for t in order)
        assert "move a" not in text and "About all three." not in text and "{{include" not in text
    assert "DO ONLY" in do and "DO ONLY" not in watch
    assert "We skipped step-01.1" in (tutorial_class / "site" / "do" / "step-01.2-to-step-01.3.qmd").read_text()


def test_the_slides_pdf_prints_a_walk_with_its_own_notes(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "With --with-notes, each stop prints its section of the walk's own notes, with what it includes."
    from timewalk.slides_pdf import deck_of
    from timewalk.walks import load_toc

    walk = load_toc(own_walk(tutorial_class))[0]
    deck = deck_of(None, walk.notes, "t", True, None, None, walk.select, True, walk.own_sections())
    assert [stop["title"] for stop in deck["steps"]] == ["The middle two", "The end"]
    assert "We skipped step-01.1" in deck["steps"][0]["notes"] and "move b" in deck["steps"][0]["notes"]
    assert "move a" not in deck["steps"][0]["notes"] and "Recap of D." in deck["steps"][1]["notes"]


@pytest.mark.parametrize("own, says", [
    ("## A\n{{include step-01.1}}\n{{include step-01.3}}\n## B\n{{include step-02}}\n", "own/notes.md:1: the stop ## A leaves out a move at step-01.3"),
    ("## A\n{{include step-01.3}}\n## B\n{{include step-01.1}}\n", "own/notes.md:3: the stop ## B includes step-01.1, which comes before"),
    ("## A\n{{include step-01}}\n## B\n{{include step-01.2}}\n", "own/notes.md:3: the stop ## B includes step-01.2, which comes before"),
    ("## A\n{{include step-01.1}}\n## B\n{{include step-01.3..step-01.1}}\n", "own/notes.md:3: the stop ## B includes {{include step-01.3..step-01.1}} at line 4, which is not"),
    ("## A\n::: do\n{{include step-01.1}}\n:::\n", "own/notes.md:3: {{include step-01.1}} is inside a ::: do block"),
    ("## A\n{{include step-01.1}}\n{{ include: step-01.2 }}\n", "own/notes.md:3: {{ include: step-01.2 }} is not an include line"),
    ("## A\n{{include step-01.1}}\n\n::: watch\nnot closed\n", "the stop ## A at walks/own/notes.md:1, with the sections it includes from notes.md: the block ::: watch is not closed"),
    ("## A\n{{include step-01.1}}\n## B\n{{include step-01.1.2}}\n", "{{include step-01.1.2}} names step-01.1.2, which is not a move"),
])
def test_a_walk_with_its_own_notes_has_its_mistakes_found_by_the_check(tutorial: timewalk.Repo, tutorial_class: Path, own: str, says: str) -> None:
    "Each mistake in a walk's own notes is an error of timewalk-check, with the file from the class folder and the line; the other stops stay."
    from timewalk.walks import check, load_toc

    errors, _ = check(load_toc(own_walk(tutorial_class, own)), tutorial.main, tutorial_class)
    assert any(says in error for error in errors), errors


def test_a_live_edit_of_a_walks_own_notes_never_moves_text_to_another_stop(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each stop is found by the place it walks: a stop added above, or one whose includes change, takes no other stop's text."
    from timewalk.walks import load_toc

    toc = own_walk(tutorial_class)
    walk = load_toc(toc)[0]
    own = tutorial_class / "walks" / "own" / "notes.md"
    own.write_text(OWN_NOTES.replace("## The middle two", "## New\n\nNEW STOP\n\n{{include step-01.1}}\n\n## The middle two"))
    first, second = walk.own_sections()
    assert "We skipped step-01.1" in first and "NEW STOP" not in first + second and "Recap of D." in second
    own.write_text(OWN_NOTES.replace("{{include step-01.3}}", ""))
    assert walk.own_sections()[0] is None, "its place is no stop's now: it shows nothing until timewalk starts again"
    tutorial.select("step-*", None, tutorial=True, select=walk.select)
    shown = timewalk.walk_notes({}, tutorial.steps, tutorial.moves, True, True, walk.own_sections())
    assert "step-01.2 to step-01.3" not in shown and "Recap of D." in shown["step-02"]["text"]


def test_a_cue_above_an_include_keeps_the_included_text(tutorial_class: Path) -> None:
    "An include starts after a blank line, so a cue's blockquote, or a paragraph, above it never takes in the shared text."
    from timewalk.export import for_mode
    from timewalk.walks import load_toc

    own_walk(tutorial_class, "## End\n\n> Say: a private cue\n{{include step-02}}\n")
    section = load_toc(tutorial_class / "toc.toml")[0].own_sections()[0]
    assert "The last." in for_mode(section, "do") and "a private cue" not in for_mode(section, "do")


def test_a_stop_of_one_move_without_a_title_takes_the_moves(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "An empty ## heading over one move shows the move's title, as a select walk does; a whole step that runs its setup twice is warned of."
    from timewalk.walks import check, load_toc

    walk = load_toc(own_walk(tutorial_class, "##\n\n$ just setup\n\n{{include step-01.2}}\n\n## All\n\n$ just setup\n\n{{include step-02}}\n"))[0]
    tutorial.select("step-*", None, tutorial=True, select=walk.select)
    assert timewalk.walk_notes({}, tutorial.steps, tutorial.moves, True, True, walk.own_sections())["step-01.2"]["title"] == "B"
    own_walk(tutorial_class, "## All\n\n$ just setup\n\n{{include step-01}}\n")
    _, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("the stop ## All runs just setup twice" in w for w in warnings), warnings


def test_a_walk_with_its_own_notes_says_its_mistakes_at_the_start(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Started on a walk whose stops are out of order, or name a move that is not there, timewalk says so as timewalk-check does."
    for own, says in (("## A\n{{include step-02}}\n## B\n{{include step-01.1}}\n", "the stop ## B includes step-01.1, which comes before"),
                      ("## A\n{{include step-01.9}}\n", "{{include step-01.9}} names step-01.9, which is not a move of the history")):
        toc = own_walk(tutorial_class, own)
        done = subprocess.run([sys.executable, "-m", "timewalk", str(tutorial.main), "--in-place", "--toc", str(toc), "--no-open", "--port", "8792"],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode != 0 and says in done.stderr and "select names" not in done.stderr, done.stderr
        done = run_export(tutorial.main, "--toc", str(toc), "--markdown", str(tutorial_class / "out"), "--github", "https://github.com/o/t")
        assert done.returncode != 0 and says in done.stderr, done.stderr


# ---------- the facts of a walk: timewalk-export --json ----------


def facts_of(
    main: Path,  # The project
    toc: Path,  # The table of contents
    walk: str,  # The walk's id
    *more: str,  # More options
) -> tuple[dict, bytes]:  # The facts, and the file as written
    "Run timewalk-export --json for one walk, check the file against the schema, and read it."
    import jsonschema

    out = toc.parent / "facts" / f"{walk}.json"
    done = run_export(main, "--toc", str(toc), "--walk", walk, "--json", str(out), "--github", "https://github.com/o/t", *more)
    assert done.returncode == 0, done.stderr
    schema = json.loads((Path(timewalk.__file__).parent / "facts.schema.json").read_text())
    facts = json.loads(out.read_text())
    jsonschema.validate(facts, schema)
    return facts, out.read_bytes()


def test_the_facts_of_a_tutorial(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each stop and move, its parts in the order of the notes with their modes, every file with its whole diff, and no private line."
    (tutorial_class / "notes.md").write_text(BLOCK_NOTES)
    facts, raw = facts_of(tutorial.main, tutorial_class / "toc.toml", "tut")
    assert facts["format"] == 4 and facts["walk"] == {"id": "tut", "title": "t, one move at a time", "description": "", "kind": "tutorial",
                                                      "mode": "do", "github": "https://github.com/o/t", "loose": []}
    assert facts["intro"] == [{"kind": "prose", "mode": "shared", "text": "The opening."}], "the text before the first step, without its title"
    assert [s["name"] for s in facts["stops"]] == ["step-00", "step-01", "step-02"]
    assert [s["number"] for s in facts["stops"]] == ["0", "1", "2"] and [m["number"] for m in facts["stops"][1]["moves"]] == ["1.1", "1.2", "1.3"]
    assert facts["observed"] is False, "the tests run no do$ line unless they ask"
    zero, one, _ = facts["stops"]
    assert zero["start"] is None and zero["tag"] == {"name": "step-00", "title": "The start.", "text": ""}
    assert [f["path"] for f in zero["files"]] == ["README.md"], "a stop with no moves has the files of its change"
    assert one["start"] == zero["commit"] and one["files"] == [] and one["title"] == "Three moves"
    first, second, third = one["moves"]
    assert first["parent"] == zero["commit"] and first["title"] == "A file a"
    assert [(p["kind"], p["mode"]) for p in first["parts"]] == [("prose", "do"), ("command", "do"), ("prose", "watch"), ("item", "shared")]
    assert ":::\na line of code, kept as it is\n\nthe blank line above stays\n```" in first["parts"][0]["text"], "fenced code is whole"
    assert first["parts"][3] == {"kind": "item", "mode": "shared", "item": "file", "path": "a.txt", "move": None, "words": "the first file.", "show": []}
    assert "Say" not in raw.decode() and "presenter" not in raw.decode() and "time:" not in raw.decode(), "the private lines are left out"
    assert second["parts"][:2] == [{"kind": "patch", "mode": "shared", "path": "README.md"}, {"kind": "patch", "mode": "shared", "path": "b.txt"}]
    assert second["parts"][2]["show"] == ["# t, with b"]
    assert [(f["path"], f["status"], f["added"], f["removed"]) for f in second["files"]] == [("README.md", "modified", 1, 1), ("b.txt", "added", 1, 0)]
    assert second["files"][0]["diff"] == "@@ -1 +1 @@\n-# t\n+# t, with b\n", "the diff from its first hunk"
    assert third["parts"][:2] == [{"kind": "patch", "mode": "shared", "path": "c.txt"}, {"kind": "prose", "mode": "shared", "text": "It is short."}]
    assert facts["slides"]["slides"]["step-01.2"] == ["deck.md#4"] and facts["slides"]["manifest"] == "slides/slides.toml"
    assert facts["errors"] == [] and str(tutorial_class) not in raw.decode() and str(tutorial.main) not in raw.decode()
    assert facts_of(tutorial.main, tutorial_class / "toc.toml", "tut")[1] == raw, "the same notes and history give the same bytes"
    private, _ = facts_of(tutorial.main, tutorial_class / "toc.toml", "tut", "--private")
    assert any("> Say: the presenter's line" in p["text"] for p in private["stops"][1]["moves"][0]["parts"] if p["kind"] == "prose")


def test_the_facts_name_files_renames_and_code(tmp_path: Path) -> None:
    "A rename keeps its old path, a binary file has no diff, a deleted file names what it removes, and a long diff is whole."
    from timewalk.facts import change_files

    main = tmp_path / "f"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "old.txt").write_text("".join(f"line {n}\n" for n in range(40)))
    (main / "gone.py").write_text("def f():\n    pass\n")
    (main / "long.txt").write_text("")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "one")
    run_git(main, "mv", "old.txt", "new.txt")
    (main / "gone.py").unlink()
    (main / "pic.bin").write_bytes(bytes(range(256)))
    (main / "long.txt").write_text("".join(f"{n}\n" for n in range(500)))
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "two")
    files = {f["path"]: f for f in change_files(main, "HEAD^", "HEAD")}
    assert files["new.txt"]["status"] == "renamed" and files["new.txt"]["old_path"] == "old.txt" and files["new.txt"]["diff"] == ""
    assert files["pic.bin"]["binary"] and files["pic.bin"]["diff"] == "" and files["pic.bin"]["status"] == "added"
    assert files["gone.py"]["status"] == "deleted" and files["gone.py"]["names"]["removed"] == ["f"]
    assert files["long.txt"]["added"] == 500 and files["long.txt"]["diff"].count("\n+") == 500 and files["long.txt"]["diff"].endswith("+499\n")
    first = {f["path"]: f for f in change_files(main, None, run_git(main, "rev-parse", "HEAD^"))}
    assert set(first) == {"old.txt", "gone.py", "long.txt"} and first["gone.py"]["names"]["added"] == ["f"], "a first commit has a change too"


def test_the_facts_of_a_narrative_a_selection_and_notes_of_its_own(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A narrative's stops have no moves; a small step starts at the commit before its first move; a walk's own text comes with its includes."
    toc = own_walk(tutorial_class)
    toc.write_text(toc.read_text() + '\n[[walk]]\nid = "small"\ntitle = "Small"\nkind = "tutorial"\nnotes = "notes.md"\n'
                   'select = [{ range = "step-01.2..step-01.3", title = "Two" }]\n'
                   '\n[[walk]]\nid = "story"\ntitle = "Story"\nkind = "narrative"\nnotes = "notes.md"\nselect = ["step-01.1", "step-02"]\n')
    own, _ = facts_of(tutorial.main, toc, "own")
    middle, end = own["stops"]
    assert middle["name"] == "step-01.2 to step-01.3" and middle["own"]["title"] == "The middle two" and middle["tag"] is None
    assert middle["start"] == tutorial.moves[1][0].sha, "the commit before its first move"
    assert [p["text"] for p in middle["parts"]] == ["just setup", "We skipped step-01.1, which makes a."]
    assert [p["text"] for p in middle["moves"][0]["parts"]] == ["move b", "ls", "Between the two.", "DO ONLY"]
    assert middle["moves"][0]["parts"][3]["mode"] == "do"
    assert "{{include" not in middle["own"]["text"] and "### step-01.2 B" in middle["own"]["text"]
    assert end["tag"]["name"] == "step-02" and end["own"]["title"] == "The end"
    assert own["intro"] == [{"kind": "prose", "mode": "shared", "text": "This walk leaves out step-01.1."}], "its intro, without the cue"
    small, _ = facts_of(tutorial.main, toc, "small")
    assert [(s["name"], s["title"]) for s in small["stops"]] == [("step-01.2 to step-01.3", "Two")]
    assert [p["text"] for p in small["stops"][0]["parts"]] == ["just setup"], "a small step leads with its step's commands"
    story, _ = facts_of(tutorial.main, toc, "story")
    assert story["walk"]["mode"] == "watch" and [s["moves"] for s in story["stops"]] == [[], []]
    assert [p["text"] for p in story["stops"][0]["parts"]] == ["just setup", "move a", "ls"], "a stop at a move: its step's commands, then the move's text, as the page shows"
    assert [f["path"] for f in story["stops"][0]["files"]] == ["a.txt"]


def test_a_files_list_after_the_check_and_an_item_for_the_lockfile_pass_the_checks(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "The order the draft gives, the check first and files: last, with an item for every file, is accepted as it is."
    from timewalk.walks import check, load_toc

    (tutorial_class / "notes.md").write_text(
        "## step-00\n\n## step-01\n\n### step-01.1 A\n\nWe add a.\n\n$ ls\n\nfiles:\n- file `a.txt`: the file.\n\n"
        "### step-01.2 B\n\nWe add b.\n\n$ ls\n\nfiles:\n- diff `README.md`: the title.\n- file `b.txt`: b.\n\n"
        "### step-01.3 C\n\n$ ls\n\nfiles:\n- file `c.txt`: c.\n\n## step-02\n")
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert (errors, unsourced(warnings)) == ([], [])


def test_the_facts_read_odd_paths_as_paths_and_ignore_the_git_config(tmp_path: Path) -> None:
    "A file named like a pattern gets its own diff, and the user's diff settings change neither the diffs nor the names."
    from timewalk.facts import change_files

    main = tmp_path / "p"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "x.txt").write_text("x\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "one")
    for name in ("a.py", "[ab].py", ":c.py"):
        (main / name).write_text("def f():\n    return 1\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "two")
    plain = {f["path"]: f for f in change_files(main, "HEAD^", "HEAD")}
    assert plain["[ab].py"]["diff"] == "@@ -0,0 +1,2 @@\n+def f():\n+    return 1\n", "a pattern's diff is its own file's alone"
    assert plain[":c.py"]["diff"] and plain[":c.py"]["names"]["added"] == ["f"]
    run_git(main, "config", "diff.noprefix", "true")
    run_git(main, "config", "diff.context", "1")
    run_git(main, "config", "diff.algorithm", "histogram")
    assert {f["path"]: f for f in change_files(main, "HEAD^", "HEAD")} == plain, "the same facts whatever the config"


def test_an_items_comment_never_reaches_a_reader() -> None:
    "An item's words leave out an HTML comment, which is the author's."
    from timewalk.facts import parts_of

    item = {"kind": "files", "items": [{"kind": "diff", "move": None, "path": "a.py", "text": "the change. <!-- > Say: a cue -->", "show": []}]}
    assert parts_of([item])[0]["words"] == "the change."


def test_the_draft_names_a_renamed_file_by_its_new_path(tmp_path: Path) -> None:
    "A rename is one item, under the new path, as the pages list it; a deleted file is a diff item."
    from timewalk import Move
    from timewalk.walks import move_section

    main = tmp_path / "r"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "app.py").write_text("".join(f"line {n}\n" for n in range(30)))
    (main / "old.txt").write_text("old\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "one")
    run_git(main, "mv", "app.py", "core.py")
    run_git(main, "rm", "-q", "old.txt")
    run_git(main, "commit", "-qm", "step-01.1: rename")
    draft = move_section(main, Move("step-01.1", run_git(main, "rev-parse", "HEAD"), "step-01.1: rename"))
    assert draft.endswith("files:\n- diff `core.py`: \n- diff `old.txt`: \n")


def test_timewalk_export_says_when_the_notes_are_missing(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A notes file that is not there is a message, not a traceback."
    (tutorial_class / "notes.md").unlink()
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--json", str(tutorial_class / "f.json"), "--github", "https://github.com/o/t")
    assert done.returncode != 0 and "cannot be read" in done.stderr and "Traceback" not in done.stderr


def test_timewalk_export_refuses_a_file_where_a_folder_goes_and_writes_nothing(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A file where the site needs a folder stops the command before it writes the facts or anything else."
    site = tutorial_class / "site"
    site.mkdir()
    (site / "watch").write_text("not a folder\n")
    done = run_export(tutorial.main, "--toc", str(tutorial_class / "toc.toml"), "--json", str(tutorial_class / "f.json"), "--site", str(site),
                      "--github", "https://github.com/o/t")
    assert done.returncode != 0 and "needs a folder" in done.stderr and "Traceback" not in done.stderr
    assert not (tutorial_class / "f.json").exists()


def test_the_facts_leave_a_cue_out_of_a_tags_text(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A tag's message is public, but a cue in it is the presenter's, as in the notes; --private keeps it."
    run_git(tutorial.main, "tag", "-f", "-a", "step-02", "-m", "step-02: d\n\nThe text.\n\n> Say: a cue in the tag", "step-02")
    facts, _ = facts_of(tutorial.main, tutorial_class / "toc.toml", "tut")
    assert facts["stops"][2]["tag"]["text"] == "The text."
    private, _ = facts_of(tutorial.main, tutorial_class / "toc.toml", "tut", "--private")
    assert "a cue in the tag" in private["stops"][2]["tag"]["text"]


# ---------- where a move's files come from: the do$ lines run ----------


def made_history(
    tmp_path: Path,  # pytest's temporary directory
    loose: str = "",  # A line loose = [...] for the walk, or ""
) -> tuple[Path, Path]:  # The project, and the class's toc.toml
    """A tutorial whose step-01.1 is made by do$ lines: one writes a file as the commit has it, one makes a file run, one
    writes a file that the author then changes, one writes a file outside the move. hand.txt comes from a patch that an
    patch$ line names, and loose.txt from one that no patch$ line names. step-01.2 has a do$ line that fails."""
    main = tmp_path / "made"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "tool.sh").write_text("echo hi\n")
    (main / "pyproject.toml").write_text('[project]\nname = "m"\ndependencies = ["ruff>=0.1"]\n')
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-00: start")
    run_git(main, "tag", "-a", "step-00", "-m", "start")
    (main / "made.txt").write_text("made\n")
    os.chmod(main / "tool.sh", 0o755)
    (main / "edited.txt").write_text("one\ntwo\n")
    (main / "hand.txt").write_text("by hand\n")
    (main / "loose.txt").write_text("no item\n")
    (main / "pyproject.toml").write_text('[project]\nname = "m"\ndependencies = ["ruff>=0.1", "click>=8"]\n')
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-01.1: made")
    (main / "late.txt").write_text("late\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-01: late")
    run_git(main, "tag", "-a", "step-01", "-m", "one")
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "notes.md").write_text(
        "## step-00\n\n## step-01 One\n\n### step-01.1 Made\n\n"
        "do$ printf 'made\\n' > made.txt\ndo$ chmod +x tool.sh\ndo$ printf 'one\\n' > edited.txt\ndo$ printf 'x\\n' > stray.txt\n"
        "do$ printf '[project]\\nname = \"m\"\\ndependencies = [\"ruff>=0.9\", \"click>=9\"]\\n' > pyproject.toml\n"
        "patch$ hand.txt\n\n$ ls\n\nfiles:\n- file `hand.txt`: by hand.\n\n### step-01.2 Late\n\ndo$ false\npatch$ late.txt\n$ ls\n\nfiles:\n"
        "- file `late.txt`: late.\n\n## step-02\n")
    toc = folder / "toc.toml"
    toc.write_text(f'[[walk]]\nid = "m"\nkind = "tutorial"\nnotes = "notes.md"\n{loose}')
    return main, toc


def test_a_do_line_that_runs_says_which_files_it_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """timewalk-check runs each move's do$ lines at the commit before it, and warns of a file that no do$ line writes and no
    item names the patch of, of a file that a do$ line writes and the move changes after, of a write outside the move, and
    of a do$ line that fails. The project is never written to."""
    from timewalk.walks import check, load_toc

    main, toc = made_history(tmp_path)
    before = run_git(main, "status", "--porcelain", "--ignored"), run_git(main, "for-each-ref")
    _, warnings = check(load_toc(toc), main, toc.parent, run=True)
    found = [w.removeprefix("walk m: ") for w in warnings]
    assert "step-01.1: loose.txt: no do$ line writes it, and no patch$ line names it; add patch$ loose.txt" in found
    assert any(w.startswith("step-01.1: edited.txt: the do$ line `printf 'one\\n' > edited.txt` writes it, and the move changes it after") for w in found)
    assert "step-01.1: the do$ line `printf 'x\\n' > stray.txt` writes stray.txt, which the move does not change" in found
    assert any(w.startswith("step-01.2: the do$ line `false` fails at the commit before the move (exit 1)") for w in found)
    assert any(w.startswith("step-01.1: pyproject.toml: the do$ line") for w in found), "newer versions differ, without loose"
    assert not any(p in w for w in found for p in ("hand.txt", "made.txt", "tool.sh", "late.txt")), found
    assert (run_git(main, "status", "--porcelain", "--ignored"), run_git(main, "for-each-ref")) == before, "the project is as it was"
    _, quiet = check(load_toc(toc), main, toc.parent, run=False)
    assert not any("edited.txt" in w or "fails" in w for w in quiet), "without a run, a move with do$ lines is not checked"


def test_loose_makes_newer_versions_the_same(tmp_path: Path) -> None:
    "A file of the walk's loose that matches by the names of its packages has no patch after its do$ line."
    from timewalk.walks import check, load_toc

    main, toc = made_history(tmp_path, 'loose = ["pyproject.toml"]\n')
    _, warnings = check(load_toc(toc), main, toc.parent, run=True)
    assert not any("pyproject.toml" in w for w in warnings), warnings


def test_the_facts_say_where_each_file_comes_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "Each do$ line has its writes, and each file its by and its patch; a second run reads the cache and runs nothing."
    import timewalk.observe as observe

    main, toc = made_history(tmp_path)
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    facts, raw = facts_of(main, toc, "m")
    assert facts["observed"] is True
    made = facts["stops"][1]["moves"][0]
    writes = [p["writes"] for p in made["parts"] if p["kind"] == "command"]
    assert writes == [["made.txt"], ["tool.sh"], ["edited.txt"], ["stray.txt"], ["pyproject.toml"], None], "a $ line has none"
    files = {f["path"]: f for f in made["files"]}
    assert (files["made.txt"]["by"], files["made.txt"]["patch"]) == (0, "")
    assert (files["tool.sh"]["by"], files["tool.sh"]["patch"]) == (1, ""), "a change of mode is a write"
    assert files["edited.txt"]["by"] == 2 and files["edited.txt"]["patch"] == "@@ -1 +1,2 @@\n one\n+two\n", "from what the line left to the commit"
    assert (files["hand.txt"]["by"], files["hand.txt"]["patch"]) == ("patch", files["hand.txt"]["diff"])
    late = facts["stops"][1]["moves"][1]
    assert [p.get("writes") for p in late["parts"] if p["kind"] in ("command", "patch")] == [[], None, None]
    assert facts["stops"][0]["files"][0]["by"] == "patch", "a stop with no moves is a patch"
    for key in [k for k in observe.MEMORY]:
        del observe.MEMORY[key]
    monkeypatch.setattr(observe, "run_commands", lambda *a: pytest.fail("ran again"))
    again = observe.observe(main, made["parent"], made["commit"], [p["text"] for p in made["parts"] if p["kind"] == "command" and p["makes"]],
                            observe.Scratch(main))
    assert [c["writes"] for c in again["commands"]] == writes[:5], "the cache, as the subprocess wrote it"
    assert facts_of(main, toc, "m")[1] == raw, "the same bytes again"


def test_a_failed_run_is_not_kept(tmp_path: Path) -> None:
    "A do$ line that fails stops the run there, and nothing goes into the cache, so the next check runs it again."
    import timewalk.observe as observe

    main, _ = made_history(tmp_path)
    late = run_git(main, "rev-parse", "step-01")
    found = observe.observe(main, run_git(main, "rev-parse", "step-01^"), late, ["false", "touch never"], observe.Scratch(main))
    assert [c["exit"] for c in found["commands"]] == [1], "the line after a failed one does not run"
    assert not observe.cache_folder().exists() or not list(observe.cache_folder().iterdir())


FAKE_UV = """#!/bin/sh
# A uv that writes the newer versions of a later day, and notes each sync in uv-synced, as a scratch run and a learner see it.
# With FAKE_UV_OFFLINE set, uv add fails, as a uv with no network does.
if [ -n "$FAKE_UV_OFFLINE" ] && [ "$1" = add ]; then echo "error: Failed to fetch: no network" >&2; exit 2; fi
case "$1" in
  add) printf '[project]\\nname = "m"\\ndependencies = ["%s>=9.1"]\\n' "$2" > pyproject.toml
       printf 'version = 1\\n[[package]]\\nname = "%s"\\nversion = "9.1"\\n' "$2" > uv.lock
       if [ "$2" = ruff ]; then printf '3.12\\n' > .python-version; fi ;;
  sync) echo "$*" >> uv-synced ;;
esac
"""


def uv_history(
    tmp_path: Path,  # pytest's temporary directory
    monkeypatch: pytest.MonkeyPatch,  # To put the fake uv first on PATH
    loose: str = "",  # A line loose = [...] for the walk, or "": a strict walk
) -> tuple[Path, Path]:  # The project, and the class's toc.toml
    """A tutorial whose step-01.1 is made by do$ uv add ruff, which writes pyproject.toml, uv.lock and .python-version, and
    whose step-01.2 is made by do$ uv add click and then a do$ line that writes pyproject.toml after it. The author's
    versions are 9.0; the fake uv on PATH writes 9.1, as a later day does."""
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "uv").write_text(FAKE_UV)
    os.chmod(tools / "uv", 0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    main = tmp_path / "uvp"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / ".gitignore").write_text("uv-synced\n")
    (main / "pyproject.toml").write_text('[project]\nname = "m"\ndependencies = []\n')
    (main / "uv.lock").write_text("version = 1\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-00: start")
    run_git(main, "tag", "-a", "step-00", "-m", "start")
    for name, extra in (("ruff", {".python-version": "3.12\n"}), ("click", {})):
        (main / "pyproject.toml").write_text(f'[project]\nname = "m"\ndependencies = ["{name}>=9.0"]\n')
        (main / "uv.lock").write_text(f'version = 1\n[[package]]\nname = "{name}"\nversion = "9.0"\n')
        for path, text in extra.items():
            (main / path).write_text(text)
        if name == "click":
            (main / "pyproject.toml").write_text('[project]\nname = "m"\ndependencies = ["click>=9.0"]\n# by hand\n')
        run_git(main, "add", "-A")
        run_git(main, "commit", "-qm", f"step-01.{1 if name == 'ruff' else 2}: {name}")
    run_git(main, "tag", "-a", "step-01", "-m", "one")
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "notes.md").write_text(
        "## step-00\n\n## step-01 One\n\n### step-01.1 Ruff\n\ndo$ uv add ruff\n\n$ ls\n\n"
        "### step-01.2 Click\n\ndo$ uv add click\ndo$ printf '[project]\\nname = \"m\"\\ndependencies = [\"click>=9.0\"]\\n# by hand\\n' > pyproject.toml\n\n$ ls\n")
    toc = folder / "toc.toml"
    toc.write_text(f'[[walk]]\nid = "u"\nkind = "tutorial"\nnotes = "notes.md"\n{loose}')
    return main, toc


def test_takes_follow_the_rule() -> None:
    "A do$ line that runs uv and writes a file compared exactly takes what it writes, and later lines that write the same join it."
    from timewalk.facts import runs_uv, takes

    sha = "0123456789abcdef0123"
    file = lambda path, by, status="modified", old=None: {"path": path, "by": by, "status": status, "old_path": old}   # noqa: E731
    line = lambda text, *writes: {"text": text, "writes": list(writes)}   # noqa: E731
    found = takes([line("uv init --lib packages/x", "packages/x/pyproject.toml", "packages/x/[odd].py", "old.txt", "new name.txt", "was.txt"),
                   line("uv add x", "pyproject.toml", "uv.lock"), line("touch y", "y")], [
        file("packages/x/pyproject.toml", 0, "added"), file("packages/x/[odd].py", 0, "added"), file("old.txt", 0, "deleted"),
        file("new name.txt", 0, "renamed", "was.txt"), file("pyproject.toml", 1), file("uv.lock", 1), file("y", 2, "added")], sha, [])
    assert found[0] == {"lines": [0], "files": ["packages/x/pyproject.toml", "packages/x/[odd].py", "new name.txt"], "deletes": ["old.txt", "was.txt"],
                        "command": "git restore --source 0123456789ab -- packages/x/pyproject.toml ':(top,literal)packages/x/[odd].py' "
                                   "'new name.txt' && rm -f -- old.txt was.txt", "after": False}, "no uv.lock: no uv sync; the files in the order of the move's"
    assert found[1]["command"] == "git restore --source 0123456789ab -- pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages"
    assert 2 not in found, "a line that is not uv"
    joined = takes([line("uv add click", "pyproject.toml", "uv.lock"), line("printf x > pyproject.toml", "pyproject.toml"), line("ls")],
                   [file("pyproject.toml", 1), file("uv.lock", 0)], sha, [])
    assert joined == {0: {"lines": [0, 1], "files": ["pyproject.toml", "uv.lock"], "deletes": [],
                          "command": "git restore --source 0123456789ab -- pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages",
                          "after": False},
                      1: {"lines": [], "files": [], "deletes": [], "command": "", "after": False}}, "a later line that writes a file of the group joins it"
    nested = takes([line("uv add --directory sub x", "sub/pyproject.toml", "sub/uv.lock")], [file("sub/pyproject.toml", 0), file("sub/uv.lock", 0)], sha, [])
    assert nested[0]["command"].endswith("&& uv sync --directory sub --locked --all-groups --all-packages"), "a uv.lock that is not at the top"
    two = [file("pyproject.toml", 0), file("uv.lock", 0)]
    assert takes([line("uv add x", "pyproject.toml", "uv.lock")], two, sha, ["pyproject.toml", "uv.lock"]) == {}, "loose names both"
    assert takes([line("uv add x", "pyproject.toml", "uv.lock")], two, sha, ["uv.lock"])[0]["files"] == ["pyproject.toml", "uv.lock"], \
        "one file compared exactly is enough, and the line takes all it writes"
    assert takes([line("uv run ruff check --fix", "src/a.py")], [file("src/a.py", 0)], sha, []) == {}, "no pyproject.toml or uv.lock"
    assert takes([line("uv remove x", "uv.lock")], [file("uv.lock", 0, "deleted")], sha, []) == {}, "a deleted file is not one to take"
    assert takes([{"text": "uv add x", "writes": None}], two, sha, [])[0]["files"] == ["pyproject.toml", "uv.lock"], "not run: by says"
    assert runs_uv("'uv' add x") and runs_uv("cd sub && uv add x") and runs_uv("UV_INDEX=x uv add y") and not runs_uv("echo uv")


TALLY = Path.home() / "Projects" / "timewalk-test"


@pytest.mark.skipif(not (TALLY / "toc.toml").is_file() or not shutil.which("uv"), reason="timewalk-test or uv is not here")
@pytest.mark.timeout(1200)
def test_the_takes_of_tallys_strict_walks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On timewalk-test's walks strict and strict-extra, run with real uv: each uv add, init and remove gets a take in place
    of it, and each uv run stays a command. walk-kit renders the pages from these takes, and its tests check that a page
    shows exactly the facts' take."""
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    monkeypatch.setenv("XDG_CACHE_HOME", os.environ.get("TIMEWALK_TEST_CACHE", str(Path.home() / ".cache")))   # the runs of earlier checks
    repo = tmp_path / "repo"
    subprocess.run([sys.executable, str(TALLY / "history" / "build.py"), str(repo)], check=True, capture_output=True)
    folder = tmp_path / "class"   # a copy, as facts_of writes beside the toc.toml
    folder.mkdir()
    for name in ("toc.toml", "notes.md", "walks", "slides"):
        (shutil.copytree if (TALLY / name).is_dir() else shutil.copy)(TALLY / name, folder / name)
    taken = []
    for walk in ("strict", "strict-extra"):
        facts, _ = facts_of(repo, folder / "toc.toml", walk)
        assert facts["observed"] is True and not facts["errors"], facts["errors"]
        for stop in facts["stops"]:
            for move in [*stop["moves"], {"commit": stop["commit"], "parts": stop["parts"]}]:
                for part in (p for p in move["parts"] if p["kind"] == "command" and p["makes"]):
                    if part["text"].startswith("uv run"):
                        assert part["take"] is None, part
                    elif part["take"] and part["take"]["command"]:
                        assert part["take"]["command"].startswith(f"git restore --source {move['commit'][:12]} -- ") and not part["take"]["after"]
                        taken.append(part["text"])
    assert len(taken) >= 5 and "uv add --dev ruff" in taken, "ruff in strict; tabulate, mdformat, the library, talib and the remove in strict-extra"


def test_the_facts_give_a_strict_walks_uv_lines_a_take(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "A do$ line of uv in a strict walk has a take of every file it last writes; a loose walk, or no run, has none."
    main, toc = uv_history(tmp_path, monkeypatch)
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    facts, _ = facts_of(main, toc, "u")
    ruff, click = facts["stops"][1]["moves"]
    sha = ruff["commit"][:12]
    assert [p["take"] for p in ruff["parts"] if p["kind"] == "command"] == [
        {"lines": [0], "files": [".python-version", "pyproject.toml", "uv.lock"], "deletes": [],
         "command": f"git restore --source {sha} -- .python-version pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages",
         "after": False}, None]
    assert [p["take"] and (p["take"]["lines"], p["take"]["files"]) for p in click["parts"] if p["kind"] == "command"] == [
        ([0, 1], ["pyproject.toml", "uv.lock"]), ([], []), None], "the printf line writes pyproject.toml after uv add click, so it joins the take"
    toc.write_text(toc.read_text() + 'loose = ["pyproject.toml", "uv.lock"]\n')
    loose, _ = facts_of(main, toc, "u")
    assert all(p["take"] is None for m in loose["stops"][1]["moves"] for p in m["parts"] if p["kind"] == "command"), "a loose walk runs uv add"
    monkeypatch.setenv("TIMEWALK_NO_RUN", "1")
    toc.write_text(toc.read_text().replace('loose = ["pyproject.toml", "uv.lock"]\n', ""))
    unrun, _ = facts_of(main, toc, "u")
    assert all(p["take"] is None for m in unrun["stops"][1]["moves"] for p in m["parts"] if p["kind"] == "command"), "no run, no by, no take"


def wait_for_takes(
    client: TestClient,  # A client that serves the walk, in a with block, so that the server's task runs between requests
    step: str,  # The step whose notes to read
) -> list[dict]:  # The do$ parts of its notes, once none waits for its run
    "Read the notes until timewalk has run the do$ lines of uv of the step, as a page reads them again on each notes event."
    for _ in range(200):
        parts = [p for p in client.get("/api/notes", params={"t": TOKEN}).json()["notes"][step]["parts"] if p.get("makes")]
        if not any(p.get("taking") or p.get("retrying") for p in parts):
            return parts
        time.sleep(0.05)
    pytest.fail("the do$ lines never ran")


def test_the_page_gets_the_take_and_the_files_then_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "The server runs a strict walk's do$ lines of uv once, gives the take as the facts do, and the take's command makes the files match."
    from timewalk.walks import load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    auth = {"t": TOKEN}
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        client.post("/api/move", params=auth, json={"to": 1})
        first = [p for p in client.get("/api/notes", params=auth).json()["notes"]["step-01"]["parts"] if p.get("makes")]
        assert [p.get("taking", False) for p in first] == [True, True, True], "every do$ line of a move with a line of uv waits for its run"
        parts = wait_for_takes(client, "step-01")
        assert [p["take"]["lines"] for p in parts] == [[0], [0, 1], []], "as the facts give them"
        command = parts[0]["take"]["command"]
        assert command.startswith(f"git restore --source {repo.moves[1][0].sha[:12]} -- .python-version pyproject.toml uv.lock")
        subprocess.run(["uv", "add", "ruff"], cwd=repo.work, check=True)   # what the learner gets on a later day
        assert sorted(client.get("/api/match", params=auth).json()["differ"]) == ["pyproject.toml", "uv.lock"]
        subprocess.run(command, shell=True, cwd=repo.work, check=True)
        found = client.get("/api/match", params=auth).json()
        assert (found["move"], found["match"]) == ("step-01.1", True), found
        assert (repo.work / "uv-synced").read_text() == "sync --locked --all-groups --all-packages\n"
        client.post("/api/show", params=auth, json={"mode": "watch"})
        assert wait_for_takes(client, "step-01")[0]["take"] == parts[0]["take"], "the notes are the same; the page uses the take in do mode only"
    toc.write_text(toc.read_text() + 'loose = ["pyproject.toml", "uv.lock"]\n')
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        parts = [p for p in client.get("/api/notes", params=auth).json()["notes"]["step-01"]["parts"] if p.get("makes")]
        assert not any("take" in p or "taking" in p for p in parts), "a loose walk runs nothing, and runs uv add"
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, notes_path=toc.parent / "notes.md", assistant="", run=True), headers=HOST) as client:
        parts = [p for p in client.get("/api/notes", params=auth).json()["notes"]["step-01"]["parts"] if p.get("makes")]
        assert not any("taking" in p for p in parts), "without a walk, no tutorial"


def test_a_stop_kills_a_run_and_keeps_nothing(tmp_path: Path) -> None:
    "When the server stops, the do$ line that runs is killed at once, no line starts after it, and the run is neither cached nor remembered."
    import threading

    import timewalk.observe as observe

    main, _ = made_history(tmp_path)
    late, parent = run_git(main, "rev-parse", "step-01"), run_git(main, "rev-parse", "step-01^")
    stop = observe.Stop()
    threading.Timer(0.5, stop.stop).start()
    began = time.time()
    found = observe.observe(main, parent, late, ["sleep 30", "touch never"], observe.Scratch(main), stop)
    assert time.time() - began < 10 and [c["text"] for c in found["commands"]] == ["sleep 30"] and found["commands"][0]["exit"] != 0
    assert not any(k for k in observe.MEMORY if observe.MEMORY[k] is found)
    assert not observe.cache_folder().exists() or not list(observe.cache_folder().iterdir())
    assert observe.observe(main, parent, late, ["true"], observe.Scratch(main), stop)["commands"] == [], "nothing starts after a stop"


def test_a_failed_run_of_the_server_gives_the_lines_as_they_are(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "Any error in a run ends the wait: the do$ lines lose taking, and the pages hear of it."
    import timewalk.facts as facts
    from timewalk.walks import load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    monkeypatch.setattr(facts, "move_takes", lambda *a: (_ for _ in ()).throw(ValueError("broken")))
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        parts = wait_for_takes(client, "step-01")
        assert not any(p.get("take") or p.get("taking") for p in parts), "no take: the page gives each do$ line as a command"


def test_a_second_window_keeps_the_step_on_show_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "A window that reads the notes while a move of the step on show runs: the end of that move still tells the pages, before the queue drains."
    import threading

    import timewalk.facts as facts
    from timewalk.walks import load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    (main / "pyproject.toml").write_text('[project]\nname = "m"\ndependencies = ["rich>=9.0"]\n')
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-02.1: rich")
    run_git(main, "tag", "-a", "step-02", "-m", "two")
    notes = toc.parent / "notes.md"
    notes.write_text(notes.read_text() + "\n## step-02 Two\n\n### step-02.1 Rich\n\ndo$ uv add rich\n")
    calls = []

    def slow(main, sha, commands, loose, stop=None):
        calls.append(sha)
        time.sleep(1.0 if len(calls) == 1 else 5.0)
        return {0: {"lines": [0], "files": ["pyproject.toml"], "deletes": [], "command": "x"}}

    monkeypatch.setattr(facts, "move_takes", slow)
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    auth = {"t": TOKEN}
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        client.post("/api/move", params=auth, json={"to": 1})
        with client.websocket_connect(f"/ws/events?t={TOKEN}") as events:
            assert events.receive_json()["type"] == "hello"
            client.get("/api/notes", params=auth)   # the first window starts the runs
            time.sleep(0.3)
            client.get("/api/notes", params=auth)   # the second window reads while the first move of step-01 runs
            got = []

            def read() -> None:
                with contextlib.suppress(Exception):
                    while True:
                        got.append(events.receive_json()["type"])

            threading.Thread(target=read, daemon=True).start()
            time.sleep(3)
            assert "notes" in got, "the pages hear of the first move of the step on show, though the queue still runs"
    assert calls[0] == repo.moves[1][0].sha and repo.steps[2].sha not in calls[:2], "the step on show first"


def test_no_run_gives_the_uv_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "With TIMEWALK_NO_RUN, or --no-run, timewalk runs nothing, and a strict walk's do$ line of uv stays a command."
    from timewalk.walks import load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent), headers=HOST) as client:
        parts = [p for p in client.get("/api/notes", params={"t": TOKEN}).json()["notes"]["step-01"]["parts"] if p.get("makes")]
        assert not any("take" in p or "taking" in p for p in parts)


def test_a_uv_command_that_runs_a_program_stays() -> None:
    """Only uv add, remove, lock, init, version and sync are left out for a take. A line of uv run runs a program: the
    learner runs it, and the take comes after it. A later line of uv add that writes the same files is still left out."""
    from timewalk.facts import only_locks, takes, uv_commands

    assert uv_commands("uv add x && uv run y") == ["add", "run"] and uv_commands("uv --directory 'a b' -q add x") == ["add"]
    assert uv_commands("cd a; UV_X=1 uv lock") == ["lock"] and uv_commands("uv --version") == [""] and uv_commands("echo uv add") == []
    assert uv_commands("uv --python-preference only-system add x") == ["add"], "an option of uv that takes a value"
    assert only_locks("cd sub && uv add x") and not only_locks("uv add pre-commit && pre-commit install"), "a program of its own runs"
    assert all(only_locks(f"uv {verb} x") for verb in ("add", "remove", "lock", "init", "version", "sync"))
    assert not any(only_locks(text) for text in ("uv run x", "uv add x && uv run y", "uv tree", "uv export -o r.txt", "uv --version", "ls"))
    sha = "0123456789abcdef0123"
    file = lambda path, by, status="modified": {"path": path, "by": by, "status": status, "old_path": None}   # noqa: E731
    line = lambda text, *writes: {"text": text, "writes": list(writes)}   # noqa: E731
    command = "git restore --source 0123456789ab -- pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages"
    alone = takes([line("uv run python make.py", "uv.lock")], [file("uv.lock", 0)], sha, [])
    assert alone == {0: {"lines": [0], "files": ["uv.lock"], "deletes": [], "after": True,
                         "command": "git restore --source 0123456789ab -- uv.lock && uv sync --locked --all-groups --all-packages"}}, \
        "uv run writes uv.lock: the learner runs it, and then takes the file"
    first = takes([line("uv run python make.py", "uv.lock"), line("uv add ruff", "pyproject.toml", "uv.lock")],
                  [file("pyproject.toml", 1), file("uv.lock", 1)], sha, [])
    assert first == {0: {"lines": [0, 1], "files": ["pyproject.toml", "uv.lock"], "deletes": [], "command": command, "after": True},
                     1: {"lines": [], "files": [], "deletes": [], "command": "", "after": False}}, "uv run runs; the uv add after it does not"
    between = takes([line("uv add a", "pyproject.toml", "uv.lock"), line("uv run python make.py", "uv.lock", "out.txt"),
                     line("uv add b", "pyproject.toml", "uv.lock")], [file("out.txt", 1, "added"), file("pyproject.toml", 2), file("uv.lock", 2)], sha, [])
    assert between == {0: {"lines": [0, 2], "files": ["out.txt", "pyproject.toml", "uv.lock"], "deletes": [], "after": False,
                           "command": command.replace("-- ", "-- out.txt ")},
                       1: {"lines": [1], "files": ["out.txt", "pyproject.toml", "uv.lock"], "deletes": [], "after": True,
                           "command": command.replace("-- ", "-- out.txt ")},
                       2: {"lines": [], "files": [], "deletes": [], "command": "", "after": False}}, \
        "uv add a is left out, uv run runs and takes the files again after it, and uv add b joins the first take"
    quiet = takes([line("uv add a", "pyproject.toml", "uv.lock"), line("uv run python make.py", "out.txt"), line("uv run ruff check", "out.txt")],
                  [file("out.txt", 2, "added"), file("pyproject.toml", 0), file("uv.lock", 0)], sha, [])
    assert list(quiet) == [0] and quiet[0]["lines"] == [0], "a uv run that writes no file of the take runs as it is, with no take"
    mixed = takes([line("uv add a && uv run make", "pyproject.toml", "uv.lock")], [file("pyproject.toml", 0), file("uv.lock", 0)], sha, [])
    assert mixed[0]["after"] is True, "a line that runs a program too is run, and then the take"


def one_commit_step(
    main: Path,  # The project of uv_history
    toc: Path,  # Its toc.toml
) -> None:
    "Add step-02, a step of one commit, made by do$ uv add rich, with no ### sections, to uv_history's project and notes."
    (main / "pyproject.toml").write_text('[project]\nname = "m"\ndependencies = ["rich>=9.0"]\n')
    (main / "uv.lock").write_text('version = 1\n[[package]]\nname = "rich"\nversion = "9.0"\n')
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-02: rich")
    run_git(main, "tag", "-a", "step-02", "-m", "two")
    notes = toc.parent / "notes.md"
    notes.write_text(notes.read_text() + "\n## step-02 Two\n\ndo$ uv add rich\n\n$ ls\n")


def test_a_step_of_one_commit_gets_a_take(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A strict step of one commit, with no ### sections, is a change of its own: its do$ line of uv gets a take in the
    facts and on the page, its files their by, and the check takes the line as a move's."""
    from timewalk.walks import check, load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    one_commit_step(main, toc)
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    facts, _ = facts_of(main, toc, "u")
    two = facts["stops"][2]
    sha = two["commit"][:12]
    assert two["moves"] == [] and [p["take"] for p in two["parts"] if p["kind"] == "command"] == [
        {"lines": [0], "files": ["pyproject.toml", "uv.lock"], "deletes": [], "after": False,
         "command": f"git restore --source {sha} -- pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages"}, None]
    assert [f["by"] for f in two["files"]] == [0, 0], "uv add rich writes both files"
    errors, warnings = check(load_toc(toc), main, toc.parent, run=True)
    assert not errors and not any("step-02: the do$ line" in w or "step-02: pyproject.toml: no do$ line" in w for w in warnings), warnings
    assert any("step-02: pyproject.toml: the do$ line `uv add rich` writes it" in w for w in warnings), "checked as a move is"
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        assert client.post("/api/move", params={"t": TOKEN}, json={"to": 2}).json()["current"] == 2
        parts = wait_for_takes(client, "step-02")
        assert [p["take"] for p in parts] == [two["parts"][0]["take"]], "the page gives the take that the facts give"


def test_a_run_that_fails_says_why_and_runs_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When uv add fails in the scratch copy, as with no network, do mode says why on each do$ line, and does not give the
    plain command silently. Try again (/api/takes) runs it again, and then the take comes. The facts have an error."""
    import timewalk.observe as observe
    from timewalk.facts import TakeError, move_takes
    from timewalk.walks import load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_UV_OFFLINE", "1")
    observe.MEMORY.clear()
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    with pytest.raises(TakeError, match=re.escape("uv add ruff failed (exit 2): error: Failed to fetch: no network")):
        move_takes(main, repo.moves[1][0].sha, ["uv add ruff"], [])
    auth = {"t": TOKEN}
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        client.post("/api/move", params=auth, json={"to": 1})
        parts = wait_for_takes(client, "step-01")
        assert [bool(p.get("untaken")) for p in parts] == [True, True, False] and not any(p.get("take") for p in parts), \
            "once for each move, at its first line of uv"
        assert "uv add ruff failed (exit 2): error: Failed to fetch: no network" in parts[0]["untaken"]
        again = wait_for_takes(client, "step-01")
        assert again[0].get("untaken") and not again[0].get("taking"), "a read soon after does not run it again, so no loop of runs"
        monkeypatch.delenv("FAKE_UV_OFFLINE")
        with client.websocket_connect(f"/ws/events?t={TOKEN}") as events:
            assert events.receive_json()["type"] == "hello"
            assert client.post("/api/takes", params=auth, json={}).json() == {"ok": True}
            assert events.receive_json()["type"] == "notes", "every window reads the notes again"
        parts = wait_for_takes(client, "step-01")
        assert [p["take"]["lines"] for p in parts] == [[0], [0, 1], []] and not any(p.get("untaken") for p in parts), "the run, again, finds the takes"
    monkeypatch.setenv("FAKE_UV_OFFLINE", "1")
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))   # not the cache of the run that worked
    facts, _ = facts_of(main, toc, "u")
    assert any("step-01.1: do mode cannot take the files that its uv commands write from the commit, because the do$ line uv add ruff "
               "failed (exit 2)" in e for e in facts["errors"]), facts["errors"]
    assert all(p["take"] is None for p in facts["stops"][1]["moves"][0]["parts"] if p["kind"] == "command"), "no take, as on the page"
    from timewalk.walks import check
    observe.MEMORY.clear()   # not the run that worked, in this process
    errors, _ = check(load_toc(toc), main, toc.parent, run=True)
    assert any("step-01.1: do mode cannot take the files" in e for e in errors), "timewalk-check says the same error as the facts"


def test_which_failed_runs_hide_the_takes() -> None:
    """A run gives the takes unless a line did not run, a line of uv add or the like failed, or a line failed and wrote
    nothing. The last line may fail after it writes, as uv run ruff check --fix does, but not when its command is not found."""
    from timewalk.facts import run_problem

    ran = lambda text, code, *writes: {"text": text, "exit": code, "writes": list(writes), "said": "no"}   # noqa: E731
    assert run_problem([ran("uv add x", 0, "uv.lock"), ran("uv run ruff check --fix", 1, "a.py")], ["uv add x", "uv run ruff check --fix"]) == ""
    assert run_problem([ran("uv add x", 2)], ["uv add x", "ls"]) == "the do$ line uv add x failed (exit 2): no"
    assert run_problem([ran("uv sync", 1, "uv.lock")], ["uv sync"]).startswith("the do$ line uv sync failed"), "uv sync writes its files and fails"
    assert run_problem([ran("uv run python x.py", 127, "uv.lock")], ["uv run python x.py"]).endswith("(exit 127): no"), "a command not found"
    assert run_problem([ran("uv run x", 1)], ["uv run x"]) != "", "a line that failed and wrote nothing"
    assert run_problem([ran("ls", 0)], ["ls", "uv add x"]) == "the do$ line uv add x did not run"
    assert run_problem([ran("uv add ruff && ruff check --fix .", 1, "a.py")], ["uv add ruff && ruff check --fix ."]) == "", \
        "a last line that runs a program as well as uv add may fail after it writes, as timewalk-check allows"


def test_the_review_cases_of_takes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A uv.lock in a folder whose name starts with - is synced with --directory ./-w, never an option. A do copy says deletes
    after a program. A do$ line in a tag on the commit of the step before is a warning. --no-run warns of a strict step of
    one commit. What a failed line said names the scratch clone by the project's name, not by a path of this machine."""
    import timewalk.observe as observe
    from timewalk.export import take_lines
    from timewalk.facts import take_command
    from timewalk.walks import check, load_toc

    assert take_command([{"path": "-w/uv.lock", "status": "modified", "old_path": None}], "c" * 40)[2].endswith(
        "&& uv sync --directory ./-w --locked --all-groups --all-packages")
    said = take_lines({"lines": [0], "files": ["uv.lock"], "deletes": ["old.txt"], "command": "x", "after": True}, ["uv run y"], "uv run y")
    assert "It also writes `uv.lock`, and deletes `old.txt`." in said[0]
    main, toc = uv_history(tmp_path, monkeypatch)
    run_git(main, "tag", "-a", "step-02", "-m", "same", "step-01^{commit}")
    (toc.parent / "notes.md").write_text((toc.parent / "notes.md").read_text() + "\n## step-02 Same\n\ndo$ uv add rich\n")
    _, warnings = check(load_toc(toc), main, toc.parent)
    assert any("step-02: the do$ line 'uv add rich' is in a step with no change of its own" in w for w in warnings), warnings
    run_git(main, "tag", "-d", "step-02")
    one_commit_step(main, toc)
    notes = toc.parent / "notes.md"
    notes.write_text(re.sub(r"### step-01\.1.*?(?=## step-02 Two)", "", notes.read_text().replace("\n## step-02 Same\n\ndo$ uv add rich\n", ""), flags=re.DOTALL))
    toc.write_text(toc.read_text() + 'loose = ["pyproject.toml", "uv.lock"]\n')
    loose_steps = run_export(main, "--toc", str(toc), "--walk", "u", "--markdown", str(tmp_path / "c"), "--github", "https://github.com/o/t")
    toc.write_text(toc.read_text().replace('loose = ["pyproject.toml", "uv.lock"]\n', ""))
    plain = run_export(main, "--toc", str(toc), "--walk", "u", "--markdown", str(tmp_path / "c"), "--github", "https://github.com/o/t", "--no-run")
    assert "gives the uv commands of step-02 as they are" in plain.stderr and "nothing runs" not in loose_steps.stderr, \
        "the warning names the step of one commit, and only a change with a do$ line of uv"
    observe.MEMORY.clear()
    parent = run_git(main, "rev-parse", "step-01~1")
    found = observe.observe(main, parent, run_git(main, "rev-parse", "step-01~0^{commit}"), ["echo \"in $PWD\"; false"], observe.Scratch(main))
    assert found["commands"][0]["said"] == "in uvp", found["commands"][0]["said"]


def test_a_small_walk_checks_its_steps_of_one_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "A walk that selects a strict step of one commit: timewalk-check runs its do$ lines, and a failed run is an error, as the facts say."
    import timewalk.observe as observe
    from timewalk.walks import check, load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    one_commit_step(main, toc)
    toc.write_text(toc.read_text() + 'select = ["step-01", "step-02"]\n')
    monkeypatch.setenv("FAKE_UV_OFFLINE", "1")
    observe.MEMORY.clear()
    errors, _ = check(load_toc(toc), main, toc.parent, run=True)
    assert any("step-02: do mode cannot take the files" in e for e in errors), errors


def test_the_do_copy_applies_nothing_after_a_take(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "A patch$ line after uv add click, whose take puts pyproject.toml in place as the commit has it: the do copy says there is nothing to apply."
    main, toc = uv_history(tmp_path, monkeypatch)
    notes = toc.parent / "notes.md"
    notes.write_text(notes.read_text().replace("do$ printf '[project]\\nname = \"m\"\\ndependencies = [\"click>=9.0\"]\\n# by hand\\n' > pyproject.toml\n",
                                               "\npatch$ pyproject.toml\n"))
    assert "patch$ pyproject.toml" in notes.read_text()
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    done = run_export(main, "--toc", str(toc), "--walk", "u", "--markdown", str(tmp_path / "c"), "--github", "https://github.com/o/t")
    do = (tmp_path / "c" / "TUTORIAL-do.md").read_text()
    assert done.returncode == 0 and "with this change in it already, so there is nothing to apply." in do and "as its change shows" not in do, do


def test_a_failed_run_is_tried_again_a_minute_later(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    "A read of the notes a minute after a failed run runs it again, as a refresh of the page does."
    import timewalk.observe as observe
    from timewalk.walks import load_toc

    main, toc = uv_history(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_UV_OFFLINE", "1")
    observe.MEMORY.clear()
    repo = timewalk.Repo(main)
    repo.select("step-*", None, tutorial=True)
    clock = [1000.0]
    monkeypatch.setattr(timewalk.time, "monotonic", lambda: clock[0])
    with TestClient(timewalk.make_app(repo, TOKEN, PORT, assistant="", walks=load_toc(toc), root=toc.parent, run=True), headers=HOST) as client:
        client.post("/api/move", params={"t": TOKEN}, json={"to": 1})
        assert wait_for_takes(client, "step-01")[0].get("untaken")
        monkeypatch.delenv("FAKE_UV_OFFLINE")
        clock[0] += 61
        first = next(p for p in client.get("/api/notes", params={"t": TOKEN}).json()["notes"]["step-01"]["parts"] if p.get("makes"))
        assert first.get("untaken") and first.get("retrying"), "while it runs again, do mode still gives the line, and says why"
        assert wait_for_takes(client, "step-01")[0]["take"]["lines"] == [0]


def test_the_do_copy_of_a_strict_walk_takes_the_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """timewalk-export --markdown and --site: the do copy of a strict walk gives the take in place of uv add, and the watch
    copy what the move ran. A loose walk gives the command. With no run, the copy gives the command, and says why."""
    main, toc = uv_history(tmp_path, monkeypatch)
    one_commit_step(main, toc)
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    out = tmp_path / "copies"
    done = run_export(main, "--toc", str(toc), "--walk", "u", "--markdown", str(out), "--site", str(out / "site"), "--github", "https://github.com/o/t")
    assert done.returncode == 0, done.stderr
    do, watch = (out / "TUTORIAL-do.md").read_text(), (out / "TUTORIAL-watch.md").read_text()
    ruff = run_git(main, "rev-parse", "step-01~1")[:12]   # the commit of step-01.1
    rich = run_git(main, "rev-parse", "step-02^{commit}")[:12]
    assert "```bash\nuv add ruff\n```" not in do and "```bash\nuv add rich\n```" not in do, do
    assert (f"The author ran `uv add ruff` here. It writes `.python-version`, `pyproject.toml` and `uv.lock`. On a later day, it can write "
            "newer versions than the author's, and this walk compares these files line by line. So run this command in its place, from the "
            "top of the project. It takes each file as the author has it, from the commit, and installs exactly the versions in `uv.lock`:\n\n"
            f"```bash\ngit restore --source {ruff} -- .python-version pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages\n```") in do
    assert "The author ran `uv add click`, then `uv add" not in do and "The author then ran `printf" in do, "the joined line runs nothing"
    assert f"git restore --source {rich} -- pyproject.toml uv.lock" in do, "a step of one commit too"
    assert f"git restore --source {rich}" in (out / "site" / "do" / "step-02.qmd").read_text(), "the website's do pages too"
    assert "git restore" not in watch and "The move ran: `uv add ruff`" in watch
    monkeypatch.setenv("TIMEWALK_NO_RUN", "1")
    plain = run_export(main, "--toc", str(toc), "--walk", "u", "--markdown", str(out), "--github", "https://github.com/o/t")
    assert plain.returncode == 0 and "```bash\nuv add ruff\n```" in (out / "TUTORIAL-do.md").read_text()
    assert "nothing runs (--no-run or TIMEWALK_NO_RUN), so the do copy gives the uv commands of step-01.1, step-01.2, step-02 as they are" \
        in plain.stderr
    toc.write_text(toc.read_text() + 'loose = ["pyproject.toml", "uv.lock"]\n')
    monkeypatch.delenv("TIMEWALK_NO_RUN")
    loose = run_export(main, "--toc", str(toc), "--walk", "u", "--markdown", str(out), "--github", "https://github.com/o/t")
    assert loose.returncode == 0 and "```bash\nuv add ruff\n```" in (out / "TUTORIAL-do.md").read_text() and "nothing runs" not in loose.stderr


def test_display_numbers() -> None:
    "A number is the name without the tags' prefix and its zeros."
    from timewalk.facts import display_number

    assert display_number("step-08b.1", "step-*") == "8b.1" and display_number("step-05.3 to step-05.6", "step-*") == "5.3 to 5.6"
    assert display_number("step-00", "step-*") == "0" and display_number("v1.2.0", "v*") == "1.2.0" and display_number("odd", "step-*") == "odd"



def test_blob_diff_compares_two_copies() -> None:
    "Two copies of a file, in no commit, compared by git: from the first hunk; empty when the same; a file on one side only."
    assert timewalk.blob_diff("a.py", b"x\n", b"x\ny\n") == "@@ -1 +1,2 @@\n x\n+y\n"
    assert timewalk.blob_diff("a.py", b"x\n", b"x\n") == "" and timewalk.blob_diff("a.py", None, None) == ""
    assert timewalk.blob_diff("a.py", None, b"new\n") == "@@ -0,0 +1 @@\n+new\n"


def test_do_mode_compares_your_file_with_the_move(tutored: TestClient, tutorial: timewalk.Repo) -> None:
    "In do mode, a file's answer has your file against the move being made; in watch mode it has none."
    auth = {"t": TOKEN}
    tutored.post("/api/show", params=auth, json={"mode": "do"})
    tutored.post("/api/move", params=auth, json={"to": 1})
    shown = tutored.get("/api/file", params={**auth, "path": "a.txt"}).json()
    assert shown["compare_name"] == "step-01.1" and shown["compare"] == "@@ -0,0 +1 @@\n+a\n" and shown["compare_loose"] == ""
    (tutorial.work / "a.txt").write_text("a\n")
    assert tutored.get("/api/file", params={**auth, "path": "a.txt"}).json()["compare"] == "", "your file matches the move"
    (tutorial.work / "a.txt").write_text("b\n")
    assert tutored.get("/api/file", params={**auth, "path": "a.txt"}).json()["compare"] == "@@ -1 +1 @@\n-b\n+a\n", "from yours to the move's"
    tutored.post("/api/show", params=auth, json={"mode": "watch"})
    assert "compare" not in tutored.get("/api/file", params={**auth, "path": "a.txt"}).json()


def test_a_run_never_reaches_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The do$ lines run in a clone with no remote, and git runs with no variable that points at another repository: a
    push fails, and GIT_INDEX_FILE does not change the project's index. A path that starts with a quote, a symbolic
    link and a process left running are all handled."""
    import timewalk.observe as observe

    main, _ = made_history(tmp_path)
    (main / '"q".txt').write_text("q\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "a file whose name starts with a quote")
    parent = run_git(main, "rev-parse", "HEAD")
    (main / "staged.txt").write_text("staged\n")
    run_git(main, "add", "staged.txt")
    before = run_git(main, "status", "--porcelain"), run_git(main, "for-each-ref")
    monkeypatch.setenv("GIT_INDEX_FILE", str(main / ".git" / "index"))
    commands = ["git commit -qm x --allow-empty", "git push -q origin HEAD:refs/heads/evil", "echo 2 > b"]
    found = observe.run_commands(observe.Scratch(main).at(parent), commands)
    assert found["commands"][1]["exit"] != 0, "no origin to push to"
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert (run_git(main, "status", "--porcelain"), run_git(main, "for-each-ref")) == before, "the project's index and refs are as they were"
    scratch = observe.Scratch(main)
    began = time.monotonic()
    found = observe.run_commands(scratch.at(parent), ["ln -s made.txt link", "echo b > b.txt; sleep 30 &"])
    assert time.monotonic() - began < 10, "a process left running does not hold the run"
    assert found["commands"][0]["writes"] == ["link"] and found["left"]["link"]["link"] is True
    assert base64.b64decode(found["left"]["link"]["data"]) == b"made.txt", "a link is its target, not the file it points to"
    assert found["commands"][1]["writes"] == ["b.txt"] and '"q".txt' not in found["left"]
    scratch.close()


def test_a_broken_cache_means_a_new_run(tmp_path: Path) -> None:
    "A cache file of the wrong shape is run again, not an error."
    import timewalk.observe as observe

    main, _ = made_history(tmp_path)
    parent, commit = run_git(main, "rev-parse", "step-00"), run_git(main, "rev-parse", "step-01^")
    first = observe.observe(main, parent, commit, ["printf 'made\\n' > made.txt"], observe.Scratch(main))
    observe.MEMORY.clear()
    for broken in ("[]", '{"layout": 2}', '{"layout": 2, "commands": [{"text": "other"}], "left": {}}'):
        for kept in observe.cache_folder().iterdir():
            kept.write_text(broken)
        observe.MEMORY.clear()
        assert observe.observe(main, parent, commit, ["printf 'made\\n' > made.txt"], observe.Scratch(main)) == first


def test_an_apply_sentence_gets_a_warning(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "The Apply bar is gone: a sentence that asks for it gets a warning, outside code and comments; the facts keep it as prose, as written."
    from timewalk.facts import parts_of
    from timewalk.walks import apply_sentences

    notes = timewalk.parse_notes("## step-01\n### step-01.1 A\nTake it with **Apply this file**.\n```\n**Apply this file**\n```\n"
                                 "### step-01.2 B\n<!-- **Apply the whole move** -->\n")
    assert apply_sentences(notes) == ["step-01.1: a sentence asks for **Apply**, which timewalk no longer has; write a line patch$ PATH for each "
                                      "file that the move changes by a patch"]
    text = "One.\nTwo,\n\n\nthree.  Use **Apply this file**.\n   \nThen run."
    assert parts_of(timewalk.parse_notes(f"## s\n{text}\n")["s"]["parts"]) == [{"kind": "prose", "mode": "shared", "text": text}], "every character"


def test_a_patch_line_is_checked(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A patch$ line names a file of its move, or it is an error; before the first move, or in a block, it gets a warning."
    from timewalk.walks import check, load_toc

    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\npatch$ a.txt\n### step-01.1 A\npatch$ a.txt\npatch$ zzz.txt\n$ ls\n"
                                             "### step-01.2 B\n::: do\npatch$ b.txt\n:::\npatch$ README.md\n$ ls\n### step-01.3 C\npatch$ c.txt\n$ ls\n\n## step-02\n")
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert errors == ["walk tut: step-01.1: the line patch$ zzz.txt names a file that the move does not change"]
    assert any("step-01: the line patch$ a.txt is before the first move" in w for w in warnings)
    assert any("step-01.2: the line patch$ b.txt is in the block ::: do" in w for w in warnings)
    assert not any("step-01" in w and "no patch$ line names it" in w for w in warnings), "a patch$ line in a block still names its file"
    assert any("step-02: d.txt: no do$ line writes it, and no patch$ line names it" in w for w in warnings), "a step of one commit is a change too"
    from timewalk.walks import do_line_problems

    one = timewalk.parse_notes("## step-02\npatch$ d.txt\n$ ls\n")["step-02"]["parts"]
    assert do_line_problems("step-02", one, True) == [], "a step of one commit is a change of its own, and may have patch$ lines"
    made = timewalk.parse_notes("## step-02\ndo$ touch d.txt\n$ ls\n")["step-02"]["parts"]
    assert do_line_problems("step-02", made, True) == [], "a step of one commit is a change of its own, and its do$ lines make it"
    assert do_line_problems("step-00", made, True, alone=False) == ["step-00: the do$ line 'touch d.txt' is in a step with no change of its own, "
                                                                    "the first step or a tag on the commit of the step before; the learner's clone "
                                                                    "has its files already, so write the command as a $ line"]
    late = timewalk.parse_notes("## step-02\n$ ls\ndo$ touch d.txt\n")["step-02"]["parts"]
    assert do_line_problems("step-02", late, True)[0].startswith("step-02: the do$ line 'touch d.txt' comes after the commands that check"), \
        "the rules of a move's do$ lines"
    blocked = timewalk.parse_notes("## step-02\n::: do\npatch$ d.txt\n:::\n$ ls\n")["step-02"]["parts"]
    assert do_line_problems("step-02", blocked, True)[0].startswith("step-02: the line patch$ d.txt is in the block"), "the step, not None"


def test_a_patch_line_of_a_step_of_one_commit_is_checked(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    """A step of one commit is a change of its own: its patch$ lines must name its files, as a move's must. A patch$ line in
    the first step gets a warning: the learner's clone starts there, with the file."""
    from timewalk.walks import check, load_toc

    (tutorial_class / "notes.md").write_text("## step-00\npatch$ README.md\n\n## step-01\n### step-01.1 A\npatch$ a.txt\n$ ls\n"
                                             "### step-01.2 B\npatch$ b.txt\npatch$ README.md\n$ ls\n### step-01.3 C\npatch$ c.txt\n$ ls\n\n"
                                             "## step-02\npatch$ dd.txt\n$ ls\n")
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert errors == ["walk tut: step-02: the line patch$ dd.txt names a file that the move does not change"], "a wrong path in a step of one commit"
    assert any("step-02: d.txt: no do$ line writes it, and no patch$ line names it" in w for w in warnings)
    assert any("step-00: the line patch$ README.md is in the first step" in w for w in warnings)
    (tutorial_class / "notes.md").write_text((tutorial_class / "notes.md").read_text().replace("## step-00\npatch$ README.md\n", "## step-00\n")
                                             .replace("patch$ dd.txt", "patch$ d.txt"))
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert errors == [] and not any("patch$" in w for w in warnings), warnings
    run_git(tutorial.main, "tag", "--annotate", "step-03", "--message", "Same commit.", "step-02")
    (tutorial_class / "notes.md").write_text((tutorial_class / "notes.md").read_text() + "\n## step-03\n$ ls\n")
    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert not any("step-03" in e for e in errors if "slide" not in e), errors
    assert not any("step-03" in w and "patch$" in w for w in warnings), "a tag on the commit of the step before changes nothing"


def test_a_patch_line_offers_apply_or_replace(tutored: TestClient, tutorial: timewalk.Repo) -> None:
    """The file of a patch$ line, as the learner has it: as before the move (apply), as the move leaves it (made), or any
    other way (replace). Keep puts the learner's version on a saved branch, and changes no file."""
    auth = {"t": TOKEN}
    tutored.post("/api/show", params=auth, json={"mode": "do"})
    tutored.post("/api/move", params=auth, json={"to": 1})
    state = lambda: tutored.get("/api/patch", params={**auth, "move": "step-01.2", "path": "README.md"}).json()  # noqa: E731
    found = state()
    assert found["state"] == "apply" and found["after"] == tutorial.moves[1][1].sha and found["exists"]
    (tutorial.work / "README.md").write_text("# t, with b\n")
    assert state()["state"] == "made"
    (tutorial.work / "README.md").write_text("# mine\n")
    assert state()["state"] == "replace"
    kept = tutored.post("/api/keep", params=auth, json={"path": "README.md", "move": "step-01.2"}).json()["branch"]
    assert kept.startswith("timewalk/saved/step-01.2-yours")
    assert run_git(tutorial.work, "show", f"{kept}:README.md") == "# mine", "the learner's version is on the branch"
    assert (tutorial.work / "README.md").read_text() == "# mine\n" and "README.md" not in run_git(tutorial.work, "diff", "--cached", "--name-only")
    assert tutored.get("/api/patch", params={**auth, "move": "step-01.2", "path": "../x"}).json() == {"state": ""}
    assert tutored.post("/api/keep", params=auth, json={"path": "../x", "move": "step-01.2"}).status_code == 409


def test_the_match_sees_a_file_made_to_run(tmp_path: Path) -> None:
    "A move of chmod +x alone: the learner's file with the same lines differs until it can run, so the move is made only by chmod."
    main = tmp_path / "m"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "hook").write_text("echo hi\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "a hook")
    os.chmod(main / "hook", 0o755)
    run_git(main, "commit", "-qa", "--allow-empty-message", "-m", "")
    repo = timewalk.Repo(main, in_place=True, commits=True)
    before, after = run_git(main, "rev-parse", "HEAD^"), run_git(main, "rev-parse", "HEAD")
    os.chmod(main / "hook", 0o644)
    assert repo.differ_from(before, after) == ["hook"], "same lines, but it cannot run"
    os.chmod(main / "hook", 0o755)
    assert repo.differ_from(before, after) == []


def test_a_patch_line_ends_a_cue() -> None:
    "A patch$ line under a cue, with no blank line between, starts a block of its own: it goes to students, as a $ line does."
    from timewalk.export import public_text

    assert public_text("> Say: hi\npatch$ a.py\n$ ls\n") == "patch$ a.py\n$ ls"


def test_replace_keeps_what_the_learner_staged_and_refuses_where_it_cannot_keep(tutorial: timewalk.Repo, tmp_path: Path) -> None:
    """keep_file keeps the staged version and the version on disk, one commit each; a folder, or a file as HEAD has it, keeps
    nothing; a copy that shares its branches with the user's repository refuses, as it may not write a branch there."""
    work = tutorial.work
    (work / "README.md").write_text("staged\n")
    run_git(work, "add", "README.md")
    (work / "README.md").write_text("disk\n")
    branch = tutorial.keep_file("README.md", "step-01.2")
    assert run_git(work, "show", f"{branch}:README.md") == "disk" and run_git(work, "show", f"{branch}^:README.md") == "staged"
    assert run_git(work, "diff", "--cached", "--name-only") == "README.md", "the index is as it was"
    run_git(work, "reset", "-q", "--hard")
    assert tutorial.keep_file("README.md", "step-01.2") is None, "nothing of the learner's to keep"
    with pytest.raises(timewalk.GitError):
        tutorial.keep_file(".", "step-01.2")
    with pytest.raises(timewalk.GitError):
        tutorial.keep_file("README.md/", "step-01.2")
    tutorial.branch = None   # as in a replay worktree of an older version, or --in-place
    before = run_git(work, "for-each-ref")
    with pytest.raises(timewalk.GitError, match="shares its branches"):
        tutorial.keep_file("README.md", "step-01.2")
    assert run_git(work, "for-each-ref") == before


def test_the_patch_state_reads_links_and_modes(tmp_path: Path) -> None:
    "A symbolic link is its target, and a file that can run differs from one that cannot, as the match sees them."
    main = tmp_path / "m"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "hook").write_text("echo hi\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-00: start")
    run_git(main, "tag", "-a", "step-00", "-m", "start")
    (main / "link").symlink_to("hook")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-01.1: a link")
    os.chmod(main / "hook", 0o755)
    run_git(main, "commit", "-qa", "--allow-empty-message", "-m", "")
    run_git(main, "commit", "-q", "--allow-empty", "-m", "step-01: end")
    run_git(main, "tag", "-a", "step-01", "-m", "one")
    repo = timewalk.Repo(main, in_place=True)
    repo.select("step-*", None, tutorial=True)
    assert repo.patch_state("step-01.1", "link")["state"] == "made", "a link is read as its target, as git keeps it"
    os.chmod(main / "hook", 0o644)
    assert repo.patch_state("step-01.2", "hook")["state"] == "apply", "the same lines, but it cannot run: as before the move"
    os.chmod(main / "hook", 0o755)
    assert repo.patch_state("step-01.2", "hook")["state"] == "made"
    assert repo.patch_state("step-01.2", "hook")["keep"] is False, "in place: no branch of its own to keep a file on"


def test_a_rename_needs_a_patch_line_for_its_old_path(tmp_path: Path) -> None:
    "A move that renames a file: a patch$ line for the new path alone gets a warning, so that the old file goes too."
    from timewalk.walks import check, load_toc

    main = tmp_path / "r"
    main.mkdir()
    run_git(main, "init", "--quiet", "-b", "main")
    run_git(main, "config", "user.name", "t")
    run_git(main, "config", "user.email", "t@e")
    (main / "old.txt").write_text("".join(f"line {n}\n" for n in range(20)))
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-00: start")
    run_git(main, "tag", "-a", "step-00", "-m", "start")
    run_git(main, "mv", "old.txt", "new.txt")
    run_git(main, "commit", "-qm", "step-01.1: rename")
    run_git(main, "commit", "-q", "--allow-empty", "-m", "step-01: end")
    run_git(main, "tag", "-a", "step-01", "-m", "one")
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "toc.toml").write_text('[[walk]]\nid = "r"\nkind = "tutorial"\nnotes = "notes.md"\n')
    (folder / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\npatch$ new.txt\n$ ls\n### step-01.2 B\n$ ls\n")
    _, warnings = check(load_toc(folder / "toc.toml"), main, folder)
    assert any("new.txt is renamed from old.txt; add patch$ old.txt" in w for w in warnings), warnings
    (folder / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\npatch$ old.txt\npatch$ new.txt\n$ ls\n### step-01.2 B\n$ ls\n")
    _, warnings = check(load_toc(folder / "toc.toml"), main, folder)
    assert not any("old.txt" in w for w in warnings), warnings


def test_the_patch_routes_refuse_odd_requests(tutored: TestClient) -> None:
    "A request with no path or move, or a body that is not an object, gets an answer, not a server error."
    auth = {"t": TOKEN}
    assert tutored.get("/api/patch", params=auth).json() == {"state": ""}
    assert tutored.post("/api/keep", params=auth, json=["x"]).status_code == 409
    assert tutored.get("/api/patch", params={"move": "step-01.1", "path": "a.txt"}).status_code == 403


def load_review():  # The module docs/style/review.py, loaded from its file
    "Load the style review script as a module."
    import importlib.util
    spec = importlib.util.spec_from_file_location("style_review", Path(__file__).resolve().parent.parent / "docs" / "style" / "review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_style_review_runs_files_together_and_counts_rounds(tmp_path, monkeypatch, capsys):
    "The style review runs its files at the same time, prints them in order, counts only rounds with findings, and passes round 3."
    review = load_review()
    run_git(tmp_path, "init", "-q")
    (tmp_path / "x").write_text("x\n")
    run_git(tmp_path, "add", "x")
    run_git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    monkeypatch.setattr(review, "ROOT", tmp_path)
    monkeypatch.setattr(review, "FINDINGS", tmp_path / "reports" / "writing")
    monkeypatch.setattr(review.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(review, "text_of", lambda file, whole: f"text of {file}")
    calls, passing = [], set()

    def fake(text):  # A review that takes a while, and finds one problem unless the file passes
        start = time.monotonic()
        time.sleep(0.5 if "a.md" in text else 0.2)   # the first file ends last
        calls.append((text, start, time.monotonic()))
        if any(f in text for f in passing):
            return {"verdict": "pass", "findings": []}
        return {"verdict": "fail", "findings": [{"file": text[8:], "quote": "q", "problem": "p", "rewrite": "r"}]}

    monkeypatch.setattr(review, "review", fake)
    monkeypatch.setattr(sys, "argv", ["review.py", "docs/a.md", "docs/b.md"])

    def run() -> tuple[bool, str]:  # Whether the check failed, and what it printed
        calls.clear()
        try:
            review.main()
            failed = False
        except SystemExit:
            failed = True
        return failed, capsys.readouterr().out

    failed, out = run()
    assert failed and out.index("docs/a.md: FAIL (round 1 of 3)") < out.index("docs/b.md: FAIL (round 1 of 3)")
    (_, a0, a1), (_, b0, b1) = sorted(calls)
    assert a0 < b1 and b0 < a1   # the two reviews overlapped
    report = tmp_path / "reports" / "writing" / "docs" / "a.md.md"
    assert "# Round 1" in report.read_text() and not (tmp_path / "review-findings").exists()

    passing.add("b.md")   # a pass is not a round
    failed, out = run()
    assert failed and "docs/a.md: FAIL (round 2 of 3)" in out and "docs/b.md: pass" in out
    assert review.round_of("docs/b.md") == 2

    failed, out = run()   # round 3 of a.md: its findings stay for a person, and the check passes
    assert "docs/a.md: round 3, 1 findings left in reports/writing/docs/a.md.md" in out
    assert "# Round 3" in report.read_text() and "docs/b.md: pass" in out and not failed

    failed, out = run()   # after round 3, a.md is not reviewed again until the next commit
    assert not failed and "docs/a.md: round 3 is done" in out and len(calls) == 1


def test_a_do_line_that_writes_its_files_and_then_fails_is_fine(tmp_path: Path) -> None:
    """A do$ line such as `ruff check --fix` writes its fixes and then exits 1 for what it cannot fix. When the files it writes
    are the move's, timewalk-check says nothing of it; a failing line that writes nothing is still a problem."""
    from timewalk.walks import check, load_toc

    main = tmp_path / "proj"
    main.mkdir()
    run_git(main, "init", "-q", "-b", "main")
    (main / "f.txt").write_text("a\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "start")
    run_git(main, "tag", "-a", "step-00", "-m", "zero")
    (main / "f.txt").write_text("b\n")
    run_git(main, "commit", "-qam", "step-01.1: fixed")
    (main / "g.txt").write_text("g\n")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-qm", "step-01.2: more")
    run_git(main, "tag", "-a", "step-01", "-m", "one")
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "notes.md").write_text(
        "## step-00\n\n## step-01 One\n\n### step-01.1 Fixed\n\ndo$ sh -c 'echo b > f.txt; exit 1'\n\n$ ls\n\nfiles:\n- file `f.txt`: fixed.\n\n"
        "### step-01.2 More\n\ndo$ false\npatch$ g.txt\n\n$ ls\n\nfiles:\n- file `g.txt`: more.\n")
    toc = folder / "toc.toml"
    toc.write_text('[[walk]]\nid = "m"\nkind = "tutorial"\nnotes = "notes.md"\n')
    _, warnings = check(load_toc(toc), main, toc.parent, run=True)
    assert not any("step-01.1" in w for w in warnings), warnings
    assert any("step-01.2: the do$ line `false` fails" in w for w in warnings), warnings
    notes = (folder / "notes.md").read_text()
    for line, why in [("do$ sh -c 'echo b > f.txt; kill -9 $$'", "a signal"), ("do$ sh -c 'echo b > f.txt; exit 127'", "not found"),
                      ("do$ sh -c 'echo b > f.txt; exit 1'\ndo$ sh -c 'echo s > stray.txt'", "not the last line"),
                      ("do$ sh -c 'echo b > f.txt; exit 1'\npatch$ f.txt", "a patch$ line names its file")]:
        (folder / "notes.md").write_text(notes.replace("do$ sh -c 'echo b > f.txt; exit 1'", line))
        _, warnings = check(load_toc(toc), main, toc.parent, run=True)
        assert any("step-01.1: the do$ line" in w and "fails" in w for w in warnings), (why, warnings)
