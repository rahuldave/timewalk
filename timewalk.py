# /// script
# requires-python = ">=3.11"
# dependencies = ["starlette>=0.40", "uvicorn>=0.30", "websockets>=13"]
# ///
"""timewalk: browse a repository one commit at a time, with a terminal that runs in it.

    uv run timewalk.py /path/to/repo                    steps are the tags matching step-*
    uv run timewalk.py /path/to/repo --notes notes.md   add private notes for a presenter view
    uv run timewalk.py /path/to/repo --slides slides.toml   add slides, one or more per step
    uv run timewalk.py /path/to/repo --tags 'v*'        steps are other tags
    uv run timewalk.py /path/to/repo --commits          steps are the commits on the current branch
    uv run timewalk.py /path/to/repo --in-place         step the repository itself, not a second copy

Two pages are served. The audience page, for the projector, shows slides for the step, the files as they are at that step, which of
them that step added or changed, and terminal tabs built on Ghostty's terminal core: one in the repository at
that step, a second one there for commands that take a while, one in the repository you started from, one that
starts an assistant (Claude Code by default) at that step so you can ask it what the code is at this commit, and
as many extra shells as you open. Files can be read,
not edited. The presenter page, for your own screen, shows your notes for the
step, a clock against the planned times, the next step, and controls: moving, opening a file, or sending a
command from there changes what the audience page shows.

By default the stepping happens in a second working copy, `<repo>-replay`, made with `git worktree`, so the
repository you point at is never moved. Moving never deletes an untracked file, so whatever a command wrote
there (a virtual environment, a database, a run's output) stays where it is.

The server listens on localhost only and every request needs the token in the address it prints, because a
terminal in a web page is a way to run commands on this machine.
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
import struct
import subprocess
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
from starlette.responses import FileResponse, JSONResponse, Response
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
    ):
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
        "Find or make a second working copy beside the repository, starting at the first step."
        path = self.main.parent / f"{self.main.name}-replay"
        known = [line.split(" ", 1)[1] for line in git(self.main, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
        if str(path) in known or str(path.resolve()) in known:
            return path
        if path.exists():
            raise GitError(f"{path} exists and is not a working copy of this repository. Remove it or use --in-place.")
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
        "Move the working copy to a step. Edits are stashed, never discarded; untracked files are left alone."
        if not 0 <= index < len(self.steps):
            raise GitError(f"there is no step {index}")
        target = self.steps[index]
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
                "steps": [asdict(s) for s in self.steps], "current": self.current(), "edits": self.edits()}


def recipes(
    directory: Path,  # Where to look for a justfile
) -> list[dict]:  # Public recipes, in the order the justfile lists them
    "List the `just` recipes available in a directory, or nothing when there is no justfile or no `just`."
    if not shutil.which("just"):
        return []
    done = subprocess.run(["just", "--dump", "--dump-format", "json"], cwd=directory, capture_output=True, text=True)
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
    text: str,  # The presenter's notes file
) -> dict[str, dict]:  # Step name to its notes: planned start, commands, and the prose
    """Read presenter notes. They are private: only the presenter page asks for them.

    The file is Markdown. `## step-name` starts the notes for a step. Inside a step:

        time: 0:14            the planned start, as minutes:seconds into the session
        $ just test           a command for the terminal at this step; one click sends it
        runs$ just train      a command for the Runs tab: a second shell at this step, for commands that take a
                              while, so the first shell stays free
        runs2$ just sweep     the same in a further Runs tab (runs2 to runs9), for a second long command while
                              the first is still going
        main$ git log         a command for the terminal in the repository you started from

    Every other line is prose, shown as written.
    """
    notes: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(\S+)", line)
        if heading:
            current = notes.setdefault(heading.group(1), {"time": None, "commands": [], "text": []})
            title = line[heading.end():].strip()
            if title:
                current["title"] = title
            continue
        if current is None:
            continue
        planned = re.match(r"^time:\s*(\d+):(\d\d)\s*$", line.strip())
        command = re.match(r"^\s*(main|runs[2-9]?)?\$\s+(.+)$", line)
        if planned:
            current["time"] = int(planned.group(1)) * 60 + int(planned.group(2))
        elif command:
            current["commands"].append({"track": command.group(1) or "replay", "text": command.group(2).strip()})
        else:
            current["text"].append(line)
    for entry in notes.values():
        entry["text"] = "\n".join(entry["text"]).strip()
    return notes


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
    if not path.lower().endswith((".md", ".markdown")):
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
    return out


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
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TIMEWALK": "1"}
        for inherited in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_RUN_RECURSION_DEPTH"):
            env.pop(inherited, None)  # the shell belongs to the browsed repository, not to this tool's environment
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
    "Tells every open page when something changed, so the audience page follows the presenter."

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
    notes_path: Path | None = None,  # The presenter's notes file, if there is one
    assistant: str = "claude",  # Command started in the assistant tab; empty for a plain shell
    slides_path: Path | None = None,  # The slides manifest, if there is one
    watch_every: float = 1.0,  # Seconds between looks for edits in the working copy
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
    showing = {"slide": 0}  # which of the current step's slides is on screen

    def slides_now() -> list[str]:  # The slides of the step the working copy is at
        "Read the manifest afresh, so slides can be edited while presenting."
        index = repo.current()
        return load_slides(slides_path).get(repo.steps[index].name, []) if index is not None else []
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def allowed(
        request: Request | WebSocket,  # Incoming request or socket
    ) -> bool:  # Whether it carries the token and came to localhost
        "Refuse anything that does not carry the token or was not addressed to this machine."
        given = request.query_params.get("t") or request.headers.get("x-timewalk-token", "") or request.cookies.get("timewalk", "")
        return secrets.compare_digest(given, token) and request.headers.get("host", "") in hosts

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
            await files(scope, receive, send)
        return serve

    @guarded
    async def state(request: Request) -> dict:
        "Where the working copy is, the list of steps, the slides of this step, and the clock."
        deck = slides_now()
        showing["slide"] = min(showing["slide"], max(len(deck) - 1, 0))
        return {**repo.state(), "clock": clock["started"], "now": time.time(), "slides": deck, "slide": showing["slide"],
                "has_slides": bool(load_slides(slides_path))}

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
        "The presenter's notes, read afresh so they can be edited while presenting."
        if notes_path is None or not notes_path.is_file():
            return {"notes": {}, "path": str(notes_path) if notes_path else None}
        return {"notes": parse_notes(notes_path.read_text(encoding="utf-8")), "path": str(notes_path)}

    @guarded
    async def show(request: Request) -> dict:
        "Ask the audience page to open a file or switch terminal."
        body = await request.json()
        await hub.tell({"type": "show", **{k: body[k] for k in ("path", "view", "track", "layout") if k in body}})
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
        await hub.tell({"type": "show", "track": track})
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
        "Keep a page informed of moves and of what the presenter asked to show."
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
        Route("/presenter", page("presenter.html")),
        Route("/api/state", state),
        Route("/api/tree", tree),
        Route("/api/file", file),
        Route("/api/move", move, methods=["POST"]),
        Route("/api/slide", slide, methods=["POST"]),
        Route("/api/recipes", just_recipes),
        Route("/api/notes", notes),
        Route("/api/show", show, methods=["POST"]),
        Route("/api/type", type_command, methods=["POST"]),
        Route("/api/clock", set_clock, methods=["POST"]),
        WebSocketRoute("/ws/term/{name}", terminal),
        WebSocketRoute("/ws/events", events),
        *mounts,
    ])


def main() -> None:
    "Parse the command line, start the server and open the audience page."
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", type=Path, nargs="?", default=Path.cwd(), help="the repository to browse (default: here)")
    parser.add_argument("--notes", type=Path, help="a Markdown file of private presenter notes, one `## step-name` section per step")
    parser.add_argument("--slides", type=Path, help="a TOML manifest of slides: [slides] step-name = [\"file.md\", \"deck.pdf#page=2\"]")
    parser.add_argument("--tags", default="step-*", help="glob for the tags that mark steps (default: step-*)")
    parser.add_argument("--commits", action="store_true", help="step through the commits of the current branch instead of tags")
    parser.add_argument("--in-place", action="store_true", help="move the repository itself instead of a second working copy")
    parser.add_argument("--assistant", default="claude", help="command started in the assistant terminal tab (default: claude)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args()

    try:
        repo = Repo(args.repo.resolve(), tags=args.tags, commits=args.commits, in_place=args.in_place)
    except GitError as exc:
        raise SystemExit(f"timewalk: {exc}") from None
    token = secrets.token_urlsafe(16)
    base = f"http://127.0.0.1:{args.port}"
    print(f"timewalk: {len(repo.steps)} steps in {repo.main}")
    print(f"timewalk: stepping in {repo.work}")
    print(f"timewalk: audience   {base}/?t={token}")
    print(f"timewalk: presenter  {base}/presenter?t={token}", flush=True)
    if not args.no_open:
        webbrowser.open(f"{base}/?t={token}")
    notes_path = args.notes.resolve() if args.notes else None
    slides_path = args.slides.resolve() if args.slides else None
    uvicorn.run(make_app(repo, token, args.port, notes_path, args.assistant, slides_path), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
