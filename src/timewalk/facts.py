"""The facts of a walk, in one JSON file, for a class to render its own pages from: `timewalk-export --json FILE`.

The file holds what the notes and the history say, and nothing of timewalk's own words or layout: the walk, its intro,
its stops in order, and for each stop its text before its first move, its moves, and each move's text and files. A
text is a list of parts, in the order of the notes, each for both modes or for one. The layout is `facts.schema.json`,
beside this module, and `docs/reference.md` explains it. The same notes and history give the same bytes.
"""

import fnmatch
import importlib.metadata
import json
import os
import re
import shlex
import tomllib
from pathlib import Path

from timewalk import Move, Step, git, parse_notes, pick_steps, select_steps, step_moves, tag_steps, walk_notes
from timewalk.observe import Scratch, forget, observe, running, sources
from timewalk.walks import Walk, check_walk, unquote_path

FORMAT = 4   # the version of the layout; a change that a renderer must know of raises it
EXACT = ("pyproject.toml", "uv.lock")   # the files that a uv command writes with the newest versions of the day, unless loose names them
# The commands of uv whose job is to write pyproject.toml and uv.lock: do mode of a strict walk takes their files in their
# place. Every other command of uv runs a program (run, tool, format, check, build), or reads or exports (tree, export,
# audit, pip, venv, python, workspace, cache, self, auth, publish, help): the learner runs it, and a take follows it.
UV_LOCKS = ("add", "remove", "lock", "init", "version", "sync")
UV_VALUES = ("--directory", "--project", "--config-file", "--cache-dir", "--color", "--allow-insecure-host", "--python-preference",
             "--python-fetch")   # uv's own options that take a value
FENCE = re.compile(r"^\s*(```+|~~~+)")
# Every diff reads the same on every machine, whatever the user's git config says, and a path is never a pattern
DIFF = ["--literal-pathspecs", "-c", "core.quotePath=false", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "-c", "diff.relative=false",
        "-c", "diff.context=3", "-c", "diff.interHunkContext=0", "-c", "diff.algorithm=myers", "-c", "diff.indentHeuristic=true",
        "-c", "diff.renames=true", "-c", "diff.suppressBlankEmpty=false", "-c", "color.diff=false"]
DIFF_OPTIONS = ["--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"]
STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added", "T": "modified"}


def empty_tree(
    main: Path,  # The repository
) -> str:  # The id of the empty tree, the base of a first commit
    "Find the empty tree, so that a first commit's change is a diff like any other."
    return git(main, "hash-object", "-t", "tree", "--stdin", input_text="")


def changed_names(
    main: Path,  # The repository
    base: str,  # The commit before
    sha: str,  # The commit after
) -> dict[str, dict[str, list[str]]]:  # Each file, by its path after, to the names it adds, removes and changes
    """Name what a change does to code, from its diff alone: in Python the functions and classes it adds, removes or
    changes, and in a justfile the recipes it adds. No guessing: only names."""
    names: dict[str, dict[str, set[str]]] = {}
    path, before, context, defines = None, None, None, False   # the hunk's enclosing function, and whether the hunk defines one itself

    def close_hunk() -> None:
        # git names the function above a hunk, even when the hunk lies after it: only a hunk that defines nothing changes it.
        if path is not None and context and not defines:
            names[path]["~"].add(context)

    for line in git(main, *DIFF, "diff", "-U0", "-M", *DIFF_OPTIONS, base, sha).splitlines():
        if line.startswith("--- "):
            close_hunk()
            name = unquote_path(line[4:].rstrip("\t"))   # git ends a name with a space by a tab, and quotes an odd one
            before, path = (name[2:] if name.startswith("a/") else None), None
            continue
        if line.startswith("+++ "):
            name = unquote_path(line[4:].rstrip("\t"))
            path, context, defines = (name[2:] if name.startswith("b/") else before), None, False   # a deleted file keeps its old path
            continue
        if path is None:
            continue
        found = names.setdefault(path, {"+": set(), "-": set(), "~": set()})
        if line.startswith("@@"):
            close_hunk()
            hunk = re.match(r"^@@ [^@]* @@\s*(?:async\s+)?(?:def|class)\s+(\w+)", line)
            context, defines = (hunk.group(1) if hunk else None), False
            continue
        if line[:1] in "+-" and path.endswith(".py"):
            named = re.match(r"^[+-]\s*(?:async\s+)?(def|class)\s+(\w+)", line)
            if named:
                defines = True
                found[line[0]].add(("class " if named.group(1) == "class" else "") + named.group(2))
        elif line[:1] == "+" and path.rsplit("/", 1)[-1].lower() == "justfile":
            recipe = re.match(r"^\+([A-Za-z][\w-]*)(?:\s+[^:=]*)?:(?!=)", line)
            if recipe:
                found["+"].add(f"recipe {recipe.group(1)}")
    close_hunk()
    out = {}
    for name, found in names.items():
        adds, removes = found["+"] - found["-"], found["-"] - found["+"]
        # A name on both sides changed its own line, for example a signature; a hunk inside a function changed its body.
        touched = {n.removeprefix("class ") for n in found["+"] & found["-"]} | found["~"]
        bare = {n.removeprefix("class ") for n in adds | removes}
        out[name] = {"added": sorted(adds), "removed": sorted(removes), "changed": sorted(touched - bare)}
    return out


