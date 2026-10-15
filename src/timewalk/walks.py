"""Several walks through one repository, from a table of contents, and the checks of a class folder.

    timewalk-check REPO --toc toc.toml                               every walk of the table
    timewalk-check REPO --notes notes.md --slides slides/slides.toml  one walk, without a table

A walk is one path through the history, with its own notes and slides. The table of contents, `toc.toml`, lists
the walks of a class; the first is the default:

    [[walk]]
    id = "narrative"                  # letters, digits, - and _
    title = "The project, step by step"
    kind = "narrative"                # the tagged steps, and nothing between them
    notes = "notes.md"
    slides = "slides/slides.toml"

    [[walk]]
    id = "pytest"
    title = "Only the tests"
    folder = "walks/pytest"           # its notes.md, and slides/slides.toml, are in this folder
    steps = ["step-01", "step-02"]    # some of the steps; or tags = "pytest-*" for steps of its own

Every path is read from the folder of `toc.toml`, the class folder. The checks find what would go wrong in a
class: a note or a slide entry for a step that is not in the walk, a step with no slides, a slide file that is
missing or outside the class folder, a slide number past the end of its deck. An error stops timewalk from
starting; a warning is printed and does not.
"""

import argparse
import dataclasses
import fnmatch
import itertools
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from timewalk import (
    FILES_ITEM,
    HOME,
    INCLUDE,
    MODES,
    SHOW_LINE,
    GitError,
    Move,
    code_span,
    expand_entry,
    expand_stop,
    git,
    loose_match,
    note_blocks,
    own_stops,
    parse_notes,
    pick_steps,
    replace_section,
    select_entry,
    select_steps,
    shown_line,
    split_slides,
    step_moves,
    stop_range,
    tag_steps,
    walk_notes,
)
from timewalk.observe import Scratch, observe, running, sources

KINDS = ("narrative", "tutorial")
WALK_KEYS = ("id", "title", "description", "kind", "folder", "notes", "slides", "steps", "select", "intro", "tags", "setup", "sync", "moves", "loose", "shared")
SLIDE_FILES = (".md", ".markdown", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".html")


@dataclass
class Walk:
    "One path through the history, with its own notes and slides."

    id: str  # Its name in the table and on the page, such as `pytest`
    title: str  # What the page shows for it
    kind: str  # "narrative": the tagged steps, and nothing between them; "tutorial": also the moves, the commits between them
    notes: Path | None  # Its notes file
    slides: Path | None  # Its slides manifest
    tags: str  # Glob for the tags that mark its steps
    steps: list[str] | None  # The steps it keeps, or None for every tag that matches
    setup: str  # "manual": `just setup` is run by hand at each step
    sync: bool = True  # In a tutorial: a move shows its first slide, and in watch mode a move's slide makes the move
    mode: str = "do"  # In a tutorial, how it starts: "do", the learner makes each move; "watch", timewalk shows each commit
    description: str = ""  # What the walk is about, in a sentence or two: the page shows it at the walk's first step
    folder: Path | None = None  # Its folder, if the table gives one: where timewalk-notes starts a notes.md
    select: list[str | dict] | None = None  # Steps, moves and ranges of moves it takes from a bigger walk, in order; see timewalk.select_entry
    intro: Path | None = None  # What its copies say before the first step, in place of the text of the notes before it
    loose: list[str] = dataclasses.field(default_factory=list)  # In do mode, globs of the files to match loosely; see timewalk.loosely_same
    shared: Path | None = None  # A walk with notes of its own: the notes its includes come from. Its select is made from its ## stops

    def intro_text(
        self,
    ) -> str:  # Its intro, in Markdown; "" if it has none, or it cannot be read
        "Read what the walk says before its first stop: the text of its own notes before the first ##, or its intro file."
        try:
            if self.shared is not None:
                return own_stops(self.notes.read_text(encoding="utf-8"))[0] if self.notes else ""
            return self.intro.read_text(encoding="utf-8") if self.intro else ""
        except (OSError, UnicodeDecodeError):
            return ""

    def own_sections(
        self,
    ) -> list[str | None] | None:  # For each entry of its select, the section of the stop that walks that place now, or None; None for a walk without notes of its own
        """Read a walk's own notes afresh, so they can be edited while the class runs, and put in the shared sections they include.
        Each stop is found by the place it walks, so a stop added, taken out or changed since the walk was read never shows
        another stop's text: its place shows nothing until timewalk starts again."""
        if self.shared is None:
            return None
        try:
            own, shared = self.notes.read_text(encoding="utf-8"), self.shared.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, AttributeError):
            return [None for _ in self.select or []]
        parsed, found = parse_notes(shared), {}
        for stop in own_stops(own)[1]:
            place = stop_range(stop)[0]
            if place and place not in found:
                found[place] = expand_stop(stop, parsed)
        return [found.get(select_entry(entry)[0]) for entry in self.select or []]


class TocError(ValueError):
    "A mistake in a table of contents, said in a sentence that names the file and the key."


def load_toc(
    toc: Path,  # The table of contents, toc.toml
) -> list[Walk]:  # Its walks, the default first
    "Read a table of contents and check its keys. Paths are made absolute from the table's folder."
    try:
        data = tomllib.loads(toc.read_text(encoding="utf-8"))
    except OSError as error:
        raise TocError(f"{toc}: cannot be read ({error.strerror})") from None
    except tomllib.TOMLDecodeError as error:
        raise TocError(f"{toc}: is not valid TOML: {error}") from None
    extra = sorted(set(data) - {"walk"})
    if extra:
        raise TocError(f"{toc}: has {', '.join(extra)}, which a table of contents does not have; it has only [[walk]] tables")
    entries = data.get("walk", [])
    if not isinstance(entries, list) or not entries:
        raise TocError(f"{toc}: lists no walk; add a [[walk]] table for each")
    walks, seen = [], set()
    for number, entry in enumerate(entries, 1):
        where = f"{toc}, walk {number}"
        unknown = sorted(set(entry) - set(WALK_KEYS))
        if unknown:
            raise TocError(f"{where}: has the key {unknown[0]}, which a walk does not have; a walk may have {', '.join(WALK_KEYS)}")
        ident = entry.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
            raise TocError(f"{where}: needs an id of letters, digits, - and _, for example id = \"tutorial\"")
        if ident in seen:
            raise TocError(f"{where}: the id {ident} is used twice")
        seen.add(ident)
        where = f"{toc}, walk {ident}"
        kind = entry.get("kind", "narrative")
        if kind not in KINDS:
            raise TocError(f"{where}: kind = {kind!r}; this version of timewalk knows only {', '.join(repr(k) for k in KINDS)}")
        for key in ("title", "description", "folder", "notes", "slides", "intro", "tags", "setup", "shared"):
            if key in entry and not isinstance(entry[key], str):
                raise TocError(f"{where}: {key} must be text in quotes")
        steps = entry.get("steps")
        if steps is not None and not (isinstance(steps, list) and steps and all(isinstance(s, str) for s in steps)):
            raise TocError(f"{where}: steps must be a list of step names, for example steps = [\"step-01\", \"step-02\"]")
        if steps is not None and "tags" in entry:
            raise TocError(f"{where}: give steps or tags, not both")
        if steps is not None and len(set(steps)) != len(steps):
            raise TocError(f"{where}: steps names a step twice")
        if steps is not None and kind == "tutorial":
            raise TocError(f"{where}: a tutorial takes every step of its tags; for a part, name its steps and moves with select, "
                           "for example select = [\"step-04\", \"step-05.3..step-05.6\"]")
        select = entry.get("select")
        if select is not None and not (isinstance(select, list) and select and all((isinstance(s, str) and s) or isinstance(s, dict) for s in select)):
            raise TocError(f"{where}: select must be a list of steps, moves and ranges of moves, for example "
                           "select = [\"step-04\", \"step-05.3..step-05.6\", \"step-10.3\"]")
        for part in select or []:
            if isinstance(part, dict) and not (set(part) <= {"range", "title"} and isinstance(part.get("range"), str) and part["range"]
                                               and isinstance(part.get("title", ""), str) and not re.search(r"[\r\n]", part.get("title", ""))):
                raise TocError(f"{where}: an entry of select in braces has a range and a title, both text in quotes on one line, for example "
                               "{ range = \"step-05.3..step-05.6\", title = \"Types\" }")
        if select is not None and steps is not None:
            raise TocError(f"{where}: give steps or select, not both")
        if entry.get("moves", "do") not in ("do", "watch"):
            raise TocError(f"{where}: moves = {entry['moves']!r}; write \"do\" or \"watch\"")
        loose = entry.get("loose", [])
        if not (isinstance(loose, list) and all(isinstance(glob, str) and glob for glob in loose)):
            raise TocError(f"{where}: loose must be a list of files, or globs of files, for example loose = [\"pyproject.toml\", \"uv.lock\"]")
        if loose and kind != "tutorial":
            raise TocError(f"{where}: loose is for a tutorial's do mode, where the learner's files are matched with each move; this walk is a {kind}")
        if "sync" in entry and not isinstance(entry["sync"], bool):
            raise TocError(f"{where}: sync must be true or false")
        if entry.get("setup", "manual") != "manual":
            raise TocError(f"{where}: setup = {entry['setup']!r}; this version of timewalk knows only 'manual'")
        folder = toc.parent / entry["folder"] if "folder" in entry else None
        # In a folder, notes.md and slides/slides.toml are each optional: a walk can have no notes, or no slides.
        notes = toc.parent / entry["notes"] if "notes" in entry else (folder / "notes.md" if folder and (folder / "notes.md").is_file() else None)
        slides = toc.parent / entry["slides"] if "slides" in entry else (
            folder / "slides" / "slides.toml" if folder and (folder / "slides" / "slides.toml").is_file() else None)
        if folder is not None and not folder.is_dir():
            raise TocError(f"{where}: its folder {entry['folder']} does not exist")
        intro = toc.parent / entry["intro"] if "intro" in entry else None
        if intro is not None and not intro.is_file():
            raise TocError(f"{where}: its intro {entry['intro']} does not exist")
        shared = toc.parent / entry["shared"] if "shared" in entry else None
        if shared is not None:
            select = own_select(where, kind, entry, notes, shared)
        walks.append(Walk(ident, entry.get("title", ident), kind, notes.resolve() if notes else None, slides.resolve() if slides else None,
                          entry.get("tags", "step-*"), steps, "manual", entry.get("sync", True), entry.get("moves", "do"),
                          entry.get("description", "").strip(), folder.resolve() if folder else None, select, intro.resolve() if intro else None, loose,
                          shared.resolve() if shared else None))
    return walks


