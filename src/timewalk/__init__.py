"""timewalk: browse a repository one commit at a time, with a terminal that runs in it.

    timewalk /path/to/repo                        steps are the tags matching step-*
    timewalk /path/to/repo --notes notes.md       add the notes, the script of each step, beside the page
    timewalk /path/to/repo --slides slides.toml   add slides, one or more per step
    timewalk /path/to/repo --tags 'v*'            steps are other tags
    timewalk /path/to/repo --commits              steps are the commits on the current branch
    timewalk /path/to/repo --in-place             step the repository itself, not a second copy
    timewalk repo --replay worktree               put the replay copy in ./worktree instead of beside the repository
    timewalk /path/to/repo --clock                show the clock band, for a class with planned times
    timewalk /path/to/repo --port 8800            listen on another port
    timewalk /path/to/repo --host 0.0.0.0         listen beyond this machine, for a cloud machine; see the warning

One page is served, at one address. It shows the slides for the step, the files as they are at that step, which
of them that step added or changed, and terminal tabs built on Ghostty's terminal core: one in the repository at
that step, a second one there for commands that take a while, one in the repository you started from, one that
starts an assistant (Claude Code by default) at that step, and as many extra shells as you open. Files can be read,
not edited. With --notes, a column beside them shows the notes, the script of each step, with its commands as
buttons; a toggle there edits the step's section of the notes file. With --clock, a band across the top shows a
clock against the planned times. Several windows can show the page at once: the step, slide, layout, open file
and terminal tab are kept here, so they all agree.

By default the stepping happens in a second working copy, `<repo>-replay`, a clone of the repository on the
branch timewalk/replay, so the repository you point at is never moved, and never written to. Moving never deletes an untracked file, so whatever a command wrote
there (a virtual environment, a database, a run's output) stays where it is.

The server listens on localhost only, unless --host says otherwise, and every request needs the token in the
address it prints, because a terminal in a web page is a way to run commands on this machine.
"""

import argparse
import asyncio
import contextlib
import fcntl
import fnmatch
import functools
import itertools
import json
import math
import os
import pty
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
import webbrowser
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

HERE = Path(__file__).resolve().parent
BRANCH = "timewalk/replay"  # the branch of the replay copy, which each move resets to its commit
SAVED = "timewalk/saved/"  # where the replay copy keeps commits a learner made, when a move resets the branch
HOME = "home"  # the name, in the replay copy, of the repository you pointed at
HOME_TAGS = "refs/timewalk/home-tags/"  # in the replay copy, your repository's tags as last fetched; the learner's own tags are apart
HOME_HEAD = "refs/timewalk/home-head"  # in the replay copy, your repository's HEAD, for --commits on a detached HEAD
PLACED = "refs/timewalk/placed"  # in the replay copy, the commit where timewalk last put the branch; later commits are a learner's
MAX_FILE_BYTES = 400_000  # larger files are not shown
SCROLLBACK_BYTES = 256_000  # terminal output replayed to a page that reconnects


class GitError(RuntimeError):
    "A git command failed; the message is what git said."


def git(
    cwd: Path,  # Directory to run git in
    *args: str,  # Arguments to git
    input_text: str | None = None,  # Text for git's standard input, if any
    env: dict[str, str] | None = None,  # Variables to add to the environment, such as GIT_INDEX_FILE
) -> str:  # What git printed, without the trailing newline
    """Run git and return its output, raising `GitError` with git's own message when it fails.

    Another git in the same copy, such as the edits watcher's `git status` or a shell prompt's, holds `index.lock` for
    a moment; a command that meets it waits a little and runs again.
    """
    # A stash is never run twice: it may have stored the edits before it met the lock.
    for wait in (0.1, 0.2, 0.4, None) if args[:1] != ("stash",) else (None,):
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", input=input_text,
                              env={**os.environ, **env} if env else None)
        if done.returncode == 0 or wait is None or "index.lock" not in done.stderr:
            break
        time.sleep(wait)
    if done.returncode != 0:
        raise GitError(done.stderr.strip() or done.stdout.strip() or f"git {' '.join(args)} failed")
    return done.stdout.rstrip("\n")


def name_status(
    cwd: Path,  # The working copy
    *args: str,  # A git command that prints --name-status -z, such as diff or diff-tree, with its arguments
) -> dict[str, str]:  # Each path, as it is, to its status letter: A, M or D
    "Run a git command that lists changed files with -z, and read it: a path with a space or an accent comes as it is."
    fields = git(cwd, args[0], "-z", *args[1:]).split("\x00")   # -z before any paths that follow --
    return {path: status[0] for status, path in zip(fields[::2], fields[1::2], strict=False) if status and path}


def blob_diff(
    path: str,  # The file's path, for git to choose how to read it
    old: bytes | None,  # The file before, or None where it does not exist
    new: bytes | None,  # The file after, or None
    config: list[str] | None = None,  # git options before the command, such as -c diff.context=3
) -> str:  # The diff from its first hunk, ending in a newline; "" when they are the same, or binary
    "Compare two copies of a file that need not be in any commit, such as a learner's file and a move's, with git's own diff."
    with tempfile.TemporaryDirectory(prefix="timewalk-diff-") as folder:
        sides = []
        for name, data in (("a", old), ("b", new)):
            if data is None:
                sides.append(os.devnull)
                continue
            side = Path(folder) / name / path.rsplit("/", 1)[-1]   # git reads the attributes of the file's own name
            side.parent.mkdir()
            side.write_bytes(data)
            sides.append(str(side))
        done = subprocess.run(["git", *(config or []), "diff", "--no-index", "--no-color", "--no-ext-diff", "--no-textconv", "--", *sides],
                              cwd=folder, capture_output=True)
    if done.returncode not in (0, 1):
        raise GitError(done.stderr.decode("utf-8", errors="replace").strip() or "git diff --no-index failed")
    lines = done.stdout.decode("utf-8", errors="replace").split("\n")
    hunk = next((at for at, line in enumerate(lines) if line.startswith("@@")), None)
    return "" if hunk is None else "\n".join(lines[hunk:])


def blob_at_commit(
    cwd: Path,  # A repository
    commit: str,  # A commit
    path: str,  # A file
) -> bytes | None:  # The file at that commit, or None if it has no such file
    "Read one file of a commit, as bytes."
    done = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=cwd, capture_output=True)
    return done.stdout if done.returncode == 0 else None


@dataclass
class Step:
    "One point in the history that the browser can stand on."

    index: int  # Position in the sequence, from 0
    name: str  # Tag name, or a short hash when stepping through commits
    sha: str  # The commit
    subject: str  # First line of the commit message
    note: str  # The annotated tag's message, or the rest of the commit message. Public: the audience sees it
    start: str = ""  # In a walk that selects, the commit before the step: the tag before it, or the commit before a small step's first move
    section: str = ""  # A small step's, or a narrative's stop at a move: the step whose section of the notes it reads its moves from
    title: str = ""  # In a walk that selects, the title that its entry gives, such as { range = "step-05.3..step-05.6", title = "Types" }


@dataclass
class Move:
    "One small commit inside a step of a tutorial: the class makes the step one move at a time."

    name: str  # The step's name and the move's number, such as `step-02.1`
    sha: str  # The commit
    subject: str  # First line of the commit message, which starts with the name and a colon, except on the step's own commit


def step_moves(
    main: Path,  # The repository
    steps: list[Step],  # The steps of the walk
) -> list[list[Move]]:  # For each step, its moves in order; empty for a step of one commit, and for the first step
    """Find the moves of each step: the commits after the step before it, on the first-parent line, up to the step's own.

    A step of one commit has no moves. The first step has none either: it is where the walk starts.
    """
    moves: list[list[Move]] = [[]]
    for before, step in itertools.pairwise(steps):
        raw = git(main, "log", "--first-parent", "--reverse", "--format=%H%x00%s", f"{before.sha}..{step.sha}")
        commits = [line.split("\x00", 1) for line in raw.splitlines() if line]
        moves.append([Move(f"{step.name}.{n}", sha, subject) for n, (sha, subject) in enumerate(commits, 1)] if len(commits) > 1 else [])
    return moves


def tag_steps(
    main: Path,  # The repository
    pattern: str,  # Glob for tag names
) -> list[Step]:  # Steps in tag-name order
    "Read the tags that mark steps. An annotated tag's message becomes the step's note."
    fmt = "%(refname:short)%00%(objecttype)%00%(objectname)%00%(*objectname)%00%(contents)%01"
    raw = git(main, "for-each-ref", f"--format={fmt}", "--sort=refname", f"refs/tags/{pattern}")
    steps = []
    for record in filter(None, (r.strip("\n") for r in raw.split("\x01"))):
        name, kind, sha, peeled, contents = record.split("\x00")
        commit = peeled if kind == "tag" else sha
        note = contents.strip() if kind == "tag" else ""
        steps.append(Step(len(steps), name, commit, git(main, "log", "-1", "--format=%s", commit), note))
    return steps


def pick_steps(
    steps: list[Step],  # Every step that the tags give
    names: list[str] | None,  # The names to keep, or None for all
) -> list[Step]:  # The steps kept, in their order, numbered again from 0
    "Keep some steps, for a walk on one part of the history. A name that is not a step raises GitError."
    if names is None:
        return steps
    known = {step.name for step in steps}
    missing = [name for name in names if name not in known]
    if missing:
        raise GitError(f"these steps are not tags of the repository: {', '.join(missing)}")
    kept = [step for step in steps if step.name in set(names)]
    return [Step(i, s.name, s.sha, s.subject, s.note) for i, s in enumerate(kept)]


def select_entry(
    entry: str | dict,  # An entry of select: "step-05.3..step-05.6", or { range = "step-05.3..step-05.6", title = "Types" }
) -> tuple[str, str]:  # The step, move or range it takes, and its title, or ""
    "Read one entry of select, a name or a table with a title."
    return (entry, "") if isinstance(entry, str) else (entry["range"], entry.get("title", "").strip())


def select_steps(
    main: Path,  # The repository
    tags: str,  # Glob for the tags that mark the steps of the whole history
    select: list[str | dict],  # Steps, moves and ranges of moves, in order: "step-04", "step-10.3", "step-05.3..step-05.6"; see select_entry
    tutorial: bool,  # A tutorial's stops have moves; a narrative's do not
) -> tuple[list[Step], list[list[Move]]]:  # The walk's steps, and the moves of each
    """Take a smaller walk out of a big one.

    A whole step keeps its moves, and its Start is the tag before it. A single move, or a range of moves of one step, is
    a small step: its Start is the commit before its first move, and its moves keep their names, so the notes, the commits
    and the selection agree. A small step's name is its move, or "step-05.3 to step-05.6". A narrative takes steps and
    single moves, each as a stop with no moves. Raises GitError for a name that is not a step or a move, a range in a
    narrative, and a selection out of order or that overlaps itself. An entry's title is the stop's title.
    """
    every = tag_steps(main, tags)
    if not every:
        raise GitError(f"no steps found: no tags match {tags!r}")
    moves = step_moves(main, every)
    where = {move.name: (i, k) for i, found in enumerate(moves) for k, move in enumerate(found, 1)}
    index = {step.name: i for i, step in enumerate(every)}
    steps: list[Step] = []
    walk_moves: list[list[Move]] = []
    last = (-1, 0)   # the end of the entry before: (step, its last move), with a whole step at its last move
    for given in select:
        entry, title = select_entry(given)
        first, ranged, end = entry.partition("..")
        if ranged and not (first and end):
            raise GitError(f"select names {entry}: a range has a move at each end, for example step-05.3..step-05.6")
        if end and not tutorial:
            raise GitError(f"select names {entry}: a narrative takes steps and single moves, not a range of moves")
        if not end and first in index:
            i = index[first]
            # a tutorial's whole step spans its moves; a narrative's stops at its tag, after the moves of the step
            span = ((i, 0), (i, len(moves[i]))) if tutorial else ((i, len(moves[i])), (i, len(moves[i])))
        elif first in where and (not end or end in where):
            (i, a), (j, b) = where[first], where[end or first]
            if j != i or b < a:
                raise GitError(f"select names {entry}: a range of moves goes forward inside one step, for example step-05.3..step-05.6")
            span = ((i, a), (i, b))
        else:
            wrong = next((name for name in (first, end) if name and name not in index and name not in where), None)
            if wrong is None:
                raise GitError(f"select names {entry}: a range goes from a move to a move of the same step, for example step-05.3..step-05.6")
            raise GitError(f"select names {wrong}, which is not a step or a move of the history")
        if span[0] <= last:
            raise GitError(f"select names {entry} out of order, or over a part taken before it; name the steps and moves in the order of the history")
        last = span[1]
        (i, a), (_, b) = span
        step, before = every[i], every[i - 1].sha if i else ""
        if not end and first in index:
            steps.append(Step(len(steps), step.name, step.sha, step.subject, step.note, before, title=title))
            walk_moves.append(moves[i] if tutorial else [])
            continue
        taken = moves[i][a - 1:b]
        start = moves[i][a - 2].sha if a > 1 else before
        name = taken[0].name if a == b else f"{taken[0].name} to {taken[-1].name}"
        steps.append(Step(len(steps), name, taken[-1].sha, taken[-1].subject, "", start, step.name, title))
        walk_moves.append(taken if tutorial else [])
    return steps, walk_moves


