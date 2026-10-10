# timewalk

timewalk is a step browser. It teaches a project through a replay of how its authors built it. Each step
is a git tag, by default `step-*`. The message of an annotated tag is the note that the audience sees.

timewalk serves one page, at `/`. The page shows slides, the files at the step, what the step changed,
and real terminals in the repository as it was then. With `--notes`, it shows the notes of each step in a
column on the right. With `--clock`, it shows a clock band across the top. The old address `/presenter`
sends the browser on to `/`.

The page can be open in several windows, for example one on the projector and one on the screen of the
presenter. The server keeps the shared state, so every window shows the same step, slide, layout, open
file, view and terminal tab. Scrolls of the slide, the file and the notes are relayed to every other window over
the events socket, as a fraction. Each window keeps its keyboard focus and whether the notes and the cues show
(sessionStorage); `?room=1` (the Room button) is the class's window: no cues, no clock band, no PDF or Edit; the browser keeps theme, text size, terminal height and run on click (localStorage). A shell has one size, and the window that the
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
- `just pdf` runs `timewalk-pdf`: the slides, one page each, and no notes.

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

- **The repository you point at never moves, and is never written to.** timewalk steps in a `<repo>-replay`
  clone on the branch `timewalk/replay`. It writes no config, hook, branch, tag or stash in your repository;
  what a learner does in the copy stays there, and a move keeps a learner's commits on `timewalk/saved/<place>`.
  A replay worktree made by an older version still works, on a detached HEAD. `--in-place` is the only exception.
- **timewalk never loses an untracked file.** Run outputs, for example `.venv`, `mlflow.db` and `runs/`, stay through
  every move. When a step has a tracked file where an untracked file sits, a move in a replay clone first keeps the
  untracked file on a branch `timewalk/saved/<place>`, then replaces it; ignored or not, it is never lost. `--in-place`,
  and a replay worktree of an older version, refuse the move instead.
- **timewalk never loses an edit, and a move in a replay clone never asks.** Before it checks out, a move commits the
  learner's uncommitted work (edits, and new files that git does not ignore) with a temporary index, so the files, the
  index and the stash stay as they are, and puts a branch `timewalk/saved/<place>` on it, with any commits the learner
  made. Your window then says where the work is kept; the Room window does not. `--in-place` asks first and stashes,
  as before. `--discard-edits` is no longer needed; only a replay worktree of an older version still throws edits away
  with it.
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
| `src/timewalk/__init__.py` | The whole server, one module: `git()`, `Repo` (steps, the replay clone and its fetches, moves, saved branches, diffs, reads), notes and slides parsers, `Terminal` (a pty), `Hub` (events to pages), `make_app` (Starlette routes) |
| `src/timewalk/walks.py` | Several walks: `load_toc` reads `toc.toml`, `check` checks notes and slides against the steps, and the commands `timewalk-check` and `timewalk-notes` (a tutorial's notes: a `## step-NN Title` section for each step, and drafts of its `### step-NN.k` sections, from each move's diff) |
| `src/timewalk/slides_pdf.py` | The command `timewalk-pdf`: the PDF of the slides and, with `--with-notes`, the notes, drawn by Chrome or Edge through Playwright. `--brand DIR` gives it the look of a brand folder (font, colours, cover, dividers); only this command and `print.js` know brands, and the page does not. The **PDF** button calls it through `/api/pdf`, with no brand |
| `src/timewalk/static/` | `index.html`/`app.js` (the page, at `/`), `print.*` (for the PDF), `common.js`, `app.css` |
| `src/timewalk/static/vendor/` | ghostty-web, highlight.js, marked, each with its licence. Vendored: do not edit |
| `tests/test_timewalk.py` | The git layer, notes and slides, the guards of the app, a real terminal |
| `demo/timewalk-demo` | A git submodule: the sample repository, five tagged steps, at github.com/rahuldave/timewalk-demo. Its replay copy, `demo/timewalk-demo-replay`, is ignored |
| `docs/` | The site: one Markdown page per topic (`authoring.md` lists what a narrative and a tutorial need, for the skills that build classes), `build.py` (the order is its `PAGES` list), `site.css`, `screenshots.py`, `images/`. `_site/` is built and ignored. `.github/workflows/pages.yml` publishes it |
| `demo/notes.md`, `demo/slides/`, `demo/toc.toml`, `demo/walks/` | The notes and slides of the demo, and a second walk. They stay here, outside the sample, as in a class kit |

