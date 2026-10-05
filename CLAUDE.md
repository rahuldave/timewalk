# timewalk

timewalk is a step browser. It teaches a project through a replay of how its authors built it. Each step
is a git tag, by default `step-*`. The message of an annotated tag is the note that the audience sees.

timewalk serves one page, at `/`. The page shows slides, the files at the step, what the step changed,
and real terminals in the repository as it was then. With `--notes`, it shows the notes of each step in a
column on the right. With `--clock`, it shows a clock band across the top. The old address `/presenter`
sends the browser on to `/`.

The page can be open in several windows, for example one on the projector and one on the screen of the
presenter. The server keeps the shared state, so every window shows the same step, slide, layout, open
file, view and terminal tab. Each window keeps its scroll position, keyboard focus and whether the notes show
(sessionStorage); the browser keeps theme, text size, terminal height and run on click (localStorage). A shell has one size, and the window that the
presenter last typed in sets it.

Write every site page by the nossaifd writing guide, `docs/style/GUIDE.md`. Use the glossary and the
linked headings in `docs/style/README.md`. Check with `just style`. Define jargon where it first appears.

The user documentation is the site in `docs/`, published to rahuldave.com/timewalk. The README is a short
front page that links into the site. Keep both true when the behaviour changes. When the page looks
different, take the screenshots again with `just screenshots`.

## Who uses it, and why it is a separate repository

Rahul made timewalk for `~/Projects/babykev-class`, the class kit for `~/Projects/babykev`. The kit has
two recipes:

- `just present` runs `timewalk` on babykev with its `notes.md` and `slides/slides.toml`.
- `just handout` runs `timewalk-pdf`.

Keep these points:

- **Teaching infrastructure never goes in the repository that students read.** So timewalk, the slides
  and the notes live outside babykev. Do not add anything here that a student repository would need.
- **The notes live outside the repository that the class walks through.** timewalk refuses a notes file
  inside the repository or inside its replay copy. A move would change the file, or throw its edits away.
  The notes belong in the class folder or in the repository of the class material.
- **Keep timewalk generic.** Put nothing specific to babykev in the code. A need of a class becomes a
  general feature. For example, the `runs2$` to `runs9$` tabs came from an ablation beside a baseline.
- **Tags move between sessions, and timewalk must keep working.** In babykev-class, the history of a class
  has one commit per step on a linear `main`. Each commit has an annotated tag `step-NN`, and its message
  is the note for teaching. Fixes go back into earlier steps, and `just retag` there points the tags again.
- **A possible future mode is in `babykev-class/NARRATIVE_IDEA.md`, point 4.** It would follow a branch
  that the presenter rebuilds live, and not check out tags. Nobody has started it.

## The demo

The demo has the shape of a real class. The history is in its own repository, and the notes and slides are
outside it. To change the history of the sample, do these steps in order:

1. Work in the submodule, or in `~/Projects/timewalk-demo`.
2. Keep one commit per step, with an annotated `step-NN` tag.
3. Ask first. Then push its `main` and its tags with `--force`.
4. Commit the new submodule pointer here.
5. Check `demo/notes.md` and `demo/slides/slides.toml` against the steps.

## Rules the tool must keep

Rahul set these rules. Each rule has tests. Do not weaken them.

- **The repository you point at never moves.** timewalk steps in a `<repo>-replay` git worktree.
  `--in-place` is the only exception.
- **timewalk never deletes or overwrites an untracked file.** Run outputs, for example `.venv`,
  `mlflow.db` and `runs/`, must stay through every move. If a later step has a tracked file where an
  untracked file sits, refuse.
- **timewalk never discards an edit.** A move with edits asks first, and then runs `git stash` with the
  step name. The one exception is the flag `--discard-edits`, for a replay copy that you throw away. With
  it, a move runs `git checkout --force`, and the step bar of every window warns. timewalk refuses it with
  `--in-place`.
- **The file view cannot write.** No route changes a file in the repository or the replay copy. The one
  file that the page writes is the notes file. **Save** in the notes column sends one section to
  `POST /api/notes`. The server writes only that section, and refuses if the section changed in the file
  since the page read it. The terminals write files as any terminal does.
- **Localhost by default, a new token at each start, and other Host headers refused.** Every page, API
  call, socket and static asset needs the token. `--host` (for a cloud machine) listens beyond this machine:
  timewalk then warns at start and accepts any Host header, so the token is the only guard. Keep the
  default, and keep recommending an SSH tunnel over `--host 0.0.0.0` in the docs.

