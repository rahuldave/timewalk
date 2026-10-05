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

By default the stepping happens in a second working copy, `<repo>-replay`, made with `git worktree`, so the
repository you point at is never moved. Moving never deletes an untracked file, so whatever a command wrote
there (a virtual environment, a database, a run's output) stays where it is.

The server listens on localhost only, unless --host says otherwise, and every request needs the token in the
address it prints, because a terminal in a web page is a way to run commands on this machine.
"""

import argparse
import asyncio
import contextlib
import fcntl
import json
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

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

HERE = Path(__file__).resolve().parent
MAX_FILE_BYTES = 400_000  # larger files are not shown
SCROLLBACK_BYTES = 256_000  # terminal output replayed to a page that reconnects


class GitError(RuntimeError):
    "A git command failed; the message is what git said."


def git(
    cwd: Path,  # Directory to run git in
    *args: str,  # Arguments to git
) -> str:  # What git printed, without the trailing newline
    "Run git and return its output, raising `GitError` with git's own message when it fails."
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
        self.main = Path(git(main, "rev-parse", "--show-toplevel"))
        self.steps = self._commit_steps() if commits else self._tag_steps(tags)
        if not self.steps:
            raise GitError(f"no steps found: no tags match {tags!r}. Use --commits to step through commits instead.")
        self.work = self.main if in_place else self._replay_copy()

    def _tag_steps(
        self,
        pattern: str,  # Glob for tag names
    ) -> list[Step]:  # Steps in tag-name order
        "Read the tags that mark steps. An annotated tag's message becomes the step's note."
        fmt = "%(refname:short)%00%(objecttype)%00%(objectname)%00%(*objectname)%00%(contents)%01"
        raw = git(self.main, "for-each-ref", f"--format={fmt}", "--sort=refname", f"refs/tags/{pattern}")
        steps = []
        for record in filter(None, (r.strip("\n") for r in raw.split("\x01"))):
            name, kind, sha, peeled, contents = record.split("\x00")
            commit = peeled if kind == "tag" else sha
            note = contents.strip() if kind == "tag" else ""
            steps.append(Step(len(steps), name, commit, git(self.main, "log", "-1", "--format=%s", commit), note))
        return steps

    def _commit_steps(self) -> list[Step]:  # Steps in history order, oldest first
        "Use every commit on the current branch's first-parent history as a step."
        raw = git(self.main, "log", "--first-parent", "--reverse", "--format=%H%x00%h%x00%s%x00%b%x01")
        steps = []
        for record in filter(None, (r.strip("\n") for r in raw.split("\x01"))):
            sha, short, subject, body = record.split("\x00")
            steps.append(Step(len(steps), short, sha, subject, body.strip()))
        return steps

    def _replay_copy(self) -> Path:  # The working copy that will be moved between steps
        "Find or make a second working copy, beside the repository or where --replay says, starting at the first step."
        path = self.replay or self.main.parent / f"{self.main.name}-replay"
        if path.resolve().is_relative_to(self.main.resolve()):
            raise GitError(f"the replay copy {path} would be inside the repository. Put it outside, for example beside it.")
        known = [line.split(" ", 1)[1] for line in git(self.main, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
        if str(path) in known or str(path.resolve()) in known:
            return path
        if path.exists():
            raise GitError(f"{path} exists and is not a working copy of this repository. Remove it or use --in-place.")
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.main, "worktree", "add", "--detach", str(path), self.steps[0].sha)
        return path

    def current(self) -> int | None:  # Index of the step the working copy is at, if it is exactly at one
        "Say which step the working copy stands on."
        head = git(self.work, "rev-parse", "HEAD")
        return next((s.index for s in self.steps if s.sha == head), None)

    def edits(self) -> list[str]:  # Paths of tracked files with uncommitted changes
        "List tracked files that differ from the step. Untracked files are not edits and are never touched."
        out = git(self.work, "status", "--porcelain", "--untracked-files=no")
        return [line[3:] for line in out.splitlines() if line]

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
    ) -> None:
        """Move the working copy to a step. Edits are stashed, never discarded; untracked files are left alone.

        With `discard`, the move is `git checkout -f`: edits to tracked files are thrown away, and an untracked
        file is replaced only where the step has a file of that name. Every other untracked file still stays.
        """
        if not 0 <= index < len(self.steps):
            raise GitError(f"there is no step {index}")
        target = self.steps[index]
        if self.discard:
            git(self.work, "checkout", "--force", "--detach", "--quiet", target.sha)
            return
        if self.edits():
            if not set_aside:
                raise GitError("uncommitted edits")
            now = self.current()
            label = self.steps[now].name if now is not None else "an unknown step"
            git(self.work, "stash", "push", "--message", f"timewalk: edits made at {label}")
        untracked = set(git(self.work, "ls-files", "--others").splitlines())
        in_the_way = sorted(untracked & set(git(self.work, "ls-tree", "-r", "--name-only", target.sha).splitlines()))
        if in_the_way:
            raise GitError("these untracked files exist where the step has a file, and will not be overwritten: " + ", ".join(in_the_way[:5]))
        git(self.work, "checkout", "--detach", "--quiet", target.sha)

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

    def tree(self) -> dict:  # Files of the working copy, with what the current step did to each
        "Describe the working copy: its tracked files and which of them the current step changed."
        index = self.current()
        changed = self.changes(index) if index is not None else {}
        files = [{"path": p, "status": changed.get(p, "")} for p in git(self.work, "ls-files").splitlines()]
        stat = ""
        if index:
            stat = git(self.work, "diff", "--shortstat", self.steps[index - 1].sha, self.steps[index].sha).strip()
        return {"files": files, "deleted": sorted(p for p, s in changed.items() if s == "D"), "summary": stat, "edits": self.edits()}

    def read(
        self,
        relative: str,  # Path inside the working copy
    ) -> dict:  # The file's text, or the reason it is not shown
        "Read one file from the working copy, refusing paths that leave it."
        path = (self.work / relative).resolve()
        if not path.is_relative_to(self.work.resolve()) or not path.is_file():
            return {"path": relative, "missing": True}
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
        "Show what the current step did to one file."
        index = self.current()
        if not index:
            return ""
        return git(self.work, "diff", "--no-color", self.steps[index - 1].sha, self.steps[index].sha, "--", relative)

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
        return {"main": str(self.main), "work": str(self.work), "in_place": self.work == self.main,
                "discard": self.discard, "steps": [asdict(s) for s in self.steps], "current": self.current(),
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

    Every other line is prose, shown as written. Inside a fenced code block every line is prose, so a `$ ` line
    there is code to read, not a command. `parts` keeps the prose and the commands in the order of the file, so the
    page can show each command where it is written. `raw` keeps the section as written, for editing.
    """
    notes: dict[str, dict] = {}
    current: dict | None = None
    fence: str | None = None  # the fence character while inside a fenced code block, ` or ~
    for line in text.splitlines():
        mark = re.match(r"^\s*(```+|~~~+)", line)
        heading = re.match(r"^##\s+(\S+)", line) if fence is None else None
        if heading:
            current = notes.setdefault(heading.group(1), {"time": None, "commands": [], "text": [], "raw": [], "parts": []})
            title = line[heading.end():].strip()
            if title:
                current["title"] = title
            continue
        if current is None:
            continue
        current["raw"].append(line)
        planned = re.match(r"^time:\s*(\d+):(\d\d)\s*$", line.strip()) if fence is None else None
        command = re.match(r"^\s*(main|runs[2-9]?)?\$\s+(.+)$", line) if fence is None else None
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        parts = current["parts"]
        if planned:
            current["time"] = int(planned.group(1)) * 60 + int(planned.group(2))
        elif command:
            entry = {"track": command.group(1) or "replay", "text": command.group(2).strip()}
            current["commands"].append(entry)
            parts.append({"kind": "command", **entry})
        else:
            current["text"].append(line)
            if parts and parts[-1]["kind"] == "text":
                parts[-1]["text"] += "\n" + line
            else:
                parts.append({"kind": "text", "text": line})
    for entry in notes.values():
        entry["text"] = "\n".join(entry["text"]).strip()
        entry["raw"] = "\n".join(entry["raw"]).strip("\n")
        entry["parts"] = [p for p in ({**p, "text": p["text"].strip("\n")} if p["kind"] == "text" else p for p in entry["parts"])
                          if p["kind"] == "command" or p["text"].strip()]
    return notes


def replace_section(
    text: str,  # The whole notes file
    step: str,  # The step whose section to replace
    body: str,  # The new section, below its heading
) -> str:  # The notes file with that section replaced, or added at the end if the step had none
    "Put a new body under one step's `## step` heading, leaving every other line of the file as it was."
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if re.match(rf"^##\s+{re.escape(step)}(\s|$)", line)), None)
    new = body.strip("\n").split("\n") if body.strip() else []
    if start is None:
        return text.rstrip("\n") + f"\n\n## {step}\n\n" + "\n".join(new) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^##\s", lines[i])), len(lines))
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


