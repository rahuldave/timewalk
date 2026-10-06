"""Tests for timewalk: the git layer, the notes and slides readers, and the web application's guards."""

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
    "A working copy on a commit that is not a step says so."
    run_git(sample, "commit", "--quiet", "--allow-empty", "--message", "after the steps")
    run_git(repo.work, "checkout", "--quiet", "--detach", run_git(sample, "rev-parse", "main"))
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


def test_edits_block_a_move(repo: timewalk.Repo) -> None:
    "Uncommitted edits to tracked files stop a move, and the working copy stays where it was."
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    assert repo.edits() == ["src/greet.py"]
    with pytest.raises(timewalk.GitError, match="uncommitted edits"):
        repo.move(2)
    assert repo.current() == 1
    assert (repo.work / "src" / "greet.py").read_text() == "edited\n"


def test_edits_are_set_aside_not_discarded(repo: timewalk.Repo) -> None:
    "Asked to, a move stashes the edits under a name that says where they were made."
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    repo.move(2, set_aside=True)
    assert repo.current() == 2
    assert repo.edits() == []
    assert "timewalk: edits made at step-01" in run_git(repo.work, "stash", "list")
    assert "edited" in run_git(repo.work, "stash", "show", "--patch", "stash@{0}")


def test_discard_throws_edits_away_and_replaces_only_the_files_the_step_has(sample: Path) -> None:
    "With --discard-edits a move never asks: edits go, a new file the step has is replaced, any other new file stays."
    repo = timewalk.Repo(sample, discard=True)
    repo.move(1)
    (repo.work / "src" / "greet.py").write_text("edited\n")
    (repo.work / "tests").mkdir()
    (repo.work / "tests" / "test_greet.py").write_text("mine\n")  # step-02 has this file
    (repo.work / "scratch.txt").write_text("keep\n")  # no step has this one
    repo.move(2)
    assert repo.current() == 2
    assert repo.edits() == []
    assert "edited" not in (repo.work / "src" / "greet.py").read_text()
    assert (repo.work / "tests" / "test_greet.py").read_text() == "def test_it():\n    assert True\n"
    assert (repo.work / "scratch.txt").read_text() == "keep\n"
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


def test_an_untracked_file_is_never_overwritten(repo: timewalk.Repo) -> None:
    "If a later step has a file where an untracked one sits, the move is refused and the file kept."
    (repo.work / "justfile").write_text("mine\n")
    with pytest.raises(timewalk.GitError, match="will not be overwritten: justfile"):
        repo.move(1)
    assert repo.current() == 0
    assert (repo.work / "justfile").read_text() == "mine\n"


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
    "A terminal or event socket without the token is closed before it is accepted."
    for path in ("/ws/term/replay", "/ws/events", "/ws/term/replay?t=wrong"):
        with pytest.raises(Exception), served.websocket_connect(path):  # noqa: B017 - any refusal will do
            pass


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


def test_api_reports_edits_instead_of_moving(served: TestClient, repo: timewalk.Repo) -> None:
    "A move over uncommitted edits answers 409 with the files, and succeeds when asked to set them aside."
    auth = {"t": TOKEN}
    served.post("/api/move", params=auth, json={"to": 1})
    (repo.work / "justfile").write_text("edited\n")
    refused = served.post("/api/move", params=auth, json={"to": 2})
    assert refused.status_code == 409
    assert refused.json() == {"error": "uncommitted edits", "edits": ["justfile"]}
    assert served.post("/api/move", params=auth, json={"to": 2, "set_aside": True}).json()["current"] == 2


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
    "The page shows the notes column, the Notes toggle and the PDF button only when timewalk has a notes file."
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


def test_a_scroll_in_one_window_reaches_the_other_windows(served: TestClient) -> None:
    "A window that scrolls says so on its event socket; the server passes it on to the other windows, not back."
    auth = f"?t={TOKEN}"
    with served.websocket_connect("/ws/events" + auth) as mine, served.websocket_connect("/ws/events" + auth) as theirs:
        mine.send_json({"type": "scroll", "pane": "notes", "at": 0.4})
        assert theirs.receive_json() == {"type": "scroll", "pane": "notes", "at": 0.4}
        mine.send_json({"type": "scroll", "pane": "nowhere", "at": 0.4})   # not a pane: ignored
        mine.send_json({"type": "scroll", "pane": "slide", "at": 7})       # kept between 0 and 1
        assert theirs.receive_json() == {"type": "scroll", "pane": "slide", "at": 1.0}


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
        watcher = asyncio.create_task(timewalk.watch_content(notes, manifest, hub, every=0.05))
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
