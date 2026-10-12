"""Write a tutorial for reading without timewalk: a Markdown file for each mode, and a website.

    timewalk-export REPO --toc toc.toml --walk tutorial --markdown DIR --site DIR

A tutorial's notes have one text for both modes, and blocks for one mode: `::: do` ... `:::` and `::: watch` ...
`:::`. Each copy keeps the text outside the blocks and the blocks of its mode. It leaves out the private lines, the
presenter's cues and the planned times. Commands become code blocks. Each step links to its code at the tag, each move
to its commit, and each `files:` item to its file at the commit; a `diff` item also links to its change. A sentence
that sends the learner to **Apply** becomes a link to the move's commit.

`--markdown DIR` writes TUTORIAL-do.md and TUTORIAL-watch.md, for GitHub. `--site DIR` writes one website with two
sections, Watch and Do, and a page for each step in each: `index.qmd`, `watch/step-NN.qmd`, `do/step-NN.qmd`, and
`_smalldocs.yml` (or `_quarto.yml` with `--quarto`). smalldocs and Quarto build it. The website renders only the pages
that this command writes, so the notes never become a page. Every file it writes says so in its first lines, and it
replaces only such files.
"""

import argparse
import functools
import hashlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from timewalk import GitError, Move, Step, code_span, git, note_blocks, pick_steps, select_steps, shown_line, step_moves, tag_steps
from timewalk.walks import TocError, Walk, in_a_copy, inside, load_toc

MADE = "Made by timewalk-export from the notes of the walk. Do not edit it by hand: run timewalk-export again."
OLD_MADE = "Made by timewalk-tutorial from the notes of the walk. Do not edit it by hand: run timewalk-tutorial again."   # its line before it was renamed
MODE_WORDS = {
    "do": "In do mode, you make each move yourself. The notes give every line to type, every command and every file.",
    "watch": "In watch mode, you read each move. The notes explain what it changed, and its commit holds the code.",
}
FILE_NAME = {"do": "TUTORIAL-do.md", "watch": "TUTORIAL-watch.md"}

# The presenter's private lines: the same rule as the walk skill's scripts/private.py, which decides what reaches the
# students. A cue "> Say:" or "> Note:", in any case, bold, indented, in a list or a task box, with the whole blockquote
# around it; a cue in an HTML comment; and a planned time. Fences are read as CommonMark reads them.
CUE = r"(?:>\s*)+[*_]{0,2}(?:say|note)\s*[*_]{0,2}\s*:"
LEAD = r"^\s*(?:(?:[-*+]|\d+[.)])\s+)*(?:\[[ xX]\]\s+)?"
PRIVATE = [
    re.compile(LEAD + CUE, re.IGNORECASE),
    re.compile(r"^\s*<!--\s*" + CUE, re.IGNORECASE),
    re.compile(r"^\s*time:\s*\d+:\d\d\s*$"),
]
OPEN = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")
CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*$")
QUOTED = re.compile(LEAD + ">")
LABEL = re.compile(LEAD + r"(?:>\s*)+[*_]{0,2}[A-Z][A-Za-z]*\s*[*_]{0,2}\s*:")
NEW_BLOCK = re.compile(r"^\s*(?:#{1,6}(?:\s|$)|```|~~~|[-*+]\s|\d+[.)]\s|(?:---|\*\*\*|___)\s*$|<|(?:main|runs[2-9]?|do)?\$\s|time:)|^:{3,}\s*(?:\{[^}]*\}|[\w-]+)?\s*$")
COMMAND = re.compile(r"^\s*(main|runs[2-9]?|do)?\$\s+(.+)$")
ITEM = re.compile(r"^(\s*)-\s+(diff|file)\s+(?:([\w.-]+)\s+)?`([^`]+)`\s*:?\s*(.*)$")
DIFF_LINES = 200   # the most lines of a change that a copy shows; the link to GitHub has the rest
APPLY = re.compile(r"\*\*Apply (?:this file|the whole move)\*\*")
# "Take it with **Apply this file** in timewalk, or from the move's commit on GitHub": the words from "with" go
WITH_APPLY = re.compile(r"\bwith\s+\*\*Apply (?:this file|the whole move)\*\*(\s+too)?,?(?:\s+in\s+timewalk)?"
                        r"(?:,?\s+or\s+(?:from\s+)?the\s+move's\s+commit(?:\s+on\s+GitHub)?)?")