def change_files(
    main: Path,  # The repository
    base: str | None,  # The commit before, or None for a first commit
    sha: str,  # The commit after
) -> list[dict]:  # Each file of the change, in the order of its path: see the schema's "file"
    "List every file that a change touches, with renames found, its counts, its whole diff, and the names it changes."
    base = base or empty_tree(main)
    fields = git(main, *DIFF, "diff", "-z", "-M", "--name-status", base, sha).split("\x00")
    files, at = [], 0
    while at < len(fields) and fields[at]:
        status = fields[at]
        if status[0] in "RC":
            old, path, at = fields[at + 1], fields[at + 2], at + 3
        else:
            old, path, at = None, fields[at + 1], at + 2
        files.append({"path": path, "old_path": old if status[0] == "R" else None, "status": STATUS.get(status[0], "modified")})
    counts: dict[str, tuple[str, str]] = {}
    fields = git(main, *DIFF, "diff", "-z", "-M", "--numstat", base, sha).split("\x00")
    at = 0
    while at < len(fields):
        if fields[at].count("\t") < 2:
            at += 1
            continue
        added, removed, path = fields[at].split("\t", 2)
        if not path:   # a rename: the two paths follow
            path, at = fields[at + 2], at + 3
        else:
            at += 1
        counts[path] = (added, removed)
    names = changed_names(main, base, sha)
    for found in files:
        added, removed = counts.get(found["path"], ("-", "-"))
        binary = added == "-"
        paths = [p for p in (found["old_path"], found["path"]) if p]
        text = "" if binary else git(main, *DIFF, "diff", "-M", *DIFF_OPTIONS, base, sha, "--", *paths)
        lines = text.split("\n")
        hunk = next((i for i, line in enumerate(lines) if line.startswith("@@")), None)
        found.update({"added": 0 if binary else int(added), "removed": 0 if binary else int(removed), "binary": binary,
                      "diff": "\n".join(lines[hunk:]) + "\n" if hunk is not None else "",
                      "names": names.get(found["path"], {"added": [], "removed": [], "changed": []})})
    return sorted(files, key=lambda f: f["path"])


def chunks_of(
    text: str,  # Markdown
) -> list[tuple[str, bool]]:  # Its paragraphs and fenced code blocks, each whole, with "" for each blank line between; True for code
    "Cut Markdown into paragraphs and code blocks, so that a sentence is never looked for inside code."
    out: list[tuple[str, bool]] = []
    now: list[str] = []
    fence = None
    for line in text.split("\n"):
        mark = FENCE.match(line)
        if fence is not None:
            now.append(line)
            close = re.match(r"^ {0,3}(`{3,}|~{3,})\s*$", line)   # as CommonMark closes a fence: as long, nothing after
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
                out.append(("\n".join(now), True))
                now, fence = [], None
            continue
        if mark or not line.strip():
            if now:
                out.append(("\n".join(now), False))
            now = []
            if mark:
                now, fence = [line], mark.group(1)
            else:
                out.append((line, False))   # a blank line, as written, spaces too
            continue
        now.append(line)
    if now:
        out.append(("\n".join(now), fence is not None))
    return out


