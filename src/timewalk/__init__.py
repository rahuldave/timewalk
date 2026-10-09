"""timewalk: browse a repository one commit at a time, with a terminal that runs in it.

    timewalk /path/to/repo                        steps are the tags matching step-*
    timewalk /path/to/repo --notes notes.md       add the notes, the script of each step, beside the page
    timewalk /path/to/repo --slides slides.toml   add slides, one or more per step
    timewalk /path/to/repo --tags 'v*'            steps are other tags
    timewalk /path/to/repo --commits              steps are the commits on the current branch
    timewalk /path/to/repo --in-place             step the repository itself, not a second copy
    timewalk repo --replay worktree               put the replay copy in ./worktree instead of beside the repository
    timewalk /path/to/repo --discard-edits        a move throws edits away instead of asking; for a replay copy
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
) -> str:  # What git printed, without the trailing newline
    """Run git and return its output, raising `GitError` with git's own message when it fails.

    Another git in the same copy, such as the edits watcher's `git status` or a shell prompt's, holds `index.lock` for
    a moment; a command that meets it waits a little and runs again.
    """
    # A stash is never run twice: it may have stored the edits before it met the lock.
    for wait in (0.1, 0.2, 0.4, None) if args[:1] != ("stash",) else (None,):
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", input=input_text)
        if done.returncode == 0 or wait is None or "index.lock" not in done.stderr:
            break
        time.sleep(wait)
    if done.returncode != 0:
        raise GitError(done.stderr.strip() or done.stdout.strip() or f"git {' '.join(args)} failed")
    return done.stdout.rstrip("\n")


@dataclass
class Step:
    "One point in the history that the browser can stand on."

    index: int  # Position in the sequence, from 0
    name: str  # Tag name, or a short hash when stepping through commits
    sha: str  # The commit
    subject: str  # First line of the commit message
    note: str  # The annotated tag's message, or the rest of the commit message. Public: the audience sees it


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


class Repo:
    "The repository being browsed: its steps, and the working copy that moves between them."

    def __init__(
        self,
        main: Path,  # The repository the user pointed at
        tags: str = "step-*",  # Glob for the tags that mark steps
        commits: bool = False,  # Step through commits instead of tags
        in_place: bool = False,  # Move `main` itself instead of a second working copy
        discard: bool = False,  # A move throws uncommitted edits away instead of asking and stashing
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
        self.at: tuple[int, int] | None = None  # In a tutorial, the step and move the last move went to
        self.files_of: dict[str, list[dict]] = {}  # The files each move's commit added or changed, by commit

    def select(
        self,
        tags: str,  # Glob for the tags that mark the walk's steps
        names: list[str] | None = None,  # The steps of the walk, or None for every tag that matches
        tutorial: bool = False,  # Find the moves between the steps, for a tutorial
    ) -> None:
        "Use the steps of another walk. The working copy does not move; `move` does that."
        if self.branch:
            self._fetch(self.work)   # a tag made since the start needs its commit in the clone
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
        return self.steps[index - 1].sha if move == 0 else self.moves[index][move - 1].sha

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
    ) -> list[str]:  # The branches that keep them; empty when there was nothing to keep
        """Before a move resets the branch, keep the commits that a learner made, on a branch named for the place.

        A learner's commits are those made since timewalk last put the branch somewhere, on the branch or on a detached
        HEAD. A branch that the learner made keeps its own commits, so they are not copied.
        """
        if self.branch is None:
            return []
        placed = subprocess.run(["git", "rev-parse", "--verify", "--quiet", PLACED], cwd=self.work, capture_output=True, text=True).stdout.strip()
        tips = {git(self.work, "rev-parse", "HEAD")}
        branch = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}"], cwd=self.work, capture_output=True, text=True).stdout.strip()
        if branch:
            tips.add(branch)
        # Never a learner's: what a tag or a branch of your repository reaches, and what came before timewalk last put the branch.
        before = ([placed] if placed else []) + [f"--glob={HOME_TAGS}*", f"--remotes={HOME}", f"--glob={HOME_HEAD}"]
        kept = []
        for tip in sorted(tips):
            if not git(self.work, "rev-list", tip, "--not", *before, f"--exclude={self.branch}", "--branches"):
                continue
            names = set(git(self.work, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{SAVED}").splitlines())
            base = SAVED + (re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "step")   # a name git accepts, with no folders of its own
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
        return "between-steps"

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
        fields = git(self.work, "status", "--porcelain", "-z", "--untracked-files=no").split("\x00")
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
        """Move the working copy to a step, or to a move of a step. Edits are stashed, never discarded; untracked files are left alone.

        With `discard`, the move is `git checkout -f`: edits to tracked files are thrown away, and an untracked
        file is replaced only where the step has a file of that name. Every other untracked file still stays.

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
        if self.discard:
            self.keep_learner_commits(label)
            self._checkout(target, force=True)
            self.at = place
            self.keep_place()
            return
        # First the untracked files in the way, so that a move that cannot happen does not stash the edits away.
        untracked = set(git(self.work, "ls-files", "--others").splitlines())
        in_the_way = sorted(untracked & set(git(self.work, "ls-tree", "-r", "--name-only", target).splitlines()))
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
        step = self.steps[index]
        if index == 0:
            return dict.fromkeys(git(self.work, "ls-tree", "-r", "--name-only", step.sha).splitlines(), "A")
        out = git(self.work, "diff", "--name-status", "--no-renames", self.steps[index - 1].sha, step.sha)
        return {path: status[0] for status, path in (line.split("\t", 1) for line in out.splitlines() if line)}

    def span(self) -> tuple[str, str] | None:  # The commits before and after what the place on show changed; None at the first step
        """Say what "Changes in this step" compares: a step with the step before, or in a tutorial a move with the move before.

        At move 0 of a step, nothing of it is made yet, so both commits are the same.
        """
        index, move = self.position()
        if index is None:
            return None
        if move is not None:
            return self.commit_at(index, max(move - 1, 0)), self.commit_at(index, move)
        return (self.steps[index - 1].sha, self.steps[index].sha) if index else None

    def move_files(
        self,
        move: Move,  # A move of a tutorial
    ) -> list[dict]:  # The files its commit added or changed, each as {path, status}
        "List the files a move's commit added or changed, for a `files:` line of the notes. A commit's files never change, so once."
        if move.sha not in self.files_of:
            out = git(self.work, "diff-tree", "--no-commit-id", "--name-status", "-r", "--no-renames", move.sha)
            self.files_of[move.sha] = [{"path": path, "status": status[0]}
                                       for status, path in (line.split("\t", 1) for line in out.splitlines() if line) if status[0] != "D"]
        return self.files_of[move.sha]

    def move_named(
        self,
        name: str,  # A move's name, such as step-02.1
    ) -> Move | None:  # That move of the walk on show, if there is one
        "Find a move of the walk on show by its name."
        return next((m for moves in self.moves for m in moves if m.name == name), None)

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
    ) -> list[str]:  # The files whose contents differ, tracked or not; empty when the files match
        """Compare the learner's files with a commit, by content: every file that the commits between base and target touch,
        and every file the learner edited. A file the learner made, and git does not track yet, counts too.

        Two git commands, whatever the number of files: the commit's blob for each path, and the blob of each file on disk.
        """
        touched = git(self.work, "diff", "--name-only", "-z", "--no-renames", base, target).split("\x00")
        paths = sorted({p for p in touched if p} | set(self.edits()))
        if not paths:
            return []
        wanted = {}
        for entry in git(self.work, "ls-tree", "-r", "-z", target, "--", *paths).split("\x00"):
            if "\t" in entry:
                info, path = entry.split("\t", 1)
                wanted[path] = info.split()[2]
        on_disk = [p for p in paths if (self.work / p).is_file()]
        hashes = dict(zip(on_disk, git(self.work, "hash-object", "--stdin-paths", input_text="\n".join(on_disk)).splitlines(), strict=True)) if on_disk else {}
        return [p for p in paths if wanted.get(p) != hashes.get(p)]

    def tree(self) -> dict:  # Files of the working copy, with what the current step did to each
        "Describe the working copy: its tracked files and which of them the current step, or move, changed."
        index, span = self.current(), self.span()
        if index is None:
            changed = {}
        elif span is None:
            changed = self.changes(index)
        else:
            out = git(self.work, "diff", "--name-status", "--no-renames", *span)
            changed = {path: status[0] for status, path in (line.split("\t", 1) for line in out.splitlines() if line)}
        files = [{"path": p, "status": changed.get(p, "")} for p in git(self.work, "ls-files").splitlines()]
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
                "discard": self.discard, "steps": [asdict(s) for s in self.steps], "current": index,
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
SHOW_LINE = re.compile(r"^\s+show:\s*`(.+)`\s*$")


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
        > Say: ...            a cue; shown with the prose, set apart in its own shade
        ### step-02.1 Title   in a tutorial, starts the notes of one move of the step, which is one commit
        files:                in a tutorial, the files that the move's commit added or changed, each a link
        files:                followed by items, the files to look at, each with words:
        - diff `src/x.py`: why   a button to the change of the move (or the step), and a sentence
        - file `src/y.py`: why   a button to the file, as the move leaves it
          show: `a line`         under an item: a line of the change, drawn as a small excerpt, and marked in the reader

    Every other line is prose, shown as written. Inside a fenced code block every line is prose, so a `$ ` line
    there is code to read, not a command. `parts` keeps the prose and the commands in the order of the file, so the
    page can show each command where it is written. `raw` keeps the section as written, for editing. A move's
    heading is a part of its own, `{"kind": "move", "name": "step-02.1", "title": ...}`, and the parts after it
    belong to that move; the heading stays in `text`, as a Markdown heading, for the PDF.
    """
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
        planned = re.match(r"^time:\s*(\d+):(\d\d)\s*$", line.strip()) if fence is None else None
        command = re.match(r"^\s*(main|runs[2-9]?)?\$\s+(.+)$", line) if fence is None else None
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
            parts.append({"kind": "files", "items": items})
        elif fence is None and items is not None and not command and (item := FILES_ITEM.match(line)):
            items.append({"kind": item.group(1), "move": item.group(2), "path": item.group(3), "text": item.group(4).strip(), "show": []})
            current["text"].append(line)  # the PDF prints an item as written
        elif fence is None and items and (shown := SHOW_LINE.match(line)):
            if shown.group(1).strip():
                items[-1]["show"].append(shown.group(1))
            current["text"].append(line)
        elif fence is None and items and not command and line.startswith("  ") and line.strip() and lines[at - 1].strip():
            items[-1]["text"] = (items[-1]["text"] + " " + line.strip()).strip()   # an item's words, over more lines
            current["text"].append(line)
        elif command:
            items = None
            entry = {"track": command.group(1) or "replay", "text": command.group(2).strip()}
            current["commands"].append(entry)
            parts.append({"kind": "command", **entry})
        else:
            if line.strip():
                items = None   # a line of prose ends the items of a files: line
            current["text"].append(line)
            if parts and parts[-1]["kind"] == "text":
                parts[-1]["text"] += "\n" + line
            else:
                parts.append({"kind": "text", "text": line})
    for entry in notes.values():
        entry["text"] = "\n".join(entry["text"]).strip()
        entry["raw"] = "\n".join(entry["raw"]).strip("\n")
        entry["parts"] = [p for p in ({**p, "text": p["text"].strip("\n")} if p["kind"] == "text" else p for p in entry["parts"])
                          if p["kind"] != "text" or p["text"].strip()]
    return notes


def replace_section(
    text: str,  # The whole notes file
    step: str,  # The step whose section to replace
    body: str,  # The new section, below its heading
) -> str:  # The notes file with that section replaced, or added at the end if the step had none
    "Put a new body under one step's `## step` heading, leaving every other line of the file as it was."
    lines = text.split("\n")
    fenced = set()  # lines inside fenced code: a "## " there is code, not a heading, as parse_notes reads it
    fence: str | None = None
    for i, line in enumerate(lines):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if fence is not None or mark:
            fenced.add(i)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
    start = next((i for i, line in enumerate(lines) if i not in fenced and re.match(rf"^##\s+{re.escape(step)}(\s|$)", line)), None)
    new = body.rstrip("\n").split("\n") if body.strip() else []   # a blank line that the body starts with stays
    if start is None:
        return text.rstrip("\n") + f"\n\n## {step}\n\n" + "\n".join(new) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if i not in fenced and re.match(r"^##\s", lines[i])), len(lines))
    tail = lines[end:]
    return "\n".join(lines[:start + 1] + new + ([""] if tail else []) + tail).rstrip("\n") + "\n"


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
) -> tuple:  # Each file with its modification time and size
    "Fingerprint the notes, the manifest and every slide file it names, to see when one of them is edited."
    files = [path for path in (notes_path, slides_path) if path is not None]
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
    # Where each step was left: its slide, and how far down its slide, notes and open file were scrolled, as
    # fractions. A move back to a step brings it all back. Kept while the server runs.
    memory: dict[str, dict] = {}
    # With several walks, each keeps its own memory and the step it was left at; `memory` holds the walk on show.
    left: dict[str, tuple[dict, str | None]] = {}

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
        deck = list(listed.get(repo.steps[index].name, []))
        owners = [0] * len(deck)
        for number, move in enumerate(repo.moves[index], 1):
            own = listed.get(move.name, [])
            deck += own
            owners += [number] * len(own)
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
        last = {"name": step.name, "before": repo.steps[index - 1].sha, "after": step.sha} if index else None
        later = repo.steps[index + 1] if index + 1 < len(repo.steps) else None
        return {"last": last, "next": {"name": later.name, "before": step.sha, "after": later.sha} if later else None}

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
    private = {os.path.realpath(w.notes) for w in walks if w.notes is not None}  # never served, wherever they are

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
                         "description": active["walk"].description} if active["walk"] else None,
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
        return answer

    @guarded
    async def match(request: Request) -> dict:
        "In do mode, whether the learner's files match the commit of the move being worked on, and which differ."
        index, _ = repo.position()
        moves = repo.moves[index] if index is not None else []
        if showing["mode"] != "do" or not moves or showing["done"] >= len(moves):
            return {"move": None}
        working = showing["done"] + 1
        head = git(repo.work, "rev-parse", "HEAD")
        differ = await asyncio.to_thread(repo.differ_from, head, repo.commit_at(index, working))   # off the event loop
        return {"move": moves[working - 1].name, "number": working, "differ": differ, "match": not differ}

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
        await hub.tell({"type": "moved"})
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
        repo.select(walk.tags, walk.steps, tutorial=walk.kind == "tutorial")
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
        await hub.tell({"type": "walk"})
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
            await hub.tell({"type": "moved"})
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
    async def notes(request: Request) -> dict:
        "The notes, read afresh, so the file can be edited while the class runs."
        if active["notes"] is None or not active["notes"].is_file():
            return {"notes": {}, "path": str(active["notes"]) if active["notes"] else None}
        return {"notes": parse_notes(active["notes"].read_text(encoding="utf-8")), "path": str(active["notes"])}

    @guarded
    async def save_notes(request: Request) -> dict:
        "Write one step's section of the notes file, if nobody changed that section since the page read it."
        notes_file = active["notes"]
        if notes_file is None:
            raise GitError("this walk has no notes file to save to")
        body = await request.json()
        began = next((w for w in walks if w.id == body.get("walk")), None)
        if active["walk"] is not None and body.get("walk") != active["walk"].id and (began is None or began.notes != active["notes"]):
            raise GitError(f"the walk changed to {active['walk'].title} since you began this edit. Copy your text, and edit again in its walk")
        step, text, base = str(body["step"]), str(body["text"]), str(body.get("base", ""))
        current = notes_file.read_text(encoding="utf-8") if notes_file.is_file() else ""
        now = parse_notes(current).get(step, {}).get("raw", "")
        if now.strip() != base.strip():
            raise GitError(f"the notes for {step} changed in {notes_file.name} since this page read them. Copy your text, reload, and edit again")
        notes_file.write_text(replace_section(current, step, text), encoding="utf-8")
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
            data = await asyncio.to_thread(slides_pdf.make_pdf, active["slides"], active["notes"], title, False, None, active["root"])
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
        watchers = [asyncio.create_task(watch_edits(repo, hub, watch_every))]
        if any(w.notes or w.slides for w in walks) or active["notes"] is not None or active["slides"] is not None:
            watchers.append(asyncio.create_task(watch_content(lambda: (active["notes"], active["slides"], active["root"]), hub, watch_every)))
        try:
            yield
        finally:
            for watcher in watchers:
                watcher.cancel()

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
        Route("/api/pdf", pdf),
        Route("/api/show", show, methods=["POST"]),
        Route("/api/type", type_command, methods=["POST"]),
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
    parser.add_argument("--discard-edits", action="store_true", help="a move throws uncommitted edits away instead of asking and stashing them; meant for a replay copy")
    parser.add_argument("--clock", action="store_true", help="show the clock band: the clock, the planned times and the next step")
    parser.add_argument("--assistant", default="claude", help="command started in the assistant terminal tab (default: claude)")
    parser.add_argument("--port", type=int, default=8765, help="the port to listen on (default: 8765)")
    parser.add_argument("--host", default="127.0.0.1", help="the address to listen on (default: 127.0.0.1, this machine only). "
                        "0.0.0.0 listens on every network: anyone who can reach the port and has the address can run commands as you")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
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
        if start is not None and (start.steps is not None or start.kind == "tutorial"):
            repo.select(start.tags, start.steps, tutorial=start.kind == "tutorial")
    except GitError as exc:
        raise SystemExit(f"timewalk: {exc}") from None
    notes_path = args.notes.resolve() if args.notes else None
    slides_path = args.slides.resolve() if args.slides else None
    for path in [notes_path, *(w.notes for w in walks)]:
        if path is not None and notes_inside(repo, path):
            raise SystemExit(f"timewalk: the notes file {path} is inside the repository or its replay copy. Keep the notes "
                             "outside it: a move would change them, or throw your edits away.")
    if walks:
        from timewalk.walks import check

        errors, warnings = check(walks, repo.main, root, repo.work)
        for line in warnings:
            print(f"timewalk: warning: {line}")
        if errors:
            raise SystemExit("timewalk: the walks of " + str(args.toc) + " have errors; fix them, then start again:\n" +
                             "\n".join(f"  {line}" for line in errors))
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
                         walks=walks, root=root, start_walk=start.id if start else None),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