class NotesError(ValueError):
    "Notes that the copies cannot be made from. timewalk-check reports the same."

    def __init__(
        self,
        line: int,  # The line of the problem, from 1
        problem: str,  # What is wrong there
    ) -> None:
        "Keep the line and the problem apart, for timewalk-check."
        super().__init__(f"line {line}: {problem}")
        self.line, self.problem = line, problem


@dataclass
class Section:
    "One stop of the walk, in one mode: a step, a small step of some moves, or a narrative's stop at a move."

    name: str  # The stop, such as `step-03`, `step-10.3` or `step-05.3 to step-05.6`
    title: str  # Its title, from its heading in the notes
    body: str  # Its text, under its heading, as Markdown
    moves: list[tuple[str, str]]  # Its moves: (the text of the move's heading, the move's name, which is its heading's id on the website)


@dataclass
class Source:
    "One step's section of the notes, as written: the text before its first move, and each move's section."

    title: str  # The title on its heading
    before: list[str]  # The lines before its first move
    moves: dict[str, tuple[str, list[str]]]  # Each move's section, by name: the title on its heading, and its lines


def fenced(
    lines: list[str],  # The lines of a Markdown file
) -> set[int]:  # The index of each line of a fenced code block, its two fence lines included
    "Find the lines of fenced code, as CommonMark reads them. A fence that is never closed runs to the end."
    found, fence = set(), None
    for at, line in enumerate(lines):
        if fence is None:
            mark = OPEN.match(line)
            if mark:
                fence = mark.group(1)
                found.add(at)
            continue
        found.add(at)
        close = CLOSE.match(line)
        if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
            fence = None
    return found


def private_lines(
    text: str,  # The notes
) -> set[int]:  # The index of each line to leave out: the private lines, and the whole blockquote of each cue
    """Find the lines for the presenter alone, outside fenced code. A blockquote with a cue for the presenter and a public
    cue, such as "> Try:", cannot go in part, and stops the command."""
    lines = text.splitlines()   # as note_blocks splits them, so the indexes of the two agree
    code = fenced(lines)
    found, at = set(), 0
    while at < len(lines):
        line = lines[at]
        if at in code or not (QUOTED.match(line) or PRIVATE[1].match(line)):
            if at not in code and PRIVATE[2].match(line):
                found.add(at)
            at += 1
            continue
        # A blockquote, or a comment with a cue: up to a blank line, code, a line that starts a block without a >, or past a -->
        block = [at]
        while "-->" not in lines[block[-1]]:
            after = block[-1] + 1
            if after >= len(lines) or after in code or not lines[after].strip() or (not QUOTED.match(lines[after]) and NEW_BLOCK.match(lines[after])):
                break
            block.append(after)
        cues = [i for i in block if PRIVATE[0].match(lines[i]) or PRIVATE[1].match(lines[i])]
        if cues:
            labels = [i for i in block if LABEL.match(lines[i]) and i not in cues]
            if labels:
                raise NotesError(labels[0] + 1, f"{lines[labels[0]].strip()!r} is a public cue in the blockquote of "
                                 f"the cue {lines[cues[0]].strip()!r}; put a blank line between the two")
            found.update(block)
        at = block[-1] + 1
    return found


def squeeze(
    text: str,  # Markdown
) -> str:  # The same, with one blank line where there were several, except in fenced code
    "Close up the blank lines that a dropped line leaves behind, and never touch fenced code."
    lines = text.split("\n")
    code = fenced(lines)
    return "\n".join(line for at, line in enumerate(lines) if at in code or line.strip() or at == 0 or lines[at - 1].strip() or at - 1 in code)


def for_mode(
    text: str,  # A tutorial's notes
    mode: str | None,  # "do" or "watch"; None for the text outside every block
) -> str:  # The notes as a reader of that mode reads them, without the private lines
    "Keep the text outside the blocks and the blocks of one mode, and leave out the presenter's lines."
    blocks = note_blocks(text)
    if blocks.errors:
        line, problem = blocks.errors[0]
        raise NotesError(line, problem)
    lines = [(at, line) for at, line in enumerate(text.splitlines()) if at not in blocks.fences and blocks.mode[at] in (None, mode)]
    return without_private(lines, private_lines(text))   # found in the notes as written, so a block's fence ends a cue's blockquote


def public_text(
    text: str,  # Markdown of the author's, such as the intro of a walk
) -> str:  # The same without the presenter's lines
    "Leave out the presenter's lines of a file that has no blocks for one mode."
    return without_private(list(enumerate(text.splitlines())), private_lines(text))


