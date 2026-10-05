# timewalk

A step browser for teaching a project by replaying how it was built. Each step is a git tag (by default
`step-*`), the tag's annotated message is the note the audience sees, and the projector page shows slides,
the files at that step, what the step changed, and real terminals in the repository as it was then. The
presenter page (`/presenter`) is the same page with a clock band and the private notes beside it; what both
show (step, slide, layout, open file, terminal tab) is kept by the server, so they always agree.

The user documentation is the site in `docs/`, published to rahuldave.com/timewalk; the README is a
short front page that links into it. Keep both true when behaviour changes, and retake the screenshots
(`just screenshots`) when the pages look different.

## Who uses it, and why it is a separate repository

It was built for `~/Projects/babykev-class`, the class kit for `~/Projects/babykev`. That kit's
`just present` runs `timewalk.py` on babykev with its `notes.md` and `slides/slides.toml`, and its
`just handout` runs `slides_pdf.py`.

- **Teaching infrastructure never goes in the repository students read.** That is why timewalk, the
  slides and the notes live outside babykev. Do not add anything here that a student repo would need.
- **Keep timewalk generic.** Nothing babykev-specific in the code. A class need becomes a general feature
  (the `runs2$` to `runs9$` tabs came from running an ablation beside a baseline).
- The way a class history is made, in babykev-class: one commit per step on linear `main`, an annotated
  tag `step-NN` per commit whose message is the teaching note, fixes folded back into earlier steps and
  the tags re-pointed (`just retag` there). timewalk must keep working when tags move between sessions.
- A possible future mode is in `babykev-class/NARRATIVE_IDEA.md` point 4: follow a branch being rebuilt
  live instead of checking out tags. Not started.

## The demo

The demo is shaped like a real class: the history in its own repository, the notes and slides outside it.
To change the sample's history, work in the submodule (or `~/Projects/timewalk-demo`), keep one commit per
step with an annotated `step-NN` tag, and push its `main` and tags with `--force` only after asking. Then
commit the new submodule pointer here, and check `demo/notes.md` and `demo/slides/slides.toml` against the
steps.

## Rules the tool must keep

These were set by Rahul. Each has tests; do not weaken them.

- **The repository you point at is never moved.** Stepping happens in a `<repo>-replay` git worktree.
  `--in-place` is the only exception.
- **An untracked file is never deleted or overwritten.** Run outputs (`.venv`, `mlflow.db`, `runs/`) must
  survive moving between steps. If a later step has a tracked file where an untracked one sits, refuse.
- **An edit is never discarded.** Moving with edits asks first, then `git stash`es them with the step name.
  The one exception is asked for by name: `--discard-edits`, for a throwaway replay copy, makes a move
  `git checkout --force`, and both pages warn in the step bar. It is refused with `--in-place`.
- **The file view cannot write.** No route changes a file.
- **Localhost only, a fresh token per launch, other Host headers refused.** Every page, API call, socket
  and static asset needs the token. Only the presenter page asks for the notes.

## Layout

| File | What it is |
|---|---|
| `timewalk.py` | The whole server, one file: `git()`, `Repo` (steps, worktree, moves, diffs, reads), notes and slides parsers, `Terminal` (a pty), `Hub` (events to pages), `make_app` (Starlette routes) |
| `slides_pdf.py` | The PDF handout, drawn by headless Chrome or Edge |
| `static/` | `index.html`/`app.js` (the page, at `/` and, with notes and clock, at `/presenter`), `print.*` (for the PDF), `common.js`, `app.css` |
| `static/vendor/` | ghostty-web, highlight.js, marked, each with its licence. Vendored: do not edit |
| `tests/test_timewalk.py` | The git layer, notes and slides, the app's guards, a real terminal |
| `demo/timewalk-demo` | A git submodule: the sample repository, five tagged steps, at github.com/rahuldave/timewalk-demo. Its replay copy, `demo/timewalk-demo-replay`, is ignored |
| `docs/` | The site: one Markdown page per topic, `build.py` (the order is its `PAGES` list), `site.css`, `screenshots.py`, `images/`. `_site/` is built and ignored. `.github/workflows/pages.yml` publishes it |
| `demo/notes.md`, `demo/slides/` | The demo's presenter notes and slides. They stay here, outside the sample, as a class kit does |

There is no `pyproject.toml`. The scripts carry their dependencies as inline script metadata (PEP 723)
and run with `uv run`. The test dependencies are listed in the `justfile`.

## Commands

Use the recipes, not the commands behind them.

```
just test            # all tests; extra arguments go to pytest: just test -k slides
just lint            # ruff
just demo            # fetch the sample submodule if needed, and open it
just walk <repo> ... # run timewalk on a repository
just pdf <manifest> -o out.pdf --title "..."
just screenshots     # retake docs/images from a throwaway clone of the demo
just site            # build docs/_site and serve it at http://127.0.0.1:8000
```

The test that hung once was a terminal test waiting on output from `/api/type`. Terminal tests type
through the socket.

## Conventions

- Python 3.11+, ruff with line length 150 (`ruff.toml`). Plain stdlib plus Starlette and uvicorn.
- Dataclass fields get the same trailing comment. Every function is typed and documented in the docments style: one comment per parameter and on the
  return, and a one-line docstring.

  ```python
  def git(
      cwd: Path,  # Directory to run git in
      *args: str,  # Arguments to git
  ) -> str:  # What git printed, without the trailing newline
      "Run git and return its output, raising `GitError` with git's own message when it fails."
  ```
- Prose (README, docstrings, commit messages, UI text) is plain: short sentences, concrete names, no
  jargon. Match the README's voice.
- A new notes-line kind or slide-entry kind needs: the parser, a test, the README table, and the
  presenter or projector code that uses it.

## Checking a change

1. `just test` and `just lint`.
2. For anything in `static/`, drive both pages in headless Chrome through a throwaway clone of the demo
   (`git clone ~/Projects/timewalk-demo` into the scratchpad), not `demo/timewalk-demo-replay`, which may
   hold Rahul's own edits. Go through every step and slide,
   and type in a terminal. Playwright with `channel="chrome"` works. Safari and Firefox are not checked.

## Commits

- End every commit message with the line `Coded using Claude`. No `Co-Authored-By` or session lines.
  This is Rahul's rule and overrides the default attribution.
- Subject says what changed; the body says why, in the README's voice.
- **Ask before every commit.** Once Rahul approves a commit, push `main` to github.com/rahuldave/timewalk without asking again.