def parts_of(
    parsed: list[dict],  # Parts as parse_notes reads them, without move headings
) -> list[dict]:  # The parts of the facts: prose, command, patch and item, each with its mode
    "Turn the notes' parts into the facts' parts, in the order of the notes. Prose goes as written, every character of it."
    out: list[dict] = []
    for part in parsed:
        mode = part.get("mode") or "shared"
        if part["kind"] == "text":
            out.append({"kind": "prose", "mode": mode, "text": part["text"]})
        elif part["kind"] == "patch":
            out.append({"kind": "patch", "mode": mode, "path": part["path"]})
        elif part["kind"] == "command":
            out.append({"kind": "command", "mode": mode, "text": part["text"], "track": "" if part["track"] == "replay" else part["track"],
                        "makes": bool(part.get("makes")), "writes": None, "take": None})
        elif part["kind"] == "files":
            for item in part["items"]:
                out.append({"kind": "item", "mode": item.get("mode") or mode, "item": item["kind"], "path": item["path"], "move": item["move"],
                            "words": re.sub(r"\s*<!--.*?(-->|$)", "", item["text"], flags=re.DOTALL).strip(), "show": list(item["show"])})
    return out


def tag_of(
    step: Step,  # A whole step, at its tag
    keep=lambda words: words,  # What to do to its text: leave out the presenter's lines, unless --private keeps them
) -> dict:  # The tag's name, title and text
    "Read a step's tag: its first line is the title, without the step's name before it, and the rest is its text."
    message = (step.note or step.subject).strip()
    first, _, rest = message.partition("\n")
    title = first.split(":", 1)[1].strip() if first.startswith(f"{step.name}:") else first.strip()
    return {"name": step.name, "title": title, "text": keep(rest.strip()).strip()}


def move_title(
    move: Move,  # A move
    written: str,  # The title on its ### heading, or ""
) -> str:  # Its title
    "Name a move: the title on its heading, else its commit's subject without the move's or the step's name."
    if written:
        return written
    step = move.name.rsplit(".", 1)[0]
    if move.subject.startswith((f"{move.name}:", f"{step}:")):
        return move.subject.split(":", 1)[1].strip()
    return move.subject


def intro_parts(
    text: str,  # Markdown before the first stop
) -> list[dict]:  # Its parts, with the title line of the notes left out
    "Read a walk's intro as parts, as a step's text is read: its commands and blocks keep their kinds."
    lines = text.split("\n")
    if lines and re.match(r"^#\s+\S", lines[0]):
        lines = lines[1:]
    found = parse_notes("## intro\n" + "\n".join(lines)).get("intro", {})
    return parts_of(found.get("parts", []))


def display_number(
    name: str,  # A stop or a move, such as step-08b, step-08b.1 or step-05.3 to step-05.6
    tags: str,  # The walk's glob of tag names, such as step-*
) -> str:  # The number a reader sees, such as 8b, 8b.1 or 5.3 to 5.6
    "Number a stop or a move as a reader counts it: its name without the tags' prefix, and without zeros in front."
    prefix = tags.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
    out = []
    for part in name.split(" to "):
        part = part.strip()
        rest = part[len(prefix):] if prefix and part.startswith(prefix) and len(part) > len(prefix) else part
        out.append(re.sub(r"^0+(?=\d)", "", rest))
    return " to ".join(out)


def with_sources(
    files: list[dict],  # The files of a move, as change_files lists them
    made: dict[str, tuple[int | str, str]] | None,  # Each file's source and patch, from observe.sources; None when not run
) -> list[dict]:  # The same files, each with its by and its patch
    "Say where each file comes from: the index of the do$ command that writes it, or a patch, and what is changed by hand."
    return [{**file, "by": made[file["path"]][0] if made else None, "patch": made[file["path"]][1] if made else None} for file in files]


def exact(
    path: str,  # A file of the project, from the top
    loose: list[str],  # The walk's loose globs
) -> bool:  # True for a pyproject.toml or a uv.lock that loose does not name, which do mode compares line by line
    "Say whether do mode compares a file that uv writes exactly, as it is in the move's commit."
    return path.rsplit("/", 1)[-1] in EXACT and not any(fnmatch.fnmatchcase(path, glob) for glob in loose)