def cue_problems(
    text: str,  # Notes, or the intro of a walk
) -> list[tuple[int, str]]:  # What stops timewalk-export from leaving out the presenter's lines: (line from 1, problem), or nothing
    "Find a cue that timewalk-export cannot leave out alone, for timewalk-check."
    try:
        private_lines(text)
    except NotesError as error:
        return [(error.line, error.problem)]
    return []


def without_private(
    lines: list[tuple[int, str]],  # The lines to keep or drop, each with its index in the text
    drop: set[int] | list[int],  # The indexes of the presenter's lines
) -> str:  # The lines kept, with the end of a comment that a kept line opened
    "Drop the presenter's lines, keep the `-->` of a comment that a kept line opened, and close up the blank lines."
    drop = set(drop)
    kept, comment = [], False   # comment: a kept line opened an HTML comment that is not closed yet
    for at, line in lines:
        if at not in drop:
            kept.append(line)
            if "<!--" in line or "-->" in line:
                comment = line.rfind("<!--") > line.rfind("-->") or (comment and "-->" not in line)
        elif comment and "-->" in line:
            kept.append(re.match(r"\s*", line).group() + "-->")   # the end of a comment that a kept line opened
            comment = False
    return squeeze("\n".join(kept))


def github_url(
    main: Path,  # The project
    given: str | None,  # The address from --github, if any
) -> str:  # The project's address on GitHub, with no / at the end
    "Find the project's address on GitHub: --github, else its remote origin."
    if given:
        return given.rstrip("/").removesuffix(".git")
    try:
        origin = git(main, "remote", "get-url", "origin")
    except GitError:
        origin = ""
    found = re.match(r"^(?:(?:https?|ssh|git)://)?(?:[^@/]+@)?github\.com(?::\d+(?=/))?[:/](.+?)(?:\.git)?/?$", origin)
    if not found:
        raise SystemExit("timewalk-export: the project's origin is not on GitHub; give its address with --github https://github.com/OWNER/NAME")
    return f"https://github.com/{found.group(1)}"


def read_sections(
    text: str,  # The notes of one mode, from for_mode
) -> tuple[str, str, dict[str, Source]]:  # The title, the text before the first step, and each step's section by name
    "Read the notes into the sections of the steps, and of the moves in each. Fenced code holds no heading."
    lines = text.split("\n")
    code = fenced(lines)
    title, opening, found = "", [], {}
    current: Source | None = None
    name = ""   # the step whose section is being read
    lines_now: list[str] = opening
    for at, line in enumerate(lines):
        step = re.match(r"^##\s+(\S+)\s*(.*)$", line) if at not in code else None
        move = re.match(r"^###\s+(\S+)\s*(.*)$", line) if at not in code else None
        if current is None and not title and at not in code and re.match(r"^#\s+\S", line):
            title = line[1:].strip()
        elif step and (current is not None or re.match(r"^[\w-]*\d", step.group(1))):
            name = step.group(1)
            current = found.setdefault(name, Source(step.group(2).strip(), [], {}))
            lines_now = current.before
        elif current is not None and move and re.match(rf"^{re.escape(name)}\.\d+$", move.group(1)):
            current.moves[move.group(1)] = (move.group(2).strip(), [])
            lines_now = current.moves[move.group(1)][1]
        else:
            lines_now.append(line)
    return title, "\n".join(opening).strip(), found