timewalk is a package, `src/timewalk`, built with hatchling from `pyproject.toml`. It gives four commands,
`timewalk`, `timewalk-pdf`, `timewalk-check` and `timewalk-notes`. Its dependencies are Starlette, uvicorn, websockets, Playwright and pypdf;
the tests use the `dev` group. In a clone, run `uv run timewalk`. Users run it from GitHub with
`uvx --from git+https://github.com/rahuldave/timewalk@v1.0.14 timewalk`, so it is not on PyPI, by Rahul's choice. When a change must reach users,
raise the version in `pyproject.toml`, tag the commit `vX.Y.Z`, push the tag, and update the pin in the
docs and in the kits.

## Commands

Use the recipes, and not the commands behind them.

```
just test            # all tests; extra arguments go to pytest: just test -k slides
just lint            # ruff
just demo            # fetch the sample submodule if needed, and open it with --clock
just demo-walks      # the same, with the two walks of demo/toc.toml
just walk <repo> ... # run timewalk on a repository
just pdf <manifest> -o out.pdf --title "..."
just screenshots     # retake docs/images from a throwaway clone of the demo, and the tutorial from ../timewalk-test
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
- **Keep the page's elements consistent.** A button that does something has a bordered look, an icon and a verb
  (the toggles of the bar, such as Notes, Shell, Dark and the layouts, are nouns and need no icon):
  `↺ Restart step`, `Show ▶`, `± See diff <path>`, `▤ See file <path>`, `Next step: step-NN ▶`. A button keeps one shape:
  do not split its label into a button and loose text, or turn a button into a band. The words of the page are in its
  font, and paths, commands and code in the font of the code. Before a change to how something looks, find its kind
  (button, band, label, bar) and match the others of that kind. The colours come from one theme, GitHub's, in
  `app.css` and `TERM_THEMES`; do not add colours of your own.
- A new kind of notes line or slide entry needs four things. They are the parser, a test, the table in the
  site, and the code of the page that uses it.

## Checking a change

1. Run `just test` and `just lint`.
2. Run timewalk-test's suite against this copy: `cd ~/Projects/timewalk-test && TIMEWALK_LOCAL=~/Projects/timewalk just test`.
   It drives every walk, step, move and slide in two windows. For anything in `src/timewalk/static/`, also drive the
   page in headless Chrome, in two windows, through a throwaway clone
   of the demo. Run `git clone ~/Projects/timewalk-demo` into the scratchpad. Do not use
   `demo/timewalk-demo-replay`, which can hold edits of Rahul.
3. Go through every step and slide, and type in a terminal. Playwright with `channel="chrome"` works.
   Nobody checks Safari or Firefox.
4. **An adversarial review by an Opus subagent before every commit.** When the change is built and its checks
   pass, and before you ask Rahul to commit, start a subagent with the model `opus`. Ask it to break the change: wrong state in a second
   window, a restart, an edit or untracked file in the way, a tag that moved, a slow git, odd paths, the rules above.
   Fix what it finds, with a test for each finding, and run the checks again. Before anything goes onto `main` (a
   release, or a merge of a branch), run one more review over the whole change, and fix its findings first.

## Commits

- End every commit message with the line `Coded using Claude`. Add no `Co-Authored-By` or session lines.
  Rahul set this rule, and it overrides the default attribution.
- The subject says what changed. The body says why, in the voice of the README.
- **Ask before every commit.** After Rahul approves a commit, push `main` to github.com/rahuldave/timewalk
  and do not ask again.

## Releasing a change

Rahul expects every change to the page to ship as a release, with its docs, in one go:

1. Build it, drive it in headless Chrome against a throwaway clone, and fix the findings of the adversarial
   review (see Checking a change). For a long
   file to scroll, clone timewalk itself and run it with `--tags 'v*'`.
2. Update the docs that describe it (often `page.md`, `notes.md`, `model.md`, `reference.md`, `class.md`),
   and retake the screenshots with `just screenshots` when the page looks different.
3. Raise `version` in `pyproject.toml`, move the `timewalk@vX.Y.Z` pins in README, CLAUDE.md and `docs/`
   with sed, commit, tag `vX.Y.Z` (annotated), and push `main` and the tag. The site deploys from the push.
4. Check that the site deployed, and that `uvx --refresh-package timewalk --from git+https://github.com/rahuldave/timewalk`
   gets the change. babykev-walk, babykev-class and the `walk` skill follow `main` unpinned, so they pick it up.

