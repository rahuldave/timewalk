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
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from timewalk import GitError, expand_entry, git, parse_notes, pick_steps, split_slides, step_moves, tag_steps

KINDS = ("narrative", "tutorial")
WALK_KEYS = ("id", "title", "kind", "folder", "notes", "slides", "steps", "tags", "setup", "sync")
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
    sync: bool = True  # In a tutorial: a move shows its first slide, and a move's slide makes the move


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
        for key in ("title", "folder", "notes", "slides", "tags", "setup"):
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
            raise TocError(f"{where}: a tutorial takes every step of its tags, so that the moves between them are its own; "
                           "for a part, give it tags of its own, for example tags = \"part-*\"")
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
        walks.append(Walk(ident, entry.get("title", ident), kind, notes.resolve() if notes else None, slides.resolve() if slides else None,
                          entry.get("tags", "step-*"), steps, "manual", entry.get("sync", True)))
    return walks


def steps_of(
    walk: Walk,  # A walk
    main: Path,  # The repository
) -> list[str]:  # The names of its steps, in order
    "List the steps of a walk. Raises GitError when its tags match nothing or a named step is not a tag."
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


def check_walk(
    walk: Walk,  # The walk to check
    main: Path,  # The repository
    root: Path,  # The class folder: every slide must be inside it
    replay: Path | None = None,  # The replay copy, where notes and slides must not be either
    slide_folders: list[Path] | None = None,  # With a table of contents: the walks' slides folders, the only ones the page serves
) -> tuple[list[str], list[str], set[Path]]:  # Errors, warnings, and the slide files the walk uses
    "Check one walk's notes and slides against its steps and against each other."
    errors: list[str] = []
    warnings: list[str] = []
    used: set[Path] = set()
    name = f"walk {walk.id}"
    try:
        steps = steps_of(walk, main)
    except GitError as error:
        return [f"{name}: {error}"], [], used
    moves: dict[str, list] = {}
    if walk.kind == "tutorial":
        picked = pick_steps(tag_steps(main, walk.tags), walk.steps)
        moves = {step.name: found for step, found in zip(picked, step_moves(main, picked), strict=True)}
        for found in moves.values():
            for move in found[:-1]:  # the last move is the step's own commit, with the step's subject
                if not move.subject.startswith(f"{move.name}:"):
                    errors.append(f"{name}: the commit {move.sha[:7]}, move {move.name}, has the subject {move.subject!r}; start it with '{move.name}:'")
    names = set(steps) | {move.name for found in moves.values() for move in found}
    for label, path in (("notes", walk.notes), ("slides", walk.slides)):
        if path is not None and any(inside(path, folder) for folder in (main, replay) if folder):
            errors.append(f"{name}: its {label}, {path}, are inside the repository or its replay copy; keep them in the class folder")
    if walk.notes is not None:
        if not walk.notes.is_file():
            errors.append(f"{name}: the notes file {walk.notes} does not exist")
        else:
            sections = parse_notes(walk.notes.read_text(encoding="utf-8"))
            for section in sections:
                if section not in steps:
                    errors.append(f"{name}: {walk.notes.name} has a section ## {section}, which is not a step of the walk")
            for step in steps:
                if step not in sections:
                    warnings.append(f"{name}: {step} has no section in {walk.notes.name}")
            for step, found in moves.items():
                written = [part["name"] for part in sections.get(step, {}).get("parts", []) if part["kind"] == "move"]
                wanted = [move.name for move in found]
                for extra in sorted(set(written) - set(wanted)):
                    errors.append(f"{name}: {walk.notes.name} has a section ### {extra}, which is not a move of {step}")
                for missing in [m for m in wanted if m not in written]:
                    errors.append(f"{name}: {missing} has no section ### {missing} in {walk.notes.name}")
                kept = [m for m in written if m in wanted]
                if kept != [m for m in wanted if m in kept]:
                    errors.append(f"{name}: the ### sections of {step} in {walk.notes.name} are not in the order of the commits: {', '.join(kept)}")
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
    for step in steps:
        if not listed.get(step):
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
        more_errors, more_warnings, files = check_walk(walk, main, root, replay, folders)
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
    errors, warnings = check(walks, main_repo, root, replay)
    for line in errors:
        print(f"error: {line}")
    for line in warnings:
        print(f"warning: {line}")
    print(f"timewalk-check: {len(walks)} walk{'s' if len(walks) != 1 else ''}, {len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