def build(
    sources: dict[str, Source],  # The sections of the notes
    steps: list[Step],  # The stops of the walk
    moves: list[list[Move]],  # The moves of each stop
    url: str,  # The project on GitHub
    level: int,  # The depth of a move's heading: 3 in the Markdown file, 2 on a page of the website, with the move as its id
    warnings: set[str],  # Where to put what is missing, each said once
    history: dict[str, str] | None = None,  # The commit of every step and move of the whole history, which an item may name
    history_starts: dict[str, str] | None = None,  # The commit before each step of the whole history, for an item that names one
    diff: Callable[[str, str, str], str] | None = None,  # The change of a file from one commit to another; None shows no changes
    watch: bool = False,  # For a reader who watches: a do$ line is what the move ran; see render
    tutorial: bool = False,  # A tutorial: a whole step with moves, like a small step, stands first on its Start
) -> list[Section]:  # The stops, each with its text and its links
    """Make each stop of the walk from the sections of the notes: a whole step is its section; a small step is the
    commands at the top of its step's section, such as `$ just setup`, and its moves; a narrative's stop is the text of a
    step before its first move, or one move's section. In a tutorial every stop with moves links first to the code at its Start, as
    the page stands it there, and a whole step then to the code at its tag, after its moves."""
    starts = {**(history_starts or {}), **{step.name: step.start or (steps[i - 1].sha if i else "") for i, step in enumerate(steps)}}
    shas = {**(history or {}), **{step.name: step.sha for step in steps}, **{m.name: m.sha for found in moves for m in found}}
    out = []
    for step, found in zip(steps, moves, strict=True):
        source = sources.get(step.section or step.name)
        if source is None:
            warnings.add(f"the notes have no section ## {step.section or step.name}, which {step.name} takes")
            source = Source("", [], {})
        names = [m.name for m in found] or ([step.name] if step.section else [])
        if step.section and found:   # a small step of a tutorial stands first on its Start
            link = f"[The code at its Start]({url}/tree/{starts[step.name]}), the commit before {names[0]}." if starts[step.name] else ""
        elif tutorial and found and starts[step.name]:   # a whole step with moves: the page stands it on its Start, as do mode begins it
            link = (f"[The code at its Start]({url}/tree/{starts[step.name]}), the commit before {found[0].name}. "
                    f"[The code at {step.name}]({url}/tree/{quote(step.name)}) is the code after its moves.")
        else:   # a step at its tag; a narrative's stop at a move, at the move's commit
            link = f"[The code at {step.name}]({url}/tree/{step.sha if step.section else quote(step.name)})"
        body: list[tuple[str, str | None]] = [(link, None), ("", None)]
        body += render(lead(source.before) if step.section else source.before, step.name, url, shas, starts, diff, watch)
        section = Section(step.name, source.title, "", [])
        for name in names:
            if name not in source.moves:
                warnings.add(f"the notes have no section ### {name}, which {step.name} takes")
                continue
            words, lines = source.moves[name]
            if len(names) == 1 and step.section:
                section.title = words
            if found:   # a tutorial: each move is a section of its stop
                heading = f"{name} {words}".strip()
                section.moves.append((heading, name))
                anchor = f" {{#{name}}}" if level == 2 else ""
                body += [(line, None) for line in ["", f"{'#' * level} {heading}{anchor}", "",
                                                   f"[The commit of this move]({url}/commit/{shas[name]}) shows what the move changes.", ""]]
            else:   # a narrative's stop at a move: its commit, then its words
                body += [(f"[The commit of {name}]({url}/commit/{shas[name]}) shows what it changes.", None), ("", None)]
            body += render(lines, name, url, shas, starts, diff, watch)
        if step.section and len(names) > 1:
            section.title = ""
        section.title = step.title or section.title   # a title that select gives
        section.body = squeeze(apply_links(body, url).strip("\n")) + "\n"
        out.append(section)
    return out


def lead(
    lines: list[str],  # The text of a step before its first move
) -> list[str]:  # Its commands at the top, before its first words
    "Find the commands at the top of a step's section, such as `$ just setup`, which the Start of a small step needs too."
    out = []
    for line in lines:
        if command := COMMAND.match(line):
            if command.group(1) != "do":   # a do$ line makes a move, not a Start
                out.append(line)
        elif line.strip():
            break
    return out