## Pitfalls met before

- **The port probe in `main()` sets `SO_REUSEADDR`, as uvicorn does.** Without it, a timewalk just stopped
  blocked a restart on the same port for a minute, through connections in TIME_WAIT.
- **Run the checks so that a failure stops you.** `just lint | tail -1` and `just style > /dev/null && ...; next`
  let failures through, and two releases went out with a lint error or a doc that failed the guide. Run
  `just test`, `uvx ruff check .`, `just style` and the link check as separate commands, or under
  `set -o pipefail`, and read each result before you commit.
- **A Python script that edits files must fail loudly.** Assert that each old text is found once, and stop
  the release if it is not. One release shipped without its docs because an edit missed a table row.
- **Quote heredocs** (`<<'EOF'`). Without quotes, the shell runs every `` `backtick` `` inside the text.
- **In `app.js`, a `const` that `refresh()` uses must be declared near the top.** `refresh()` runs during the
  module's first top-level `await`, before later declarations exist.
- **CSS rules of equal specificity: the later one wins.** A rule meant to override a general one must come
  after it, or be more specific (the slide command buttons were once 24px for this reason).
- **Per window or per browser:** `sessionStorage` is per tab (Notes and Cues toggles), `localStorage` is per
  browser (theme, size, pane widths, run on click). The docs must say which.
- **`app.js` uses internals of ghostty-web.** `startRenderLoop()` and `animationFrameId` stop a hidden terminal
  from drawing; `setDrawing` checks that they exist, and does nothing if they do not. `term.renderer.devicePixelRatio`
  is rounded up to a whole number: at a fractional ratio (a zoomed window, a scaled screen) the renderer made a new
  canvas on every frame, in every terminal of every window. After an update of the vendored ghostty-web, run
  timewalk-test's `test_drawing.py`, which checks both.
- **ghostty-web's `fit.fit()` skips a size equal to the last one it fitted**, though a Room size may have come
  between. `fitTerminal` compares `proposeDimensions()` with the terminal's real size instead.
- **What the server shares:** walk, step and move, slide, layout, Shell, open file and view, `of` and `at` (the move
  whose change, or whose version of the file, the reader shows), tab, clock, and in a
  tutorial the mode (`do` or `watch`) and the count of moves marked done (`showing`); a per-step
  memory of slide and scroll (`memory`); scrolls (as fractions, and a terminal's as lines) are relayed between windows over `/ws/events`. In Shell mode
  each shell keeps the Room's size (`Terminal.room_size`), and a window that asks for another is told it again. A shell gets
  an Enter after a move only when it is idle at an empty line (`Terminal.refresh_prompt`). The lines that an item of
  the notes marks (`marks`) are relayed in the `show` event only, and not kept in `showing`, so a window opened later
  shows the file without them.
- **An events socket says `hello` once it is on the list of windows.** The test client returns from
  `websocket_connect` when the server accepts, a moment before the server adds the socket to `hub.pages`. A test that
  then posts and waits for the event missed it, and `receive_json()` waited for ever: the unit tests hung, now and then,
  for hours. Wait for `{"type": "hello"}` first. `pytest-timeout` (60 s here, 120 s in timewalk-test) turns any other
  hang into a failure with a traceback.
- **`/api/match` runs often.** Every window in do mode asks every two seconds. It runs `Repo.differ_from` off the event
  loop (`asyncio.to_thread`), so a slow git does not hold up the sockets. Read git paths with `-z` (`status --porcelain -z`,
  `diff --name-only -z`, `ls-tree -z`): without it, git quotes a path with a space or an accent.