class Repo:
    "The repository being browsed: its steps, and the working copy that moves between them."

    def __init__(
        self,
        main: Path,  # The repository the user pointed at
        tags: str = "step-*",  # Glob for the tags that mark steps
        commits: bool = False,  # Step through commits instead of tags
        in_place: bool = False,  # Move `main` itself instead of a second working copy
        discard: bool = False,  # In a replay worktree of an older version: a move throws edits away. A replay clone always keeps them
        replay: Path | None = None,  # Where the replay copy goes, instead of `<repo>-replay` beside the repository
    ):
        if discard and in_place:
            # Discarding is for a throwaway replay copy. In place, it would throw away uncommitted work in the real repository.
            raise GitError("--discard-edits cannot be used with --in-place: it would throw away uncommitted work in the repository itself")
        if replay is not None and in_place:
            raise GitError("--replay cannot be used with --in-place: in place, there is no replay copy")
        self.discard = discard
        self.replay = Path(replay).resolve() if replay is not None else None
        self.branch: str | None = None  # BRANCH in a replay clone; None in place, or in a replay worktree of an older version
        self.legacy = False  # whether the replay copy is a worktree, as older versions made it
        self.main = Path(git(main, "rev-parse", "--show-toplevel"))
        self.steps = self._commit_steps() if commits else tag_steps(self.main, tags)
        if not self.steps:
            raise GitError(f"no steps found: no tags match {tags!r}. Use --commits to step through commits instead.")
        self.work = self.main if in_place else self._replay_copy()
        self.moves: list[list[Move]] = [[] for _ in self.steps]  # For a tutorial, the moves of each step
        self.history: dict[str, Move] = {}  # In a walk that selects, every move of the whole history, which an item of the notes may name
        self.at: tuple[int, int] | None = None  # In a tutorial, the step and move the last move went to
        self.kept: list[str] = []  # The branches that the last move kept a learner's work on
        self.files_of: dict[str, list[dict]] = {}  # The files each move's commit added or changed, by commit

    def select(
        self,
        tags: str,  # Glob for the tags that mark the walk's steps
        names: list[str] | None = None,  # The steps of the walk, or None for every tag that matches
        tutorial: bool = False,  # Find the moves between the steps, for a tutorial
        select: list[str | dict] | None = None,  # Steps, moves and ranges of moves to take, in place of names; see select_steps
    ) -> None:
        "Use the steps of another walk. The working copy does not move; `move` does that."
        if self.branch:
            self._fetch(self.work)   # a tag made since the start needs its commit in the clone
        self.history = {}
        if select:
            self.steps, self.moves = select_steps(self.main, tags, select, tutorial)
            every = tag_steps(self.main, tags)
            self.history = {move.name: move for found in step_moves(self.main, every) for move in found}
            self.at = None
            return
        steps = pick_steps(tag_steps(self.main, tags), names)
        if not steps:
            raise GitError(f"no steps found: no tags match {tags!r}")
        self.steps = steps
        self.moves = step_moves(self.main, steps) if tutorial else [[] for _ in steps]
        self.at = None

    def commit_at(
        self,
        index: int,  # A step
        move: int | None,  # A move of it: 0 is before its first move, which is the step before; None for a step without moves
    ) -> str:  # The commit the working copy stands on there
        "Find the commit of a place in a tutorial: a step, and how many of its moves are made."
        if not self.moves[index] or move is None:
            return self.steps[index].sha
        return self.before(index) if move == 0 else self.moves[index][move - 1].sha

    def before(
        self,
        index: int,  # A step of the walk
    ) -> str | None:  # The commit before it: its own Start in a walk that selects, else the step before; None for the first step
        "Find the commit that a step's change starts from."
        return self.steps[index].start or (self.steps[index - 1].sha if index else None)

    def position(self) -> tuple[int | None, int | None]:  # The step, and the move within it (None for a step without moves)
        """Say where the working copy stands: on a step, and in a tutorial on one of its moves.

        A step's last move is the step's own commit, and its move 0 is the step before, so one commit can be two
        places. The place the last move went to wins, while the working copy is still there.
        """
        head = git(self.work, "rev-parse", "HEAD")
        if self.at is not None and self.commit_at(*self.at) == head:
            return self.at
        if self.at is None and any(self.moves):
            kept = self.kept_place(head)  # after a restart: the place the last run left, if the copy is still there
            if kept is not None:
                self.at = kept
                return kept
        for step in self.steps:
            if step.sha == head:
                return step.index, (len(self.moves[step.index]) if self.moves[step.index] else None)
        for index, moves in enumerate(self.moves):
            for number, move in enumerate(moves, 1):
                if move.sha == head:
                    return index, number
        for index, moves in enumerate(self.moves):   # the Start of a step whose commit before it is not a stop of the walk
            if moves and self.before(index) == head:
                return index, 0
        return None, None

    def _commit_steps(self) -> list[Step]:  # Steps in history order, oldest first
        "Use every commit on the current branch's first-parent history as a step."
        raw = git(self.main, "log", "--first-parent", "--reverse", "--format=%H%x00%h%x00%s%x00%b%x01")
        steps = []
        for record in filter(None, (r.strip("\n") for r in raw.split("\x01"))):
            sha, short, subject, body = record.split("\x00")
            steps.append(Step(len(steps), short, sha, subject, body.strip()))
        return steps

    def _replay_copy(self) -> Path:  # The working copy that will be moved between steps
        """Find or make the replay copy, beside the repository or where --replay says, starting at the first step.

        The replay copy is a clone of the repository, on a branch of its own. A clone shares nothing with your
        repository: what a learner does there, a commit, a branch, a stash, a change to git's config, stays there.
        Its objects are hard links, so it costs little. Each start fetches your tags and branches into it, so a step
        that you tagged again is seen. A replay copy that an older version made as a worktree is used as it is.
        """
        path = self.replay or self.main.parent / f"{self.main.name}-replay"
        if path.resolve().is_relative_to(self.main.resolve()):
            raise GitError(f"the replay copy {path} would be inside the repository. Put it outside, for example beside it.")
        known = [line.split(" ", 1)[1] for line in git(self.main, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
        if str(path) in known or str(path.resolve()) in known:
            self.legacy = True  # a worktree: it shares the config and the branches of your repository
            return path
        if (path / ".git").is_dir():
            try:
                home = git(path, "remote", "get-url", HOME)
            except GitError:
                home = ""
            if home and Path(home).resolve() == self.main.resolve():
                self._fetch(path)
                self.branch = BRANCH
                return path
            if home:
                raise GitError(f"{path} is a replay copy of {home}, not of {self.main}. If the repository moved, point the copy at "
                               f"it, which keeps what a learner did there: git -C {path} remote set-url {HOME} {self.main}")
        if path.exists():
            raise GitError(f"{path} exists and is not a working copy of this repository. Remove it or use --in-place.")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Made in a folder of its own, then renamed: a start that stops halfway leaves no half-made replay copy behind.
        making = path.with_name(f".{path.name}.making")
        if making.exists():
            shutil.rmtree(making)   # what an earlier start left halfway; nothing in it was ever used
        # A local path clones with hard links where it can, and copies where it cannot, for example onto another disk.
        git(self.main.parent, "clone", "--quiet", "--no-checkout", "--origin", HOME, str(self.main), str(making))
        self._fetch(making)
        git(making, "checkout", "--quiet", "-B", BRANCH, self.steps[0].sha)   # -B: your repository may have a branch of that name
        for other in git(making, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines():
            if other != BRANCH:
                git(making, "branch", "--quiet", "-D", other)   # the clone's own copy of your branches would only confuse
        git(making, "update-ref", PLACED, self.steps[0].sha)
        making.rename(path)
        self.branch = BRANCH
        return path

    def _fetch(
        self,
        path: Path,  # The replay clone
    ) -> None:
        """Bring the replay clone up to date with your repository: its branches, its HEAD, and its tags, moved ones too.

        Your tags are fetched into a place of their own, HOME_TAGS, and copied to the tags a learner sees only where the
        learner has no tag of that name, or has the copy that timewalk made. A tag that your repository dropped goes only
        if it is still that copy. So a learner's own tags are never moved or deleted.
        """
        before = self._refs(path, HOME_TAGS)
        git(path, "fetch", "--quiet", "--force", "--prune", "--no-tags", HOME,
            f"+refs/heads/*:refs/remotes/{HOME}/*", f"+refs/tags/*:{HOME_TAGS}*", f"+HEAD:{HOME_HEAD}")
        after = self._refs(path, HOME_TAGS)
        seen = self._refs(path, "refs/tags/")
        for name, sha in after.items():
            if seen.get(name) in (None, before.get(name)) and seen.get(name) != sha:
                git(path, "update-ref", f"refs/tags/{name}", sha)
        for name, sha in before.items():
            if name not in after and seen.get(name) == sha:
                git(path, "update-ref", "-d", f"refs/tags/{name}")

    @staticmethod
    def _refs(
        path: Path,  # A repository
        prefix: str,  # A folder of refs, such as refs/tags/
    ) -> dict[str, str]:  # Each ref's name under the prefix, and the object it points at
        "List the refs under a prefix, with what they point at."
        out = git(path, "for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
        return {name.removeprefix(prefix): sha for name, sha in (line.split("\x00") for line in out.splitlines() if line)}

    def keep_learner_commits(
        self,
        label: str,  # The step or move the copy was at, for the name of the branch that keeps them
        snapshot: str | None = None,  # A commit of the learner's edits on top of HEAD, from `save_work`, kept in place of HEAD
    ) -> list[str]:  # The branches that keep them; empty when there was nothing to keep
        """Before a move resets the branch, keep the commits that a learner made, on a branch named for the place.

        A learner's commits are those made since timewalk last put the branch somewhere, on the branch or on a detached
        HEAD. A branch that the learner made keeps its own commits, so they are not copied.
        """
        if self.branch is None:
            return []
        placed = subprocess.run(["git", "rev-parse", "--verify", "--quiet", PLACED], cwd=self.work, capture_output=True, text=True).stdout.strip()
        head = git(self.work, "rev-parse", "HEAD")
        tips = {snapshot or head}
        branch = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}"], cwd=self.work, capture_output=True, text=True).stdout.strip()
        if branch and not (snapshot and branch == head):   # the snapshot sits on top of HEAD, and keeps its commits too
            tips.add(branch)
        # Never a learner's: what a tag or a branch of your repository reaches, and what came before timewalk last put the branch.
        before = ([placed] if placed else []) + [f"--glob={HOME_TAGS}*", f"--remotes={HOME}", f"--glob={HOME_HEAD}"]
        kept = []
        for tip in sorted(tips, key=lambda tip: (tip != snapshot, tip)):   # the edits' branch first
            if not git(self.work, "rev-list", tip, "--not", *before, f"--exclude={self.branch}", "--branches"):
                continue
            names = set(git(self.work, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{SAVED}").splitlines())
            base = SAVED + (re.sub(r"\.\.+", "-", re.sub(r"[^A-Za-z0-9._-]+", "-", label)).strip("-.") or "step")   # a name git accepts, with no folders of its own
            name, n = base, 1
            while name in names:
                n += 1
                name = f"{base}-{n}"
            git(self.work, "branch", name, tip)
            kept.append(name)
        return kept

    def place_name(self) -> str:  # The name of the step or move where timewalk last put the branch, for a saved branch
        "Name the place of the commit where timewalk last put the branch: a step, or a move of a tutorial."
        placed = subprocess.run(["git", "rev-parse", "--verify", "--quiet", PLACED], cwd=self.work, capture_output=True, text=True).stdout.strip()
        if self.at is not None:
            index, move = self.at
            return self.moves[index][move - 1].name if move else self.steps[index].name
        for step in self.steps:
            if step.sha == placed:
                return step.name
        for moves in self.moves:
            for move in moves:
                if move.sha == placed:
                    return move.name
        # A stop of another walk, such as a tag that a walk which selects leaves out
        tags = git(self.work, "tag", "--points-at", placed, "--list", "--sort=refname").split() if placed else []
        return tags[0] if tags else "between-steps"

    def in_the_way(
        self,
        *commits: str,  # The commits whose files a move writes: HEAD, which `reset --hard` puts back, and the target
    ) -> tuple[list[str], list[str]]:  # The untracked files the move would replace or delete, and the nested repositories in its way
        """Find every untracked file, ignored or not, that the files of these commits would replace or delete.

        A file is in the way when a commit has a file of its name, a folder of its name (files under it), or a file where
        one of its folders is. Names are compared ignoring case when git does, as on a Mac. A git repository of its own
        inside the copy shows as one folder; in the way, it cannot be kept, so the move refuses.
        """
        ignorecase = subprocess.run(["git", "config", "--bool", "core.ignorecase"], cwd=self.work, capture_output=True, text=True).stdout.strip()
        fold = str.lower if ignorecase == "true" else str
        files = {fold(path) for commit in commits for path in git(self.work, "ls-tree", "-r", "-z", "--name-only", commit).split("\x00") if path}
        folders = {path.rsplit("/", i)[0] for path in files for i in range(1, path.count("/") + 1)}

        def blocks(path: str) -> bool:
            name = fold(path.rstrip("/"))
            parents = [name.rsplit("/", i)[0] for i in range(1, name.count("/") + 1)]
            return name in files or name in folders or any(parent in files for parent in parents)

        others = [path for path in git(self.work, "ls-files", "--others", "-z").split("\x00") if path]
        nested = [path for path in others if path.endswith("/") and blocks(path)]   # a repository of its own shows as a folder
        # A repository of its own where a tracked file was: git lists nothing under it, and reset or stash deletes it whole.
        raw = git(self.work, "diff-files", "--raw", "-z").split("\x00")
        changed = [path for info, path in zip(raw[::2], raw[1::2], strict=False) if not info.startswith(":160000")]   # not a submodule's folder
        nested += [path + "/" for path in changed
                   if (self.work / path).is_dir() and not (self.work / path).is_symlink() and (self.work / path / ".git").exists()]
        return [path for path in others if not path.endswith("/") and blocks(path)], nested

    def hidden_edits(
        self,
        env: dict[str, str] | None = None,  # GIT_INDEX_FILE, for a copy of the index; None for the index itself
    ) -> None:
        "Clear the flags that hide a file's edits from git (assume-unchanged, and skip-worktree where the file is on disk)."
        flagged = []
        for entry in git(self.work, "ls-files", "-v", "-z", env=env).split("\x00"):
            tag, path = entry[:1], entry[2:]
            if path and (tag.islower() or (tag == "S" and (self.work / path).exists())):
                flagged.append(path)
        if flagged:
            git(self.work, "update-index", "--no-assume-unchanged", "-z", "--stdin", input_text="\x00".join(flagged), env=env)
            git(self.work, "update-index", "--no-skip-worktree", "-z", "--stdin", input_text="\x00".join(flagged), env=env)

    def save_work(
        self,
        label: str,  # The step or move the copy was at, for the commit's message
        going: str,  # The name of the place the move goes to, for the commit's message
        in_the_way: list[str],  # The untracked files the move would replace or delete, from `in_the_way`
    ) -> str | None:  # The commit of the learner's work, on top of HEAD; None when there is nothing to keep
        """Make commits of the learner's uncommitted work, without touching their files, index or stash.

        What was staged with `git add` is one commit, if it differs from HEAD; the edits to tracked files and the files in
        the way are another, on top. Copies of the index build them, so git does not read every file again. Other
        untracked files are not in the commits: the move leaves them where they are.
        """
        message = f"{label}: your %s, kept by timewalk before the move to {going}"
        head = git(self.work, "rev-parse", "HEAD")
        index = Path(git(self.work, "rev-parse", "--absolute-git-dir")) / "index"
        with tempfile.TemporaryDirectory() as folder:
            env = {"GIT_INDEX_FILE": str(Path(folder) / "index"), "GIT_LITERAL_PATHSPECS": "1"}   # a name like *.out is a name

            def copy() -> None:
                if index.exists():
                    shutil.copy2(index, env["GIT_INDEX_FILE"])   # with its time: git then checks an entry as new as the index ("racy git")
                else:
                    git(self.work, "read-tree", "HEAD", env=env)

            # What was staged. In a merge stopped on a conflict, the conflicted files as they are on disk.
            copy()
            unmerged = {line.split("\t", 1)[1] for line in git(self.work, "ls-files", "--unmerged", "-z", env=env).split("\x00") if "\t" in line}
            if unmerged:
                git(self.work, "add", "--sparse", "--", *sorted(unmerged), env=env)
            staged = git(self.work, "write-tree", env=env)
            parent = head
            if staged != git(self.work, "rev-parse", "HEAD^{tree}"):
                parent = git(self.work, "commit-tree", staged, "-p", head, "-m", message % "staged changes", env=self.identity())
            # The edits, hidden ones included, and the files in the way, ignored or not.
            copy()
            self.hidden_edits(env)
            git(self.work, "add", "--update", "--sparse", env=env)   # edits and deletions of tracked files; an unresolved file as it is
            if in_the_way:
                git(self.work, "update-index", "--add", "-z", "--stdin", input_text="\x00".join(in_the_way), env=env)
            tree = git(self.work, "write-tree", env=env)
        if tree == git(self.work, "rev-parse", f"{parent}^{{tree}}"):
            return parent if parent != head else None
        return git(self.work, "commit-tree", tree, "-p", parent, "-m", message % "edits", env=self.identity())

    def keep_file(
        self,
        relative: str,  # A file of the copy, which the learner changed
        label: str,  # The move that a patch$ line is about to replace the file for
    ) -> str | None:  # The saved branch that holds the learner's version; None when the file is as HEAD has it
        """Keep the learner's version of one file on a branch `timewalk/saved/...`, before the page's Replace button puts the
        move's version in its place. What was staged with `git add` is one commit on top of HEAD, if it differs; the file as
        it is on disk, or its absence, is another on top. Only git's objects and the branch are written: no file of the copy,
        and not its index. A copy that is the user's own repository, or a replay worktree of an older version, has no
        branch of its own, and refuses."""
        if self.branch is None:
            raise GitError("this copy shares its branches with your repository, so timewalk keeps no branch here; copy your file "
                           "aside yourself, or use Catch me up")
        if relative != os.path.normpath(relative) or ((self.work / relative).is_dir() and not (self.work / relative).is_symlink()):
            raise GitError(f"{relative} is not a file of the copy")
        head = git(self.work, "rev-parse", "HEAD")
        message = f"{label}: your %s of {relative}, kept by timewalk before Replace"
        with tempfile.TemporaryDirectory() as folder:
            env = {"GIT_INDEX_FILE": str(Path(folder) / "index"), "GIT_LITERAL_PATHSPECS": "1"}
            git(self.work, "read-tree", "HEAD", env=env)
            parent = head
            staged = git(self.work, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", relative).split("\x00")[0]
            if "\t" in staged and " " in staged:   # what git add put in the index, as it is there
                mode, blob = staged.split("\t", 1)[0].split()[:2]
                git(self.work, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{relative}", env=env)
                tree = git(self.work, "write-tree", env=env)
                if tree != git(self.work, "rev-parse", "HEAD^{tree}"):
                    parent = git(self.work, "commit-tree", tree, "-p", head, "-m", message % "staged version", env=self.identity())
            if (self.work / relative).exists() or (self.work / relative).is_symlink():
                git(self.work, "update-index", "--add", "--", relative, env=env)
            else:
                git(self.work, "update-index", "--force-remove", "--", relative, env=env)
            tree = git(self.work, "write-tree", env=env)
        commit = parent
        if tree != git(self.work, "rev-parse", f"{parent}^{{tree}}"):
            commit = git(self.work, "commit-tree", tree, "-p", parent, "-m", message % "version", env=self.identity())
        if commit == head:
            return None   # the learner's file is as HEAD has it: nothing of theirs to keep
        names = set(git(self.work, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{SAVED}").splitlines())
        base = SAVED + (re.sub(r"\.\.+", "-", re.sub(r"[^A-Za-z0-9._-]+", "-", f"{label}-yours")).strip("-.") or "yours")
        name, n = base, 1
        while name in names:
            n += 1
            name = f"{base}-{n}"
        git(self.work, "branch", name, commit)
        return name

    def patch_state(
        self,
        name: str,  # A move
        relative: str,  # A file that a patch$ line of the move names
    ) -> dict:  # state: "made", the file is as the move leaves it; "apply", as before the move; "replace", any other way
        """Say what the learner's file is, against a move's patch: so that the page offers Apply only where the patch fits
        exactly, and otherwise Replace, with no merge and no conflict. A file is its contents and whether it can run; a
        symbolic link is its target. `keep` says whether Replace can keep the learner's version on a branch."""
        move = self.move_named(name)
        if move is None:
            return {"state": ""}
        before, after = git(self.work, "rev-parse", f"{move.sha}^"), move.sha
        file = self.work / relative
        mine = None
        if file.is_symlink():
            mine = (os.fsencode(os.readlink(file)), "l")
        elif file.is_file():
            mine = (file.read_bytes(), "x" if os.access(file, os.X_OK) and self.file_modes() else "-")
        def theirs(commit: str) -> tuple | None:
            entry = git(self.work, "--literal-pathspecs", "ls-tree", "-z", commit, "--", relative).split("\x00")[0]
            if "\t" not in entry:
                return None
            mode, _, blob = entry.split("\t", 1)[0].split()
            data = subprocess.run(["git", "cat-file", "blob", blob], cwd=self.work, capture_output=True).stdout
            return (data, {"100755": "x" if self.file_modes() else "-", "120000": "l"}.get(mode, "-"))
        made, was = theirs(after), theirs(before)
        state = "made" if mine == made else "apply" if mine == was else "replace"
        return {"state": state, "before": before, "after": after, "exists": made is not None, "keep": self.branch is not None, "work": str(self.work)}

    def file_modes(self) -> bool:  # Whether git in the copy sees whether a file can run
        "Read core.fileMode: on a file system where every file can run, git does not compare the mode, and neither does timewalk."
        found = subprocess.run(["git", "config", "--bool", "core.fileMode"], cwd=self.work, capture_output=True, text=True).stdout.strip()
        return found != "false"

    def remove_kept(
        self,
        paths: list[str],  # The files in the way, relative to the copy, already kept on a saved branch
    ) -> None:
        "Delete the files that a move would replace, now that they are kept, then the folders they leave empty, deepest first."
        folders = set()
        for path in paths:
            target = self.work / path
            target.unlink(missing_ok=True)   # a symlink goes, not what it points to
            folders.update(parent for parent in target.parents if parent != self.work and parent.is_relative_to(self.work))
        for folder in sorted(folders, key=lambda folder: len(folder.parts), reverse=True):
            with contextlib.suppress(OSError):
                folder.rmdir()   # only an empty folder goes

    def wait_for_index(self) -> None:
        "Wait a moment for another git to let go of the index; refuse, before saving anything, if it does not."
        lock = Path(git(self.work, "rev-parse", "--absolute-git-dir")) / "index.lock"
        for wait in (0.1, 0.2, 0.4, 0.8):
            if not lock.exists():
                return
            time.sleep(wait)
        if lock.exists():
            raise GitError(f"another git is using the replay copy ({lock} exists). If no git is running there, delete that file")

    def identity(self) -> dict[str, str]:  # The author and committer for timewalk's commits, when git has none set up
        "Name timewalk as the author of a commit it makes, when the copy has no user.name or user.email of its own."
        names = {}
        for key, variable, value in (("user.name", "NAME", "timewalk"), ("user.email", "EMAIL", "timewalk@localhost")):
            if not subprocess.run(["git", "config", key], cwd=self.work, capture_output=True, text=True).stdout.strip():
                names[f"GIT_AUTHOR_{variable}"] = names[f"GIT_COMMITTER_{variable}"] = value
        return names

    def end_unfinished(self) -> None:
        "Leave a rebase, cherry-pick or revert that the learner left half done, once their work is kept: the move takes the branch."
        folder = Path(git(self.work, "rev-parse", "--absolute-git-dir"))
        if (folder / "rebase-apply" / "applying").exists():
            subprocess.run(["git", "am", "--quit"], cwd=self.work, capture_output=True)   # git am uses rebase-apply too
        for marks, command in ((("rebase-merge", "rebase-apply"), "rebase"), (("CHERRY_PICK_HEAD", "sequencer"), "cherry-pick"), (("REVERT_HEAD",), "revert")):
            if any((folder / mark).exists() for mark in marks):
                subprocess.run(["git", command, "--quit"], cwd=self.work, capture_output=True)

    def _checkout(
        self,
        target: str,  # The commit to stand on
        force: bool = False,  # Throw away edits to tracked files, as --discard-edits does
    ) -> None:
        "Put the working copy on a commit: in a clone, the branch reset to it; in place or in an old worktree, a detached HEAD."
        if self.branch:
            # checkout -B, not switch -C: with --force it replaces an untracked file where the step has one, as documented
            git(self.work, "checkout", "--quiet", *(["--force"] if force else []), "-B", self.branch, target)
            git(self.work, "update-ref", PLACED, target)
        else:
            git(self.work, "checkout", *(["--force"] if force else []), "--detach", "--quiet", target)

    def place_file(self) -> Path:  # Where a tutorial notes its place, in the replay copy's own git folder, outside its files
        "Name the file that keeps a tutorial's place across a restart: move 0 of a step and the step before are one commit."
        folder = Path(git(self.work, "rev-parse", "--git-dir"))
        return (folder if folder.is_absolute() else self.work / folder) / "timewalk-place.json"

    def keep_place(self) -> None:
        "Write down the tutorial's place, with the commit it stands on; at a step without moves, forget the place written before."
        if self.at is None or not self.moves[self.at[0]]:
            with contextlib.suppress(OSError, GitError):
                self.place_file().unlink(missing_ok=True)
            return
        with contextlib.suppress(OSError, GitError):
            self.place_file().write_text(json.dumps({"head": self.commit_at(*self.at), "step": self.steps[self.at[0]].name, "move": self.at[1]}))

    def kept_place(
        self,
        head: str,  # The commit the working copy stands on
    ) -> tuple[int, int] | None:  # The place written down for that commit, if any and still valid
        "Read the place a tutorial wrote down, if the working copy is still on its commit."
        try:
            kept = json.loads(self.place_file().read_text())
        except (OSError, ValueError, GitError):
            return None
        names = [step.name for step in self.steps]
        if not isinstance(kept, dict) or kept.get("head") != head or kept.get("step") not in names:
            return None
        index, move = names.index(kept["step"]), kept.get("move")
        if not self.moves[index] or not isinstance(move, int) or not 0 <= move <= len(self.moves[index]) or self.commit_at(index, move) != head:
            return None
        return index, move

    def current(self) -> int | None:  # Index of the step the working copy is at, if it is exactly at one or at one of its moves
        "Say which step the working copy stands on."
        return self.position()[0]

    def edits(self) -> list[str]:  # Paths of tracked files with uncommitted changes
        "List tracked files that differ from the step. Untracked files are not edits and are never touched."
        # -z: a path with a space or an accent comes as it is, not quoted
        # --no-optional-locks: this runs every second or two, and must not take index.lock from the learner's own git
        fields = git(self.work, "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=no").split("\x00")
        paths, i = [], 0
        while i < len(fields):
            entry = fields[i]
            if len(entry) > 3:
                paths.append(entry[3:])
                if entry[0] in "RC":
                    i += 1   # a rename or copy is followed by the path it came from
            i += 1
        return paths

    def edit_marks(self) -> tuple:  # Each edited path with its modification time and size, None for a deleted file
        "Fingerprint the edits cheaply, so a second edit to an already edited file is noticed too."
        marks = []
        for relative in self.edits():
            try:
                info = (self.work / relative).stat()
                marks.append((relative, info.st_mtime_ns, info.st_size))
            except OSError:
                marks.append((relative, None, None))
        return tuple(marks)

    def move(
        self,
        index: int,  # Step to move to
        set_aside: bool = False,  # Stash uncommitted edits first instead of refusing
        here: str | None = None,  # The name of the step the copy is at, for the stash, when the steps just changed
        move: int | None = None,  # In a tutorial, the move of the step to go to; default: 0, the start of the step
        anywhere: bool = False,  # Kept for callers; any move can be reached
    ) -> None:
        """Move the working copy to a step, or to a move of a step, and never lose a learner's work.

        In a replay clone, the edits and any file in the way are kept on a branch `timewalk/saved/<place>`, then the move
        replaces them; it never asks. In place, or in a replay worktree of an older version, edits are stashed after a
        question, or with `discard` (a worktree only) thrown away, and an untracked file in the way stops the move.

        In a tutorial, any move of a step can be reached: each one is a commit, right for its own place.
        """
        if not 0 <= index < len(self.steps):
            raise GitError(f"there is no step {index}")
        moves = self.moves[index]
        place = (index, (move or 0) if moves else None)
        if moves and not 0 <= place[1] <= len(moves):
            raise GitError(f"{self.steps[index].name} has no move {place[1]}")
        target = self.commit_at(*place)
        if self.branch and subprocess.run(["git", "cat-file", "-e", f"{target}^{{commit}}"], cwd=self.work, capture_output=True).returncode:
            self._fetch(self.work)   # a step tagged since the start: its commit is not in the clone yet
        step_now, move_now = self.position()
        label = (self.steps[step_now].name if step_now is not None and not here else None) or here or (
            self.place_name() if self.branch else "an unknown step")
        if here is None and move_now:
            label = self.moves[step_now][move_now - 1].name  # in a tutorial, the move made
        self.kept = []
        if self.branch:
            # A replay clone never asks and never loses work: the edits, and a file in the way, go on a saved branch with
            # the learner's commits, and then the move replaces them. Other untracked files stay, as always.
            going = self.moves[index][place[1] - 1].name if moves and place[1] else self.steps[index].name
            in_the_way, nested = self.in_the_way("HEAD", target)
            if nested:
                raise GitError(f"{nested[0].rstrip('/')} is a git repository of its own, where a step has files. "
                               "Move it out of the copy, then move again")
            self.wait_for_index()
            snapshot = self.save_work(label, going, in_the_way)
            self.kept = self.keep_learner_commits(label, snapshot)
            self.end_unfinished()
            # Every move is a plain checkout, never --force: timewalk removes only what it has kept, and if anything was
            # missed, git refuses rather than overwrite it. A move that stops puts the kept work back in the files.
            try:
                self.hidden_edits()
                self.remove_kept(in_the_way)
                git(self.work, "reset", "--hard", "--quiet")
                self._checkout(target)
            except (GitError, OSError) as exc:
                back = bool(snapshot) and subprocess.run(["git", "restore", "--source", snapshot, "--worktree", "--", "."],
                                                         cwd=self.work, capture_output=True).returncode == 0
                where = f"; your work is kept on {self.kept[0]}" if self.kept else ""
                raise GitError(f"{exc}. The move stopped{where}" + (", and is back in your files" if back else "")) from None
            self.at = place
            self.keep_place()
            return
        if self.discard:
            self._checkout(target, force=True)
            self.at = place
            self.keep_place()
            return
        # First the untracked files in the way, so that a move that cannot happen does not stash the edits away. In place,
        # timewalk does not keep them on a branch of your repository: it refuses.
        files, nested = self.in_the_way("HEAD", target)   # a stash puts HEAD's files back too
        in_the_way = sorted(files + [path.rstrip("/") for path in nested])
        if in_the_way:
            raise GitError("these untracked files exist where the step has a file, and will not be overwritten: " + ", ".join(in_the_way[:5]))
        if self.edits():
            if not set_aside:
                raise GitError("uncommitted edits")
            git(self.work, "stash", "push", "--message", f"timewalk: edits made at {label}")
        self.keep_learner_commits(label)
        self._checkout(target)
        self.at = place
        self.keep_place()

    def changes(
        self,
        index: int,  # Step whose changes to list
    ) -> dict[str, str]:  # Path to A, M or D, relative to the step before
        "List what a step added, modified or deleted. For the first step, everything is added."
        step, before = self.steps[index], self.before(index)
        if before is None:
            return dict.fromkeys((path for path in git(self.work, "ls-tree", "-r", "-z", "--name-only", step.sha).split("\x00") if path), "A")
        return name_status(self.work, "diff", "--name-status", "--no-renames", before, step.sha)

    def span(self) -> tuple[str, str] | None:  # The commits before and after what the place on show changed; None at the first step
        """Say what "Changes in this step" compares: a step with the step before, or in a tutorial a move with the move before.

        At move 0 of a step, nothing of it is made yet, so both commits are the same.
        """
        index, move = self.position()
        if index is None:
            return None
        if move is not None:
            return self.commit_at(index, max(move - 1, 0)), self.commit_at(index, move)
        before = self.before(index)
        return (before, self.steps[index].sha) if before else None

    def move_files(
        self,
        move: Move,  # A move of a tutorial
    ) -> list[dict]:  # The files its commit added or changed, each as {path, status}
        "List the files a move's commit added or changed, for a `files:` line of the notes. A commit's files never change, so once."
        if move.sha not in self.files_of:
            changed = name_status(self.work, "diff-tree", "--no-commit-id", "--name-status", "-r", "--no-renames", move.sha)
            self.files_of[move.sha] = [{"path": path, "status": status} for path, status in changed.items() if status != "D"]
        return self.files_of[move.sha]

    def move_named(
        self,
        name: str,  # A move's name, such as step-02.1
    ) -> Move | None:  # That move of the walk on show, if there is one
        "Find a move of the walk on show by its name, or in a walk that selects, any move of the whole history."
        return next((m for moves in self.moves for m in moves if m.name == name), None) or self.history.get(name)

    def move_diff(
        self,
        name: str,  # A move's name
        relative: str,  # Path inside the working copy
    ) -> str:  # What that move's commit did to the file, whatever the working copy stands on
        "Show what one move changed in one file: the answer for a learner who makes the move by hand."
        move = self.move_named(name)
        if move is None:
            return ""
        return git(self.work, "diff", "--no-color", f"{move.sha}^", move.sha, "--", relative)

    def read_at(
        self,
        commit: str,  # A commit
        relative: str,  # Path inside the repository
    ) -> dict:  # The file as that commit has it, or the reason it is not shown
        "Read one file as a commit has it: the answer of a move, read only, whatever the working copy is."
        found = subprocess.run(["git", "cat-file", "blob", f"{commit}:{relative}"], cwd=self.work, capture_output=True)
        if found.returncode:
            return {"path": relative, "missing": True}
        data = found.stdout
        if len(data) > MAX_FILE_BYTES:
            return {"path": relative, "skipped": f"{len(data):,} bytes, too large to show"}
        if b"\x00" in data:
            return {"path": relative, "skipped": "a binary file"}
        return {"path": relative, "text": data.decode("utf-8", errors="replace")}

    def differ_from(
        self,
        base: str,  # The commit the working copy stands on
        target: str,  # The commit to compare the learner's files with
        loose: list[str] | None = None,  # Globs of the files to compare loosely, from the walk's key loose; see loosely_same
        found: dict[str, str] | None = None,  # Filled in: each loose file that differs as written, but matches loosely, and how
    ) -> list[str]:  # The files whose contents differ, tracked or not; empty when the files match
        """Compare the learner's files with a commit, by content: every file that the commits between base and target touch,
        and every file the learner edited. A file the learner made, and git does not track yet, counts too.

        Two git commands, whatever the number of files: the commit's blob for each path, and the blob of each file on disk.
        A file that differs and matches a glob of `loose` costs one more, to read the commit's version.
        """
        touched = git(self.work, "diff", "--name-only", "-z", "--no-renames", base, target).split("\x00")
        paths = sorted({p for p in touched if p} | set(self.edits()))
        if not paths:
            return []
        wanted, modes = {}, {}
        for entry in git(self.work, "ls-tree", "-r", "-z", target, "--", *paths).split("\x00"):
            if "\t" in entry:
                info, path = entry.split("\t", 1)
                wanted[path], modes[path] = info.split()[2], info.split()[0]
        on_disk = [p for p in paths if (self.work / p).is_file()]
        hashes = dict(zip(on_disk, git(self.work, "hash-object", "--stdin-paths", input_text="\n".join(on_disk)).splitlines(), strict=True)) if on_disk else {}
        # A file that can run differs from one that cannot, so that a move of chmod +x alone is made only by chmod
        runs = lambda p: modes.get(p) == "100755"   # noqa: E731
        modal = self.file_modes()
        differ = [p for p in paths if wanted.get(p) != hashes.get(p)
                  or (modal and p in hashes and p in modes and modes[p] in ("100644", "100755") and runs(p) != os.access(self.work / p, os.X_OK))]
        for path in [p for p in differ if any(fnmatch.fnmatchcase(p, glob) for glob in loose or [])]:
            how = loosely_same(path, self.work / path, self.read_blob(wanted[path]) if path in wanted else None)
            if how:
                differ.remove(path)
                if found is not None:
                    found[path] = how
        return differ

    def read_blob(
        self,
        blob: str,  # The hash of a blob
    ) -> bytes | None:  # Its contents, or None if git cannot read it
        "Read one file of a commit by its blob."
        done = subprocess.run(["git", "cat-file", "blob", blob], cwd=self.work, capture_output=True)
        return done.stdout if done.returncode == 0 else None

    def tree(self) -> dict:  # Files of the working copy, with what the current step did to each
        "Describe the working copy: its tracked files and which of them the current step, or move, changed."
        index, span = self.current(), self.span()
        if index is None:
            changed = {}
        elif span is None:
            changed = self.changes(index)
        else:
            changed = name_status(self.work, "diff", "--name-status", "--no-renames", *span)
        files = [{"path": p, "status": changed.get(p, "")} for p in git(self.work, "ls-files", "-z").split("\x00") if p]
        stat = git(self.work, "diff", "--shortstat", *span).strip() if span else ""
        return {"files": files, "deleted": sorted(p for p, s in changed.items() if s == "D"), "summary": stat, "edits": self.edits()}

    def read(
        self,
        relative: str,  # Path inside the working copy
    ) -> dict:  # The file's text, or the reason it is not shown
        "Read one file from the working copy, refusing paths that leave it."
        path = (self.work / relative).resolve()
        if not path.is_relative_to(self.work.resolve()) or not path.is_file():
            return {"path": relative, "missing": True}
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return {"path": relative, "skipped": f"{size:,} bytes, too large to show"}
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            return {"path": relative, "skipped": f"{len(data):,} bytes, too large to show"}
        if b"\x00" in data:
            return {"path": relative, "skipped": "a binary file"}
        return {"path": relative, "text": data.decode("utf-8", errors="replace")}

    def diff(
        self,
        relative: str,  # Path inside the working copy
    ) -> str:  # Unified diff of this file between the previous step and the current one
        "Show what the current step, or in a tutorial the current move, did to one file."
        span = self.span()
        if span is None:
            return ""
        return git(self.work, "diff", "--no-color", *span, "--", relative)

    def edit_diff(
        self,
        relative: str,  # Path inside the working copy
    ) -> str:  # Unified diff of this file's uncommitted edits, against the commit the working copy is at
        "Show what commands run here, such as a formatter, have changed in one file since the step's commit."
        if relative not in self.edits():
            return ""
        return git(self.work, "diff", "--no-color", "HEAD", "--", relative)

    def state(self) -> dict:  # Everything a page needs to draw its header
        "Summarise where the working copy is."
        index, move = self.position()
        moves = self.moves[index] if index is not None else []
        return {"main": str(self.main), "work": str(self.work), "in_place": self.work == self.main,
                "discard": self.discard and not self.branch, "steps": [asdict(s) for s in self.steps], "current": index,
                "move": move, "moves": [{**asdict(m), "files": self.move_files(m)} for m in moves],
                "edits": self.edits()}


def recipes(
    directory: Path,  # Where to look for a justfile
) -> list[dict]:  # Public recipes, in the order the justfile lists them
    """List the `just` recipes of the justfile in a directory, or nothing when it has none or there is no `just`.

    Only a justfile in the directory itself counts. `just` on its own would search the parent directories too,
    and list the recipes of some other project that happens to contain this one.
    """
    justfile = next((directory / name for name in ("justfile", "Justfile", ".justfile") if (directory / name).is_file()), None)
    if justfile is None or not shutil.which("just"):
        return []
    done = subprocess.run(["just", "--justfile", str(justfile), "--working-directory", str(directory), "--dump", "--dump-format", "json"],
                          cwd=directory, capture_output=True, text=True)
    if done.returncode != 0:
        return []
    out = []
    for name, recipe in json.loads(done.stdout)["recipes"].items():
        if recipe.get("private"):
            continue
        group = next((a["group"] for a in recipe.get("attributes", []) if isinstance(a, dict) and "group" in a), "")
        needs = [p["name"] for p in recipe["parameters"] if p["kind"] == "singular" and p["default"] is None]
        out.append({"name": name, "doc": recipe.get("doc") or "", "group": group, "needs": needs})
    return out


# Under a files: line: "- diff `src/x.py`: why", "- file step-02.1 `src/y.py`: why", and under an item "  show: `a line`".
FILES_ITEM = re.compile(r"^\s*-\s+(diff|file)\s+(?:([\w.-]+)\s+)?`([^`]+)`\s*:?\s*(.*)$")
SHOW_LINE = re.compile(r"^(\s+)show:\s*(`+)(?!`)(.+?)(?<!`)\2\s*$")   # the closing run as long as the opening one, as in CommonMark


def shown_line(
    line: str,  # A line of the notes
) -> tuple[str, str] | None:  # Its indent and the line of the change that it quotes, if it is a show: line
    """Read a show: line. The quoted line is a code span, as CommonMark reads one: in one backtick, or in two for a line
    with a backtick, such as ``the command `x` runs``. A space inside each end goes, so `` `x` `` quotes `x`."""
    found = SHOW_LINE.match(line)
    if not found:
        return None
    text = found.group(3)
    if len(text) > 1 and text[0] == text[-1] == " " and text.strip():
        text = text[1:-1]
    return found.group(1), text


def code_span(
    text: str,  # A line to quote
) -> str:  # The line as a CommonMark code span
    "Quote a line as code, in more backticks than any run of backticks in it, as shown_line reads it back."
    ticks = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text[:1] == "`" or text[-1:] == "`" or (text[:1] == text[-1:] == " " and text.strip()) else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


# A block for one mode of a tutorial: "::: do" or "::: watch" opens it, and a line ":::" closes it. The fence is a
# fenced div, as smalldocs, Quarto and Pandoc read it, so "::: {.do}" and "::::: watch" open one too.
MODES = ("do", "watch")
DIV_OPEN = re.compile(r"^:{3,}\s*(?:\{\s*\.([\w-]+)\s*\}|([\w-]+))\s*$")
DIV_CLOSE = re.compile(r"^:{3,}\s*$")


@dataclass
class Blocks:
    "Where a notes file has blocks for one mode."

    mode: list[str | None]  # For each line, the mode of the block it is in, or None outside a block
    fences: set[int]  # The lines that open or close a block, by index; they are not text
    errors: list[tuple[int, str]]  # (line number from 1, what is wrong): a block not closed, a heading in a block
    warnings: list[tuple[int, str]]  # (line number from 1, what is odd): a div of another name, a ::: that closes nothing


def note_blocks(
    text: str,  # A notes file, or a section of one
) -> Blocks:  # The mode of each line, the fence lines, and what is wrong with the blocks
    """Find the blocks for one mode: the text between "::: do" or "::: watch" and ":::".

    A block holds no heading and no other block. A heading closes a block that is still open, and is an error. Inside
    fenced code a ::: line is code, and closes nothing. A div of another name, such as ::: callout-note, is text in
    both modes, and its closing ::: is text too.
    """
    lines = text.splitlines()
    out = Blocks([None] * len(lines), set(), [], [])
    fence: str | None = None
    open_divs: list[tuple[int, str | None]] = []   # the divs open, innermost last: (line, mode), mode None for a div of another name

    def mode() -> str | None:   # the mode of the innermost block for one mode
        return next((m for _, m in reversed(open_divs) if m), None)

    for at, line in enumerate(lines):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if fence is not None:
            out.mode[at] = mode()
            if mark and mark.group(1)[0] == fence:
                fence = None
            continue
        if mark:
            fence = mark.group(1)[0]
        if re.match(r"^###?\s", line):
            block = next(((start, m) for start, m in open_divs if m), None)
            if block:
                out.errors.append((block[0] + 1, f"the block ::: {block[1]} is not closed before the heading at line {at + 1}; "
                                                 "close it with a line ::: above the heading, as a block holds no heading"))
            open_divs.clear()
            continue
        opened, closed = (None, None) if mark else (DIV_OPEN.match(line), DIV_CLOSE.match(line))
        if opened:
            name = opened.group(1) or opened.group(2)
            if name not in MODES:
                out.warnings.append((at + 1, f"::: {name} is not a mode; only ::: do and ::: watch are blocks for one mode, so "
                                             "this div shows in both modes as it is written"))
                out.mode[at] = mode()
                open_divs.append((at, None))
                continue
            if mode():
                outer = next(start for start, m in open_divs if m)
                out.errors.append((at + 1, f"::: {name} opens inside the block ::: {mode()} of line {outer + 1}; "
                                           "a block holds no other block, so close the first with ::: before"))
            open_divs.append((at, name))
            out.fences.add(at)
            continue
        if closed:
            if not open_divs:
                out.warnings.append((at + 1, "this ::: closes no block"))
                continue
            _, was = open_divs.pop()
            if was:
                out.fences.add(at)
            else:
                out.mode[at] = mode()   # the end of a div of another name, inside a block, is text of that block
            continue
        out.mode[at] = mode()
    block = next(((start, m) for start, m in open_divs if m), None)
    if block:
        out.errors.append((block[0] + 1, f"the block ::: {block[1]} is not closed; close it with a line :::"))
    return out


PATCH_LINE = re.compile(r"^\s*patch\$\s+(.+?)\s*$")   # patch$ src/x.py: a file that the move changes by a patch


def _items_follow(
    lines: list[str],  # The lines of the notes
    at: int,  # The index of a files: line
) -> bool:  # Whether its next line that is not blank is an item
    "Say whether a files: line has items, so that a files: outside a tutorial's move is read as items, not as prose."
    later = next((line for line in lines[at + 1:] if line.strip()), "")
    return bool(FILES_ITEM.match(later))


def parse_notes(
    text: str,  # The notes file
) -> dict[str, dict]:  # Step name to its notes: planned start, commands, the prose, and the section as written
    """Read the notes: the script of each step, shown on the page beside the step.

    The file is Markdown. `## step-name` starts the notes for a step. Inside a step:

        time: 0:14            the planned start, as minutes:seconds into the session
        $ just test           a command for the terminal at this step; one click sends it
        runs$ just train      a command for the Runs tab: a second shell at this step, for commands that take a
                              while, so the first shell stays free
        runs2$ just sweep     the same in a further Runs tab (runs2 to runs9), for a second long command while
                              the first is still going
        main$ git log         a command for the terminal in the repository you started from
        do$ uv add ruff       in a tutorial, the command that makes the move: a command in do mode, and in watch mode
                              shown as what the move ran, with nothing to run; never the anchor of a move
        patch$ src/x.py       in a tutorial, a file that the move changes by a patch, the diff of its commit: in do
                              mode a button that applies it, in watch mode a pointer to its diff; a path with a
                              space is written in backticks
        > Say: ...            a cue; shown with the prose, set apart in its own shade
        ### step-02.1 Title   in a tutorial, starts the notes of one move of the step, which is one commit
        ::: do                in a tutorial, a block of lines for do mode only, up to a line :::
        ::: watch             the same for watch mode
        files:                in a tutorial, the files that the move's commit added or changed, each a link
        files:                followed by items, the files to look at, each with words:
        - diff `src/x.py`: why   a button to the change of the move (or the step), and a sentence
        - file `src/y.py`: why   a button to the file, as the move leaves it
          show: `a line`         under an item: a line of the change, drawn as a small excerpt, and marked in the reader

    Every other line is prose, shown as written. Inside a fenced code block every line is prose, so a `$ ` line
    there is code to read, not a command. `parts` keeps the prose and the commands in the order of the file, so the
    page can show each command where it is written. `raw` keeps the section as written, for editing. A move's
    heading is a part of its own, `{"kind": "move", "name": "step-02.1", "title": ...}`, and the parts after it
    belong to that move; the heading stays in `text`, as a Markdown heading, for the PDF. A part inside a block for one
    mode has `"mode": "do"` or `"watch"`, and so has a command's entry; the lines that open and close a block are in
    `raw` only.
    """
    blocks = note_blocks(text)
    notes: dict[str, dict] = {}
    current: dict | None = None
    name = ""  # the step whose section this is
    fence: str | None = None  # the fence character while inside a fenced code block, ` or ~
    lines = text.splitlines()
    items: list | None = None  # the items of the files: line being read, or None
    for at, line in enumerate(lines):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        heading = re.match(r"^##\s+(\S+)", line) if fence is None else None
        if heading:
            name, items = heading.group(1), None
            current = notes.setdefault(name, {"time": None, "commands": [], "text": [], "raw": [], "parts": []})
            title = line[heading.end():].strip()
            if title:
                current["title"] = title
            continue
        if current is None:
            if mark:  # a fence before the first step counts too, as replace_section counts it
                fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
            continue
        current["raw"].append(line)
        if at in blocks.fences:
            continue   # the items of a files: line go on into a block, each item then for that mode
        mode = blocks.mode[at] if at < len(blocks.mode) else None
        planned = re.match(r"^time:\s*(\d+):(\d\d)\s*$", line.strip()) if fence is None else None
        command = re.match(r"^\s*(main|runs[2-9]?|do)?\$\s+(.+)$", line) if fence is None else None
        patched = PATCH_LINE.match(line) if fence is None else None
        move = re.match(rf"^###\s+({re.escape(name)}\.\d+)(?:\s+(.*))?$", line) if fence is None else None
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        parts = current["parts"]
        if planned:
            current["time"] = int(planned.group(1)) * 60 + int(planned.group(2))
        elif move:
            items = None
            current["text"].append(line)
            parts.append({"kind": "move", "name": move.group(1), "title": (move.group(2) or "").strip()})
        elif fence is None and line.strip() == "files:" and (any(part["kind"] == "move" for part in parts) or _items_follow(lines, at)):
            items = []
            parts.append({"kind": "files", "items": items, **({"mode": mode} if mode else {})})
        elif fence is None and items is not None and not command and (item := FILES_ITEM.match(line)):
            items.append({"kind": item.group(1), "move": item.group(2), "path": item.group(3), "text": item.group(4).strip(), "show": [],
                          **({"mode": mode} if mode else {})})
            current["text"].append(line)  # the PDF prints an item as written
        elif fence is None and items and (shown := shown_line(line)):
            if shown[1].strip():
                items[-1]["show"].append(shown[1])
            current["text"].append(line)
        elif fence is None and items and not command and line.startswith("  ") and line.strip() and lines[at - 1].strip():
            items[-1]["text"] = (items[-1]["text"] + " " + line.strip()).strip()   # an item's words, over more lines
            current["text"].append(line)
        elif patched:
            items = None
            path = patched.group(1).strip()
            path = path[1:-1] if len(path) > 1 and path[0] == path[-1] == "`" else path
            parts.append({"kind": "patch", "path": path, **({"mode": mode} if mode else {})})
            current["text"].append(line)   # the PDF prints it as written
        elif command:
            items = None
            makes = command.group(1) == "do"   # do$: the command that makes the move; run in do mode, shown as what it ran in watch mode
            entry = {"track": "replay" if makes else command.group(1) or "replay", "text": command.group(2).strip(),
                     **({"makes": True} if makes else {}), **({"mode": mode} if mode else {})}
            current["commands"].append(entry)
            parts.append({"kind": "command", **entry})
        else:
            if line.strip():
                items = None   # a line of prose ends the items of a files: line
            current["text"].append(line)
            if parts and parts[-1]["kind"] == "text" and parts[-1].get("mode") == mode:
                parts[-1]["text"] += "\n" + line
            else:
                parts.append({"kind": "text", "text": line, **({"mode": mode} if mode else {})})
    for entry in notes.values():
        entry["text"] = "\n".join(entry["text"]).strip()
        entry["raw"] = "\n".join(entry["raw"]).strip("\n")
        entry["parts"] = [p for p in ({**p, "text": p["text"].strip("\n")} if p["kind"] == "text" else p for p in entry["parts"])
                          if p["kind"] != "text" or p["text"].strip()]
    return notes


def walk_notes(
    notes: dict[str, dict],  # The notes file, as parse_notes reads it
    steps: list[Step],  # The steps of the walk
    moves: list[list[Move]],  # The moves of each step
    tutorial: bool,  # Whether the walk is a tutorial
    selects: bool = False,  # Whether the walk takes part of a bigger one, with select: a narrative then shows a step before its moves
    own: list[str | None] | None = None,  # A walk with notes of its own: each stop's section, with its includes put in, or None; see Walk.own_sections
) -> dict[str, dict]:  # The notes by step, with an entry for each small step and each stop at a move
    """Give each stop of a walk the notes it shows, from the sections of a bigger walk.

    A small step shows the commands at the top of its step's section, such as `$ just setup`, which every Start needs,
    then the sections of its moves; the text of the step before its first move is about the whole step, and is left out.
    A narrative shows a step's text before its first move, and at a move, that move's section. An entry made this way
    has "derived": True, and no `raw`: it is edited in the sections it comes from. A walk with its own notes shows each
    stop's section of them, under the stop's title.
    """
    if own is not None:
        out = {}
        for index, (step, section) in enumerate(zip(steps, own, strict=False)):   # a place no stop walks now shows nothing
            entry = next(iter(parse_notes(section).values()), None) if section else None
            if entry is not None:
                alone = moves[index][0].name if index < len(moves) and len(moves[index]) == 1 else None   # a stop of one move, with no title of its own
                title = step.title or next((p["title"] for p in entry["parts"] if p["kind"] == "move" and p["name"] == alone), "")
                out[step.name] = {**entry, "title": title, "raw": "", "derived": True}
        return out
    out = dict(notes)
    for index, step in enumerate(steps):
        source = notes.get(step.section or step.name)
        if not source:
            if step.title:   # a stop with no notes still has the title that select gives it; a whole step's can be written
                out[step.name] = {"time": None, "title": step.title, "commands": [], "text": "", "raw": "", "parts": [],
                                  **({"derived": True} if step.section else {})}
            continue
        parts = source["parts"]
        cut = [at for at, part in enumerate(parts) if part["kind"] == "move"]
        before = parts[:cut[0]] if cut else parts
        if not step.section:
            if not tutorial and cut and selects:
                out[step.name] = {**source, "parts": before, "derived": True}
            if step.title:
                out[step.name] = {**out[step.name], "title": step.title}   # its section, under the title that select gives it
            continue
        lead = [part for part in itertools.takewhile(lambda part: part["kind"] == "command", before) if not part.get("makes")]   # a do$ line makes no Start
        by_move = {parts[at]["name"]: parts[at:end] for at, end in zip(cut, [*cut[1:], len(parts)], strict=True)} if cut else {}
        names = [move.name for move in moves[index]] if tutorial else [step.name]
        taken = [part for name in names for part in by_move.get(name, [])[0 if tutorial else 1:]]
        title = step.title or (next((p["title"] for p in by_move.get(names[0], []) if p["kind"] == "move"), "") if len(names) == 1
                               else source.get("title", ""))
        out[step.name] = {"time": None, "title": title, "commands": [p for p in lead + taken if p["kind"] == "command"],
                          "text": "", "raw": "", "parts": lead + taken, "derived": True}
    return out


# A line of a walk's own notes that brings in move sections from the shared notes: {{include step-05.3..step-05.6}}
INCLUDE = re.compile(r"^\s*\{\{\s*include\s+(\S+?)\s*\}\}\s*$")


@dataclass
class OwnStop:
    "A stop of a walk's own notes: a `## Title` section, with its includes of shared move sections."

    title: str  # The words of its heading
    line: int  # The line of its heading, from 1
    body: list[str]  # Its lines below the heading, as written
    includes: list[tuple[int, str]]  # Each include: its line, from 1, and the step, move or range of moves it names


def own_stops(
    text: str,  # A walk's own notes file
) -> tuple[str, list[OwnStop]]:  # The text before the first stop, its intro, and the stops
    "Read a walk's own notes: its intro, then a stop at each `## Title`. Fenced code holds no heading and no include."
    intro: list[str] = []
    stops: list[OwnStop] = []
    fence: str | None = None
    for at, line in enumerate(text.splitlines(), 1):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        heading = re.match(r"^##(?!#)\s*(.*)$", line) if fence is None and not mark else None
        include = INCLUDE.match(line) if fence is None and not mark else None
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        if heading:
            stops.append(OwnStop(heading.group(1).strip(), at, [], []))
            continue
        if not stops:
            intro.append(line)
            continue
        stops[-1].body.append(line)
        if include:
            stops[-1].includes.append((at, include.group(1)))
    return "\n".join(intro).strip("\n"), stops


def include_span(
    entry: str,  # What an include names: "step-05", "step-05.3" or "step-05.3..step-05.6"
) -> tuple[str, int, int] | None:  # The step, and its first and last move; a whole step is 0 to 0; None if it is not one of these forms
    "Read what an include names, by its form alone; the history says whether the step and moves are there."
    first, ranged, end = entry.partition("..")
    one, two = re.fullmatch(r"(.+)\.(\d+)", first), re.fullmatch(r"(.+)\.(\d+)", end) if end else None
    if not ranged:
        return (one.group(1), int(one.group(2)), int(one.group(2))) if one else ((first, 0, 0) if first else None)
    if one and two and one.group(1) == two.group(1) and int(one.group(2)) <= int(two.group(2)):
        return one.group(1), int(one.group(2)), int(two.group(2))
    return None


def stop_range(
    stop: OwnStop,  # A stop of a walk's own notes
) -> tuple[str, str]:  # The step, move or range of moves that its includes make together, as select names it, or "" and why not
    """Join the includes of a stop into the one place it walks: a whole step, or moves of one step with no move left out,
    since do mode makes the moves one at a time from the Start. A stop with no include has none."""
    if not stop.includes:
        return "", "has no {{include ...}} line, so it has no moves and the walk leaves it out"
    spans = []
    for line, entry in stop.includes:
        span = include_span(entry)
        if span is None:
            return "", (f"includes {{{{include {entry}}}}} at line {line}, which is not a step, a move or a range of moves of one step, "
                        "for example {{include step-05}}, {{include step-05.3}} or {{include step-05.3..step-05.6}}")
        spans.append(span)
    if any(first == 0 for _, first, _ in spans):
        if len(spans) > 1:
            return "", "includes a whole step and something more; a stop is one whole step, or moves of one step"
        return spans[0][0], ""
    if len({step for step, _, _ in spans}) > 1:
        return "", "includes moves of more than one step; a stop is one whole step, or moves of one step"
    for (_, _, last), (step, first, _) in itertools.pairwise(spans):
        if first != last + 1:
            gap = "leaves out a move" if first > last + 1 else "goes back, or takes a move twice"
            return "", (f"{gap} at {step}.{first}: its includes must follow on, one move after another, as do mode makes them; "
                        "start a new ## stop for moves after a gap")
    step, first, last = spans[0][0], spans[0][1], spans[-1][2]
    return (f"{step}.{first}" if first == last else f"{step}.{first}..{step}.{last}"), ""


def split_section(
    raw: str,  # A step's section of the notes, as written
    step: str,  # The step
) -> tuple[list[str], dict[str, list[str]]]:  # Its lines before the first move, and each move's lines, its heading first
    "Cut a step's section into the text before its moves and the section of each move. Fenced code holds no heading."
    before: list[str] = []
    moves: dict[str, list[str]] = {}
    now, fence = before, None
    for line in raw.split("\n"):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        heading = re.match(rf"^###\s+({re.escape(step)}\.\d+)(\s|$)", line) if fence is None and not mark else None
        if heading:
            now = moves.setdefault(heading.group(1), [])
        now.append(line)
    return before, moves


def expand_stop(
    stop: OwnStop,  # A stop of a walk's own notes, whose includes make one place; see stop_range
    shared: dict[str, dict],  # The shared notes, as parse_notes reads them
) -> str:  # The stop as a section of notes: `## step Title`, its own text, and in place of each include the sections it names
    """Put a stop's includes in its text: a move's include brings its `### step-05.3` section, and a whole step's brings
    the step's text before its moves and every move's section. The walk's own text stays where it is written."""
    spans = [include_span(entry) for _, entry in stop.includes]
    step = spans[0][0]
    before, moves = split_section(shared.get(step, {}).get("raw", ""), step)
    included = iter(spans)
    lines = [f"## {step} {stop.title}".rstrip()]
    fence: str | None = None
    for line in stop.body:
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        if fence is not None or mark or not INCLUDE.match(line):
            lines.append(line)
            continue
        _, first, last = next(included)
        lines.append("")   # a blank line first, so the text above, a cue's blockquote or a paragraph, does not run on into it
        if first == 0:
            lines += [*before, *(line for name in moves for line in moves[name])]
        else:
            lines += [line for number in range(first, last + 1) for line in moves.get(f"{step}.{number}", [])]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def replace_section(
    text: str,  # The whole notes file
    step: str,  # The step whose section to replace
    body: str,  # The new section, below its heading
) -> str:  # The notes file with that section replaced, or added at the end if the step had none
    "Put a new body under one step's `## step` heading, leaving every other line of the file as it was."
    lines = text.splitlines(keepends=True)   # as parse_notes splits them, so the section saved is the one it read; each keeps its end
    fenced = set()  # lines inside fenced code: a "## " there is code, not a heading, as parse_notes reads it
    fence: str | None = None
    for i, line in enumerate(lines):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if fence is not None or mark:
            fenced.add(i)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
    start = next((i for i, line in enumerate(lines) if i not in fenced and re.match(rf"^##\s+{re.escape(step)}(\s|$)", line)), None)
    new = [line + "\n" for line in body.rstrip("\n").split("\n")] if body.strip() else []   # a blank line that the body starts with stays
    if start is None:
        return text.rstrip("\n") + f"\n\n## {step}\n\n" + "".join(new)
    end = next((i for i in range(start + 1, len(lines)) if i not in fenced and re.match(r"^##\s", lines[i])), len(lines))
    head, tail = lines[:start + 1], lines[end:]
    if head[-1] == head[-1].rstrip("\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"):
        head[-1] += "\n"   # the heading was the last line, with no line break
    return ("".join(head + new + (["\n"] if tail else []) + tail)).rstrip("\n") + "\n"


def intro_of(
    text: str,  # The intro of a walk of the table of contents, in Markdown, or ""; see Walk.intro_text
) -> dict[str, str]:  # Its Markdown for each mode, do and watch; empty if there is none
    """Make a walk's intro, for the notes of its first stop: what a small walk leaves out, for example. Each mode keeps
    its own blocks, as in the notes, and the planned times go. Cues stay: the page hides them where it hides the notes'."""
    if not text.strip():
        return {}
    blocks, lines = note_blocks(text), text.splitlines()
    fence: str | None = None   # in fenced code a time: line is code
    timed = set()
    for at, line in enumerate(lines):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        elif fence is None and re.match(r"^time:\s*\d+:\d\d\s*$", line.strip()):
            timed.add(at)
    return {mode: "\n".join(line for at, line in enumerate(lines) if at not in blocks.fences and at not in timed and blocks.mode[at] in (None, mode))
            for mode in MODES}


# A requirement such as "pytest>=8.3.4", "Uvicorn[standard] ; python_version < '3.12'" or "x @ git+https://...": its name,
# its extras, what comes between (the versions, or "@" and an address), and its marker after the ";".
REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?([^;]*)(?:;(.*))?$", re.S)


def package_names(
    path: str,  # The file's path in the project; its name says how to read it
    data: bytes,  # Its contents
) -> object | None:  # What it says without the versions of packages, to compare; None if it is not a file of packages
    """Read a pyproject.toml or a uv.lock with the versions left out. pyproject.toml keeps all it says, with each
    requirement cut to its name, its extras, its address if it has one, and its marker. uv.lock keeps, of the project's
    own packages, the names they ask for, and the names of the workspace's members."""
    return _package_names(path.rsplit("/", 1)[-1], data)


@functools.lru_cache(maxsize=16)
def _package_names(
    name: str,  # The file's name: pyproject.toml or uv.lock
    data: bytes,  # Its contents
) -> object | None:  # See package_names; cached, as the page asks every two seconds, and a uv.lock can be large
    "Read a file of packages once for each contents."
    if name not in ("pyproject.toml", "uv.lock"):
        return None
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None

    def cut(requirement: object) -> object:   # "pytest>=8.3.4" is pytest; a table, such as { include-group = "test" }, stays
        found = REQUIREMENT.match(requirement) if isinstance(requirement, str) else None
        if not found:
            return requirement
        extras = sorted(e.strip().lower() for e in (found.group(2) or "[]")[1:-1].split(",") if e.strip())
        between, marker = found.group(3).strip(), " ".join((found.group(4) or "").split())
        address = between[1:].strip() if between.startswith("@") else ""   # a package from an address: the address counts
        return (re.sub(r"[-_.]+", "-", found.group(1)).lower() + (f"[{','.join(extras)}]" if extras else "")
                + (f" @ {address}" if address else "") + (f"; {marker}" if marker else ""))

    def names(value: object) -> list:   # every name = "..." in a table, at any depth
        if isinstance(value, dict):
            return [*([value["name"]] if isinstance(value.get("name"), str) else []), *(n for v in value.values() for n in names(v))]
        return [n for v in value for n in names(v)] if isinstance(value, list) else []

    if name == "uv.lock":
        own = [p for p in doc.get("package", []) if isinstance(p, dict) and isinstance(p.get("source"), dict)
               and {"editable", "virtual"} & set(p["source"])]
        return {"requires-python": doc.get("requires-python"), "manifest": sorted(set(names(doc.get("manifest", {})))),
                "own": {p.get("name"): sorted(set(names({k: v for k, v in p.items() if k not in ("name", "version", "source")})))
                        for p in own}}
    lists = [("project", "dependencies"), ("build-system", "requires"), ("tool", "uv", "dev-dependencies")]
    groups = [("project", "optional-dependencies"), ("dependency-groups",)]

    def at(keys: tuple) -> object:   # the table or list at a path of keys, or None
        value = doc
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    for keys in lists:
        if isinstance(at(keys), list):
            at(keys[:-1])[keys[-1]] = sorted(map(cut, at(keys)), key=repr)
    for keys in groups:
        table = at(keys)
        for group, wanted in (table.items() if isinstance(table, dict) else []):
            if isinstance(wanted, list):
                table[group] = sorted(map(cut, wanted), key=repr)
    return doc


def loosely_same(
    path: str,  # A file of the project, named by a glob of the walk's key loose
    mine: Path,  # The learner's copy of it
    theirs: bytes | None,  # The commit's copy, or None if the commit does not have it
) -> str:  # How it matches: "names", by the names of its packages; "skipped", left out of the match; "" if it does not match
    """Compare a file loosely. A pyproject.toml or a uv.lock matches if it names the same packages, whatever their versions:
    a learner who runs uv add on a later day gets newer versions. Any other file that the key names is not compared, but
    it must be there if the commit has it, and gone if the commit has not."""
    try:
        data = mine.read_bytes() if mine.is_file() else None
    except OSError:
        data = None
    return loose_match(path, data, theirs)


def loose_match(
    path: str,  # A file of the project, named by a glob of the walk's key loose
    data: bytes | None,  # One copy of it, or None where it does not exist
    theirs: bytes | None,  # The other copy, or None
) -> str:  # See loosely_same
    "Compare two copies of a file loosely."
    if (data is None) != (theirs is None):
        return ""   # made, or deleted, on one side only: loose or not, the move is not made yet
    if path.rsplit("/", 1)[-1] not in ("pyproject.toml", "uv.lock"):
        return "skipped"
    if data is None or theirs is None:
        return ""
    ours, wanted = package_names(path, data), package_names(path, theirs)
    return "names" if ours is not None and ours == wanted else ""


def notes_inside(
    repo: "Repo",  # The repository being browsed
    path: Path,  # A notes file
) -> bool:  # Whether the file is inside the repository or its replay copy
    "Notes belong outside the repository the class walks through: a move would change them, or throw them away."
    path = path.resolve()
    return any(path.is_relative_to(folder.resolve()) for folder in {repo.main, repo.work})


def split_slides(
    text: str,  # A Markdown deck
) -> list[str]:  # Its slides, in order
    "Split a Markdown deck into slides: a line that is exactly `---` starts a new slide, except inside fenced code."
    slides: list[list[str]] = [[]]
    fence: str | None = None
    for line in text.split("\n"):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        if fence is None and line.strip() == "---":
            slides.append([])
        else:
            slides[-1].append(line)
    joined = ["\n".join(lines).strip() for lines in slides]
    return [slide for slide in joined if slide] or [""]


def expand_entry(
    entry: object,  # One manifest entry: "talk.md", "talk.md#2", "talk.md#2-4", "deck.pdf#page=3", "pic.svg", or 5
    folder: Path,  # Where the manifest is; slide files are beside it
    deck: str = "",  # The manifest's default deck, which bare numbers refer to
) -> list[str]:  # One entry per slide, each naming a single slide
    """Turn a manifest entry into single slides.

    A Markdown file stands for all of its slides; `#2` picks one and `#2-4` a run of them. A bare number or range,
    `5` or `"5-7"`, means those slides of the default deck: Markdown slides, or pages when the deck is a PDF.
    """
    text = str(entry).strip()
    bare = re.fullmatch(r"(\d+)(?:-(\d+))?", text)
    if bare:
        if not deck:
            return [text]
        numbers = range(int(bare.group(1)), int(bare.group(2) or bare.group(1)) + 1)
        prefix = "#page=" if deck.lower().endswith(".pdf") else "#"
        return [f"{deck}{prefix}{n}" for n in numbers]
    path, _, fragment = text.partition("#")
    if not path.lower().endswith((".md", ".markdown")) or fragment == "doc":
        return [text]
    picked = re.fullmatch(r"(\d+)(?:-(\d+))?", fragment)
    if picked:
        return [f"{path}#{n}" for n in range(int(picked.group(1)), int(picked.group(2) or picked.group(1)) + 1)]
    file = folder / path
    count = len(split_slides(file.read_text(encoding="utf-8"))) if file.is_file() else 1
    return [f"{path}#{n}" for n in range(1, count + 1)]


def rebase_entry(
    entry: str,  # One slide, as the manifest names it, relative to the manifest's folder
    folder: Path,  # The manifest's folder
    root: Path,  # The folder that the page serves slides from
) -> str | None:  # The same slide, relative to root; None when it lies outside root
    "Name a slide from the folder the page serves, so that a manifest may use slides from another slides folder."
    path, hash_, fragment = entry.partition("#")
    if "://" in path:
        return entry
    full = os.path.normpath(folder / path)
    base = os.path.normpath(root)
    if os.path.commonpath([full, base]) != base:
        return None
    return Path(os.path.relpath(full, base)).as_posix() + hash_ + fragment


def load_slides(
    manifest: Path | None,  # The slides manifest, a TOML file
    root: Path | None = None,  # The folder the page serves slides from; default: the manifest's folder
) -> dict[str, list[str]]:  # Step name to its slides, in order, one entry per slide, relative to root
    """Read the manifest that says which slides go with which step.

        deck = "talk.md"                                  # optional: the deck that bare numbers refer to

        [slides]
        step-00 = ["opening.md"]                          # every slide in that file
        step-01 = ["tools.md#1-3", "pictures/mask.svg"]   # slides 1 to 3 of a file, then a picture
        step-02 = [7, "8-10", "handout.pdf#page=2"]       # slides of the default deck, then a page of a PDF

        [docs]
        step-03 = "walkthrough.md"                        # one Markdown document, whole, scrolled instead of slides

    A step under [docs] shows that file as one document in the slide pane, instead of any slides: it is not split
    at `---`, and it scrolls. In the list it is the single entry `walkthrough.md#doc`, which [slides] may also use.

    Each entry is a path from the manifest's folder. A Markdown file holds one slide or several, separated by lines
    that are exactly `---`. Images are slides of their own. A PDF page is written `deck.pdf#page=3`, and a slide
    of an HTML deck with whatever fragment that deck uses, such as `deck.html#/3`. With a table of contents, the
    page serves slides from the class folder, `root`: an entry may then name a slide in another slides folder of the
    class, such as `../../../slides/talk.md#2`. An entry outside root is left out; `timewalk-check` reports it.
    """
    if manifest is None or not manifest.is_file():
        return {}
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    deck = str(data.get("deck", ""))
    folder = manifest.parent
    base = root or folder
    out = {}
    for step, entries in data.get("slides", {}).items():
        if isinstance(entries, list):
            slides = (rebase_entry(slide, folder, base) for entry in entries for slide in expand_entry(entry, folder, deck))
            out[step] = [slide for slide in slides if slide is not None]
    for step, doc in data.get("docs", {}).items():
        if isinstance(doc, str) and doc.strip():
            rebased = rebase_entry(doc.strip().partition("#")[0] + "#doc", folder, base)
            if rebased is not None:
                out[step] = [rebased]
    return out


def shell_environment(
    inherited: dict[str, str],  # This process's environment
) -> dict[str, str]:  # The environment for a shell in the browsed repository
    """Give a shell the user's environment without this tool's own Python.

    `uv run timewalk.py` puts the script's environment first on PATH and names it in VIRTUAL_ENV. Left there, `python`,
    `pytest` and the rest in a class shell would be timewalk's, not the project's."""
    env = {**inherited, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TIMEWALK": "1"}
    ours = {str(Path(sys.prefix) / "bin")}
    if env.get("VIRTUAL_ENV"):
        ours.add(str(Path(env["VIRTUAL_ENV"]) / "bin"))
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p.rstrip("/") not in ours)
    for name in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_RUN_RECURSION_DEPTH", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    return env


def typed_line(
    length: int,  # How many characters the command line held, or -1 if timewalk cannot tell
    text: str,  # What was just typed
) -> int:  # How many characters it holds now, or -1 if timewalk cannot tell
    """Follow the command line of a shell from what is typed into it, to know when it is empty.

    Enter, Ctrl-C and Ctrl-U leave it empty. A printable character adds one, and Backspace takes one away. An
    arrow key, Tab or another control key can change the line in ways timewalk cannot see, so after one the
    length is unknown until the next Enter.
    """
    for char in text:
        if char in "\r\n\x03\x15":
            length = 0
        elif char in "\x7f\x08":
            length = max(length - 1, 0) if length > 0 else length
        elif char < " " or char == "\x1b":
            length = -1
        elif length >= 0:
            length += 1
    return length


class Terminal:
    "A shell on a pseudo-terminal, shared by every page connected to it and kept alive between page loads."

    def __init__(
        self,
        cwd: Path,  # Directory the shell starts in
        startup: str = "",  # A command typed into the shell as soon as it starts, for example `claude`
    ):
        self.cwd = cwd
        self.startup = startup
        self.master: int | None = None
        self.process: subprocess.Popen | None = None
        self.clients: set[WebSocket] = set()
        self.sending: set[asyncio.Future] = set()
        self.scrollback: deque[bytes] = deque()
        self.scrollback_size = 0
        self.size = (30, 110)
        self.room_size: tuple[int, int] | None = None  # In Shell mode, the size the Room window gave; it wins over the others
        self.room_socket = None  # The Room window's socket that gave it; when that socket closes, the size is forgotten
        self.sized = False  # Whether a window has given the shell a size; a window that only opens it then leaves it alone
        self.line = 0  # the length of the command line as typed, -1 if unknown: see typed_line

    def start(self) -> None:
        "Start the shell if it is not running."
        if self.process is not None and self.process.poll() is None:
            return
        self.master, slave = pty.openpty()
        env = shell_environment(dict(os.environ))
        self.process = subprocess.Popen(
            [os.environ.get("SHELL", "/bin/zsh"), "-l"], cwd=self.cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, preexec_fn=lambda: fcntl.ioctl(slave, termios.TIOCSCTTY, 0),
        )
        os.close(slave)
        self.resize(*self.size)
        asyncio.get_running_loop().add_reader(self.master, self._readable)
        if self.startup:
            os.write(self.master, (self.startup + "\r").encode("utf-8"))

    def _readable(self) -> None:
        "Forward what the shell printed to every connected page, and remember it for pages that connect later."
        assert self.master is not None
        try:
            data = os.read(self.master, 65536)
        except OSError:
            data = b""
        if not data:  # the shell exited
            asyncio.get_running_loop().remove_reader(self.master)
            os.close(self.master)
            self.master, self.process = None, None
            data = b"\r\n[the shell exited; press any key to start a new one]\r\n"
        self.scrollback.append(data)
        self.scrollback_size += len(data)
        while self.scrollback_size > SCROLLBACK_BYTES and len(self.scrollback) > 1:
            self.scrollback_size -= len(self.scrollback.popleft())
        for client in list(self.clients):
            task = asyncio.ensure_future(self._send(client, data))
            self.sending.add(task)  # keep a reference until it finishes, or it can be collected mid-send
            task.add_done_callback(self.sending.discard)

    async def _send(
        self,
        client: WebSocket,  # Page to send to
        data: bytes,  # Terminal output
    ) -> None:
        "Send output to one page, dropping the page if it has gone away."
        try:
            await client.send_bytes(data)
        except Exception:
            self.clients.discard(client)

    def write(
        self,
        text: str,  # Keystrokes
    ) -> None:
        "Type into the shell."
        self.start()
        assert self.master is not None
        self.line = typed_line(self.line, text)
        os.write(self.master, text.encode("utf-8"))

    def refresh_prompt(self) -> bool:  # Whether the shell was idle, and so got an Enter
        """Press Enter in an idle shell, so that it draws its prompt again, with the new HEAD after a move.

        The shell is idle when it is itself in the foreground of its terminal, waiting for a command, and nothing is
        typed on its command line. A running program, such as a training run or Claude, never gets the Enter.
        """
        if self.master is None or self.process is None or self.process.poll() is not None or self.line != 0:
            return False
        try:
            idle = os.tcgetpgrp(self.master) == os.getpgid(self.process.pid)
        except OSError:
            return False
        if idle:
            os.write(self.master, b"\r")
        return idle

    def resize(
        self,
        rows: int,  # Height in character cells
        cols: int,  # Width in character cells
    ) -> None:
        "Tell the shell how big the terminal is."
        self.size = (rows, cols)
        if self.master is not None:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Hub:
    "Tells every open window when something changed, so all of them show the same thing."

    def __init__(self) -> None:
        self.pages: set[WebSocket] = set()

    async def tell(
        self,
        event: dict,  # What happened, for example {"type": "moved"}
    ) -> None:
        "Send an event to every page, forgetting pages that have closed."
        for page in list(self.pages):
            try:
                await page.send_json(event)
            except Exception:
                self.pages.discard(page)


def content_marks(
    notes_path: Path | None,  # The notes file, if there is one
    slides_path: Path | None,  # The slides manifest, if there is one
    root: Path | None = None,  # The folder slides are served from; default: the manifest's folder
    shared: Path | None = None,  # The shared notes that a walk's own notes include, if it has them
) -> tuple:  # Each file with its modification time and size
    "Fingerprint the notes, the manifest and every slide file it names, to see when one of them is edited."
    files = [path for path in (notes_path, slides_path, shared) if path is not None]
    if slides_path is not None and slides_path.is_file():
        base = root or slides_path.parent
        try:
            entries = {entry.split("#")[0] for slides in load_slides(slides_path, base).values() for entry in slides}
        except (OSError, ValueError):
            entries = set()
        files += [base / entry for entry in sorted(entries) if "://" not in entry]
    marks = []
    for path in files:
        try:
            info = path.stat()
            marks.append((str(path), info.st_mtime_ns, info.st_size))
        except OSError:
            marks.append((str(path), None, None))
    return tuple(marks)


async def watch_content(
    paths,  # A function that gives the notes file, the slides manifest and the folder slides are served from, now
    hub: "Hub",  # Where to announce changes
    every: float = 1.0,  # Seconds between looks
) -> None:
    "Tell every page when the notes or a slide changes, so an edit in your editor shows at once, without a reload."
    seen = None
    while True:
        now = await asyncio.to_thread(content_marks, *paths())
        if seen is not None and now != seen:
            await hub.tell({"type": "content"})
        seen = now
        await asyncio.sleep(every)


async def watch_edits(
    repo: Repo,  # The repository being browsed
    hub: Hub,  # Where to announce changes
    every: float = 1.0,  # Seconds between looks
) -> None:
    "Tell every page when the edits in the working copy change, so a command such as `just fmt` shows at once."
    seen = None
    while True:
        try:
            now = await asyncio.to_thread(repo.edit_marks)
        except GitError:
            now = seen
        if seen is not None and now != seen:
            await hub.tell({"type": "edits"})
        seen = now
        await asyncio.sleep(every)


def make_app(
    repo: Repo,  # The repository to browse
    token: str,  # Secret every request must carry
    port: int,  # Port the server listens on, for checking where requests come from
    notes_path: Path | None = None,  # The notes file, the script of each step, if there is one
    assistant: str = "claude",  # Command started in the assistant tab; empty for a plain shell
    slides_path: Path | None = None,  # The slides manifest, if there is one
    watch_every: float = 1.0,  # Seconds between looks for edits in the working copy
    show_clock: bool = False,  # Show the clock band on the page
    any_host: bool = False,  # Accept requests addressed to any host name, when timewalk listens beyond this machine
    walks: list | None = None,  # The walks of a table of contents, the default first; each has its own notes and slides
    root: Path | None = None,  # With walks: the class folder, where every walk's slides are served from
    start_walk: str | None = None,  # With walks: the id of the walk to start on, instead of the first
    run: bool | None = None,  # Whether to run a strict walk's do$ lines of uv, to see the files they write; None: unless TIMEWALK_NO_RUN is set
) -> Starlette:  # The web application
    "Build the web application: the two pages, the read-only repository API, the terminals, and the event hub."
    terminals: dict[str, Terminal] = {}
    walks = walks or []
    root = root.resolve() if root is not None else None  # as load_toc resolves the walks' paths, or slides would fall outside
    first = next((w for w in walks if w.id == start_walk), walks[0]) if walks else None
    # The walk on show, and its files. Without a table of contents there is one walk, with no id, the files given.
    active: dict = {"walk": first, "notes": first.notes if first else notes_path, "slides": first.slides if first else slides_path,
                    "root": root if walks else (slides_path.parent if slides_path else None)}

    def terminal_for(
        name: str,  # Tab name: replay, runs, runs2 to runs9, main, assistant, or extra-N
    ) -> Terminal | None:  # The shell behind that tab, made on first use
        "Find or start the shell for a tab. Every tab runs at the current step except `main`."
        if not re.fullmatch(r"replay|runs[2-9]?|main|assistant|extra-\d{1,2}", name):
            return None
        if name not in terminals:
            terminals[name] = Terminal(repo.main if name == "main" else repo.work, startup=assistant if name == "assistant" else "")
        return terminals[name]
    hub = Hub()
    clock: dict[str, float | None] = {"started": None}
    # What every window shows, kept here so that all the windows on the page agree.
    # `shell` is the Shell toggle: the terminals take the space of the slides and the files, in every window.
    showing: dict = {"slide": 0, "layout": "split", "path": None, "view": "file", "track": "replay", "step": None, "shell": False,
                     "of": None, "mode": "do", "done": 0}
    # In a tutorial, `mode` is "do" (the learner makes each move by hand; `done` counts the moves of the step marked
    # done) or "watch" (timewalk checks out each move's commit). Every window shares both.
    showing["mode"] = first.mode if first else "do"
    # After a restart, the moves already made at the step count as done: the code is there.
    showing["done"] = repo.position()[1] or 0 if repo.steps else 0
    # Where each step was left: its slide, and how far down its slide, notes and open file were scrolled, as
    # fractions. A move back to a step brings it all back. Kept while the server runs.
    memory: dict[str, dict] = {}
    # With several walks, each keeps its own memory and the step it was left at; `memory` holds the walk on show.
    left: dict[str, tuple[dict, str | None]] = {}
    # A strict walk's do$ lines of uv that do mode replaces by a take of their files: what facts.takes finds for each move,
    # by its commit, its do$ lines and the walk's loose. A move not found yet waits in `waiting`, for one task to run.
    if run is None:
        from timewalk.observe import running  # imported here: observe imports this module

        run = running()
    found_takes: dict[tuple, dict[int, dict]] = {}
    failed_takes: dict[tuple, tuple[str, float]] = {}   # a move whose run failed, as when uv has no network: why, and when
    retrying: dict[tuple, str] = {}   # a move that failed and runs again: why it failed, which do mode still says until the run ends
    waiting: list[tuple] = []
    finder: dict = {"task": None, "key": None, "urgent": set()}   # the task, the move it runs now, and the moves of the step on show
    from timewalk.observe import Stop  # imported here: observe imports this module

    stopper: dict = {"now": Stop()}   # at the server's end, kills a run, so that Ctrl-C does not wait for it; one for each lifespan

    async def find_takes() -> None:
        "Run the do$ lines of each waiting move in a scratch clone, one move at a time, and tell the pages after each."
        from timewalk.facts import move_takes  # imported here: facts imports this module

        try:
            while waiting:
                key = finder["key"] = waiting.pop(0)
                sha, commands, loose = key
                try:
                    found_takes[key] = await asyncio.to_thread(move_takes, repo.main, sha, list(commands), list(loose), stopper["now"])
                    failed_takes.pop(key, None)
                    retrying.pop(key, None)
                except Exception as error:   # any failure: do mode says why, gives the do$ lines as they are, and the next move runs
                    if stopper["now"].stopped.is_set():
                        return
                    print(f"timewalk: cannot run the do$ lines of {sha[:12]} to see what they write: {error}")
                    failed_takes[key] = (str(error) or type(error).__name__, time.monotonic())
                    retrying.pop(key, None)
                if stopper["now"].stopped.is_set():
                    return
                if key in finder["urgent"] or not waiting:   # the pages draw again for the step on show, and once at the end
                    await hub.tell({"type": "notes"})
        finally:
            finder["task"] = finder["key"] = None

    def with_takes(
        notes: dict[str, dict],  # The notes of the walk, by stop, as walk_notes gives them
    ) -> dict[str, dict]:  # The same, with a take on each do$ line that do mode replaces, and taking on one not known yet
        """Give each do$ line of a strict walk its take, as the facts do: see facts.takes. A move whose lines have not run yet
        has taking on each do$ line, and goes to the task that runs them; the move of the step on show goes first. A step
        of one commit is a change of its own, as a move is. A move whose run failed has untaken, why, on its first line of uv;
        it runs again after Try again (/api/takes), or when the notes are read a minute after the failure."""
        from timewalk.facts import may_take, runs_uv  # imported here: facts imports this module

        walk = active["walk"]
        if walk is None or walk.kind != "tutorial" or not run:
            return notes
        loose = tuple(walk.loose)
        out, first, others, urgent = {}, [], [], set()
        here = showing["step"]
        steps = {step.name: (at, step) for at, step in enumerate(repo.steps)}
        for name, entry in notes.items():
            parts = [dict(part) for part in entry.get("parts", [])]
            heads = [at for at, part in enumerate(parts) if part["kind"] == "move"]
            changes = [(repo.move_named(parts[at]["name"]), at + 1, end) for at, end in zip(heads, [*heads[1:], len(parts)], strict=False)]
            at, step = steps.get(name, (None, None))
            if step is not None and not heads and not repo.moves[at] and not step.section and one_commit(step.sha, step.start or (repo.steps[at - 1].sha if at else "")):
                changes = [(Move(step.name, step.sha, step.subject), 0, len(parts))]   # a step of one commit: its whole section
            for move, begin, end in changes:
                lines = [part for part in parts[begin:end] if part["kind"] == "command" and part.get("makes")]
                commands = tuple(part["text"] for part in lines)
                if move is None or not may_take(list(commands), [f["path"] for f in repo.move_files(move)], list(loose)):
                    continue
                key = (move.sha, commands, loose)
                if key in failed_takes and time.monotonic() - failed_takes[key][1] > 60:
                    retrying[key] = failed_takes.pop(key)[0]
                    found_takes.pop(key, None)
                if key in failed_takes or key in retrying:   # do mode says why once, at the first line of uv, and gives every line as it is
                    first_uv = next((part for part in lines if runs_uv(part["text"])), lines[0])
                    first_uv["untaken"] = failed_takes[key][0] if key in failed_takes else retrying[key]
                    if key in failed_takes:
                        continue
                    first_uv["retrying"] = True   # and runs it again, below
                if key in found_takes:
                    for number, part in enumerate(lines):
                        part["take"] = found_takes[key].get(number)
                    continue
                for part in lines if key not in retrying else []:
                    part["taking"] = True   # until the run says which lines a take stands for
                if name == here:
                    urgent.add(key)   # the move that runs now too: its end tells the pages
                if key != finder["key"]:
                    (first if name == here else others).append(key)
            out[name] = {**entry, "parts": parts}
        finder["urgent"] = urgent
        waiting[:] = list(dict.fromkeys(first + others))   # only what these notes need: an older text's moves do not run
        if waiting and finder["task"] is None:
            finder["task"] = asyncio.create_task(find_takes())
        return out

    @functools.cache
    def one_commit(
        sha: str,  # A step's commit
        start: str,  # The commit where the step starts: the step before it, or "" for the first step
    ) -> bool:  # Whether the step is one commit after its start
        "Say whether a step without moves is a change of its own, one commit, as facts.gather finds it."
        try:
            return bool(start) and git(repo.main, "rev-parse", f"{sha}^") == start
        except GitError:
            return False

    def remember(step: str | None, **what) -> None:
        "Note where a step is: its slide, or the scroll of one pane."
        if step:
            memory.setdefault(step, {"slide": 0, "scroll": {}})
            memory[step].update({k: v for k, v in what.items() if k != "scroll"})
            memory[step]["scroll"].update(what.get("scroll", {}))

    def deck_now() -> tuple[list[str], list[int]]:  # The slides of the step on show, and the move each belongs to (0: the step's own)
        """Read the manifest afresh, so slides can be edited while presenting.

        In a tutorial, a step's deck is its own slides, then the slides of each of its moves, in order.
        """
        index = repo.current()
        if index is None:
            return [], []
        listed = load_slides(active["slides"], active["root"])
        step = repo.steps[index]
        deck = [] if step.section and repo.moves[index] else list(listed.get(step.name, []))   # a small step's slides are the slides of its moves
        owners = [0] * len(deck)
        for number, move in enumerate(repo.moves[index], 1):
            own = listed.get(move.name, [])
            deck += own
            owners += [number] * len(own)
        if not deck and step.section:   # a small step, or a stop at a move, with no slides of its own: its step's
            deck = list(listed.get(step.section, []))
            owners = [0] * len(deck)
        return deck, owners

    def changes_now() -> dict:  # "last" and "next": each {label, name, before, after}, or None
        """Say what the reader's two tabs compare, for the place on show and the mode.

        Last change looks back: the move (or the step) just made. Next change looks forward: the move to make next, or
        the next step. In a tutorial's do mode the code does not move, so both follow the moves marked done.
        """
        index, move = repo.position()
        if index is None:
            return {"last": None, "next": None}
        moves = repo.moves[index]

        def of_move(number: int) -> dict:
            return {"name": moves[number - 1].name, "before": repo.commit_at(index, number - 1), "after": repo.commit_at(index, number)}

        if moves:
            made = move if showing["mode"] == "watch" else showing["done"]
            return {"last": of_move(made) if made else None, "next": of_move(made + 1) if made < len(moves) else None}
        step = repo.steps[index]
        last = {"name": step.name, "before": repo.before(index), "after": step.sha} if repo.before(index) else None
        later = repo.steps[index + 1] if index + 1 < len(repo.steps) else None
        return {"last": last, "next": {"name": later.name, "before": later.start or step.sha, "after": later.sha} if later else None}

    def slides_now() -> list[str]:  # The slides of the step the working copy is at
        "The deck of the step on show."
        return deck_now()[0]

    def synced() -> bool:  # Whether slides and moves follow each other
        "Say whether going to a move shows its slides, and going to a move's slide makes that move. The manifest's `sync` wins over the walk's."
        manifest = active["slides"]
        if manifest is not None and manifest.is_file():
            try:
                value = tomllib.loads(manifest.read_text(encoding="utf-8")).get("sync")
            except tomllib.TOMLDecodeError:
                value = None
            if isinstance(value, bool):
                return value
        return bool(active["walk"] and active["walk"].sync)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def allowed(
        request: Request | WebSocket,  # Incoming request or socket
    ) -> bool:  # Whether it carries the token and was addressed to this machine
        """Refuse anything that does not carry the token, or, on this machine only, was addressed to another host name.

        The host check stops a hostile web page that points a name of its own at 127.0.0.1. When timewalk listens
        beyond this machine (--host), people reach it by an IP address or a DNS name, so only the token guards it.
        """
        given = request.query_params.get("t") or request.headers.get("x-timewalk-token", "") or request.cookies.get("timewalk", "")
        return secrets.compare_digest(given, token) and (any_host or request.headers.get("host", "") in hosts)

    def guarded(handler):
        async def wrapper(request: Request) -> Response:
            if not allowed(request):
                return JSONResponse({"error": "missing or wrong token"}, status_code=403)
            try:
                return JSONResponse(await handler(request))
            except GitError as exc:
                return JSONResponse({"error": str(exc), "edits": repo.edits()}, status_code=409)
        return wrapper

    def page(name: str):
        async def serve(request: Request) -> Response:
            if not allowed(request):
                return Response("Open the address timewalk printed, including its ?t=... part.", status_code=403)
            response = FileResponse(HERE / "static" / name, headers={"cache-control": "no-store"})
            # The page's own scripts, styles and slides are then fetched with this cookie instead of the address.
            response.set_cookie("timewalk", token, httponly=True, samesite="strict")
            return response
        return serve

    def behind_token(files: StaticFiles):
        "Serve a folder only to a page that was opened with the token."
        async def serve(scope, receive, send) -> None:
            if scope["type"] == "http" and not allowed(Request(scope)):
                await Response("Open the address timewalk printed, including its ?t=... part.", status_code=403)(scope, receive, send)
                return
            async def revalidated(message) -> None:
                # Scripts change between versions, and a page that mixes a new script with a cached old one is blank.
                if message["type"] == "http.response.start":
                    message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-cache")]
                await send(message)
            await files(scope, receive, revalidated)
        return serve

    # With several walks, slides are served from the class folder, but only from the walks' slides folders: the
    # class folder also holds the notes, with their private cues, and the table of contents.
    slide_folders = [os.path.realpath(w.slides.parent) for w in walks if w.slides is not None]
    private = {os.path.realpath(path) for w in walks for path in (w.notes, w.shared) if path is not None}  # never served, wherever they are

    def slides_only(files: StaticFiles):
        "Serve a file of the class folder only if it lies in a slides folder of a walk."
        async def serve(scope, receive, send) -> None:
            if scope["type"] == "http" and walks:
                path, mounted = scope["path"], scope.get("root_path", "")
                path = path[len(mounted):] if mounted and path.startswith(mounted) else path   # as StaticFiles reads it
                # realpath: a link in a slides folder that leads out of it is outside too.
                wanted = os.path.realpath(os.path.join(str(active["root"]), unquote(path).lstrip("/")))
                served = any(os.path.commonpath([wanted, folder]) == folder for folder in slide_folders)
                if not served or wanted in private or os.path.basename(wanted) == "toc.toml":
                    await Response("Not a slide.", status_code=404)(scope, receive, send)
                    return
            await files(scope, receive, send)
        return serve

    @guarded
    async def state(request: Request) -> dict:
        "Where the working copy is, the list of steps, the slides of this step, and the clock."
        deck, owners = deck_now()
        showing["slide"] = min(showing["slide"], max(len(deck) - 1, 0))
        where = repo.state()
        showing["step"] = repo.steps[where["current"]].name if where["current"] is not None else None
        return {**where, "clock": clock["started"], "now": time.time(), "slides": deck, "slide": showing["slide"],
                "restore": memory.get(showing["step"] or "", {}).get("scroll", {}),
                "has_slides": bool(load_slides(active["slides"], active["root"])), "has_notes": active["notes"] is not None, "show_clock": show_clock, "layout": showing["layout"], "path": showing["path"],
                "view": showing["view"], "track": showing["track"], "shell": showing["shell"],
                "room_sizes": {name: term.room_size for name, term in terminals.items() if showing["shell"] and term.room_size},
                "slide_moves": owners, "sync": synced(), "mode": showing["mode"], "done": showing["done"], "of": showing["of"],
                "changes": changes_now(), "at": showing.get("at"),
                "walk": {"id": active["walk"].id, "title": active["walk"].title, "kind": active["walk"].kind, "mode": active["walk"].mode,
                         "description": active["walk"].description, "intro": intro_of(active["walk"].intro_text()),
                         "own": active["walk"].shared is not None} if active["walk"] else None,
                "walks": [{"id": w.id, "title": w.title, "kind": w.kind} for w in walks]}

    @guarded
    async def tree(request: Request) -> dict:
        "The files at the current step."
        return repo.tree()

    @guarded
    async def file(request: Request) -> dict:
        """One file: its text in the working copy, the Last change and the Next change to it, and its edits.

        With `of`, the change of that move instead of the Last change. With `at`, the text as that move or step leaves
        it, read only: for a file the learner has not made yet.
        """
        path = request.query_params["path"]
        found = repo.read(path)
        if not (repo.work / path).resolve().is_relative_to(repo.work.resolve()):
            return {**found, "diff": "", "edits": ""}   # outside the copy: nothing at all, not even a diff
        of, at = request.query_params.get("of"), request.query_params.get("at")
        changes = changes_now()
        diff = lambda c: git(repo.work, "diff", "--no-color", c["before"], c["after"], "--", path) if c else ""   # noqa: E731
        last = repo.move_diff(of, path) if of else (diff(changes["last"]) if changes["last"] else ("" if found.get("missing") else repo.diff(path)))
        answer = {**found, "diff": last, "next": diff(changes["next"]), "edits": repo.edit_diff(path),
                  "last_name": of or (changes["last"] or {}).get("name"), "next_name": (changes["next"] or {}).get("name")}
        if of and repo.move_named(of):
            answer["of"] = of
        if at:
            commit = (repo.move_named(at).sha if repo.move_named(at) else next((s.sha for s in repo.steps if s.name == at), None))
            if commit:
                answer["at"], answer["at_text"] = at, repo.read_at(commit, path)
        answer.update(await asyncio.to_thread(compare_now, path))
        return answer

    def compare_now(
        path: str,  # A file of the working copy
    ) -> dict:  # compare: the diff from the learner's file to the move's; compare_name: that move; compare_loose: how it matches loosely
        """In do mode: compare the learner's file with the move being worked on, as the match does. A file of the walk's
        loose that matches by its packages' names says so, and shows its diff too."""
        index, _ = repo.position()
        moves = repo.moves[index] if index is not None else []
        if showing["mode"] != "do" or not moves or showing["done"] >= len(moves):
            return {}
        working = showing["done"] + 1
        commit = repo.commit_at(index, working)
        file = repo.work / path
        name = moves[working - 1].name
        if file.is_file() and file.stat().st_size > MAX_FILE_BYTES:
            return {"compare": "", "compare_name": name, "compare_loose": ""}   # too large to show, as the file itself is
        theirs = blob_at_commit(repo.work, commit, path)
        if theirs is not None and len(theirs) > MAX_FILE_BYTES:
            return {"compare": "", "compare_name": name, "compare_loose": ""}
        mine = file.read_bytes() if file.is_file() else None
        loose = active["walk"].loose if active["walk"] else []
        how = loosely_same(path, file, theirs) if any(fnmatch.fnmatchcase(path, glob) for glob in loose) else ""
        mode = ""   # the same lines, but one can run and the other cannot: a diff of lines does not show that
        if mine is not None and mine == theirs and repo.file_modes():
            entry = git(repo.work, "--literal-pathspecs", "ls-tree", commit, "--", path).split("\t")[0].split()
            runs = bool(entry) and entry[0] == "100755"
            if runs != os.access(file, os.X_OK):
                mode = "runs" if runs else "stops"
        return {"compare": blob_diff(path, mine, theirs), "compare_name": name, "compare_loose": how, "compare_mode": mode}

    @guarded
    async def match(request: Request) -> dict:
        "In do mode, whether the learner's files match the commit of the move being worked on, and which differ."
        index, _ = repo.position()
        moves = repo.moves[index] if index is not None else []
        if showing["mode"] != "do" or not moves or showing["done"] >= len(moves):
            return {"move": None}
        working = showing["done"] + 1
        head = git(repo.work, "rev-parse", "HEAD")
        loose: dict[str, str] = {}
        differ = await asyncio.to_thread(repo.differ_from, head, repo.commit_at(index, working),   # off the event loop
                                         active["walk"].loose if active["walk"] else None, loose)
        return {"move": moves[working - 1].name, "number": working, "differ": differ, "match": not differ, "loose": loose}

    @guarded
    async def move(request: Request) -> dict:
        "Move to another step, or in a tutorial to a move of a step, and tell every page."
        body = await request.json()
        to, number = int(body["to"]), body.get("move")
        if body.get("name") is not None and not (0 <= to < len(repo.steps) and repo.steps[to].name == body["name"]):
            raise GitError(f"the steps changed while you asked for {body['name']}; ask again")  # another window changed the walk
        if number is not None:
            check_move_order(to, int(number))
        repo.move(to, set_aside=bool(body.get("set_aside")), move=int(number) if number is not None else None)
        # A move to a step starts it with no move done; a move to a move (watch mode, or Catch me up) has made that many.
        showing["done"] = int(number) if number is not None else 0
        showing["at"] = None   # the answer of a move: it belongs to the place it was opened at
        showing["step"] = repo.steps[to].name
        showing["slide"] = memory.get(showing["step"], {}).get("slide", 0)   # back where you left this step
        if repo.moves[to] and synced():
            owners = deck_now()[1]
            place = int(number or 0)
            earlier = [i for i, owner in enumerate(owners) if owner < place]
            # The move's first slide; for a move without slides, the last slide before it, so that Down goes on, not back.
            showing["slide"] = owners.index(place) if place in owners else (earlier[-1] if earlier else 0)
        await hub.tell({"type": "moved", **({"kept": repo.kept} if repo.kept else {})})
        # The shells at the step are now at another commit. An idle one draws its prompt again, to show the new HEAD.
        for term in terminals.values():
            if term.cwd == repo.work:
                term.refresh_prompt()
        return repo.state()

    @guarded
    async def change_walk(request: Request) -> dict:
        "Show another walk, in every window: its steps, notes and slides. The working copy moves to where that walk was left."
        body = await request.json()
        walk = next((w for w in walks if w.id == body.get("id")), None)
        if walk is None:
            raise GitError(f"there is no walk called {body.get('id')}")
        if walk is active["walk"]:
            return repo.state()
        before = repo.steps
        here = showing["step"]  # the step on show, named in the walk on show, for a stash of edits
        step_now, move_now = repo.position()
        if move_now:
            here = repo.moves[step_now][move_now - 1].name  # in a tutorial, the move made
        before_moves, before_at = repo.moves, repo.at
        repo.select(walk.tags, walk.steps, tutorial=walk.kind == "tutorial", select=walk.select)
        names = [step.name for step in repo.steps]
        saved, step, number = left.get(walk.id, ({}, None, None))
        index = names.index(step) if step in names else 0
        number = number if step in names and repo.moves[index] and isinstance(number, int) and 0 <= number <= len(repo.moves[index]) else None
        try:
            repo.move(index, set_aside=bool(body.get("set_aside")), here=here, move=number, anywhere=True)
        except GitError:
            repo.steps, repo.moves, repo.at = before, before_moves, before_at  # the walk on show stays, with its steps and place
            raise
        left[active["walk"].id] = (dict(memory), showing["step"], move_now)
        memory.clear()
        memory.update(saved)
        active.update(walk=walk, notes=walk.notes, slides=walk.slides)
        showing["mode"], showing["done"] = walk.mode, number or 0
        showing["step"] = names[index]
        showing["slide"] = memory.get(showing["step"], {}).get("slide", 0)
        await hub.tell({"type": "walk", **({"kept": repo.kept} if repo.kept else {})})
        for term in terminals.values():
            if term.cwd == repo.work:
                term.refresh_prompt()
        return repo.state()

    def check_move_order(
        index: int,  # The step of the move asked for
        number: int,  # The move asked for; 0 is the step's Start
    ) -> None:
        """Keep a tutorial's moves in order: forward one move at a time, and back only to the step's Start.

        A step starts with `just setup`, so any step is a safe place to land; a move has no setup of its own, so it
        builds on the environment that the moves before it left. A skip would miss what a move did to it.
        """
        step_now, move_now = repo.position()
        reached = (move_now or 0) if showing["mode"] == "watch" else showing["done"]
        if number == 0 or (step_now == index and number in (reached, reached + 1)):
            return
        if step_now != index:
            raise GitError(f"a step starts at its Start: go to {repo.steps[index].name} first")
        nxt = repo.moves[index][reached].name if reached < len(repo.moves[index]) else "none"
        raise GitError(f"the moves go in order: the next is {nxt}. To go back, go to the step's Start and run just setup")

    @guarded
    async def mark_done(request: Request) -> dict:
        "In do mode, mark how many of the step's moves the learner has made. The code does not move; with sync, the slides follow."
        body = await request.json()
        if body.get("step") != showing["step"]:
            raise GitError(f"the step changed while you marked a move of {body.get('step')}; mark it again")
        index = repo.current()
        moves = repo.moves[index] if index is not None else []
        wanted = int(body.get("done", 0))
        if wanted not in (showing["done"], showing["done"] + 1):
            # Done goes forward one move at a time. Back is the step's Start, which also puts the code back.
            raise GitError("Done goes one move at a time. To go back, go to the step's Start and run just setup")
        showing["done"] = min(max(wanted, 0), len(moves))
        remember(showing["step"], done=showing["done"])
        if moves and synced():
            owners = deck_now()[1]
            working = min(showing["done"] + 1, len(moves))   # the move the learner works on now
            if working in owners:
                showing["slide"] = owners.index(working)
        await hub.tell({"type": "done"})
        return {"done": showing["done"]}

    @guarded
    async def slide(request: Request) -> dict:
        "Show another of this step's slides and tell every page. With sync, a slide of another move makes that move."
        body = await request.json()
        if body.get("step") is not None and body["step"] != showing["step"]:
            raise GitError(f"the step changed while you asked for a slide of {body['step']}; ask again")  # another window moved
        deck, owners = deck_now()
        to = min(max(int(body["to"]), 0), max(len(deck) - 1, 0))
        index, number = repo.position()
        owner = owners[to] if to < len(owners) else 0
        # In watch mode, a slide of another move shows that move. In do mode the learner moves the code, so slides do not.
        if index is not None and number is not None and owner and owner > number and synced() and showing["mode"] == "watch":
            if owner > number + 1:
                # That slide is past the next move: make the next move only, and show its first slide, or stay if it has none.
                owner = number + 1
                to = owners.index(owner) if owner in owners else showing["slide"]
            repo.move(index, set_aside=bool(body.get("set_aside")), move=owner)   # refuses a move with edits, until set aside
            showing["slide"], showing["done"] = to, owner
            remember(showing["step"], slide=to)
            await hub.tell({"type": "moved", **({"kept": repo.kept} if repo.kept else {})})
            for term in terminals.values():
                if term.cwd == repo.work:
                    term.refresh_prompt()
            return {"slide": to, "slides": deck}
        showing["slide"] = to
        remember(showing["step"], slide=showing["slide"])
        await hub.tell({"type": "slide"})
        return {"slide": showing["slide"], "slides": deck}

    @guarded
    async def just_recipes(request: Request) -> dict:
        "The `just` recipes at the current step, for each track."
        return {"replay": recipes(repo.work), "main": recipes(repo.main)}

    @guarded
    async def take_again(request: Request) -> dict:
        "Try again: forget each failed run of do$ lines, and tell the pages, whose read of the notes then runs them again."
        for key in list(failed_takes):
            retrying[key] = failed_takes.pop(key)[0]
        await hub.tell({"type": "notes"})
        return {"ok": True}

    @guarded
    async def notes(request: Request) -> dict:
        "The notes, read afresh, so the file can be edited while the class runs."
        if active["notes"] is None or not active["notes"].is_file():
            return {"notes": {}, "path": str(active["notes"]) if active["notes"] else None}
        walk = active["walk"]
        own = walk.own_sections() if walk else None   # a walk with notes of its own reads the shared notes through its includes
        parsed = parse_notes(active["notes"].read_text(encoding="utf-8")) if own is None else {}
        tutorial = walk is not None and walk.kind == "tutorial"
        return {"notes": with_takes(walk_notes(parsed, repo.steps, repo.moves, tutorial, bool(walk and walk.select), own)), "path": str(active["notes"])}

    @guarded
    async def save_notes(request: Request) -> dict:
        "Write one step's section of the notes file, if nobody changed that section since the page read it."
        notes_file = active["notes"]
        if notes_file is None:
            raise GitError("this walk has no notes file to save to")
        if active["walk"] is not None and active["walk"].shared is not None:
            raise GitError(f"the notes of {active['walk'].title} are made from {notes_file.name} and the sections it includes; edit them in your editor")
        body = await request.json()
        began = next((w for w in walks if w.id == body.get("walk")), None)
        if active["walk"] is not None and body.get("walk") != active["walk"].id and (began is None or began.notes != active["notes"]):
            raise GitError(f"the walk changed to {active['walk'].title} since you began this edit. Copy your text, and edit again in its walk")
        step, text, base = str(body["step"]), str(body["text"]), str(body.get("base", ""))
        with open(notes_file, encoding="utf-8", newline="") if notes_file.is_file() else contextlib.nullcontext(None) as read:
            current = read.read() if read else ""   # as written: a file with Windows line endings keeps them
        parsed = parse_notes(current)
        tutorial = active["walk"] is not None and active["walk"].kind == "tutorial"
        shown = walk_notes(parsed, repo.steps, repo.moves, tutorial, bool(active["walk"] and active["walk"].select))
        if shown.get(step, {}).get("derived") or (step not in parsed and step not in {s.name for s in repo.steps}):
            raise GitError(f"{step} has no section of its own in {notes_file.name}; edit the sections of the steps it comes from")
        now = parsed.get(step, {}).get("raw", "")
        if now.strip() != base.strip():
            raise GitError(f"the notes for {step} changed in {notes_file.name} since this page read them. Copy your text, reload, and edit again")
        crlf = "\r\n" in current
        written = replace_section(current, step, text.replace("\r\n", "\n"))
        if crlf:   # the lines of the new section, and an end added, take the file's line ends
            written = re.sub(r"(?<!\r)\n", "\r\n", written)
        with open(notes_file, "w", encoding="utf-8", newline="") as out:
            out.write(written)
        await hub.tell({"type": "notes"})
        return {"ok": True}

    async def pdf(request: Request) -> Response:
        "Make a PDF of the slides, one page each and no notes, and send it to download."
        if not allowed(request):
            return JSONResponse({"error": "missing or wrong token"}, status_code=403)
        if active["slides"] is None:
            return JSONResponse({"error": "there are no slides to put in a PDF"}, status_code=404)
        from timewalk import slides_pdf  # imported here: slides_pdf imports this module

        try:
            title = active["walk"].title if active["walk"] else repo.main.name
            walk = active["walk"]
            data = await asyncio.to_thread(slides_pdf.make_pdf, active["slides"], walk.shared if walk and walk.shared else active["notes"],
                                           title, False, None, active["root"],
                                           walk.select if walk else None, not walk or walk.kind == "tutorial",
                                           walk.own_sections() if walk else None)
        except SystemExit as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        except Exception as exc:  # a browser that fails to start, a page that fails to draw: say what happened
            return JSONResponse({"error": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"}, status_code=500)
        return Response(data, media_type="application/pdf",
                        headers={"content-disposition": f'attachment; filename="{repo.main.name}.pdf"', "cache-control": "no-store"})

    async def presenter(request: Request) -> Response:
        "The old presenter address: there is one page now, so go there, keeping the token."
        query = request.url.query
        return RedirectResponse("/" + (f"?{query}" if query else ""), status_code=307)

    @guarded
    async def show(request: Request) -> dict:
        "Change what both pages show: the layout, the Shell toggle, the open file and its view, the terminal tab in front."
        body = await request.json()
        if body.get("layout") in ("slides", "split", "code"):
            showing["layout"] = body["layout"]
        if isinstance(body.get("shell"), bool):
            showing["shell"] = body["shell"]
            if not body["shell"]:
                for term in terminals.values():
                    term.room_size = None  # out of Shell mode, the window you type in sets the size again
        if body.get("mode") in ("do", "watch"):
            showing["mode"] = body["mode"]
        if body.get("path"):
            showing["path"], showing["view"] = str(body["path"]), body.get("view") or "file"
            showing["of"] = str(body["of"]) if body.get("of") else None  # the move whose change the reader shows, or none
            showing["at"] = str(body["at"]) if body.get("at") else None  # the move whose version of the file the reader shows
            if showing["layout"] == "slides":
                showing["layout"] = "split"  # a file asked for must be seen
        if body.get("track") and terminal_for(str(body["track"])) is not None:
            showing["track"] = str(body["track"])
        await hub.tell({"type": "show", **{k: body[k] for k in ("path", "view", "of", "at", "marks", "track", "layout", "from", "focus") if k in body},
                        "layout": showing["layout"], "shell": showing["shell"], "mode": showing["mode"]})
        return {"ok": True}

    def inside_copy(
        path: str,  # A path from the page
    ) -> bool:  # Whether it names a place inside the replay copy
        "Refuse a path that leaves the copy, such as ../x."
        return bool(path) and (repo.work / path).resolve().is_relative_to(repo.work.resolve())

    @guarded
    async def patch(request: Request) -> dict:
        "For a patch$ line: whether the learner's file is as the move leaves it, as it was before the move, or neither."
        path, name = request.query_params.get("path", ""), request.query_params.get("move", "")
        if not inside_copy(path) or not name:
            return {"state": ""}
        return await asyncio.to_thread(repo.patch_state, name, path)

    @guarded
    async def keep(request: Request) -> dict:
        "Keep the learner's version of a file on a saved branch, before Replace types the command that puts the move's in its place."
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        path, name = str(body.get("path", "")), str(body.get("move", ""))
        if not inside_copy(path) or repo.move_named(name) is None:
            raise GitError(f"there is no file {path} of a move {name} to keep")
        return {"branch": await asyncio.to_thread(repo.keep_file, path, name)}

    @guarded
    async def type_command(request: Request) -> dict:
        "Type a command into one of the shells, as if at the keyboard."
        body = await request.json()
        track = body.get("track", "replay")
        term = terminal_for(track)
        if term is None:
            raise GitError(f"there is no terminal called {track}")
        term.write(body["text"] + ("\r" if body.get("enter", True) else ""))
        showing["track"] = track
        await hub.tell({"type": "show", "track": track, "from": body.get("from", ""), "focus": "sender", "layout": showing["layout"], "shell": showing["shell"]})
        return {"ok": True}

    @guarded
    async def set_clock(request: Request) -> dict:
        "Start or reset the session clock."
        body = await request.json()
        clock["started"] = time.time() if body.get("action") == "start" else None
        await hub.tell({"type": "clock"})
        return {"clock": clock["started"], "now": time.time()}

    async def refuse(socket: WebSocket) -> None:
        "Refuse a socket with code 4403, which a page can read: a socket closed before its handshake only says it failed."
        await socket.accept()
        await socket.close(code=4403)

    async def terminal(socket: WebSocket) -> None:
        "Connect a page to one of the shells."
        term = terminal_for(socket.path_params["name"]) if allowed(socket) else None
        if term is None:
            await refuse(socket)
            return
        await socket.accept()
        term.start()
        if term.scrollback:
            await socket.send_bytes(b"".join(term.scrollback))
        term.clients.add(socket)
        try:
            while True:
                message = json.loads(await socket.receive_text())
                if message["type"] == "input":
                    term.write(message["data"])
                elif message["type"] == "resize":
                    try:
                        rows, cols = int(message["rows"]), int(message["cols"])
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                    if not (0 < rows <= 1000 and 0 < cols <= 2000):
                        continue
                    if showing["shell"] and message.get("room"):
                        # In Shell mode the Room window sizes the shell; the other windows take the same rows and columns.
                        term.room_size, term.room_socket, term.sized = (rows, cols), socket, True
                        term.resize(rows, cols)
                    elif not (showing["shell"] and term.room_size):
                        # A window that only opened the shell gives its size to a new shell, not to one already sized.
                        if not (message.get("opening") and term.sized):
                            term.resize(rows, cols)
                            term.sized = True
                        continue
                    # The Room's size stands while Shell is on: a window that asks for another size is told it again.
                    await hub.tell({"type": "size", "track": socket.path_params["name"], "rows": term.size[0], "cols": term.size[1]})
        except WebSocketDisconnect:
            pass
        finally:
            term.clients.discard(socket)
            if term.room_socket is socket:
                term.room_size = term.room_socket = None  # the Room window went away: the other windows size the shell again
                await hub.tell({"type": "unsized", "track": socket.path_params["name"]})

    async def events(socket: WebSocket) -> None:
        "Keep a window informed of moves, and of what another window asked to show."
        if not allowed(socket):
            await refuse(socket)
            return
        await socket.accept()
        hub.pages.add(socket)
        # Say so, once it is on the list: what the server tells the windows from now on reaches this one too.
        await socket.send_json({"type": "hello"})
        try:
            while True:
                # A window that scrolls a slide, a file or the notes says so here, and the other windows follow.
                try:
                    message = json.loads(await socket.receive_text())
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "scroll" and message.get("pane") == "term":
                    # A terminal scrolled back: the other windows scroll the same shell back as many lines. Not remembered.
                    lines = message.get("lines")
                    if not isinstance(lines, (int, float)) or isinstance(lines, bool) or not math.isfinite(lines):
                        continue
                    relay = {"type": "scroll", "pane": "term", "track": str(message.get("track", "")), "lines": max(int(lines), 0)}
                    for page in list(hub.pages):
                        if page is not socket:
                            try:
                                await page.send_json(relay)
                            except Exception:
                                hub.pages.discard(page)
                    continue
                if message.get("type") == "scroll" and message.get("pane") in ("slide", "file", "notes"):
                    try:
                        at = float(message.get("at", 0))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if not math.isfinite(at):
                        continue
                    at = min(max(at, 0.0), 1.0)
                    # Remember it for this step: the slide's scroll with its slide, the file's with its path.
                    place = {"slide": [showing["slide"], at], "notes": at, "file": [showing["path"], at]}[message["pane"]]
                    remember(showing["step"], scroll={message["pane"]: place})
                    for page in list(hub.pages):
                        if page is not socket:
                            try:
                                await page.send_json({"type": "scroll", "pane": message["pane"], "at": at})
                            except Exception:
                                hub.pages.discard(page)
        except WebSocketDisconnect:
            pass
        finally:
            hub.pages.discard(socket)

    mounts = [Mount("/static", behind_token(StaticFiles(directory=HERE / "static")))]
    if active["root"] is not None:
        mounts.append(Mount("/slides", behind_token(slides_only(StaticFiles(directory=active["root"])))))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        "Watch the working copy for edits, and the notes and slides for changes, while the server runs."
        stopper["now"] = Stop()
        watchers = [asyncio.create_task(watch_edits(repo, hub, watch_every))]
        if any(w.notes or w.slides for w in walks) or active["notes"] is not None or active["slides"] is not None:
            watchers.append(asyncio.create_task(watch_content(lambda: (active["notes"], active["slides"], active["root"],
                                                                           active["walk"].shared if active["walk"] else None), hub, watch_every)))
        try:
            yield
        finally:
            for watcher in watchers:
                watcher.cancel()
            stopper["now"].stop()   # a run of do$ lines in its thread ends now
            if finder["task"] is not None:
                finder["task"].cancel()

    return Starlette(lifespan=lifespan, routes=[
        Route("/", page("index.html")),
        Route("/presenter", presenter),
        Route("/api/state", state),
        Route("/api/tree", tree),
        Route("/api/file", file),
        Route("/api/move", move, methods=["POST"]),
        Route("/api/slide", slide, methods=["POST"]),
        Route("/api/walk", change_walk, methods=["POST"]),
        Route("/api/done", mark_done, methods=["POST"]),
        Route("/api/match", match),
        Route("/api/recipes", just_recipes),
        Route("/api/notes", notes),
        Route("/api/notes", save_notes, methods=["POST"]),
        Route("/api/takes", take_again, methods=["POST"]),
        Route("/api/pdf", pdf),
        Route("/api/show", show, methods=["POST"]),
        Route("/api/type", type_command, methods=["POST"]),
        Route("/api/patch", patch),
        Route("/api/keep", keep, methods=["POST"]),
        Route("/api/clock", set_clock, methods=["POST"]),
        WebSocketRoute("/ws/term/{name}", terminal),
        WebSocketRoute("/ws/events", events),
        *mounts,
    ])


def main() -> None:
    "Parse the command line, start the server and open the page."
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", type=Path, nargs="?", default=Path.cwd(), help="the repository to browse (default: here)")
    parser.add_argument("--notes", type=Path, help="a Markdown file of notes, the script of each step, in one `## step-name` section per step; outside the repository")
    parser.add_argument("--slides", type=Path, help="a TOML manifest of slides: [slides] step-name = [\"file.md\", \"deck.pdf#page=2\"]")
    parser.add_argument("--toc", type=Path, help="a table of contents, toc.toml, of several walks, each with its own notes and slides; in place of --notes and --slides")
    parser.add_argument("--walk", help="with --toc: the id of the walk to start on (default: the first)")
    parser.add_argument("--tags", default="step-*", help="glob for the tags that mark steps (default: step-*)")
    parser.add_argument("--commits", action="store_true", help="step through the commits of the current branch instead of tags")
    parser.add_argument("--replay", type=Path, help="where to put the replay copy (default: <repo>-replay, beside the repository)")
    parser.add_argument("--in-place", action="store_true", help="move the repository itself instead of a second working copy")
    parser.add_argument("--discard-edits", action="store_true", help="no longer needed: a move keeps edits on a branch timewalk/saved/<place>, without asking. "
                        "Only a replay worktree of an older version still throws them away")
    parser.add_argument("--clock", action="store_true", help="show the clock band: the clock, the planned times and the next step")
    parser.add_argument("--assistant", default="claude", help="command started in the assistant terminal tab (default: claude)")
    parser.add_argument("--port", type=int, default=8765, help="the port to listen on (default: 8765)")
    parser.add_argument("--host", default="127.0.0.1", help="the address to listen on (default: 127.0.0.1, this machine only). "
                        "0.0.0.0 listens on every network: anyone who can reach the port and has the address can run commands as you")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    parser.add_argument("--no-run", action="store_true", help="in a strict tutorial, do not run the do$ lines of uv to see which files they write: "
                        "do mode then gives the uv command itself (as TIMEWALK_NO_RUN=1 does)")
    args = parser.parse_args()

    walks, root, start = [], None, None
    if args.toc:
        from timewalk.walks import TocError, load_toc  # imported here: walks imports this module

        if args.notes or args.slides or args.commits or args.tags != "step-*":
            raise SystemExit("timewalk: give --toc, or --notes, --slides and --tags; with --toc each walk names its own")
        try:
            walks = load_toc(args.toc.resolve())
        except TocError as exc:
            raise SystemExit(f"timewalk: {exc}") from None
        root = args.toc.resolve().parent
        start = next((w for w in walks if w.id == args.walk), None) if args.walk else walks[0]
        if start is None:
            raise SystemExit(f"timewalk: {args.toc} has no walk called {args.walk}; it has {', '.join(w.id for w in walks)}")
    elif args.walk:
        raise SystemExit("timewalk: --walk needs --toc")
    try:
        repo = Repo(args.repo.resolve(), tags=start.tags if start else args.tags, commits=args.commits, in_place=args.in_place,
                    discard=args.discard_edits, replay=args.replay)
    except GitError as exc:
        raise SystemExit(f"timewalk: {exc}") from None
    try:
        if start is not None and (start.steps is not None or start.select or start.kind == "tutorial"):
            repo.select(start.tags, start.steps, tutorial=start.kind == "tutorial", select=start.select)
    except GitError as exc:
        from timewalk.walks import own_errors_text  # imported here: walks imports this module

        raise SystemExit(f"timewalk: {own_errors_text(start, repo.main, root, exc)}") from None
    if args.discard_edits and repo.branch:
        print("timewalk: --discard-edits is no longer needed: a move keeps edits on a branch timewalk/saved/<place>, without asking")
    notes_path = args.notes.resolve() if args.notes else None
    slides_path = args.slides.resolve() if args.slides else None
    for path in [notes_path, *(path for w in walks for path in (w.notes, w.shared))]:
        if path is not None and notes_inside(repo, path):
            raise SystemExit(f"timewalk: the notes file {path} is inside the repository or its replay copy. Keep the notes "
                             "outside it: a move would change them, or throw your edits away.")
    if walks:
        from timewalk.walks import check

        errors, warnings = check(walks, repo.main, root, repo.work, run=False)   # timewalk-check runs the do$ lines
        for line in warnings:
            print(f"timewalk: warning: {line}")
        if errors:
            raise SystemExit("timewalk: the walks of " + str(args.toc) + " have errors; fix them, then start again:\n" +
                             "\n".join(f"  {line}" for line in errors))
    if start is not None and start.select and repo.branch and repo.position()[0] is None:
        # A replay clone at no stop of a walk that selects goes to its first stop; a move keeps any edits. In place, nothing moves.
        try:
            repo.move(0, move=0 if repo.moves[0] else None, anywhere=True)
        except GitError as exc:
            raise SystemExit(f"timewalk: {exc}") from None
        if repo.kept:   # no window is open yet to say it
            print(f"timewalk: to go to the first stop of {start.id}, your work is kept on {', '.join(repo.kept)}")
    local = args.host in ("127.0.0.1", "localhost", "::1")
    with socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET) as probe:
        # As uvicorn binds: the connections of a timewalk just stopped linger a minute, and must not stop a restart.
        # A port that another program listens on is still refused.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((args.host, args.port))
            probe.listen()
        except OSError as exc:
            raise SystemExit(f"timewalk: cannot listen on {args.host} port {args.port} ({exc.strerror}). If another timewalk "
                             "uses the port, stop it, or pass --port with another number.") from None
    token = secrets.token_urlsafe(16)
    shown = "127.0.0.1" if local else (socket.gethostname() if args.host in ("0.0.0.0", "::") else args.host)
    address = f"http://{shown}:{args.port}/?t={token}"
    print(f"timewalk: {len(repo.steps)} steps in {repo.main}")
    print(f"timewalk: stepping in {repo.work}" + (f", on the branch {repo.branch}" if repo.branch else ""))
    if repo.legacy:
        print(f"timewalk: {repo.work} is a git worktree, as older versions made it. It shares your repository's config and "
              f"branches. For a replay copy of its own, a clone: move the folder aside, keep what you need from it, run "
              f"git worktree prune, and start timewalk again")
    if not local:
        print(f"timewalk: WARNING: listening on {args.host}, beyond this machine. Anyone who can reach port {args.port} and has the")
        print("timewalk: address below can run commands as you. The connection is not encrypted. An SSH tunnel is safer.")
    print(f"timewalk: open       {address}")
    if walks:
        print(f"timewalk: {len(walks)} walks in {args.toc}; starting on {start.id}")
    if notes_path is not None or walks:
        print(f"timewalk: for the class, without the cues: {address}&cues=off")
    sys.stdout.flush()
    if not args.no_open and local:
        webbrowser.open(address)
    uvicorn.run(make_app(repo, token, args.port, notes_path, args.assistant, slides_path, show_clock=args.clock, any_host=not local,
                         walks=walks, root=root, start_walk=start.id if start else None, run=False if args.no_run else None),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