def line_commands(
    text: str,  # A do$ line
) -> list[tuple[str, str]]:  # Each command of the line, in order: its first word, and for uv its command, such as add ("" for none)
    """Split a line of shell into its commands, at &&, ||, ;, | and brackets. A command's first word comes past any
    NAME=value. The command of uv is the first word after uv that is not an option, past the value of an option of uv that
    takes one, such as --directory sub."""
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = text.split()
    found: list[tuple[str, str]] = []
    start, after_uv, skip = True, False, False   # a command begins; the words after a uv, before its command; an option's value
    for word in words:
        if set(word) <= set("&|;()"):
            if after_uv:
                found.append(("uv", ""))
            start, after_uv, skip = True, False, False
        elif after_uv:
            if skip:
                skip = False
            elif word.startswith("-"):
                skip = word in UV_VALUES
            else:
                found.append(("uv", word))
                after_uv = False
        elif start and re.fullmatch(r"[A-Za-z_]\w*=.*", word, re.DOTALL):
            continue
        elif start:
            after_uv, start = word == "uv", False
            if not after_uv:
                found.append((word, ""))
    if after_uv:
        found.append(("uv", ""))
    return found


def uv_commands(
    text: str,  # A do$ line
) -> list[str]:  # The command of uv that each uv of the line runs, such as add or run, in order; "" for none, as in uv --version
    "Find each uv that a line of shell runs, and its command."
    return [command for word, command in line_commands(text) if word == "uv"]


def runs_uv(
    text: str,  # A do$ line
) -> bool:  # True when a command of the line is uv, as in uv add x, or cd sub && uv add x
    "Say whether a line of shell runs uv."
    return bool(uv_commands(text))


def hidden_uv(
    text: str,  # A do$ line
) -> bool:  # True when the line names uv more times than it runs uv as a command, as in env A=1 uv add x or sudo uv add x
    """Say whether a line runs uv in a way that takes cannot read: behind another program (env, time, sudo, bash -c), or by
    a path such as ~/.local/bin/uv. A uv counts at the start of a word or after / = ( ` : or -, and before a space, `, ) or } or
    the end of the word; so echo uv, or cd uv && uv add x, counts too."""
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = text.split()
    named = sum(len(re.findall(r"(?:^|[\s/=(`:-])uv(?=$|[\s`)}])", word)) for word in words)
    return named > len(uv_commands(text))


def only_locks(
    text: str,  # A do$ line
) -> bool:  # True when the line runs uv, and each uv of it is a command whose job is to write pyproject.toml and uv.lock
    """Say whether do mode can leave a line out, and take its files in its place: each command of it is uv add, remove,
    lock, init, version or sync, or cd. A line with uv run, any other command of uv, or any other program, such as
    uv add pre-commit && pre-commit install, runs a program, so the learner runs it, and the take comes after it."""
    found = line_commands(text)
    return any(word == "uv" for word, _ in found) and all(command in UV_LOCKS if word == "uv" else word == "cd" for word, command in found)


def may_take(
    commands: list[str],  # A move's do$ lines, in order
    paths: list[str],  # The files that the move's commit adds or changes
    loose: list[str],  # The walk's loose globs
) -> bool:  # Whether a do$ line of the move can be one that takes, so that its commands are worth running to see
    "Say, without running anything, whether takes can find a uv command in a move: a do$ line of uv, and a file compared exactly."
    return any(runs_uv(text) for text in commands) and any(exact(path, loose) for path in paths)


def git_path(
    path: str,  # A file of the project, from the top
) -> str:  # The path for git restore, quoted for the shell
    "Quote a path for git. A path that git would read as a pattern or as magic gets :(top,literal), so it names one file."
    return shlex.quote(path if not path.startswith(":") and not re.search(r"[*?\[\\]", path) else ":(top,literal)" + path)