def render(
    lines: list[str],  # Lines of the notes of one step or move
    where: str,  # The step or move they belong to
    url: str,  # The project on GitHub
    shas: dict[str, str],  # The commit of each step and move of the walk
    starts: dict[str, str],  # The commit before each step of the walk, or ""
    diff: Callable[[str, str, str], str] | None = None,  # The change of a file from one commit to another, to show under a diff item
    watch: bool = False,  # For a reader who watches, in watch mode or a narrative: a do$ line is what the move ran, not a command
) -> list[tuple[str, str | None]]:  # Each line, with the link to its change for an Apply in it; None: leave it as it is
    """Turn lines of the notes into Markdown to read: commands in code blocks, items as links to the code, and after a
    files: list, the change of each of its diff items."""
    code = fenced(lines)
    body: list[tuple[str, str | None]] = []
    commands: list[tuple[str, str]] = []   # a run of commands: (track, command)
    items = False   # whether the lines are a files: list
    changes: list[tuple[str, str]] = []   # the diff items of that list: (the step or move, the path)

    def flush() -> None:
        if not commands:
            return
        while body and not body[-1][0].strip():
            body.pop()
        block = ["", "```bash"]
        for track, command in commands:
            if track not in ("", "main") and (len(block) == 2 or not block[-1].startswith("# ")):
                block.append("# in a second terminal")
            block.append(command)
        body.extend((line, None) for line in [*block, "```", ""])
        commands.clear()

    def show_changes() -> None:   # after a files: list, the change of each diff item, as GitHub and smalldocs colour it
        for named, path in changes:
            text = diff(starts.get(named) or f"{shas[named]}^", shas[named], path) if diff else ""
            hunk = next((at for at, line in enumerate(text.split("\n")) if line.startswith("@@")), None)
            if hunk is None:   # a binary file, or a change of mode alone: nothing to read
                continue
            lines = text.split("\n")[hunk:]   # the hunks; the heading above the block names the file
            if len(lines) > DIFF_LINES:
                lines = [*lines[:DIFF_LINES], f"... {len(lines) - DIFF_LINES} more lines: see its change on GitHub"]
            fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
            while body and not body[-1][0].strip():
                body.pop()
            body.extend((line, None) for line in ["", f"The change of `{path}`:", "", f"{fence}diff", *lines, fence, ""])
        changes.clear()

    def change(name: str) -> str:   # the link to what a step or a move changed
        if starts.get(name):
            return f"{url}/compare/{starts[name]}...{shas[name]}"
        return f"{url}/commit/{shas[name]}"

    for at, line in enumerate(lines):
        if at in code:
            if items:   # fenced code ends a files: list: its changes come first
                show_changes()
                items = False
            flush()
            body.append((line, None))
            continue
        if command := COMMAND.match(line):
            if items:   # a command ends a files: list: its changes come first
                show_changes()
                items = False
            if command.group(1) == "do" and watch:   # what the move ran: to read, not to run
                flush()
                while body and not body[-1][0].strip():
                    body.pop()
                body += [("", None), (f"The move ran: {code_span(command.group(2).strip())}", None), ("", None)]
                continue
            if command.group(1) == "do":   # what makes the move: a block of its own, apart from the commands that check it
                flush()
                commands.append(("", command.group(2).strip()))
                flush()
                continue
            commands.append((command.group(1) or "", command.group(2).strip()))
            continue
        if not line.strip() and commands:
            continue
        flush()
        if line.strip() == "files:":
            show_changes()
            items = True
            body.append(("Files to read:", None))
            continue
        if items and (item := ITEM.match(line)):
            indent, kind, other, path, words = item.groups()
            named = other if other in shas else where
            link = f"[`{path}`]({url}/blob/{shas[named]}/{quote(path)})"
            if kind == "diff":
                link += f", [its change]({change(named)}#diff-{hashlib.sha256(path.encode()).hexdigest()})"
                changes.append((named, path))
            body.append((f"{indent}- {link}" + (f": {words}" if words else ""), None))
            continue
        if items and (shown := shown_line(line)):
            body.append((f"{shown[0]}- look at: {code_span(shown[1])}", None))
            continue
        if line.strip() and not line.startswith(" "):
            if items:
                show_changes()
            items = False
        body.append((line, change(where)))
    flush()
    show_changes()
    return body


def apply_links(
    body: list[tuple[str, str | None]],  # The lines of one step, each with the link to its move's change, or None to leave it
    url: str,  # The project on GitHub
) -> str:  # The text, with timewalk's Apply buttons made links to the move's commit
    """Turn timewalk's Apply buttons, which a reader without timewalk does not have, into the commit of the move: "with
    **Apply this file** in timewalk, or from the move's commit on GitHub" becomes "from the move's commit", and any other
    **Apply ...** becomes a link to it. Each run of prose lines is read whole, as a phrase can go over two lines."""
    out: list[str] = []
    at = 0
    while at < len(body):
        line, link = body[at]
        if link is None or not line.strip():
            out.append(line)
            at += 1
            continue
        run = [line]
        while at + len(run) < len(body) and body[at + len(run)][1] == link and body[at + len(run)][0].strip():
            run.append(body[at + len(run)][0])
        text = "\n".join(run)
        if APPLY.search(text):
            target = f"[the move's commit]({link})"
            text = WITH_APPLY.sub(lambda m, target=target: f"{(m.group(1) or '').strip()}{',' if m.group(1) else ''} from {target}".lstrip(), text)
            text = APPLY.sub(target, text)
        out.extend(text.split("\n"))
        at += len(run)
    return "\n".join(out)


def slug(
    name: str,  # A stop of the walk, such as `step-05.3 to step-05.6`
) -> str:  # A name for its page: `step-05.3-to-step-05.6`
    "Make a file name for a stop's page."
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")


