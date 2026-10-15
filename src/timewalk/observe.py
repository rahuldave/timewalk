"""Where each file of a move comes from: what its `do$` commands write, seen by running them.

A file of a move gets its content in one of two ways. A `do$` command writes it, such as `uv add ruff`, which writes
`pyproject.toml` and `uv.lock`, or the author brings it in with a patch. To tell which, timewalk runs the move's `do$`
commands, in order, at the commit before the move, in a scratch clone of the project. It notes the files that each
command writes. Then it compares what the commands left with the move's commit:

- a file that no command writes comes from a patch: its whole change;
- a file that a command writes and the commit has as the command left it comes from the command alone;
- a file that a command writes and the author then changed has a patch too: the diff from what the command left to
  the commit. A file that the walk's `loose` names and that matches loosely (a `uv.lock` with newer versions) has none.

The scratch clone is a `git clone --shared` in a folder of its own, made afresh for each move, which writes nothing in
the project. It has no remote, and git runs with no variable that points it at another repository, such as
`GIT_INDEX_FILE`. The result of
each move is kept in a cache, `~/.cache/timewalk/observed` (or `$XDG_CACHE_HOME/timewalk/observed`), by the commit before,
the commit and the commands, so a second check runs nothing. A run where a command fails is not kept. With
`--no-run`, or `TIMEWALK_NO_RUN=1`, nothing runs and nothing is read from the cache.
"""

import base64
import contextlib
import fnmatch
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from timewalk import GitError, blob_diff, git, loose_match, shell_environment

LAYOUT = 2   # the version of a cache file's layout
TIMEOUT = 900   # seconds that one command may run


def cache_folder() -> Path:  # Where the results of moves are kept
    "Find the cache folder: $XDG_CACHE_HOME/timewalk/observed, or ~/.cache/timewalk/observed."
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "timewalk" / "observed"


def running() -> bool:  # Whether commands run by default
    "Say whether this process runs do$ commands: not when TIMEWALK_NO_RUN is set."
    return os.environ.get("TIMEWALK_NO_RUN", "") in ("", "0")


# Variables that point git at another repository: a run in the scratch clone must never reach the project through them
GIT_PLACES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR",
              "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM")


def clean_environment() -> dict[str, str]:  # This process's environment, without the variables that point git elsewhere
    "Give git and the commands an environment that names no repository, so that git finds the scratch clone by its folder."
    return {name: value for name, value in os.environ.items() if name not in GIT_PLACES}


def scratch_git(
    cwd: Path,  # A folder of the scratch clone, or the folder that holds it
    *args: str,  # Arguments to git
) -> bytes:  # What git printed
    "Run git for the scratch clone, in a clean environment, and raise GitError with git's message when it fails."
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=clean_environment())
    if done.returncode:
        raise GitError(done.stderr.decode("utf-8", errors="replace").strip() or f"git {' '.join(args)} failed")
    return done.stdout


class Scratch:
    """A clone of the project in a folder of its own, made afresh for each move, for its commands to run in. It shares
    the project's objects, and has no remote, so that no command can push into the project."""

    def __init__(
        self,
        main: Path,  # The project
    ) -> None:
        self.main, self.folder, self.holder = main, None, None

    def at(
        self,
        commit: str,  # The commit to stand on
    ) -> Path:  # A new clone at that commit: no file, config, hook or stash that an earlier move's commands left
        "Make a new clone at a commit."
        self.close()
        self.holder = tempfile.mkdtemp(prefix="timewalk-run-")
        self.folder = Path(self.holder) / self.main.name   # the project's own name, for a command that reads its folder's name
        scratch_git(Path(self.holder), "clone", "--quiet", "--shared", "--no-checkout", "--no-hardlinks", str(self.main), str(self.folder))
        scratch_git(self.folder, "remote", "remove", "origin")
        scratch_git(self.folder, "checkout", "--quiet", "--force", "--detach", commit)
        return self.folder

    def close(self) -> None:
        "Remove the clone."
        if self.holder:
            shutil.rmtree(self.holder, ignore_errors=True)
        self.folder = self.holder = None


def file_mark(
    path: Path,  # A file of the working copy
) -> tuple[bytes, str]:  # Its contents, or a link's target, and its kind: "x" can run, "-" cannot, "l" a link
    "Read a file as git keeps it: a symbolic link is its target, never the file it points to."
    if path.is_symlink():
        return os.fsencode(os.readlink(path)), "l"
    return path.read_bytes(), "x" if os.access(path, os.X_OK) else "-"