def take_command(
    files: list[dict],  # The files to take, as change_files lists them
    sha: str,  # The move's commit
) -> tuple[list[str], list[str], str]:  # The files kept, the files deleted (and the old paths of those renamed), and the command
    """Make the command that takes files from the move's commit, from the top of the project: git restore, rm -f for a file
    that the move deletes, and uv sync of each uv.lock, in every group and every member of a workspace."""
    kept = [f["path"] for f in files if f["status"] != "deleted"]
    gone = [f["path"] for f in files if f["status"] == "deleted"] + [f["old_path"] for f in files if f["status"] == "renamed" and f["old_path"]]
    command = f"git restore --source {sha[:12]} -- " + " ".join(git_path(path) for path in kept)
    if gone:
        command += " && rm -f -- " + " ".join(shlex.quote(path) for path in gone)
    for lock in [path for path in kept if path.rsplit("/", 1)[-1] == "uv.lock"]:
        folder = lock.rsplit("/", 1)[0]
        where = f" --directory {shlex.quote(folder if not folder.startswith('-') else './' + folder)}" if "/" in lock else ""   # never an option
        command += f" && uv sync{where} --locked --all-groups --all-packages"
    return kept, gone, command


def takes(
    makes: list[dict],  # A move's do$ lines, in order, each with its text and its writes (None when not run): a file's by counts in them
    files: list[dict],  # The move's files, each with its by, as with_sources gives them
    sha: str,  # The move's commit
    loose: list[str],  # The walk's loose globs
) -> dict[int, dict]:  # For each do$ line that do mode does not run, or runs and then takes after: its take; see the schema's take
    """Find the uv commands whose files do mode takes from the move's commit. On a later day, uv add, uv lock and the like
    write newer versions than the author's, and a strict walk compares pyproject.toml and uv.lock line by line. A do$
    line counts when it runs uv and writes a file of the move, a pyproject.toml or a uv.lock that loose does not name. A
    later do$ line that writes a file that such a line writes joins it: the commit has each file only as the move ends,
    so one command takes every file that the lines write.

    A line whose uv commands only write the files, such as uv add (only_locks), is left out, and so is any other line
    that joins: the command stands in place of the first line, and a joined line gets a take with no lines and no
    command. A line of uv that runs a program, such as uv run, stays: the learner runs it, and the command comes after it
    (after). The first line of the group gives the command, and each later line of uv run that writes a file compared
    exactly gives it again."""
    paths = {p for f in files for p in (f["path"], f["old_path"]) if p}

    def wrote(at: int) -> set[str]:
        writes = makes[at].get("writes")
        if writes is None:   # not run: the files whose last writer is the line
            return {p for f in files if f["by"] == at for p in (f["path"], f["old_path"]) if p}
        return set(writes) & paths

    def strict(found: set[str]) -> bool:   # a file that the move keeps and do mode compares line by line
        return any(exact(f["path"], loose) and f["status"] != "deleted" and f["path"] in found for f in files)

    out: dict[int, dict] = {}
    grouped: set[int] = set()
    for at, part in enumerate(makes):
        if at in grouped:
            continue
        mine = wrote(at)
        if not runs_uv(part["text"]) or not strict(mine):
            continue
        group, grew = [at], True
        while grew:   # until no later line writes a file of the group
            grew = False
            for later in range(at + 1, len(makes)):
                if later not in group and later not in grouped and wrote(later) & mine:
                    group.append(later)
                    mine |= wrote(later)
                    grew = True
        group.sort()
        grouped |= set(group)
        kept, gone, command = take_command([f for f in files if f["path"] in mine or f["old_path"] in mine], sha)
        runs = {n for n in group if runs_uv(makes[n]["text"]) and not only_locks(makes[n]["text"])}   # the learner runs these
        out[at] = {"lines": [at, *(n for n in group[1:] if n not in runs)], "files": kept, "deletes": gone, "command": command, "after": at in runs}
        for later in group[1:]:
            if later not in runs:
                out[later] = {"lines": [], "files": [], "deletes": [], "command": "", "after": False}
            elif strict(wrote(later)):
                out[later] = {"lines": [later], "files": kept, "deletes": gone, "command": command, "after": True}
    return out


class TakeError(RuntimeError):
    "The do$ lines of a move did not run to the end, so the files that they write, and so the takes, are not known."