## Layout

| File | What it is |
|---|---|
| `src/timewalk/__init__.py` | The whole server, one module: `git()`, `Repo` (steps, worktree, moves, diffs, reads), notes and slides parsers, `Terminal` (a pty), `Hub` (events to pages), `make_app` (Starlette routes) |
| `src/timewalk/slides_pdf.py` | The command `timewalk-pdf`: the PDF of the slides and, with `--with-notes`, the notes, drawn by Chrome or Edge through Playwright. The **PDF** button calls it through `/api/pdf` |
| `src/timewalk/static/` | `index.html`/`app.js` (the page, at `/`), `print.*` (for the PDF), `common.js`, `app.css` |
| `src/timewalk/static/vendor/` | ghostty-web, highlight.js, marked, each with its licence. Vendored: do not edit |
| `tests/test_timewalk.py` | The git layer, notes and slides, the guards of the app, a real terminal |
| `demo/timewalk-demo` | A git submodule: the sample repository, five tagged steps, at github.com/rahuldave/timewalk-demo. Its replay copy, `demo/timewalk-demo-replay`, is ignored |
| `docs/` | The site: one Markdown page per topic, `build.py` (the order is its `PAGES` list), `site.css`, `screenshots.py`, `images/`. `_site/` is built and ignored. `.github/workflows/pages.yml` publishes it |
| `demo/notes.md`, `demo/slides/` | The notes and slides of the demo. They stay here, outside the sample, as in a class kit |

timewalk is a package, `src/timewalk`, built with hatchling from `pyproject.toml`. It gives two commands,
`timewalk` and `timewalk-pdf`. Its dependencies are Starlette, uvicorn, websockets, Playwright and pypdf;
the tests use the `dev` group. In a clone, run `uv run timewalk`. Users run it from GitHub with
`uvx --from git+https://github.com/rahuldave/timewalk@v1.0.2 timewalk`, so it is not on PyPI, by Rahul's choice. When a change must reach users,
raise the version in `pyproject.toml`, tag the commit `vX.Y.Z`, push the tag, and update the pin in the
docs and in the kits.

## Commands

Use the recipes, and not the commands behind them.

```
just test            # all tests; extra arguments go to pytest: just test -k slides
just lint            # ruff
just demo            # fetch the sample submodule if needed, and open it with --discard-edits and --clock
just walk <repo> ... # run timewalk on a repository
just pdf <manifest> -o out.pdf --title "..."
just screenshots     # retake docs/images from a throwaway clone of the demo
just site            # build docs/_site and serve it at http://127.0.0.1:8000
```

If port 8765 is in use, timewalk stops with a message. Give `--port` with another number.

A terminal test once hung while it waited for output from `/api/type`. The terminal tests type through the
socket.

## Conventions

- Python 3.11+, ruff with line length 150 (`ruff.toml`). Plain stdlib, with Starlette, uvicorn, Playwright
  and pypdf.
- Dataclass fields get the same trailing comment. Type and document every function in the docments style.
  Give one comment per parameter and one on the return, and a docstring of one line.

  ```python
  def git(
      cwd: Path,  # Directory to run git in
      *args: str,  # Arguments to git
  ) -> str:  # What git printed, without the trailing newline
      "Run git and return its output, raising `GitError` with git's own message when it fails."
  ```
- Keep prose plain in the README, docstrings, commit messages and the text of the page. Write short
  sentences and concrete names, with no jargon. Match the voice of the README.
- A new kind of notes line or slide entry needs four things. They are the parser, a test, the table in the
  site, and the code of the page that uses it.

## Checking a change

1. Run `just test` and `just lint`.
2. For anything in `src/timewalk/static/`, drive the page in headless Chrome, in two windows, through a throwaway clone
   of the demo. Run `git clone ~/Projects/timewalk-demo` into the scratchpad. Do not use
   `demo/timewalk-demo-replay`, which can hold edits of Rahul.
3. Go through every step and slide, and type in a terminal. Playwright with `channel="chrome"` works.
   Nobody checks Safari or Firefox.

## Commits

- End every commit message with the line `Coded using Claude`. Add no `Co-Authored-By` or session lines.
  Rahul set this rule, and it overrides the default attribution.
- The subject says what changed. The body says why, in the voice of the README.
- **Ask before every commit.** After Rahul approves a commit, push `main` to github.com/rahuldave/timewalk
  and do not ask again.