def heading(
    section: Section,  # A stop of the walk
) -> str:  # Its name and title
    "Name a stop as its heading and its page do."
    return f"{section.name} {section.title}".strip()


def markdown_file(
    mode: str | None,  # "do" or "watch" in a tutorial; None in a narrative, which has one copy
    title: str,  # The title of the tutorial
    head: str,  # What goes before the first step: the intro, or the notes' text before the first step
    steps: list[Section],  # The stops
) -> str:  # TUTORIAL-<mode>.md, or TUTORIAL.md
    "Write the tutorial of one mode as one Markdown file, for GitHub."
    lines = [f"<!-- {MADE} -->", "", f"# {title}", ""]
    if mode:
        other = "watch" if mode == "do" else "do"
        lines += [f"{MODE_WORDS[mode]} [The same tutorial in {other} mode]({FILE_NAME[other]}).", ""]
    lines += [head.strip(), ""] if head.strip() else []
    for section in steps:
        lines += [f"## {heading(section)}", "", section.body]
    return squeeze("\n".join(lines)).rstrip() + "\n"


def site_files(
    title: str,  # The title of the tutorial
    head: str,  # What goes before the first step, on the front page
    by_mode: dict[str | None, list[Section]],  # The stops of each mode, with their moves at the depth of a page; one key, None, in a narrative
    quarto: bool,  # Write _quarto.yml, not _smalldocs.yml
) -> dict[str, str]:  # Each file of the website, by its path in the site's folder
    """Write the website: a front page, and a page for each stop. A tutorial has two sections, Watch and Do, in one site,
    so that a reader can change modes at any step, from a link at the top of each page, as the switch Do / Watch does in
    timewalk. A narrative has one."""
    files: dict[str, str] = {}
    sidebar: list[str] = ["      - index.qmd"]
    front = [f"<!-- {MADE} -->", "", *([head.strip(), ""] if head.strip() else [])]
    if None not in by_mode:
        front += ["The tutorial has two ways through it. Both have the same steps and moves.", ""]
    for mode in [m for m in ("watch", "do", None) if m in by_mode]:
        folder, indent = (f"{mode}/", "    ") if mode else ("", "")
        if mode:
            label = mode.capitalize()
            front += [f"## {label}", "", MODE_WORDS[mode], ""]
            sidebar += [f"      - section: {json.dumps(label)}", "        contents:"]
        for section in by_mode[mode]:
            page, name = f"{folder}{slug(section.name)}.qmd", f"{section.name}: {section.title}" if section.title else section.name
            switch = ""
            if mode:
                other = "watch" if mode == "do" else "do"
                switch = f"{mode.capitalize()} mode. [The same step in {other} mode](../{other}/{slug(section.name)}.qmd).\n\n"
            files[page] = f"---\ntitle: {json.dumps(name)}\n---\n\n<!-- {MADE} -->\n\n{switch}{section.body.rstrip()}\n"
            text = re.sub(r"([\\\[\]])", r"\\\1", name)   # a bracket in a title ends no link
            front.append(f"- [{text}]({page})")
            if section.moves:
                sidebar += [f"{indent}      - section: {json.dumps(name)}", f"{indent}        href: {page}", f"{indent}        contents:"]
                sidebar += [f"{indent}          - text: {json.dumps(move)}\n{indent}            href: {page}#{ident}" for move, ident in section.moves]
            else:
                sidebar += [f"{indent}      - text: {json.dumps(name)}\n{indent}        href: {page}"]
        front.append("")
    files["index.qmd"] = f"---\ntitle: {json.dumps(title)}\n---\n\n" + "\n".join(front).rstrip() + "\n"
    options = ["theme: cosmo", "css: styles.css", "toc: true", "toc-depth: 3", "code-copy: true",
               "from: markdown-tex_math_dollars-smart  # a $ is a dollar, not mathematics; -- stays two hyphens"]
    pages = ", ".join(json.dumps(p) for p in ["index.qmd", *(f"{m}/*.qmd" for m in by_mode if m), *(p for p in files if None in by_mode and p != "index.qmd")])
    config = [f"# {MADE}", "project:", "  type: website", "  output-dir: _site",
              f"  render: [{pages}]   # only these pages: never the notes", "",
              "website:", f"  title: {json.dumps(title)}", "  page-navigation: true", "  search: true", "  sidebar:",
              "    style: docked", "    contents:", *sidebar, ""]
    config += ["format:", "  html:", *(f"    {line}" for line in options)] if quarto else options
    files["_quarto.yml" if quarto else "_smalldocs.yml"] = "\n".join(config) + "\n"
    files["styles.css"] = (f"/* {MADE} */\n/* Inline code keeps the code font, without the shaded box and the colour the theme gives it. */\n"
                           "code:not(pre code) {\n  background-color: transparent !important;\n  color: inherit !important;\n"
                           "  padding: 0 !important;\n}\n")
    return files