def move_takes(
    main: Path,  # The repository
    sha: str,  # A move's commit, or the commit of a step of one commit
    commands: list[str],  # Its do$ lines, in order
    loose: list[str],  # The walk's loose globs
    stop=None,  # An observe.Stop, which ends the run when the server stops
) -> dict[int, dict]:  # What takes finds, from a run of the commands at the commit before, or from the cache
    """Run a move's do$ lines, or read the cache, to see which files each writes, and find the takes. Raise TakeError when
    a line did not run, or a line of uv failed, as when uv has no network: the run is then forgotten, so a later call
    runs it again."""
    files = change_files(main, f"{sha}^", sha)
    if not may_take(commands, [f["path"] for f in files if f["status"] != "deleted"], loose):
        return {}
    parent = git(main, "rev-parse", f"{sha}^")
    scratch = Scratch(main)
    try:
        seen = observe(main, parent, sha, commands, scratch, stop)
    finally:
        scratch.close()
    ran = seen["commands"]
    problem = run_problem(ran, commands)
    if problem:
        forget(parent, sha, commands)
        raise TakeError(problem)
    makes = [{"text": text, "writes": ran[at]["writes"] if at < len(ran) else None} for at, text in enumerate(commands)]
    return takes(makes, with_sources(files, sources(main, sha, files, seen, loose, DIFF)[0]), sha, loose)


def run_problem(
    ran: list[dict],  # The commands that ran, from observe
    commands: list[str],  # The do$ lines, in order
) -> str:  # Why the files that the lines write are not known, in a sentence; "" when they are
    """Say why a run does not give the takes: a line that did not run, a line of uv add or the like that failed, or a line
    that failed and wrote nothing. The last line may fail after it writes, as uv run ruff check --fix does, with an exit
    from 1 to 125, as timewalk-check allows: not a timeout, a signal, or a command not found."""
    for at, command in enumerate(ran):
        excused = 0 < command["exit"] < 126 and at == len(commands) - 1 and command["writes"] and not only_locks(command["text"])
        if command["exit"] and not excused:
            code = f"exit {command['exit']}" if command["exit"] > 0 else "it was stopped" if command["exit"] == -9 else "it ran too long"
            said = f": {command['said'].rstrip('.')}" if command["said"] else ""
            return f"the do$ line {command['text']} failed ({code}){said}"
    if len(ran) < len(commands):
        return f"the do$ line {commands[len(ran)]} did not run"
    return ""


def change_takes(
    main: Path,  # The repository
    walk: Walk,  # A walk
    steps: list[Step],  # Its stops
    moves: list[list[Move]],  # The moves of each
    text: str,  # The notes the walk reads; a walk with notes of its own reads them through own
    own: list[str | None] | None,  # A walk with notes of its own: each stop's section, with its includes
    run: bool = True,  # Whether to run the do$ lines; without, no takes, and each change that would run is named
) -> tuple[dict[str, list[tuple[str, dict | None]]], list[str]]:  # Each change's do$ lines with their takes; and why some cannot be known
    """Find the takes of every move of a tutorial, and of every step of one commit, as the facts give them, for the do copies
    of timewalk-export. Only a change with a do$ line of uv and a file compared exactly runs, and the cache serves a run
    that a check made."""
    if walk.kind != "tutorial":
        return {}, []
    notes = walk_notes(parse_notes(text), steps, moves, True, bool(walk.select), own)
    out: dict[str, list[tuple[str, dict | None]]] = {}
    problems: list[str] = []
    for at, (step, found) in enumerate(zip(steps, moves, strict=True)):
        parts = (notes.get(step.name) or {}).get("parts", [])
        heads = [n for n, part in enumerate(parts) if part["kind"] == "move"]
        changes = [(parts[n]["name"], next((m.sha for m in found if m.name == parts[n]["name"]), None), parts[n + 1:end])
                   for n, end in zip(heads, [*heads[1:], len(parts)], strict=False)]
        start = step.start or (steps[at - 1].sha if at else "")
        if not found and not heads and start and git(main, "rev-parse", f"{step.sha}^") == start:
            changes = [(step.name, step.sha, parts)]   # a step of one commit: its whole section
        for name, sha, mine in changes:
            commands = [part["text"] for part in mine if part["kind"] == "command" and part.get("makes")]
            if sha is None or not commands:
                continue
            if not run:
                files = [f["path"] for f in change_files(main, f"{sha}^", sha) if f["status"] != "deleted"]
                if may_take(commands, files, list(walk.loose)):
                    problems.append(name)
                continue
            try:
                taken = move_takes(main, sha, commands, list(walk.loose))
            except TakeError as error:
                problems.append(f"{name}: the do copy gives its uv commands as they are, and not the author's files, because {error}")
                continue
            out[name] = [(command, taken.get(n)) for n, command in enumerate(commands)]
    return out, problems


