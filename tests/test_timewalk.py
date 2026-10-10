"""Tests for timewalk: the git layer, the notes and slides readers, and the web application's guards."""

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
    served.post("/api/notes", params=auth, json={"step": "step-09", "base": "", "text": "New notes."})
    assert timewalk.parse_notes(notes.read_text())["step-09"]["text"] == "New notes."


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


def test_the_checks_of_a_tutorial(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "Each move needs a ### section, in the order of the commits, and a commit's subject starts with its move's name."
    from timewalk.walks import check, load_toc

    errors, warnings = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert (errors, warnings) == ([], [])
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.2 B\n### step-01.1 A\n### step-01.4 D\n\n## step-02\n")
    (tutorial_class / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\n"step-01.9" = ["deck.md#3"]\nstep-02 = ["deck.md#6"]\n')
    errors, _ = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert "walk tut: notes.md has a section ### step-01.4, which is not a move of step-01" in errors
    assert "walk tut: step-01.3 has no section ### step-01.3 in notes.md" in errors
    assert "walk tut: the ### sections of step-01 in notes.md are not in the order of the commits: step-01.2, step-01.1" in errors
    assert "walk tut: slides.toml has an entry for step-01.9, which is not a step or a move of the walk" in errors
    assert not any("has the subject" in e for e in errors), "the subjects are right"


def test_a_wrong_subject_is_an_error(tutorial: timewalk.Repo, tutorial_class: Path) -> None:
    "A move whose commit subject does not start with its name is named, with the subject to write."
    from timewalk.walks import check, load_toc

    run_git(tutorial.main, "checkout", "--quiet", "--detach", tutorial.steps[0].sha)
    run_git(tutorial.main, "commit", "--quiet", "--allow-empty", "--message", "an untidy subject")
    run_git(tutorial.main, "commit", "--quiet", "--allow-empty", "--message", "step-01: the end")
    run_git(tutorial.main, "tag", "--force", "--annotate", "step-01", "--message", "again")
    run_git(tutorial.main, "tag", "--delete", "step-02")
    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n### step-01.1 A\n### step-01.2 B\n")
    (tutorial_class / "slides" / "slides.toml").write_text('[slides]\nstep-00 = ["deck.md#1"]\nstep-01 = ["deck.md#2"]\n')
    errors, _ = check(load_toc(tutorial_class / "toc.toml"), tutorial.main, tutorial_class)
    assert any("move step-01.1, has the subject 'an untidy subject'; start it with 'step-01.1:'" in e for e in errors), errors


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
    from timewalk.walks import add_move_sections, check, load_toc, move_summary

    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01 S\n\n### step-01.1 Mine\nmy words\n\n$ ls\n\n## step-02\n")
    walk = load_toc(tutorial_class / "toc.toml")[0]
    text, added, drafts = add_move_sections(walk, tutorial.main)
    assert [d.split("\n", 1)[0] for d in drafts] == ["### step-01.2 b", "### step-01.3 c"]
    assert added == ["step-01.2", "step-01.3"]
    assert "### step-01.1 Mine\nmy words\n\n$ ls" in text
    assert "### step-01.2 b\n" in text and "### step-01.3 c\n" in text, "titles without the move's or the step's prefix"
    assert move_summary(tutorial.main, tutorial.moves[1][1].sha) == ["- `README.md`: 1 line added, 1 removed", "- `b.txt`: a new file of 1 line"]
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
    from timewalk.walks import move_summary

    summary = move_summary(main, run_git(main, "rev-parse", "HEAD"))
    assert summary == ["- `justfile`: 3 lines added; adds `recipe check`", "- `m.py`: 6 lines added, 2 removed; adds `class C`; changes `a`, `b`"]


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
    assert not any("with b" in w or "a.txt" in w for w in warnings)


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
    from timewalk.walks import move_summary
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
    summary = "\n".join(move_summary(main, "HEAD"))
    assert "`sp ace.py`: a new file of 2 lines; adds `b`" in summary and "`café.py`: a new file of 2 lines; adds `b`" in summary


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
    "A drafted move lists its files as items: file for a new one, diff for a changed one, with the words left to write."
    from timewalk.walks import add_move_sections, load_toc

    (tutorial_class / "notes.md").write_text("## step-00\n\n## step-01\n\n## step-02\n")
    _, _, drafts = add_move_sections(load_toc(tutorial_class / "toc.toml")[0], tutorial.main)
    assert "files:\n- diff `README.md`: \n- file `b.txt`: \n" in drafts[1]


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
    assert not any("a.txt" in w for w in warnings)


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