def write(
    plan: list[tuple[Path, dict[str, str], list[str]]],  # For each folder: its files by path, and globs of the files an earlier run made
    forbidden: list[Path],  # The project, its replay copy and its worktrees: never written to
) -> list[str]:  # The paths written or removed
    """Write the files, replacing only files that this command made, and remove its own files that a step no longer has.
    Every file is checked before the first is written, so a refusal writes nothing."""
    def made_here(
        path: Path,  # A file in a folder to write
    ) -> bool:  # Whether this command made it, under its name now or its old name
        "Find the line that this command puts in each file it makes."
        text = path.read_text(encoding="utf-8", errors="replace")
        return MADE in text or OLD_MADE in text

    def check(target: Path) -> None:
        real = target.resolve()
        if any(inside(real, base) for base in forbidden) or in_a_copy(target, forbidden[0]):
            raise SystemExit(f"timewalk-export: {target} is inside the project or its replay copy; timewalk never writes there")
        if target.is_dir() or (target.exists() and not made_here(target)):
            raise SystemExit(f"timewalk-export: {target} exists, and timewalk-export did not make it; move it away, or write to another folder")

    stale: list[tuple[Path, Path]] = []
    for folder, files, ours in plan:
        for path in files:
            check(folder / path)
        for pattern in ours:
            for old in sorted(folder.glob(pattern)):
                rel = old.relative_to(folder).as_posix()
                if rel not in files and old.is_file() and made_here(old):
                    check(old)
                    stale.append((folder, old))
    done = []
    for folder, files, _ in plan:
        for path, text in files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                target.write_text(text, encoding="utf-8")
                done.append(str(target))
    for _, old in stale:
        old.unlink()
        done.append(f"{old} (removed)")
    return done