def relative(
    message: str,  # A message of timewalk-check
    main: Path,  # The repository
    root: Path,  # The class folder
) -> str:  # The same, without the paths of this machine
    "Write a message's paths from the class folder, and the project by its name, so the facts are the same on any machine."
    for folder, name in ((root, ""), (main, main.name), (Path.home(), "~")):
        message = message.replace(f"{folder}{os.sep}", f"{name}/" if name else "").replace(str(folder), name or ".")
    return message


def walk_facts(
    walk: Walk,  # The walk
    main: Path,  # The repository
    root: Path,  # The class folder, which paths are written from
    url: str,  # The project on GitHub
    title: str,  # The walk's title
    text: str,  # The notes the walk reads, the shared notes of a walk with notes of its own, without the private lines unless kept
    intro: str,  # The intro, as text before the first stop
    own: list[str | None] | None,  # A walk with notes of its own: each stop's section, with its includes, as text to read
    folders: list[Path] | None = None,  # The walks' slides folders, for the checks
    keep=lambda words: words,  # What to do to a tag's text: leave out the presenter's lines, as the notes' are left out
    run: bool | None = None,  # Whether to run the do$ commands, to see the files they write; None: unless TIMEWALK_NO_RUN is set
) -> dict:  # The facts, in the layout of facts.schema.json
    "Gather the facts of one walk: the history, the notes read as the page reads them, the slides and the checks."
    run = running() if run is None else run
    scratch = Scratch(main)
    try:
        return gather(walk, main, root, url, title, text, intro, own, folders, keep, run, scratch)
    finally:
        scratch.close()