def load_slides(
    manifest: Path | None,  # The slides manifest, a TOML file
) -> dict[str, list[str]]:  # Step name to its slides, in order, one entry per slide
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

    Each entry is a path beside the manifest. A Markdown file holds one slide or several, separated by lines
    that are exactly `---`. Images are slides of their own. A PDF page is written `deck.pdf#page=3`, and a slide
    of an HTML deck with whatever fragment that deck uses, such as `deck.html#/3`.
    """
    if manifest is None or not manifest.is_file():
        return {}
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    deck = str(data.get("deck", ""))
    out = {}
    for step, entries in data.get("slides", {}).items():
        if isinstance(entries, list):
            out[step] = [slide for entry in entries for slide in expand_entry(entry, manifest.parent, deck)]
    for step, doc in data.get("docs", {}).items():
        if isinstance(doc, str) and doc.strip():
            out[step] = [doc.strip().partition("#")[0] + "#doc"]
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
        os.write(self.master, text.encode("utf-8"))

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
) -> Starlette:  # The web application
    "Build the web application: the two pages, the read-only repository API, the terminals, and the event hub."
    terminals: dict[str, Terminal] = {}

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
    showing: dict = {"slide": 0, "layout": "split", "path": None, "view": "file", "track": "replay"}

    def slides_now() -> list[str]:  # The slides of the step the working copy is at
        "Read the manifest afresh, so slides can be edited while presenting."
        index = repo.current()
        return load_slides(slides_path).get(repo.steps[index].name, []) if index is not None else []
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

    @guarded
    async def state(request: Request) -> dict:
        "Where the working copy is, the list of steps, the slides of this step, and the clock."
        deck = slides_now()
        showing["slide"] = min(showing["slide"], max(len(deck) - 1, 0))
        return {**repo.state(), "clock": clock["started"], "now": time.time(), "slides": deck, "slide": showing["slide"],
                "has_slides": bool(load_slides(slides_path)), "has_notes": notes_path is not None, "show_clock": show_clock, "layout": showing["layout"], "path": showing["path"],
                "view": showing["view"], "track": showing["track"]}

    @guarded
    async def tree(request: Request) -> dict:
        "The files at the current step."
        return repo.tree()

    @guarded
    async def file(request: Request) -> dict:
        "One file's text, what the current step did to it, and what has been edited since."
        path = request.query_params["path"]
        found = repo.read(path)
        return {**found, "diff": "" if found.get("missing") else repo.diff(path), "edits": repo.edit_diff(path)}

    @guarded
    async def move(request: Request) -> dict:
        "Move to another step and tell every page."
        body = await request.json()
        repo.move(int(body["to"]), set_aside=bool(body.get("set_aside")))
        showing["slide"] = 0
        await hub.tell({"type": "moved"})
        return repo.state()

    @guarded
    async def slide(request: Request) -> dict:
        "Show another of this step's slides and tell every page."
        body = await request.json()
        deck = slides_now()
        showing["slide"] = min(max(int(body["to"]), 0), max(len(deck) - 1, 0))
        await hub.tell({"type": "slide"})
        return {"slide": showing["slide"], "slides": deck}

    @guarded
    async def just_recipes(request: Request) -> dict:
        "The `just` recipes at the current step, for each track."
        return {"replay": recipes(repo.work), "main": recipes(repo.main)}

    @guarded
    async def notes(request: Request) -> dict:
        "The notes, read afresh, so the file can be edited while the class runs."
        if notes_path is None or not notes_path.is_file():
            return {"notes": {}, "path": str(notes_path) if notes_path else None}
        return {"notes": parse_notes(notes_path.read_text(encoding="utf-8")), "path": str(notes_path)}

    @guarded
    async def save_notes(request: Request) -> dict:
        "Write one step's section of the notes file, if nobody changed that section since the page read it."
        if notes_path is None:
            raise GitError("timewalk was started without --notes, so there is no notes file to save to")
        body = await request.json()
        step, text, base = str(body["step"]), str(body["text"]), str(body.get("base", ""))
        current = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""
        now = parse_notes(current).get(step, {}).get("raw", "")
        if now.strip() != base.strip():
            raise GitError(f"the notes for {step} changed in {notes_path.name} since this page read them. Copy your text, reload, and edit again")
        notes_path.write_text(replace_section(current, step, text), encoding="utf-8")
        await hub.tell({"type": "notes"})
        return {"ok": True}

    async def pdf(request: Request) -> Response:
        "Make a PDF of the slides and the notes, as the handout would be, and send it to download."
        if not allowed(request):
            return JSONResponse({"error": "missing or wrong token"}, status_code=403)
        if slides_path is None and notes_path is None:
            return JSONResponse({"error": "there are no slides and no notes to put in a PDF"}, status_code=404)
        from timewalk import slides_pdf  # imported here: slides_pdf imports this module

        try:
            data = await asyncio.to_thread(slides_pdf.make_pdf, slides_path, notes_path, repo.main.name, True)
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
        "Change what both pages show: the layout, the open file and its view, the terminal tab in front."
        body = await request.json()
        if body.get("layout") in ("slides", "split", "code"):
            showing["layout"] = body["layout"]
        if body.get("path"):
            showing["path"], showing["view"] = str(body["path"]), body.get("view") or "file"
            if showing["layout"] == "slides":
                showing["layout"] = "split"  # a file asked for must be seen
        if body.get("track") and terminal_for(str(body["track"])) is not None:
            showing["track"] = str(body["track"])
        await hub.tell({"type": "show", **{k: body[k] for k in ("path", "view", "track", "layout", "from", "focus") if k in body},
                        "layout": showing["layout"]})
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
        await hub.tell({"type": "show", "track": track, "from": body.get("from", ""), "focus": "sender", "layout": showing["layout"]})
        return {"ok": True}

    @guarded
    async def set_clock(request: Request) -> dict:
        "Start or reset the session clock."
        body = await request.json()
        clock["started"] = time.time() if body.get("action") == "start" else None
        await hub.tell({"type": "clock"})
        return {"clock": clock["started"], "now": time.time()}

    async def terminal(socket: WebSocket) -> None:
        "Connect a page to one of the shells."
        term = terminal_for(socket.path_params["name"]) if allowed(socket) else None
        if term is None:
            await socket.close(code=4403)
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
                    term.resize(int(message["rows"]), int(message["cols"]))
        except WebSocketDisconnect:
            pass
        finally:
            term.clients.discard(socket)

    async def events(socket: WebSocket) -> None:
        "Keep a window informed of moves, and of what another window asked to show."
        if not allowed(socket):
            await socket.close(code=4403)
            return
        await socket.accept()
        hub.pages.add(socket)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.pages.discard(socket)

    mounts = [Mount("/static", behind_token(StaticFiles(directory=HERE / "static")))]
    if slides_path is not None:
        mounts.append(Mount("/slides", behind_token(StaticFiles(directory=slides_path.parent))))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        "Watch the working copy for edits while the server runs."
        watcher = asyncio.create_task(watch_edits(repo, hub, watch_every))
        try:
            yield
        finally:
            watcher.cancel()

    return Starlette(lifespan=lifespan, routes=[
        Route("/", page("index.html")),
        Route("/presenter", presenter),
        Route("/api/state", state),
        Route("/api/tree", tree),
        Route("/api/file", file),
        Route("/api/move", move, methods=["POST"]),
        Route("/api/slide", slide, methods=["POST"]),
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

    try:
        repo = Repo(args.repo.resolve(), tags=args.tags, commits=args.commits, in_place=args.in_place, discard=args.discard_edits,
                    replay=args.replay)
    except GitError as exc:
        raise SystemExit(f"timewalk: {exc}") from None
    notes_path = args.notes.resolve() if args.notes else None
    slides_path = args.slides.resolve() if args.slides else None
    if notes_path is not None and notes_inside(repo, notes_path):
        raise SystemExit(f"timewalk: the notes file {notes_path} is inside the repository or its replay copy. Keep the notes "
                         "outside it: a move would change them, or throw your edits away.")
    local = args.host in ("127.0.0.1", "localhost", "::1")
    with socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET) as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError as exc:
            raise SystemExit(f"timewalk: cannot listen on {args.host} port {args.port} ({exc.strerror}). If another timewalk "
                             "uses the port, stop it, or pass --port with another number.") from None
    token = secrets.token_urlsafe(16)
    shown = "127.0.0.1" if local else (socket.gethostname() if args.host in ("0.0.0.0", "::") else args.host)
    address = f"http://{shown}:{args.port}/?t={token}"
    print(f"timewalk: {len(repo.steps)} steps in {repo.main}")
    print(f"timewalk: stepping in {repo.work}")
    if not local:
        print(f"timewalk: WARNING: listening on {args.host}, beyond this machine. Anyone who can reach port {args.port} and has the")
        print("timewalk: address below can run commands as you. The connection is not encrypted. An SSH tunnel is safer.")
    print(f"timewalk: open       {address}", flush=True)
    if not args.no_open and local:
        webbrowser.open(address)
    uvicorn.run(make_app(repo, token, args.port, notes_path, args.assistant, slides_path, show_clock=args.clock, any_host=not local),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