def snapshot(
    folder: Path,  # A working copy
) -> dict[str, str]:  # Each file that git tracks or would track, to its blob and its kind
    "Note every file of a working copy that git does not ignore: its contents, and its kind, so that chmod +x counts as a write."
    listed = scratch_git(folder, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\x00")
    out = {}
    for raw in dict.fromkeys(listed):
        if not raw:
            continue
        path = os.fsdecode(raw)
        file = folder / path
        if not (file.is_symlink() or file.is_file()):
            continue   # deleted, or a folder such as a submodule
        data, kind = file_mark(file)
        blob = hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()   # git's own name for the contents
        out[path] = f"{blob} {kind}"
    return out


class Stop:
    """A way to end the runs of a server that stops: no command starts after it, and each one running is killed."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.groups: set[int] = set()   # the process groups of the commands that run now
        self.lock = threading.Lock()

    def stop(self) -> None:
        "Kill every command that runs, and start no more."
        with self.lock:
            self.stopped.set()
            for group in self.groups:
                with contextlib.suppress(OSError):
                    os.killpg(group, signal.SIGKILL)


def run_commands(
    folder: Path,  # The scratch clone, at the commit before the move
    commands: list[str],  # The move's do$ commands, in order
    stop: Stop | None = None,  # Ends the run when the server stops
) -> dict:  # What each command wrote, and the files as they left them; see observe
    """Run each command in a shell of its own process group, and note the files it adds, changes or removes. Its output
    goes to a file, not a pipe, so a process that a command leaves running does not hold the run up; each such process is
    stopped when its command ends."""
    env = {**shell_environment(clean_environment()), "TERM": "dumb"}
    env.pop("TIMEWALK_NO_RUN", None)
    shell = shutil.which("bash") or "/bin/sh"
    done, before, written = [], snapshot(folder), set()
    for text in commands:
        with tempfile.TemporaryFile() as said_file:
            with stop.lock if stop else contextlib.nullcontext():   # a stop either comes first, or sees the new group
                if stop and stop.stopped.is_set():
                    break   # the server stops: the run ends here, as a failed one does, and is not kept
                child = subprocess.Popen([shell, "-c", text], cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=said_file,
                                         stderr=subprocess.STDOUT, start_new_session=True)
                if stop:
                    stop.groups.add(child.pid)
            try:
                code = child.wait(timeout=TIMEOUT)
                said_file.seek(0)
                said = said_file.read().decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                code, said = -1, f"it ran for more than {TIMEOUT} seconds"
            finally:
                with contextlib.suppress(OSError):
                    os.killpg(child.pid, signal.SIGKILL)   # the command, if it ran too long, and anything it left running
                child.wait()
                if stop:
                    with stop.lock:
                        stop.groups.discard(child.pid)
        if stop and stop.stopped.is_set():
            code = code or -9   # killed by the stop: never a run to keep
        after = snapshot(folder)
        writes = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        for place in sorted({str(folder), os.path.realpath(folder)}, key=len, reverse=True):   # the scratch clone by its name: no path of this machine
            said = said.replace(place, folder.name)
        lines = [line for line in said.strip().splitlines() if line.strip()]
        done.append({"text": text, "writes": writes, "exit": code, "said": lines[-1] if lines else ""})
        written |= set(writes)
        before = after
        if code:
            break   # the commands after a failed one would run on a state that the move never had
    left = {}
    for path in sorted(written):
        if path in before:
            data, kind = file_mark(folder / path)
            left[path] = {"data": base64.b64encode(data).decode(), "exec": kind == "x", "link": kind == "l"}
        else:
            left[path] = None
    return {"layout": LAYOUT, "commands": done, "left": left}


MEMORY: dict[str, dict] = {}   # the results of this process, failed ones too, so that one check runs a move once


def run_key(
    parent: str,  # The commit before the move
    commit: str,  # The move's commit
    commands: list[str],  # Its do$ commands, in order
) -> str:  # The name of the run, in MEMORY and in the cache
    "Name a run of a move's commands."
    return hashlib.sha256(json.dumps([LAYOUT, parent, commit, commands]).encode()).hexdigest()


def forget(
    parent: str,  # The commit before the move
    commit: str,  # The move's commit
    commands: list[str],  # Its do$ commands, in order
) -> None:
    "Forget a failed run of this process, so that the next call runs the commands again, as when the network comes back."
    MEMORY.pop(run_key(parent, commit, commands), None)


def observe(
    main: Path,  # The project
    parent: str,  # The commit before the move
    commit: str,  # The move's commit
    commands: list[str],  # Its do$ commands, in order
    scratch: Scratch,  # The clone to run them in
    stop: Stop | None = None,  # Ends the run when the server stops
) -> dict:  # {"commands": [{text, writes, exit, said}], "left": {path: {data, exec} or None}}
    "Run a move's commands at the commit before it, or read what they did from the cache."
    key = run_key(parent, commit, commands)
    if key in MEMORY:
        return MEMORY[key]
    kept = cache_folder() / f"{key}.json"
    try:
        found = json.loads(kept.read_text(encoding="utf-8"))
        if well_formed(found, commands):
            MEMORY[key] = found
            return found
    except (OSError, ValueError):
        pass   # no cache, or a broken one: run again
    found = run_commands(scratch.at(parent), commands, stop)
    if stop and stop.stopped.is_set():
        return found   # cut short by a server that stops: neither kept nor remembered
    MEMORY[key] = found
    if len(found["commands"]) == len(commands) and all(command["exit"] == 0 for command in found["commands"]):
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            partial = kept.with_suffix(f".{os.getpid()}.part")
            partial.write_text(json.dumps(found), encoding="utf-8")
            partial.replace(kept)
        except OSError:
            pass   # a cache that cannot be written only costs time
    return found


def well_formed(
    found: object,  # A result read from the cache
    commands: list[str],  # The commands it should be for
) -> bool:  # Whether it has the layout of a run of these commands
    "Check a cached result before it is used, so that a broken file means a new run, and not an error."
    try:
        return (isinstance(found, dict) and found.get("layout") == LAYOUT and [c["text"] for c in found["commands"]] == commands
                and all(isinstance(c["writes"], list) and isinstance(c["exit"], int) for c in found["commands"])
                and isinstance(found["left"], dict) and all(v is None or {"data", "exec", "link"} <= set(v) for v in found["left"].values()))
    except (KeyError, TypeError, AttributeError):
        return False


def blob_of(
    main: Path,  # The project
    commit: str,  # A commit
    path: str,  # A file
) -> tuple[bytes | None, str]:  # The file at that commit, or None, and its kind: "x" can run, "-" cannot, "l" a link, "" none
    "Read a file of a commit, with its mode."
    entry = git(main, "--literal-pathspecs", "ls-tree", "-z", commit, "--", path).split("\x00")[0]
    if "\t" not in entry:
        return None, ""
    mode, _, blob = entry.split("\t", 1)[0].split()
    done = subprocess.run(["git", "cat-file", "blob", blob], cwd=main, capture_output=True)
    return (done.stdout if done.returncode == 0 else None), {"100755": "x", "120000": "l"}.get(mode, "-")


def sources(
    main: Path,  # The project
    commit: str,  # The move's commit
    files: list[dict],  # Its files, as facts.change_files lists them
    found: dict,  # What its commands did, from observe
    loose: list[str],  # The walk's loose globs
    config: list[str] | None = None,  # git options for every diff, so that it reads the same on any machine
) -> tuple[dict[str, tuple[int | str, str]], list[tuple[int, str]]]:  # Each file's source and patch; each write outside the move
    """Say where each file of a move comes from: the index of the last command that writes it, or "patch". Its patch is
    what a person changes by hand: the whole diff for a patch, and for a file of a command, the diff from what the
    command left to the commit, "" when there is none. Also list each write of a command to a file that the move does
    not change."""
    out: dict[str, tuple[int | str, str]] = {}
    mine = {p for f in files for p in (f["path"], f["old_path"]) if p}
    for file in files:
        by = None
        for at, command in enumerate(found["commands"]):
            if file["path"] in command["writes"]:
                by = at
        if by is None:
            out[file["path"]] = ("patch", file["diff"])
            continue
        left = found["left"].get(file["path"])
        data = base64.b64decode(left["data"]) if left else None
        wanted, kind = blob_of(main, commit, file["path"])
        same = data == wanted and (left is None or ("l" if left["link"] else "x" if left["exec"] else "-") == kind)
        if not same and any(fnmatch.fnmatchcase(file["path"], glob) for glob in loose) and loose_match(file["path"], data, wanted):
            same = True   # newer versions than the commit's, on a later day: the walk's loose says that is the same
        out[file["path"]] = (by, "" if same else blob_diff(file["path"], data, wanted, config or []))
    outside = [(at, path) for at, command in enumerate(found["commands"]) for path in command["writes"] if path not in mine]
    return out, outside
