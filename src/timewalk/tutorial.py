"""Write a tutorial for reading without timewalk: a Markdown file for each mode, and a website.

    timewalk-tutorial REPO --toc toc.toml --walk tutorial --markdown DIR --site DIR

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
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from timewalk import GitError, Move, Step, git, note_blocks, pick_steps, step_moves, tag_steps
from timewalk.walks import TocError, inside, load_toc

MADE = "Made by timewalk-tutorial from the notes of the walk. Do not edit it by hand: run timewalk-tutorial again."
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
NEW_BLOCK = re.compile(r"^\s*(?:#{1,6}(?:\s|$)|```|~~~|[-*+]\s|\d+[.)]\s|(?:---|\*\*\*|___)\s*$|<|(?:main|runs[2-9]?)?\$\s|time:)")
COMMAND = re.compile(r"^\s*(main|runs[2-9]?)?\$\s+(.+)$")
ITEM = re.compile(r"^(\s*)-\s+(diff|file)\s+(?:([\w.-]+)\s+)?`([^`]+)`\s*:?\s*(.*)$")
SHOW = re.compile(r"^(\s+)show:\s*`(.+)`\s*$")
APPLY = re.compile(r"\*\*Apply (?:this file|the whole move)\*\*")
# "Take it with **Apply this file** in timewalk, or from the move's commit on GitHub": the words from "with" go
WITH_APPLY = re.compile(r"\bwith\s+\*\*Apply (?:this file|the whole move)\*\*(\s+too)?,?(?:\s+in\s+timewalk)?"
                        r"(?:,?\s+or\s+(?:from\s+)?the\s+move's\s+commit(?:\s+on\s+GitHub)?)?")


@dataclass
class Section:
    "One step of the tutorial, in one mode."

    name: str  # The step, such as `step-03`
    title: str  # Its title, from its heading in the notes
    body: str  # Its text, under its heading, as Markdown
    moves: list[tuple[str, str]]  # Its moves: (the text of the move's heading, the move's name, its heading's id on the website)


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
    lines = text.split("\n")
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
                raise SystemExit(f"timewalk-tutorial: in the notes, {lines[labels[0]].strip()!r} is a public cue in the blockquote of "
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
        raise SystemExit(f"timewalk-tutorial: line {line} of the notes: {problem}")
    lines = [line for at, line in enumerate(text.splitlines()) if at not in blocks.fences and blocks.mode[at] in (None, mode)]
    drop = private_lines("\n".join(lines))
    kept, comment = [], False   # comment: a kept line opened an HTML comment that is not closed yet
    for at, line in enumerate(lines):
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
        raise SystemExit("timewalk-tutorial: the project's origin is not on GitHub; give its address with --github https://github.com/OWNER/NAME")
    return f"https://github.com/{found.group(1)}"


def sections(
    text: str,  # The notes of one mode, from for_mode
    steps: dict[str, Step],  # The steps of the walk, by name, in order
    moves: dict[str, Move],  # The moves of the walk, by name
    url: str,  # The project on GitHub
    level: int,  # The depth of a move's heading: 3 in the Markdown file, 2 on a page of the website, with the move as its id
    warnings: set[str] | None = None,  # Where to put what is left out, each said once
) -> tuple[str, str, list[Section]]:  # The title, the text before the first step, and the steps
    """Read one mode's notes into steps, each with its links to the code: commands in code blocks, items as links. A
    section of a step or a move that is not in the walk is left out, with a warning."""
    lines = text.split("\n")
    code = fenced(lines)
    names = list(steps)
    title, opening, found = "", [], []
    current: Section | None = None
    body: list[tuple[str, str | None]] = []   # each line, with the commit of its move for an Apply in it; None: leave as it is
    commands: list[tuple[str, str]] = []   # a run of commands: (track, command)
    items, skip = False, False   # whether the lines are a files: list; a section left out

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

    def close() -> None:
        flush()
        if current:
            current.body = squeeze(apply_links(body, url).strip("\n")) + "\n"
            found.append(current)
        body.clear()

    def change(name: str) -> str:   # the link to what a step or a move changed
        if name in moves:
            return f"{url}/commit/{moves[name].sha}"
        before = names.index(name)
        return f"{url}/compare/{steps[names[before - 1]].sha}...{steps[name].sha}" if before else f"{url}/commit/{steps[name].sha}"

    where = ""   # the step or move being read
    for at, line in enumerate(lines):
        if at in code:
            if not skip:
                flush()
                (body.append((line, None)) if current else opening.append(line))
            continue
        step = re.match(r"^##\s+(\S+)\s*(.*)$", line)
        move = re.match(r"^###\s+(\S+)\s*(.*)$", line)
        if not current and not title and re.match(r"^#\s+\S", line):
            title = line[1:].strip()
            continue
        if step:
            if step.group(1) not in steps:
                if current or step.group(1).startswith(tuple(n.split("-")[0] + "-" for n in names)):
                    (warnings if warnings is not None else set()).add(f"the notes have ## {step.group(1)}, which is not a step of the walk; it is left out")
                    flush()
                    skip = True
                    continue
            else:
                close()
                skip = False
                where = name = step.group(1)
                current, items = Section(name, step.group(2).strip(), "", []), False
                body.extend([(f"[The code at {name}]({url}/tree/{quote(name)})", None), ("", None)])
                continue
        if current is not None and move and re.match(rf"^{re.escape(current.name)}\.\d+$", move.group(1)):
            flush()
            if move.group(1) not in moves:
                (warnings if warnings is not None else set()).add(f"the notes have ### {move.group(1)}, which is not a move of the walk; it is left out")
                skip = True
                continue
            skip = False
            where = name = move.group(1)
            heading = f"{name} {move.group(2).strip()}".strip()
            items = False
            current.moves.append((heading, name))
            anchor = f" {{#{name}}}" if level == 2 else ""
            body.extend((line, None) for line in ["", f"{'#' * level} {heading}{anchor}", "",
                                                  f"[The commit of this move]({url}/commit/{moves[name].sha}) shows what the move changes.", ""])
            continue
        if skip:
            continue
        if current is None:
            opening.append(line)
            continue
        if command := COMMAND.match(line):
            commands.append((command.group(1) or "", command.group(2).strip()))
            continue
        if not line.strip() and commands:
            continue
        flush()
        if line.strip() == "files:":
            items = True
            body.append(("Files to read:", None))
            continue
        if items and (item := ITEM.match(line)):
            indent, kind, other, path, words = item.groups()
            named = other if other in moves or other in steps else where
            at_sha = moves[named].sha if named in moves else steps[named].sha
            link = f"[`{path}`]({url}/blob/{at_sha}/{quote(path)})"
            if kind == "diff":
                link += f", [its change]({change(named)}#diff-{hashlib.sha256(path.encode()).hexdigest()})"
            body.append((f"{indent}- {link}" + (f": {words}" if words else ""), None))
            continue
        if items and (shown := SHOW.match(line)):
            body.append((f"{shown.group(1)}- look at: `{shown.group(2)}`", None))
            continue
        if line.strip() and not line.startswith(" "):
            items = False
        body.append((line, change(where)))
    close()
    return title, "\n".join(opening).strip(), found


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


def markdown_file(
    mode: str,  # "do" or "watch"
    title: str,  # The title of the tutorial
    intro: str,  # Markdown to put first, or ""
    opening: str,  # The notes' text before the first step, or ""
    steps: list[Section],  # The steps
) -> str:  # TUTORIAL-<mode>.md
    "Write the tutorial of one mode as one Markdown file, for GitHub."
    other = "watch" if mode == "do" else "do"
    head = [f"<!-- {MADE} -->", "", f"# {title}", "", f"{MODE_WORDS[mode]} [The same tutorial in {other} mode]({FILE_NAME[other]}).", ""]
    parts = [*head, *([intro.strip(), ""] if intro.strip() else []), *([opening, ""] if opening else [])]
    for section in steps:
        parts += [f"## {section.name} {section.title}".rstrip(), "", section.body]
    return squeeze("\n".join(parts)).rstrip() + "\n"


def site_files(
    title: str,  # The title of the tutorial
    intro: str,  # Markdown to put first, or ""
    opening: str,  # The notes' text before the first step, or ""
    by_mode: dict[str, list[Section]],  # The steps of each mode, with their moves at the depth of a page
    quarto: bool,  # Write _quarto.yml, not _smalldocs.yml
) -> dict[str, str]:  # Each file of the website, by its path in the site's folder
    """Write the website: a front page, and in each of two sections, Watch and Do, a page for each step. One site lets a
    reader change modes at any step, from a link at the top of each page, as the switch Do / Watch does in timewalk."""
    files: dict[str, str] = {}
    sidebar: list[str] = ["      - index.qmd"]
    front = [f"<!-- {MADE} -->", "", *([intro.strip(), ""] if intro.strip() else []), *([opening, ""] if opening else []),
             "The tutorial has two ways through it. Both have the same steps and moves.", ""]
    for mode in ("watch", "do"):
        other = "watch" if mode == "do" else "do"
        label = mode.capitalize()
        front += [f"## {label}", "", MODE_WORDS[mode], ""]
        sidebar += [f"      - section: {json.dumps(label)}", "        contents:"]
        for section in by_mode[mode]:
            page = f"{mode}/{section.name}.qmd"
            heading = f"{section.name}: {section.title}".rstrip(": ")
            switch = f"{label} mode. [The same step in {other} mode](../{other}/{section.name}.qmd)."
            files[page] = f"---\ntitle: {json.dumps(heading)}\n---\n\n<!-- {MADE} -->\n\n{switch}\n\n{section.body.rstrip()}\n"
            front.append(f"- [{heading}]({page})")
            if section.moves:
                sidebar += [f"          - section: {json.dumps(heading)}", f"            href: {page}", "            contents:"]
                sidebar += [f"              - text: {json.dumps(move)}\n                href: {page}#{name}" for move, name in section.moves]
            else:
                sidebar += [f"          - text: {json.dumps(heading)}\n            href: {page}"]
        front.append("")
    files["index.qmd"] = f"---\ntitle: {json.dumps(title)}\n---\n\n" + "\n".join(front).rstrip() + "\n"
    options = ["theme: cosmo", "css: styles.css", "toc: true", "toc-depth: 3", "code-copy: true",
               "from: markdown-tex_math_dollars-smart  # a $ is a dollar, not mathematics; -- stays two hyphens"]
    config = [f"# {MADE}", "project:", "  type: website", "  output-dir: _site",
              '  render: ["index.qmd", "watch/*.qmd", "do/*.qmd"]   # only these pages: never the notes', "",
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
    def check(target: Path) -> None:
        real = target.resolve()
        if any(inside(real, base) for base in forbidden):
            raise SystemExit(f"timewalk-tutorial: {target} is inside the project or its replay copy; timewalk never writes there")
        if target.is_dir() or (target.exists() and MADE not in target.read_text(encoding="utf-8", errors="replace")):
            raise SystemExit(f"timewalk-tutorial: {target} exists, and timewalk-tutorial did not make it; move it away, or write to another folder")

    stale: list[tuple[Path, Path]] = []
    for folder, files, ours in plan:
        for path in files:
            check(folder / path)
        for pattern in ours:
            for old in sorted(folder.glob(pattern)):
                rel = old.relative_to(folder).as_posix()
                if rel not in files and old.is_file() and MADE in old.read_text(encoding="utf-8", errors="replace"):
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
    parser = argparse.ArgumentParser(description="Write a tutorial for reading without timewalk: a Markdown file for each mode, "
                                                 "and a website with a page for each step in each mode.")
    parser.add_argument("repo", type=Path, help="the project, with its step tags: its commits give the links")
    parser.add_argument("--toc", type=Path, help="the table of contents, toc.toml")
    parser.add_argument("--walk", help="the id of a tutorial walk in toc.toml (default: the first tutorial)")
    parser.add_argument("--notes", type=Path, help="the notes file, instead of --toc")
    parser.add_argument("--tags", default="step-*", help="with --notes, the glob of the step tags (default: step-*)")
    parser.add_argument("--github", help="the project's address for the links, https://github.com/OWNER/NAME (default: its origin)")
    parser.add_argument("--title", help="the title (default: the notes' # heading, else the walk's title)")
    parser.add_argument("--intro", type=Path, help="a Markdown file to put first, under the title")
    parser.add_argument("--no-opening", action="store_true", help="leave out the notes' text before the first step")
    parser.add_argument("--markdown", type=Path, help="the folder for TUTORIAL-do.md and TUTORIAL-watch.md")
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
        raise SystemExit(f"timewalk-tutorial: {args.repo}: {error}") from None
    title, tags, notes, steps_named = None, args.tags, args.notes, None
    if args.toc:
        try:
            walks = load_toc(args.toc.resolve())
        except TocError as error:
            raise SystemExit(f"timewalk-tutorial: {error}") from None
        walk = next((w for w in walks if w.id == args.walk), None) if args.walk else next((w for w in walks if w.kind == "tutorial"), None)
        if walk is None or walk.kind != "tutorial" or walk.notes is None:
            raise SystemExit(f"timewalk-tutorial: {args.walk or 'no walk'} is not a tutorial walk with notes in {args.toc}")
        title, tags, notes, steps_named = walk.title, walk.tags, walk.notes, walk.steps
    text = notes.read_text(encoding="utf-8")
    picked = pick_steps(tag_steps(main_repo, tags), steps_named)
    if not picked:
        raise SystemExit(f"timewalk-tutorial: no tags match {tags!r} in {main_repo}")
    steps = {step.name: step for step in picked}
    moves = {move.name: move for found in step_moves(main_repo, picked) for move in found}
    url = github_url(main_repo, args.github)
    intro = args.intro.read_text(encoding="utf-8") if args.intro else ""
    warned: set[str] = set()
    named, opening, _ = sections(for_mode(text, None), steps, moves, url, 3, warned)   # the text outside the blocks, for the front page
    title = args.title or named or title or "Tutorial"
    opening = "" if args.no_opening else opening
    plan: list[tuple[Path, dict[str, str], list[str]]] = []
    if args.markdown:
        files = {}
        for mode in ("do", "watch"):
            _, own, found = sections(for_mode(text, mode), steps, moves, url, 3, warned)
            files[FILE_NAME[mode]] = markdown_file(mode, title, intro, "" if args.no_opening else own, found)
        plan.append((args.markdown, files, []))
    if args.site:
        by_page = {mode: sections(for_mode(text, mode), steps, moves, url, 2, warned)[2] for mode in ("do", "watch")}
        plan.append((args.site, site_files(title, intro, opening, by_page, args.quarto), ["watch/*.qmd", "do/*.qmd"]))
    for warning in sorted(warned):
        print(f"timewalk-tutorial: {warning}", file=sys.stderr)
    written = write(plan, forbidden_folders(main_repo))
    print("\n".join(f"  {path}" for path in written) or "  nothing changed")
    print(f"timewalk-tutorial: {len(steps)} steps and {len(moves)} moves, in do mode and in watch mode")