def forbidden_folders(
    main: Path,  # The project
) -> list[Path]:  # The project, its replay copy beside it, and every worktree of it
    "List the folders that timewalk never writes to: the project, and the copies that timewalk steps in."
    worktrees = [Path(line.split(" ", 1)[1]) for line in git(main, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
    return [main, main.parent / f"{main.name}-replay", *worktrees]


def main() -> None:
    "Write the tutorial of a walk for reading without timewalk: Markdown for GitHub, and a website."
    parser = argparse.ArgumentParser(description="Write a walk for reading without timewalk: a Markdown file for each mode of a tutorial, "
                                                 "and a website with a page for each step in each mode. A narrative has one of each.")
    parser.add_argument("repo", type=Path, help="the project, with its step tags: its commits give the links")
    parser.add_argument("--toc", type=Path, help="the table of contents, toc.toml")
    parser.add_argument("--walk", help="the id of a walk in toc.toml (default: the first tutorial)")
    parser.add_argument("--notes", type=Path, help="the notes file of a tutorial, instead of --toc")
    parser.add_argument("--tags", default="step-*", help="with --notes, the glob of the step tags (default: step-*)")
    parser.add_argument("--github", help="the project's address for the links, https://github.com/OWNER/NAME (default: its origin)")
    parser.add_argument("--title", help="the title (default: the notes' # heading, else the walk's title)")
    parser.add_argument("--intro", type=Path, help="a Markdown file to put first, under the title")
    parser.add_argument("--no-opening", action="store_true", help="leave out the notes' text before the first step")
    parser.add_argument("--markdown", type=Path, help="the folder for TUTORIAL-do.md and TUTORIAL-watch.md, or a narrative's TUTORIAL.md")
    parser.add_argument("--site", type=Path, help="the folder for the website")
    parser.add_argument("--quarto", action="store_true", help="write _quarto.yml for the website, not _smalldocs.yml")
    args = parser.parse_args()
    if not args.markdown and not args.site:
        parser.error("give --markdown, --site, or both")
    if bool(args.toc) == bool(args.notes):
        parser.error("give --toc (and --walk), or --notes")
    try:
        main_repo = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel"))
    except GitError as error:
        raise SystemExit(f"timewalk-export: {args.repo}: {error}") from None
    walk = Walk("notes", "", "tutorial", args.notes.resolve() if args.notes else None, None, args.tags, None, "manual")
    if args.toc:
        try:
            walks = load_toc(args.toc.resolve())
        except TocError as error:
            raise SystemExit(f"timewalk-export: {error}") from None
        walk = next((w for w in walks if w.id == args.walk), None) if args.walk else next((w for w in walks if w.kind == "tutorial"), None)
        if walk is None or walk.notes is None:
            raise SystemExit(f"timewalk-export: {args.walk or 'no tutorial'} is not a walk with notes in {args.toc}")
    tutorial = walk.kind == "tutorial"
    try:
        if walk.select:
            steps, moves = select_steps(main_repo, walk.tags, walk.select, tutorial)
        else:
            steps = pick_steps(tag_steps(main_repo, walk.tags), walk.steps)
            moves = step_moves(main_repo, steps) if tutorial else [[] for _ in steps]
    except GitError as error:
        raise SystemExit(f"timewalk-export: {error}") from None
    if not steps:
        raise SystemExit(f"timewalk-export: no tags match {walk.tags!r} in {main_repo}")
    every = tag_steps(main_repo, walk.tags)
    history = {**{s.name: s.sha for s in every}, **{m.name: m.sha for found in step_moves(main_repo, every) for m in found}}
    history_starts = {s.name: every[i - 1].sha for i, s in enumerate(every) if i}
    text = walk.notes.read_text(encoding="utf-8")
    url = github_url(main_repo, args.github)
    warned: set[str] = set()

    @functools.cache
    def diff(
        base: str,  # The commit before: a step's Start, or a move's commit with ^
        sha: str,  # The commit after
        path: str,  # A file of the project
    ) -> str:  # What git prints for the change of the file, or "" when git cannot say
        "Find the change of one file between two commits. A commit's own change comes from git show, which knows a first commit."
        own = base == f"{sha}^"
        try:
            return git(main_repo, *(["show", "--format=", sha] if own else ["diff", base, sha]), "--no-color", "--no-ext-diff", "--", path)
        except GitError:
            return ""

    try:
        texts = {mode: for_mode(text, mode) for mode in (None, *(["do", "watch"] if tutorial else ["watch"]))}
    except NotesError as error:
        raise SystemExit(f"timewalk-export: {walk.notes}: {error}") from None
    intros = []
    for path in (args.intro, walk.intro):
        try:
            intros.append(public_text(path.read_text(encoding="utf-8")) if path else "")
        except NotesError as error:
            raise SystemExit(f"timewalk-export: {path}: {error}") from None
    named, opening, sources = read_sections(texts[None])
    for name in sources:
        if name not in history:
            warned.add(f"the notes have ## {name}, which is not a step of the history; it is left out")
    title = args.title or (walk.title if walk.select or not named else named) or "Tutorial"
    head = "\n\n".join(part.strip() for part in [intros[0], intros[1] if walk.intro else "" if args.no_opening else opening]
                         if part.strip())
    modes: list[str | None] = ["do", "watch"] if tutorial else [None]
    plan: list[tuple[Path, dict[str, str], list[str]]] = []
    if args.markdown:
        files = {}
        for mode in modes:
            found = build(read_sections(texts[mode or "watch"])[2], steps, moves, url, 3, warned, history, history_starts, diff, mode != "do",
                          tutorial)
            files[FILE_NAME[mode] if mode else "TUTORIAL.md"] = markdown_file(mode, title, head, found)
        plan.append((args.markdown, files, ["TUTORIAL-do.md", "TUTORIAL-watch.md", "TUTORIAL.md"]))
    if args.site:
        by_mode = {mode: build(read_sections(texts[mode or "watch"])[2], steps, moves, url, 2, warned, history, history_starts, diff, mode != "do",
                                  tutorial)
                   for mode in modes}
        plan.append((args.site, site_files(title, head, by_mode, args.quarto), ["watch/*.qmd", "do/*.qmd", "*.qmd"]))
    for warning in sorted(warned):
        print(f"timewalk-export: {warning}", file=sys.stderr)
    written = write(plan, forbidden_folders(main_repo))
    print("\n".join(f"  {path}" for path in written) or "  nothing changed")
    count = sum(len(found) for found in moves)
    print(f"timewalk-export: {len(steps)} steps and {count} moves" + (", in do mode and in watch mode" if tutorial else ", a narrative"))