def gather(
    walk: Walk,  # The walk
    main: Path,  # The repository
    root: Path,  # The class folder
    url: str,  # The project on GitHub
    title: str,  # The walk's title
    text: str,  # The notes the walk reads
    intro: str,  # The intro
    own: list[str | None] | None,  # A walk with notes of its own: each stop's section
    folders: list[Path] | None,  # The walks' slides folders
    keep,  # What to do to a tag's text
    run: bool,  # Whether to run the do$ commands
    scratch: Scratch,  # The clone they run in
) -> dict:  # The facts; see walk_facts
    "Gather the facts of one walk, for walk_facts."
    tutorial = walk.kind == "tutorial"
    if walk.select:
        steps, moves = select_steps(main, walk.tags, walk.select, tutorial)
    else:
        steps = pick_steps(tag_steps(main, walk.tags), walk.steps)
        moves = step_moves(main, steps) if tutorial else [[] for _ in steps]
    notes = walk_notes(parse_notes(text), steps, moves, tutorial, bool(walk.select), own)
    stops = []
    for at, (step, found) in enumerate(zip(steps, moves, strict=True)):
        start = step.start or (steps[at - 1].sha if at else "")
        entry = notes.get(step.name) or {}
        parsed = entry.get("parts", [])
        cut = [i for i, part in enumerate(parsed) if part["kind"] == "move"]
        before = parsed[:cut[0]] if cut else parsed
        by_move = {parsed[i]["name"]: (parsed[i], parsed[i + 1:end]) for i, end in zip(cut, [*cut[1:], len(parsed)], strict=True)} if cut else {}
        walked = []
        for move in found:
            heading, mine = by_move.get(move.name, ({}, []))
            files = change_files(main, f"{move.sha}^", move.sha)
            parent = git(main, "rev-parse", f"{move.sha}^")
            parts = parts_of(mine)
            moved = made_by(main, parent, move.sha, parts, files, walk, run, scratch)
            walked.append({"name": move.name, "number": display_number(move.name, walk.tags), "title": move_title(move, heading.get("title", "")),
                           "commit": move.sha, "parent": parent, "parts": parts, "files": moved})
        whole = not step.section
        files = [] if found else change_files(main, start or None, step.sha)
        own_parts = parts_of(before)
        if tutorial and not found and not cut and start and git(main, "rev-parse", f"{step.sha}^") == start:   # a step of one commit is a change of its own
            files = made_by(main, start, step.sha, own_parts, files, walk, run, scratch)
        else:
            files = with_sources(files, {f["path"]: ("patch", f["diff"]) for f in files})
        section = own[at] if own is not None and at < len(own) else None
        stops.append({
            "name": step.name,
            "number": display_number(step.name, walk.tags),
            "title": step.title or entry.get("title", "") or (tag_of(step)["title"] if whole else ""),
            "tag": tag_of(step, keep) if whole else None,
            "commit": step.sha,
            "start": start or None,
            "own": {"title": step.title, "text": section.split("\n", 1)[1].strip("\n") + "\n"} if section else None,
            "parts": own_parts,
            "moves": walked,
            "files": files,
        })
    slides = None
    if walk.slides is not None and walk.slides.is_file():
        data = tomllib.loads(walk.slides.read_text(encoding="utf-8"))
        wanted = {s["name"] for s in stops} | {m["name"] for s in stops for m in s["moves"]} | {step.section for step in steps if step.section}
        slides = {"manifest": os.path.relpath(walk.slides, root).replace(os.sep, "/"), "deck": data.get("deck"),
                  "slides": {name: list(entries) for name, entries in data.get("slides", {}).items() if name in wanted},
                  "docs": {name: doc for name, doc in data.get("docs", {}).items() if name in wanted}}
    every = tag_steps(main, walk.tags)
    history = {**{step.name: step.sha for step in every}, **{move.name: move.sha for found in step_moves(main, every) for move in found}}
    errors, warnings, _ = check_walk(walk, main, root, main.parent / f"{main.name}-replay", folders, run)
    return {
        "format": FORMAT,
        "timewalk": importlib.metadata.version("timewalk"),
        "made": "",
        "observed": run,
        "walk": {"id": walk.id, "title": title, "description": walk.description, "kind": walk.kind, "mode": walk.mode if tutorial else "watch",
                 "github": url, "loose": list(walk.loose)},
        "intro": intro_parts(intro),
        "stops": stops,
        "slides": slides,
        "history": dict(sorted(history.items())),
        "errors": [relative(e, main, root) for e in errors],
        "warnings": [relative(w, main, root) for w in warnings],
    }


def made_by(
    main: Path,  # The repository
    parent: str,  # The commit before it
    sha: str,  # Its commit
    parts: list[dict],  # Its parts, from parts_of: each do$ line gets its writes and its take
    files: list[dict],  # Its files, from change_files
    walk: Walk,  # The walk
    run: bool,  # Whether to run the do$ lines
    scratch: Scratch,  # The clone they run in
) -> list[dict]:  # The files, each with its by and its patch, as with_sources gives them
    "Run a change's do$ lines, or read the cache: say what each line writes, where each file comes from, and the takes."
    makes = [part for part in parts if part["kind"] == "command" and part["makes"]]
    commands = [part["text"] for part in makes]
    seen = observe(main, parent, sha, commands, scratch) if commands and run else {"commands": [], "left": {}} if not commands else None
    made = sources(main, sha, files, seen, walk.loose, DIFF)[0] if seen is not None else None
    moved = with_sources(files, made)
    for at, part in enumerate(makes):
        ran = seen["commands"][at] if seen and at < len(seen["commands"]) else None   # a command after a failed one did not run
        part["writes"] = ran["writes"] if ran else None
    taken = takes(makes, moved, sha, list(walk.loose)) if made is not None else {}
    if seen and run_problem(seen["commands"], commands):   # as the page and the do copy: no take, and timewalk-check gives the error
        taken = {}
    for at, part in enumerate(makes):
        part["take"] = taken.get(at)
    return moved


def facts_text(
    facts: dict,  # The facts of a walk
    made: str,  # The line that says which command made the file
) -> str:  # The JSON file, the same bytes for the same facts
    "Write the facts as JSON, with the keys in the order they are made, and the line that marks the file as made by timewalk."
    return json.dumps({**facts, "made": made}, ensure_ascii=False, indent=1, default=str) + "\n"