def own_select(
    where: str,  # The table and the walk, for an error
    kind: str,  # The walk's kind
    entry: dict,  # The walk's table in toc.toml
    notes: Path | None,  # Its own notes
    shared: Path,  # The notes its includes come from
) -> list[dict]:  # Its select: for each ## stop with an include, the place its includes make, and its title
    """Make the select of a walk with notes of its own from its ## stops, each the place its includes make. A stop with no
    include, or whose includes make no one place, is left out, and timewalk-check says why; the order of the stops is
    checked against the history, there and when the walk starts."""
    if kind != "tutorial":
        raise TocError(f"{where}: shared is for a tutorial that walks part of a bigger one, with notes of its own; this walk is a {kind}")
    for key, why in (("select", "its ## stops and their includes are its select"), ("steps", "its ## stops and their includes are its select"),
                     ("intro", "its intro is the text of its notes before the first ## stop")):
        if key in entry:
            raise TocError(f"{where}: has shared and {key}; leave out {key}: {why}")
    if notes is None:
        raise TocError(f"{where}: has shared, but no notes of its own; add notes = \"walks/{entry.get('id', 'part')}/notes.md\"")
    for label, path in (("notes", notes), ("shared notes", shared)):
        if not path.is_file():
            raise TocError(f"{where}: its {label} {path} do not exist")
    try:
        _, stops = own_stops(notes.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise TocError(f"{where}: its notes {notes} cannot be read ({error})") from None
    select = [{"range": place, "title": stop.title} for stop in stops if (place := stop_range(stop)[0])]
    if not select:
        raise TocError(f"{where}: {notes.name} has no ## stop whose {{{{include ...}}}} lines make a place to walk, so the walk has no steps; "
                       "timewalk-check says what is wrong with each stop")
    return select


def steps_of(
    walk: Walk,  # A walk
    main: Path,  # The repository
) -> list[str]:  # The names of its steps, in order
    "List the steps of a walk. Raises GitError when its tags match nothing, or a named step or move is not one."
    if walk.select:
        return [step.name for step in select_steps(main, walk.tags, walk.select, walk.kind == "tutorial")[0]]
    steps = pick_steps(tag_steps(main, walk.tags), walk.steps)
    if not steps:
        raise GitError(f"no tags match {walk.tags!r}")
    return [step.name for step in steps]


def inside(
    path: Path,  # A file
    folder: Path,  # A folder
) -> bool:  # Whether the file is in the folder, without following links
    "Say whether a path lies inside a folder, by its written form."
    full, base = os.path.normpath(path), os.path.normpath(folder)
    return os.path.commonpath([full, base]) == base


def in_a_copy(
    path: Path,  # A file to write, which may not exist yet
    main: Path,  # The project
) -> bool:  # Whether the file is in the project, a worktree of it, or a replay copy of it wherever it is
    "Say whether timewalk must not write a file: a replay copy is known by its remote home, which names the project."
    real = path.resolve()
    if any(inside(real, folder.resolve()) for folder in (main, main.parent / f"{main.name}-replay")):
        return True
    folder = next((p for p in [real, *real.parents] if p.is_dir()), None)
    if folder is None:
        return False
    found = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True)
    if found.returncode:
        return False
    top = Path(found.stdout.strip())
    worktrees = [Path(line.split(" ", 1)[1]) for line in git(main, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
    home = subprocess.run(["git", "remote", "get-url", HOME], cwd=top, capture_output=True, text=True).stdout.strip()
    return any(top.resolve() == w.resolve() for w in worktrees) or bool(home and Path(home).resolve() == main.resolve())


def check_walk(
    walk: Walk,  # The walk to check
    main: Path,  # The repository
    root: Path,  # The class folder: every slide must be inside it
    replay: Path | None = None,  # The replay copy, where notes and slides must not be either
    slide_folders: list[Path] | None = None,  # With a table of contents: the walks' slides folders, the only ones the page serves
    run: bool | None = None,  # Whether to run the do$ commands, to see the files they write; None: unless TIMEWALK_NO_RUN is set
) -> tuple[list[str], list[str], set[Path]]:  # Errors, warnings, and the slide files the walk uses
    "Check one walk's notes and slides against its steps and against each other."
    from timewalk.export import cue_problems  # here, as timewalk.export imports this module

    errors: list[str] = []
    warnings: list[str] = []
    used: set[Path] = set()
    name = f"walk {walk.id}"
    if walk.shared is not None:
        own_errors, own_warnings = own_problems(walk, main, root)
        errors += [f"{name}: {problem}" for problem in own_errors]
        warnings += [f"{name}: {problem}" for problem in own_warnings]
        if own_errors:
            return errors, warnings, used
    try:
        steps = steps_of(walk, main)
    except GitError as error:
        return [f"{name}: {error}"], [], used
    moves: dict[str, list] = {}
    whole: list[Move] = []   # the steps of one commit after the first, each a change of its own
    history = set(steps)   # the steps and moves of the whole history: a walk that selects may name them in shared notes and slides
    if walk.select:
        taken, taken_moves = select_steps(main, walk.tags, walk.select, walk.kind == "tutorial")
        every = tag_steps(main, walk.tags)
        history = {step.name for step in every} | {move.name for found in step_moves(main, every) for move in found}
        if walk.kind == "tutorial":
            moves = {step.name: found for step, found in zip(taken, taken_moves, strict=True)}
            whole = [Move(step.name, step.sha, step.subject) for step, found in zip(taken, taken_moves, strict=True)   # a whole step of one commit
                     if not found and not step.section and step.start and git(main, "rev-parse", f"{step.sha}^") == step.start]
    elif walk.kind == "tutorial":
        picked = pick_steps(tag_steps(main, walk.tags), walk.steps)
        moves = {step.name: found for step, found in zip(picked, step_moves(main, picked), strict=True)}
        whole = [Move(step.name, step.sha, step.subject) for before, step in itertools.pairwise(picked)   # a tag on the commit of the step
                 if not moves[step.name] and step.sha != before.sha]                                      # before has no change of its own
    names = set(steps) | {move.name for found in moves.values() for move in found} | history
    alone: set[str] = set()   # the steps of the history that are a change of one commit, after the first, whose do$ lines make it
    if walk.kind == "tutorial":
        tagged = tag_steps(main, walk.tags)
        alone = {step.name for before, step, found in zip(tagged, tagged[1:], step_moves(main, tagged)[1:], strict=False)
                 if not found and step.sha != before.sha}
    changed = {path for found in moves.values() for move in found
               for path in git(main, "diff-tree", "-r", "--root", "--no-commit-id", "--name-only", "-z", move.sha).split("\x00") if path} if walk.loose else set()
    for glob in walk.loose:
        if not any(fnmatch.fnmatchcase(path, glob) for path in changed):
            warnings.append(f"{name}: loose names {glob}, which no move of the walk changes")
    for move in (move for found in moves.values() for move in found) if walk.loose else []:
        touched = [path for path in git(main, "diff", "--name-only", "-z", "--no-renames", f"{move.sha}^", move.sha).split("\x00") if path]
        if touched and all(any(fnmatch.fnmatchcase(path, glob) for glob in walk.loose) for path in touched) and all(
                loose_match(path, blob_at(main, move.sha, path), blob_at(main, f"{move.sha}^", path)) for path in touched):
            warnings.append(f"{name}: {move.name} changes only what loose does not compare, such as versions, so do mode marks it "
                            "done before the learner makes it; take its files out of loose, or put the move with one that changes more")
    for label, path in (("notes", walk.notes), ("slides", walk.slides), ("shared notes", walk.shared)):
        if path is not None and any(inside(path, folder) for folder in (main, replay) if folder):
            errors.append(f"{name}: its {label}, {path}, are inside the repository or its replay copy; keep them in the class folder")
    if walk.intro is not None and walk.intro.is_file() and walk.shared is None:
        errors += [f"{name}: {walk.intro.name}:{line}: {problem}" for line, problem in cue_problems(walk.intro.read_text(encoding="utf-8"))]
    if walk.notes is not None:
        if not walk.notes.is_file():
            errors.append(f"{name}: the notes file {walk.notes} does not exist")
        else:
            text = walk.notes.read_text(encoding="utf-8")
            sections = parse_notes(text if walk.shared is None else walk.shared.read_text(encoding="utf-8"))
            blocks = note_blocks(text)
            errors += [f"{name}: {walk.notes.name}:{line}: {problem}" for line, problem in blocks.errors]
            warnings += [f"{name}: {walk.notes.name}:{line}: {problem}" for line, problem in blocks.warnings]
            errors += [f"{name}: {walk.notes.name}:{line}: {problem}" for line, problem in cue_problems(text)]
            warnings += [f"{name}: {walk.notes.name}:{line}: {problem}" for line, problem in show_problems(text)]
            if (walk.kind == "tutorial" or not walk.select) and walk.shared is None:   # a walk that selects shares notes that its tutorial checks
                warnings += [f"{name}: {walk.notes.name}: {problem}" for step, found in sections.items()
                             for problem in do_line_problems(step, found.get("parts", []), walk.kind == "tutorial", step in alone)]
            if walk.kind != "tutorial" and not walk.select and "do" in blocks.mode:   # a walk that selects shares a tutorial's notes
                line = blocks.mode.index("do")
                warnings.append(f"{name}: {walk.notes.name}:{line}: a narrative has no do mode, so its ::: do blocks never show; "
                                "it shows the ::: watch blocks")
            if walk.select:
                problems = selection_problems(walk, main, sections, taken, taken_moves, history, root)
                errors += [f"{name}: {problem}" for problem in problems[0]]
                warnings += [f"{name}: {problem}" for problem in problems[1]]
            for section in sections if not walk.select else []:
                if section not in steps:
                    errors.append(f"{name}: {walk.notes.name} has a section ## {section}, which is not a step of the walk")
            for step in steps if not walk.select else []:
                if step not in sections:
                    warnings.append(f"{name}: {step} has no section in {walk.notes.name}")
            for step, found in moves.items() if not walk.select else []:
                written = [part["name"] for part in sections.get(step, {}).get("parts", []) if part["kind"] == "move"]
                wanted = [move.name for move in found]
                for extra in sorted(set(written) - set(wanted)):
                    errors.append(f"{name}: {walk.notes.name} has a section ### {extra}, which is not a move of {step}")
                for missing in [m for m in wanted if m not in written]:
                    errors.append(f"{name}: {missing} has no section ### {missing} in {walk.notes.name}")
                kept = [m for m in written if m in wanted]
                if kept != [m for m in wanted if m in kept]:
                    errors.append(f"{name}: the ### sections of {step} in {walk.notes.name} are not in the order of the commits: {', '.join(kept)}")
                every = {m.name: m for moves_of_step in moves.values() for m in moves_of_step}   # an item may name an earlier move
                parts = sections.get(step, {}).get("parts", [])
                # Each mode reads the parts outside the blocks and its own blocks: check each as its reader sees it
                for problem in dict.fromkeys(p for mode in MODES for p in item_problems(main, in_mode(parts, mode), every)):
                    warnings.append(f"{name}: {problem}")
                missing = {mode: moves_without_commands(in_mode(parts, mode)) for mode in MODES}
                for move in dict.fromkeys(m for found in missing.values() for m in found):
                    modes = [mode for mode in MODES if move in missing[mode]]
                    where = "" if len(modes) == len(MODES) else f" in {modes[0]} mode"
                    warnings.append(f"{name}: {move} has no command{where} in {walk.notes.name}; add one that shows what the move did, "
                                    "for example $ just test")
            if walk.kind == "tutorial":
                walked = taken_moves if walk.select else list(moves.values())
                every_move = list({move.name: move for found in walked for move in found}.values())
                first = steps[0] if steps and not walk.select else None   # where the walk starts: the learner's clone has its files
                for part in sections.get(first, {}).get("parts", []) if first else []:
                    if part["kind"] == "patch":
                        warnings.append(f"{name}: {first}: the line patch$ {part['path']} is in the first step, where the walk starts; the "
                                        "learner's clone has the file already, so take the line out")
                more_errors, more_warnings = source_problems(walk, main, sections, every_move + whole, running() if run is None else run,
                                                             {step.name for step in whole})
                errors += [f"{name}: {problem}" for problem in more_errors]
                warnings += [f"{name}: {problem}" for problem in more_warnings]
            warnings += [f"{name}: {walk.notes.name}: {problem}" for problem in apply_sentences(sections)]
    if walk.slides is None:
        if walk.notes is not None:
            warnings.append(f"{name}: has no slides.toml; give every step at least one slide")
        return errors, warnings, used
    if not walk.slides.is_file():
        errors.append(f"{name}: the slides manifest {walk.slides} does not exist")
        return errors, warnings, used
    try:
        data = tomllib.loads(walk.slides.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        errors.append(f"{name}: {walk.slides} is not valid TOML: {error}")
        return errors, warnings, used
    for table in ("slides", "docs"):
        if not isinstance(data.get(table, {}), dict):
            errors.append(f"{name}: {walk.slides.name} has {table} that is not a table; write [{table}] and one line for each step under it")
            return errors, warnings, used
    folder, deck = walk.slides.parent, str(data.get("deck", ""))
    listed = {**data.get("slides", {}), **{step: [doc] for step, doc in data.get("docs", {}).items()}}
    for step in listed:
        if step not in names:
            errors.append(f"{name}: slides.toml has an entry for {step}, which is not a step{' or a move' if moves else ''} of the walk")
    if "sync" in data and not isinstance(data["sync"], bool):
        errors.append(f"{name}: {walk.slides.name} has sync = {data['sync']!r}; write true or false")
    sections_of = {s.name: s.section for s in taken} if walk.select else {}
    for step in steps:
        # a small step shows its moves' slides, and with none of its own, its step's
        if not listed.get(step) and not any(listed.get(move.name) for move in moves.get(step, [])) and not listed.get(sections_of.get(step, "")):
            errors.append(f"{name}: {step} has no entry in slides.toml; give it at least one slide")
    for step, entries in data.get("slides", {}).items():
        if not isinstance(entries, list):
            errors.append(f"{name}: {step} in slides.toml must be a list in brackets, for example {step} = [\"talk.md#1\"]")
            continue
        for entry in entries:
            for slide in expand_entry(entry, folder, deck):
                problem = slide_problem(slide, folder, root, used, slide_folders)
                if problem:
                    errors.append(f"{name}: {step}: {problem}")
    for step, doc in data.get("docs", {}).items():
        problem = slide_problem(str(doc).partition("#")[0], folder, root, used, slide_folders)  # a document is checked whole
        if problem:
            errors.append(f"{name}: {step}: {problem}")
    return errors, warnings, used


def own_problems(
    walk: Walk,  # A walk with notes of its own
    main: Path,  # The repository
    root: Path,  # The class folder, which the messages name the files from
) -> tuple[list[str], list[str]]:  # Errors and warnings
    """Check a walk's own notes. Each include is a line of its own, outside a block, and names a step or moves of the
    history; a stop's includes make one place, and the stops follow the history without taking a move twice. The walk's
    own text has no files: list, do$ line or move heading, since its moves and their files come only from the shared notes,
    and each stop, with what it includes, reads as notes do. Warn of a stop with no include, of a stop that leaves out the
    commands, such as `$ just setup`, that its step runs at every Start, and of a stop that runs them twice."""
    from timewalk.export import cue_problems  # here, as timewalk.export imports this module

    errors, warnings = [], []
    file, shared_file = os.path.relpath(walk.notes, root), os.path.relpath(walk.shared, root)
    every = tag_steps(main, walk.tags)
    found_moves = step_moves(main, every)
    order = {step.name: (i, 0) for i, step in enumerate(every)} | {m.name: (i, k) for i, moves in enumerate(found_moves) for k, m in enumerate(moves, 1)}
    text = walk.notes.read_text(encoding="utf-8")
    blocks = note_blocks(text)
    _, stops = own_stops(text)
    shared = parse_notes(walk.shared.read_text(encoding="utf-8"))
    last = None   # where the stop before ends in the history: (step, move), a whole step at its last move
    for stop in stops:
        missing = False
        for line, entry in stop.includes:
            if line - 1 < len(blocks.mode) and blocks.mode[line - 1]:
                errors.append(f"{file}:{line}: {{{{include {entry}}}}} is inside a ::: {blocks.mode[line - 1]} block; "
                              "an include brings moves for both modes, so put it outside the block")
            first, _, end = entry.partition("..")
            for named in filter(None, (first, end)):
                kind = "move" if re.fullmatch(r".+\.\d+", named) else "step"   # as include_span reads it
                if named not in order or (order[named][1] == 0) != (kind == "step"):
                    errors.append(f"{file}:{line}: {{{{include {entry}}}}} names {named}, which is not a {kind} of the history")
                    missing = True
        fence = None
        for at, line in enumerate(stop.body, stop.line + 1):
            mark = re.match(r"^\s*(```+|~~~+)", line)
            if mark:
                fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
            if fence is not None or mark:
                continue
            if line.strip() == "files:":
                errors.append(f"{file}:{at}: a files: list in the walk's own text; the files come with the moves that it includes")
            elif re.match(r"^\s*do\$\s", line):
                errors.append(f"{file}:{at}: a do$ line in the walk's own text; the command that makes a move comes with the move it includes")
            elif re.match(r"^###\s+\S+\.\d+(\s|$)", line):
                errors.append(f"{file}:{at}: a move's heading in the walk's own text; a move comes only from an include, "
                              "for example {{include step-05.3}}")
            elif "{{" in line and re.search(r"\{\{\s*include\b", line) and not INCLUDE.match(line):
                errors.append(f"{file}:{at}: {line.strip()} is not an include line; write it alone on its line, as {{{{include step-05.3}}}}")
        place, problem = stop_range(stop)
        if not stop.includes:
            warnings.append(f"{file}:{stop.line}: the stop ## {stop.title} {problem}")
            continue
        if not place:
            errors.append(f"{file}:{stop.line}: the stop ## {stop.title} {problem}")
            continue
        if missing:
            continue
        first, _, end = place.partition("..")
        span = (order[first], order[end or first]) if order[first][1] else ((order[first][0], 0), (order[first][0], len(found_moves[order[first][0]])))
        if last is not None and span[0] <= last:
            errors.append(f"{file}:{stop.line}: the stop ## {stop.title} includes {place}, which comes before the stop above it in the "
                          "history, or takes a move it took; put the stops in the order of the history")
        last = span[1]
        section = expand_stop(stop, shared)
        problems = note_blocks(section).errors + cue_problems(section)
        for _, problem in dict.fromkeys(problems):
            errors.append(f"the stop ## {stop.title} at {file}:{stop.line}, with the sections it includes from {shared_file}: {problem}")
        step = first.rpartition(".")[0] if order[first][1] else first
        parts = shared.get(step, {}).get("parts", [])
        lead = [p["text"] for p in itertools.takewhile(lambda p: p["kind"] == "command", parts) if not p.get("makes")]
        first_include = stop.includes[0][0] - stop.line - 1
        runs = {p["text"] for p in parse_notes("## x\n" + "\n".join(stop.body[:first_include])).get("x", {}).get("commands", [])}
        if not order[first][1]:   # a whole step brings the commands at its top with it
            for command in (c for c in lead if c in runs):
                warnings.append(f"{file}:{stop.line}: the stop ## {stop.title} runs {command} twice: it includes all of {step}, "
                                f"whose section in {shared_file} runs it first; take $ {command} out of the stop")
            continue
        for command in (c for c in lead if c not in runs):
            warnings.append(f"{file}:{stop.line}: the stop ## {stop.title} starts at a Start of {step}, and does not run "
                            f"{command}, which the section of {step} in {shared_file} runs first; add $ {command} above its first include")
    return errors, warnings


def own_errors_text(
    walk: Walk,  # A walk that timewalk could not take its steps from
    main: Path,  # The repository
    root: Path,  # The class folder
    error: GitError,  # What select_steps said
) -> str:  # What to tell the user: the errors in the walk's own notes, else the error itself
    "Say a mistake in a walk's own notes as timewalk-check says it, and not as a mistake in a select that the user never wrote."
    if walk.shared is None:
        return str(error)
    found = own_problems(walk, main, root)[0]
    return f"walk {walk.id} has mistakes in its notes:\n" + "\n".join(f"  {line}" for line in found) if found else str(error)


def selection_problems(
    walk: Walk,  # A walk with select
    main: Path,  # The repository
    sections: dict[str, dict],  # Its notes, as parse_notes reads them
    taken: list,  # Its steps, from select_steps
    taken_moves: list[list],  # The moves of each
    history: set[str],  # The steps and moves of the whole history
    root: Path | None = None,  # The class folder: a walk with notes of its own names its shared notes from it, as its own are often called notes.md too
) -> tuple[list[str], list[str]]:  # Errors and warnings
    """Check the notes of a walk that takes part of a bigger walk: each section it takes must be there, and the items and
    commands of each are checked as its readers see them. A section it does not take is checked with the whole walk."""
    errors, warnings = [], []
    file = os.path.relpath(walk.shared, root) if walk.shared and root else (walk.shared or walk.notes).name
    tutorial = walk.kind == "tutorial"
    for section in sections:
        if section not in history:
            errors.append(f"{file} has a section ## {section}, which is not a step of the history")
    view = walk_notes(sections, taken, taken_moves, tutorial, True, walk.own_sections())
    every = {m.name: m for found in step_moves(main, tag_steps(main, walk.tags)) for m in found}
    for step, found in zip(taken, taken_moves, strict=True):
        source = step.section or step.name
        if source not in sections:
            errors.append(f"{step.name} takes the section ## {source}, and {file} has none")
            continue
        order = [part["name"] for part in sections[source]["parts"] if part["kind"] == "move"]
        written = set(order)
        for move in [m.name for m in found] or ([step.name] if step.section else []):
            if move not in written:
                errors.append(f"{step.name} takes the section ### {move}, and {file} has none")
        numbers = [int(name.rsplit(".", 1)[1]) for name in order if name.rsplit(".", 1)[0] == source and name.rsplit(".", 1)[1].isdigit()]
        if numbers != sorted(numbers):
            errors.append(f"the ### sections of {source} in {file} are not in the order of the commits: {', '.join(order)}")
        if not tutorial:
            continue
        parts = view.get(step.name, {}).get("parts", [])
        for problem in dict.fromkeys(p for mode in MODES for p in item_problems(main, in_mode(parts, mode), every)):
            warnings.append(problem)
        missing = {mode: moves_without_commands(in_mode(parts, mode)) for mode in MODES}
        for move in dict.fromkeys(m for gone in missing.values() for m in gone):
            modes = [mode for mode in MODES if move in missing[mode]]
            where = "" if len(modes) == len(MODES) else f" in {modes[0]} mode"
            warnings.append(f"{move} has no command{where} in {file}; add one that shows what the move did, for example $ just test")
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def in_mode(
    parts: list[dict],  # The parts of a step's notes
    mode: str,  # "do" or "watch"
) -> list[dict]:  # The parts that the page shows in that mode
    "Keep the parts that a mode shows: those outside any block, and those in the blocks of that mode. So for the items of a files: line."
    kept = [part for part in parts if part.get("mode") in (None, mode)]
    return [{**part, "items": [i for i in part["items"] if i.get("mode") in (None, mode)]} if part["kind"] == "files" else part for part in kept]


def blob_at(
    main: Path,  # The repository
    commit: str,  # A commit, or a commit with ^
    path: str,  # A file of the project
) -> bytes | None:  # The file at that commit, or None if the commit has no such file
    "Read one file of a commit."
    done = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], cwd=main, capture_output=True)
    return done.stdout if done.returncode == 0 else None


def do_line_problems(
    step: str,  # The step whose section it is
    parts: list[dict],  # The parts of its notes
    tutorial: bool,  # Whether the walk is a tutorial
    alone: bool = True,  # Whether the step is a change of one commit after the step before it; else, without moves, it makes nothing
) -> list[str]:  # What is wrong with its do$ lines, each a sentence
    """Check the do$ lines of a step: each makes a move, or a step of one commit, so it is in a move's section, or in the
    section of a step without moves after the first; in the shared text; and before the commands that check the change."""
    problems: list[str] = []
    sections: list[tuple[str | None, list[dict]]] = [(None, [])]
    for part in parts:
        if part["kind"] == "move":
            sections.append((part["name"], []))
        else:
            sections[-1][1].append(part)
    for move, found in sections:
        for part in (part for part in found if part["kind"] == "patch" and tutorial):
            if move is None and len(sections) > 1:   # a step of one commit is a change of its own, and may have patch$ lines
                problems.append(f"{step}: the line patch$ {part['path']} is before the first move; put it in the section of the move "
                                "that changes the file")
            elif part.get("mode"):
                problems.append(f"{move or step}: the line patch$ {part['path']} is in the block ::: {part['mode']}; a patch$ line shows in both "
                                "modes, so put it outside the block")
        makes = [at for at, part in enumerate(found) if part["kind"] == "command" and part.get("makes")]
        if not makes:
            continue
        where = move or step   # a step of one commit is a change of its own, as a move is
        if not tutorial:
            problems.append(f"{where}: a narrative has no moves to make, so its do$ line {found[makes[0]]['text']!r} is shown as what a "
                            "move ran; write it as a $ line, or as text")
            continue
        if move is None and len(sections) == 1 and not alone:   # the first step, or a tag on the commit of the step before
            problems.append(f"{step}: the do$ line {found[makes[0]]['text']!r} is in a step with no change of its own, the first step "
                            "or a tag on the commit of the step before; the learner's clone has its files already, so write the command "
                            "as a $ line")
            continue
        if move is None and len(sections) > 1:
            problems.append(f"{step}: the do$ line {found[makes[0]]['text']!r} is before the first move; a do$ line makes a move, so put "
                            "it in the section of the move it makes")
            continue
        for at in makes:
            if found[at].get("mode"):
                problems.append(f"{where}: the do$ line {found[at]['text']!r} is in the block ::: {found[at]['mode']}; a do$ line shows in "
                                "both modes, a command in do mode and what the move ran in watch mode, so put it outside the block")
        checks = [at for at, part in enumerate(found) if part["kind"] == "command" and not part.get("makes") and not part.get("mode")]
        last_check = checks[-1] if checks else -1
        if last_check >= 0 and makes[-1] > last_check:
            problems.append(f"{where}: the do$ line {found[makes[-1]]['text']!r} comes after the commands that check the move; "
                            "put the commands that make the move first")
    return problems


def show_problems(
    text: str,  # A notes file
) -> list[tuple[int, str]]:  # (line from 1, what is wrong) for each show: line that GitHub ends early
    """Find the show: lines under an item that GitHub reads wrongly: a quote that holds a run of backticks as long as the run
    around it, which timewalk reads but GitHub ends early, and a quote that ends in one backtick too many, which neither reads."""
    lines, found, fence, under = text.splitlines(), [], None, False   # under: the line before is an item, or a line of one
    for at, line in enumerate(lines):
        mark = re.match(r"^\s*(```+|~~~+)", line)   # fenced code as parse_notes reads it
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
            under = False
            continue
        if fence is not None:
            continue
        shown, loose = SHOW_LINE.match(line), re.match(r"^\s+show:\s*`(.*)`\s*$", line)
        if under and shown and len(shown.group(2)) in {len(run) for run in re.findall(r"`+", shown.group(3))}:
            found.append((at + 1, f"GitHub ends this show: line's quote at a backtick inside it; write it as show: {code_span(shown_line(line)[1])}"))
        elif under and not shown and loose and loose.group(1).strip():
            found.append((at + 1, f"this show: line is not a quote as GitHub reads it, and timewalk does not read it; "
                                  f"write it as show: {code_span(loose.group(1))}"))
        under = bool(FILES_ITEM.match(line) or (under and line.startswith(" ") and line.strip()))
    return found


def item_problems(
    main: Path,  # The repository
    parts: list[dict],  # The parts of a step's notes
    moves: dict,  # The moves of the walk, by name
) -> list[str]:  # What is wrong with its files: items, each a sentence
    """Check the files: items of a step's moves against the commits: a diff item names a file that its move changes, a
    file item a file of the move's commit, and each show: line is a line of that change. Each is a warning."""
    problems, current = [], None
    for part in parts:
        if part["kind"] == "move":
            current = part["name"]
        if part["kind"] != "files" or not part.get("items"):
            continue
        for item in part["items"]:
            move = moves.get(item["move"] or current)
            if move is None:
                if item["move"]:
                    problems.append(f"{current or 'a step'}: the item {item['path']} names {item['move']}, which is not a move of the walk")
                continue
            path = item["path"]
            if item["kind"] == "file":
                if subprocess.run(["git", "cat-file", "-e", f"{move.sha}:{path}"], cwd=main, capture_output=True).returncode:
                    problems.append(f"{move.name}: the file item {path} is not in the move's commit")
                continue
            diff = git(main, "--literal-pathspecs", "diff", "--no-color", f"{move.sha}^", move.sha, "--", path)
            if not diff:
                problems.append(f"{move.name}: the diff item {path} is not changed by the move")
                continue
            lines = [line[1:].strip() for line in diff.splitlines() if line[:1] in "+- " and not line.startswith(("+++", "---"))]
            for shown in item["show"]:
                if not any(shown.strip() in line for line in lines if line):
                    problems.append(f"{move.name}: the line {code_span(shown)} is no longer in the change of {path}")
    return problems


def move_parts(
    sections: dict,  # The notes, as parse_notes reads them
    name: str,  # A move, such as step-02.1
) -> list[dict]:  # The parts of its ### section, without its heading
    "Find the parts of one move's section."
    parts, mine = sections.get(name.rsplit(".", 1)[0], {}).get("parts", []), None
    for at, part in enumerate(parts):
        if part["kind"] == "move":
            if mine is not None:
                return parts[mine:at]
            if part["name"] == name:
                mine = at + 1
    return parts[mine:] if mine is not None else []


def source_problems(
    walk: Walk,  # A tutorial
    main: Path,  # The repository
    sections: dict,  # The notes it reads, as parse_notes reads them
    moves: list[Move],  # Its moves
    run: bool,  # Whether to run the do$ lines; without, a move with do$ lines is not checked for where its files come from
    whole: set[str] = frozenset(),  # The steps of one commit among the moves: the whole section of each is its change
) -> tuple[list[str], list[str]]:  # Errors and warnings, each a sentence
    """Check where each file of each move comes from: a do$ line writes it, or a patch$ line names it. A file that a do$
    line writes and the move then changes needs a patch$ line too. Also check that each do$ line runs, and writes only
    files of the move. A patch$ line that names a file that the move does not change is an error; the rest are warnings."""
    from timewalk.facts import DIFF, change_files, may_take, run_problem  # here, as timewalk.facts imports this module

    errors: list[str] = []
    problems: list[str] = []
    scratch = Scratch(main)
    try:
        for move in moves:
            parts = sections.get(move.name, {}).get("parts", []) if move.name in whole else move_parts(sections, move.name)
            commands = [part["text"] for part in parts if part["kind"] == "command" and part.get("makes")]
            files = change_files(main, f"{move.sha}^", move.sha)
            paths = {p for f in files for p in (f["path"], f["old_path"]) if p}
            named = [part["path"] for part in parts if part["kind"] == "patch"]
            for path in dict.fromkeys(named):
                if path not in paths:
                    errors.append(f"{move.name}: the line patch$ {path} names a file that the move does not change")
            if commands and not run:
                continue
            found = observe(main, git(main, "rev-parse", f"{move.sha}^"), move.sha, commands, scratch) if commands else {"commands": [], "left": {}}
            made, outside = sources(main, move.sha, files, found, walk.loose, DIFF)
            failed = run_problem(found["commands"], commands)
            if failed and may_take(commands, [f["path"] for f in files if f["status"] != "deleted"], list(walk.loose)):   # a strict walk's uv
                errors.append(f"{move.name}: do mode cannot take the files that its uv commands write from the commit, because {failed}; "
                              "if the network was down, run the check again when it is up")
            before = len(problems) + len(errors)
            for file in files:
                by, patch = made[file["path"]]
                if file["old_path"] and file["old_path"] not in named and (by == "patch" or patch):   # a rename removes the old file too
                    problems.append(f"{move.name}: {file['path']} is renamed from {file['old_path']}; add patch$ {file['old_path']}, so that the old "
                                    "file goes too")
                if file["path"] in named:
                    continue
                if by == "patch":
                    problems.append(f"{move.name}: {file['path']}: no do$ line writes it, and no patch$ line names it; add patch$ {file['path']}")
                elif patch:
                    problems.append(f"{move.name}: {file['path']}: the do$ line {code_span(commands[by])} writes it, and the move changes it after that; "
                                    f"add patch$ {file['path']} for that change")
            for at, path in outside:
                problems.append(f"{move.name}: the do$ line {code_span(commands[at])} writes {path}, which the move does not change")
            # A do$ line that fails is a problem, but for one such as `ruff check --fix`, which writes its fixes and then exits 1
            # for what it cannot fix: the move's last do$ line, with an exit from 1 to 125 (not a timeout, a signal, or a
            # command not found), that wrote a file of the move that no patch$ line names, in a move with no other problem
            wrote = {by for path, (by, _) in made.items() if isinstance(by, int) and path not in named}
            for at, command in enumerate(found["commands"]):
                excused = 0 < command["exit"] < 126 and at == len(commands) - 1 and at in wrote and len(problems) + len(errors) == before
                if command["exit"] and not excused:
                    code = f" (exit {command['exit']})" if command["exit"] > 0 else ""
                    said = f": {command['said'].rstrip('.')}" if command["said"] else ""
                    problems.append(f"{move.name}: the do$ line {code_span(command['text'])} fails at the commit before the move{code}{said}. "
                                    "A do$ line runs before the move's patches; one that needs a patch gets a move of its own")
    finally:
        scratch.close()
    return errors, problems


def apply_sentences(
    sections: dict,  # Notes, as parse_notes reads them
) -> list[str]:  # A warning for each sentence that asks for **Apply this file** or **Apply the whole move**
    """Find the sentences that old notes wrote for the Apply bar, which is gone: a patch$ line takes their place."""
    from timewalk.facts import chunks_of  # here, as timewalk.facts imports this module

    found = []
    for step, entry in sections.items():
        where = step
        for part in entry.get("parts", []):
            if part["kind"] == "move":
                where = part["name"]
            if part["kind"] != "text":
                continue
            for chunk, code in chunks_of(part["text"]):
                prose = re.sub(r"<!--.*?(-->|$)", "", chunk, flags=re.DOTALL) if not code else ""
                if re.search(r"\*\*Apply (this file|the whole move)\*\*", prose):
                    found.append(f"{where}: a sentence asks for **Apply**, which timewalk no longer has; write a line patch$ PATH for each "
                                 "file that the move changes by a patch")
                    break
    return found


def moves_without_commands(
    parts: list[dict],  # The parts of a step's notes
) -> list[str]:  # The moves whose section has no command
    "Find the moves that give the learner nothing to run: a command anchors a move, in watch mode and in do mode."
    missing, current, has = [], None, False
    for part in [*parts, {"kind": "move", "name": None}]:
        if part["kind"] == "move":
            if current and not has:
                missing.append(current)
            current, has = part["name"], False
        elif part["kind"] == "command" and not part.get("makes"):   # a do$ line makes the move, and anchors nothing
            has = True
    return missing


def unquote_path(
    name: str,  # A path as a diff's header prints it, perhaps in git's quotes: "b/caf\303\251.py"
) -> str:  # The path as it is
    "Undo git's quoting of a path in a diff header: the C escapes, and octal bytes for UTF-8."
    if not (len(name) >= 2 and name.startswith('"') and name.endswith('"')):
        return name
    escapes = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, '"': 34, "\\": 92}
    out, text, i = bytearray(), name[1:-1], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            if text[i + 1:i + 4].isdigit():
                out.append(int(text[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(escapes.get(text[i + 1], ord(text[i + 1])))
            i += 2
            continue
        out.extend(text[i].encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def move_section(
    main: Path,  # The repository
    move,  # A move of a tutorial
    blocks: bool = True,  # Kept for old callers: every draft now has a block for each mode
) -> str:  # A ### section for it, for the author to finish
    """Write a first draft of a move's notes, in the order a reader needs them: the problem and the change, for both
    modes, with a patch$ line for each file; what to read in each patch, in do mode; what to look at, in watch mode; the command that checks it; and last, an item
    for every file of the commit, for the author's words."""
    step = move.name.rsplit(".", 1)[0]
    prefixed = move.subject.startswith((f"{move.name}:", f"{step}:"))   # a move's subject, or the step's own on its last move
    title = move.subject.split(":", 1)[1].strip() if prefixed else move.subject
    # Renames found as the facts find them, so each item names a file that the pages list: a renamed file by its new path
    fields = git(main, "diff-tree", "-z", "-M", "--no-commit-id", "--name-status", "-r", move.sha).split("\x00")
    statuses, at = {}, 0
    while at < len(fields) and fields[at]:
        width = 3 if fields[at][0] in "RC" else 2
        statuses[fields[at + width - 1]] = fields[at][0]
        at += width
    items = [f"- {'file' if status in 'AC' else 'diff'} `{path}`: " for path, status in sorted(statuses.items())]
    paths = sorted({p for found in fields_paths(fields) for p in found})
    patches = [f"patch$ {f'`{path}`' if ' ' in path else path}" for path in paths]
    return "\n".join([
        f"### {move.name} {title}", "",
        "<!-- For both modes: the problem this change solves, and the change. A reader who has never heard of",
        "timewalk reads it. Do not write \"step\", \"move\" or \"commit\". Each file comes from one line below: keep",
        "the patch line of each file changed by hand, and put a do line, the command, in place of the patch lines of",
        "the files that a command writes. -->", "",
        *patches, "",
        "::: do",
        "<!-- What to read in each patch, and where in the file. Copy no code: the patch comes from the project. -->",
        ":::", "",
        "::: watch",
        "<!-- What the change does, and where to see it. Say it; do not only send the reader to a diff. -->",
        ":::", "",
        "<!-- A command that checks the result, and what it prints, for example: $ just test -->", "",
        "files:", *items, ""])


def fields_paths(
    fields: list[str],  # What git diff-tree -z --name-status printed, split at its NULs
) -> list[tuple[str, ...]]:  # The paths of each change: one, or for a rename or a copy, the old one and the new one
    "Read the paths of a --name-status list, both paths of a rename, so that a patch$ line names each file a move changes."
    out, at = [], 0
    while at < len(fields) and fields[at]:
        width = 3 if fields[at][0] in "RC" else 2
        out.append(tuple(fields[at + 1:at + width]) if fields[at][0] == "R" else (fields[at + width - 1],))
        at += width
    return out


def add_move_sections(
    walk: Walk,  # A tutorial walk
    main: Path,  # The repository
    blocks: bool = False,  # Draft a block for each mode in each new move
) -> tuple[str, list[str], list[str]]:  # The notes with the missing move sections added, the moves added, and their drafts
    """Add a section for each step, and a draft section for each move, that the notes do not have yet. A step's heading
    takes its title from the first line of its tag's message. Sections already there stay as they are."""
    text = walk.notes.read_text(encoding="utf-8") if walk.notes and walk.notes.is_file() else ""
    if walk.select:   # a walk that takes part of a bigger one: the sections it takes, in the notes of the whole
        picked = tag_steps(main, walk.tags)
        taken, taken_moves = select_steps(main, walk.tags, walk.select, True)
        wanted: dict[str, list] = {}
        for step, found in zip(taken, taken_moves, strict=True):
            wanted.setdefault(step.section or step.name, []).extend(found)
        pairs = [(step, wanted[step.name]) for step in picked if step.name in wanted]
    else:
        picked = pick_steps(tag_steps(main, walk.tags), walk.steps)
        pairs = list(zip(picked, step_moves(main, picked), strict=True))
    added: list[str] = []
    drafts: list[str] = []
    for step, moves in pairs:
        if step.name not in parse_notes(text):
            first = (step.note or step.subject).strip().split("\n")[0].strip()
            title = first.split(":", 1)[1].strip() if first.startswith(f"{step.name}:") else first   # a lightweight tag: its commit's subject
            heading = f"## {step.name} {title}".rstrip() + "\n"
            # Before the next step that has a section, so that the notes keep the order of the steps; else at the end.
            later = [s.name for s in picked[picked.index(step) + 1:]]
            spot = next((m for name in later if (m := re.search(rf"^##\s+{re.escape(name)}(\s|$)", text, re.MULTILINE))), None)
            if spot:
                text = text[:spot.start()] + heading + "\n" + text[spot.start():]
            else:
                text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + heading
            added.append(step.name)
            drafts.append(heading)
        written = {part["name"] for part in parse_notes(text).get(step.name, {}).get("parts", []) if part["kind"] == "move"}
        new = [move_section(main, move, blocks) for move in moves if move.name not in written]
        if not new:
            continue
        added += [move.name for move in moves if move.name not in written]
        drafts.extend(new)
        raw = parse_notes(text).get(step.name, {}).get("raw", "")
        drafted = {move.name: section for move, section in zip([m for m in moves if m.name not in written], new, strict=True)}
        text = replace_section(text, step.name, "\n" + place_moves(raw, step.name, drafted))
    return text, added, drafts


def place_moves(
    raw: str,  # A step's section, as written, below its heading
    step: str,  # The step
    drafts: dict[str, str],  # New move sections, by move name
) -> str:  # The section with each new move before the first move written after it in the commits, or at the end
    "Put drafted move sections in the order of the commits, and leave the sections already written as they are."
    def number(name: str) -> int:
        return int(name.rsplit(".", 1)[1])

    lines, out, fence = raw.rstrip().split("\n") if raw.strip() else [], [], None
    waiting = sorted(drafts, key=number)
    for line in lines:
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if mark:
            fence = mark.group(1)[0] if fence is None else (None if mark.group(1)[0] == fence else fence)
        heading = re.match(rf"^###\s+{re.escape(step)}\.(\d+)(\s|$)", line) if fence is None and not mark else None
        while heading and waiting and number(waiting[0]) < int(heading.group(1)):
            out += [drafts[waiting.pop(0)].rstrip("\n"), ""]
        out.append(line)
    if waiting:
        out += ([""] if out else []) + ["\n\n".join(drafts[name].rstrip("\n") for name in waiting)]
    return "\n".join(out) + "\n"


def notes_main() -> None:
    "Add a section for each step, and a draft ### section for each move, that a tutorial's notes do not have."
    parser = argparse.ArgumentParser(description="Draft the notes of a tutorial: a section for each step, and for each move what its "
                                                 "commit changed, for you to finish.")
    parser.add_argument("repo", type=Path, help="the repository that the class walks through")
    parser.add_argument("--toc", type=Path, required=True, help="the table of contents, toc.toml")
    parser.add_argument("--walk", required=True, help="the id of a tutorial walk")
    parser.add_argument("--write", action="store_true", help="write the sections into the notes file; without it, print them")
    parser.add_argument("--blocks", action="store_true", help="kept for old scripts: every draft now has a block ::: do and a block ::: watch")
    args = parser.parse_args()
    main_repo = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel"))
    try:
        walk = next((w for w in load_toc(args.toc.resolve()) if w.id == args.walk), None)
    except TocError as error:
        raise SystemExit(f"timewalk-notes: {error}") from None
    if walk is not None and walk.notes is None and walk.folder is not None:
        walk = dataclasses.replace(walk, notes=walk.folder / "notes.md")   # a walk's folder without notes yet: start them
    if walk is not None and walk.shared is not None:
        walk = dataclasses.replace(walk, notes=walk.shared)   # its moves' sections are in the shared notes, where it drafts them
    if walk is None or walk.kind != "tutorial" or walk.notes is None:
        raise SystemExit(f"timewalk-notes: {args.walk} is not a tutorial walk with a notes file or a folder in {args.toc}")
    text, added, drafts = add_move_sections(walk, main_repo, args.blocks)
    if not added:
        print(f"timewalk-notes: every step and move has a section in {os.path.relpath(walk.notes, args.toc.resolve().parent)}")
        return
    if args.write:
        if in_a_copy(walk.notes, main_repo):
            raise SystemExit(f"timewalk-notes: {walk.notes} is inside the project or its replay copy; timewalk never writes there. "
                             "Keep the notes in the class folder")
        crlf = walk.notes.is_file() and b"\r\n" in walk.notes.read_bytes()   # keep a file's Windows line endings
        walk.notes.write_text(text, encoding="utf-8", newline="\r\n" if crlf else None)
        print(f"timewalk-notes: added {len(added)} sections to {walk.notes}: {', '.join(added)}")
    else:
        print("\n".join(drafts))
        print(f"timewalk-notes: {len(added)} sections to add ({', '.join(added)}); run again with --write to write them")


def slide_problem(
    slide: str,  # One slide, as the manifest names it
    folder: Path,  # The manifest's folder
    root: Path,  # The class folder
    used: set[Path],  # Slide files seen so far; this one is added
    slide_folders: list[Path] | None = None,  # The folders the page serves slides from, when there is a table of contents
) -> str | None:  # What is wrong with it, or None
    "Check that a slide's file exists inside the class folder, and that its slide or page number is in range."
    path, _, fragment = slide.partition("#")
    if "://" in path or re.fullmatch(r"\d+(-\d+)?", path):
        return None
    file = Path(os.path.normpath(folder / path))
    if not inside(file, root):
        return f"{path} is outside the class folder {root}"
    if slide_folders and not any(inside(file, f) for f in slide_folders):
        return f"{path} is not in a slides folder of a walk, and the page serves slides only from those"
    if not file.is_file():
        return f"{path} does not exist"
    used.add(file)
    if file.suffix.lower() in (".md", ".markdown"):
        pictures = pictures_of(file)
        used |= pictures
        outside = sorted(str(p) for p in pictures if slide_folders and not any(inside(p, f) for f in slide_folders))
        if outside:
            return f"{path} shows {os.path.relpath(outside[0], folder)}, which is not in a slides folder of a walk, so the page cannot load it"
    if file.suffix.lower() in (".md", ".markdown") and re.fullmatch(r"\d+", fragment):
        count = len(split_slides(file.read_text(encoding="utf-8")))
        if int(fragment) > count:
            return f"{path} has {count} slides; the entry asks for slide {fragment}"
    page = re.fullmatch(r"page=(\d+)", fragment)
    if file.suffix.lower() == ".pdf" and page:
        from pypdf import PdfReader

        try:
            count = len(PdfReader(file).pages)
        except Exception as error:  # a damaged or encrypted PDF: say so, as a sentence
            return f"{path} cannot be read as a PDF ({type(error).__name__})"
        if int(page.group(1)) > count:
            return f"{path} has {count} pages; the entry asks for page {page.group(1)}"
    return None


def pictures_of(
    deck: Path,  # A Markdown slide file
) -> set[Path]:  # The local files it shows or links to, as `![...](path)`, `[...](path)` or `src="path"`
    "Find the files that a Markdown deck uses, so that a picture used inside a slide counts as used."
    text = deck.read_text(encoding="utf-8")
    found = re.findall(r"\]\(\s*<?([^)\s>]+)", text) + re.findall(r"""src\s*=\s*["']([^"']+)["']""", text)
    return {Path(os.path.normpath(deck.parent / link.partition("#")[0])) for link in found if "://" not in link and not link.startswith(("#", "mailto:"))}


def check(
    walks: list[Walk],  # The walks of a class
    main: Path,  # The repository
    root: Path,  # The class folder
    replay: Path | None = None,  # The replay copy
    run: bool | None = None,  # Whether to run the do$ commands; None: unless TIMEWALK_NO_RUN is set
) -> tuple[list[str], list[str]]:  # Errors and warnings, each a sentence
    "Check every walk, and warn of slide files in the walks' slides folders that no walk uses."
    errors: list[str] = []
    warnings: list[str] = []
    used: set[Path] = set()
    folders = [walk.slides.parent for walk in walks if walk.slides] if len(walks) > 1 or walks[0].id != "default" else None
    for folder in sorted(set(folders or [])):
        # The page serves every file of a slides folder: the notes, with their cues, and the table must not be in one.
        if os.path.normpath(folder) == os.path.normpath(root):
            errors.append(f"a slides manifest is at the top of the class folder, {root}; put it in a slides folder of its own, "
                          "so that the page does not serve the notes and toc.toml")
        for walk in walks:
            if walk.notes is not None and inside(walk.notes, folder):
                errors.append(f"walk {walk.id}: its notes, {walk.notes}, are in a slides folder, which the page serves; move them out")
    for walk in walks:
        more_errors, more_warnings, files = check_walk(walk, main, root, replay, folders, run)
        errors += more_errors
        warnings += more_warnings
        used |= files
    for folder in sorted({walk.slides.parent for walk in walks if walk.slides and walk.slides.is_file()}):
        if os.path.normpath(folder) == os.path.normpath(root):
            continue  # a manifest at the top of the class folder: everything there is not a slide
        for file in sorted(folder.rglob("*")):
            if file.is_file() and file.suffix.lower() in SLIDE_FILES and Path(os.path.normpath(file)) not in used:
                warnings.append(f"{file.relative_to(root) if inside(file, root) else file} is in a slides folder, and no walk uses it")
    return errors, warnings


def main() -> None:
    "Check the walks of a class folder, and print what is wrong. The exit code is 1 when there is an error."
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", type=Path, help="the repository that the class walks through")
    parser.add_argument("--toc", type=Path, help="the table of contents, toc.toml, of a class with several walks")
    parser.add_argument("--notes", type=Path, help="without a table: the notes file of the one walk")
    parser.add_argument("--slides", type=Path, help="without a table: the slides manifest of the one walk")
    parser.add_argument("--tags", default="step-*", help="without a table: the glob of the tags that mark steps (default: step-*)")
    parser.add_argument("--replay", type=Path, help="where the replay copy is, if timewalk runs with --replay (default: <repo>-replay)")
    parser.add_argument("--no-run", action="store_true", help="do not run the do$ commands to see which files they write; "
                        "a move with do$ lines is then not checked for where its files come from")
    args = parser.parse_args()
    main_repo = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel"))
    replay = args.replay.resolve() if args.replay else main_repo.parent / f"{main_repo.name}-replay"
    if args.toc:
        if args.notes or args.slides:
            raise SystemExit("timewalk-check: give --toc, or --notes and --slides, not both")
        try:
            walks = load_toc(args.toc.resolve())
        except TocError as error:
            raise SystemExit(f"timewalk-check: {error}") from None
        root = args.toc.resolve().parent
    else:
        notes = args.notes.resolve() if args.notes else None
        slides = args.slides.resolve() if args.slides else None
        walks = [Walk("default", "default", "narrative", notes, slides, args.tags, None, "manual")]
        root = slides.parent if slides else (notes.parent if notes else Path.cwd())
    errors, warnings = check(walks, main_repo, root, replay, False if args.no_run else None)
    for line in errors:
        print(f"error: {line}")
    for line in warnings:
        print(f"warning: {line}")
    print(f"timewalk-check: {len(walks)} walk{'s' if len(walks) != 1 else ''}, {len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
