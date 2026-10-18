# timewalk

timewalk is a step browser. It teaches a project through a replay of how its authors built it. Each step
is a git tag, by default `step-*`. The message of an annotated tag is the note that the audience sees.

timewalk serves one page, at `/`. The page shows slides, the files at the step, what the step changed,
and real terminals in the repository as it was then. With `--notes`, it shows the notes of each step in a
column on the right. With `--clock`, it shows a clock band across the top. The old address `/presenter`
sends the browser on to `/`.

The page can be open in several windows, for example one on the projector and one on the screen of the
presenter. The server keeps the shared state, so every window shows the same step, slide, layout, open
file, view and terminal tab. The server relays scrolls of the slide, the file and the notes to every other
window over the events socket, as a fraction. A shell has one size, and the window that the presenter last
typed in sets it.

Each window keeps its keyboard focus, and whether the notes and the cues show (sessionStorage). The window
at `?room=1` (the Room button) is the class's window. It shows no cues and no clock band, and has no PDF or
Edit button. The browser keeps the theme, text size, terminal height and run on click (localStorage).

Write every site page by the nossaifd writing guide, `docs/style/GUIDE.md`. Use the glossary and the
linked headings in `docs/style/README.md`. Define jargon where it first appears. Check with `just style`.
It checks the site, the README, and the notes and slides of the demo. It does not check this file.

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
  clone on the branch `timewalk/replay`. It writes no config, hook, branch, tag or stash in your repository.
  What a learner does in the copy stays there, and a move keeps a learner's commits on `timewalk/saved/<place>`.
  A replay worktree made by an older version still works, on a detached HEAD. `--in-place` is the only exception.
- **timewalk never loses an untracked file.** Run outputs, for example `.venv`, `mlflow.db` and `runs/`, stay through
  every move. A step can have a tracked file where an untracked file sits. Then a move in a replay clone first keeps
  the untracked file on a branch `timewalk/saved/<place>`, and then replaces it. Ignored or not, the file is never lost.
  `--in-place`, and a replay worktree of an older version, refuse the move instead.
- **timewalk never loses an edit, and a move in a replay clone never asks.** Before it checks out, a move commits the
  learner's uncommitted work with a temporary index. The work is the edits, and the new files that git does not ignore.
  The temporary index keeps the files, the index and the stash as they are. The move puts a branch
  `timewalk/saved/<place>` on that commit, with any commits the learner made. Your window then says where the work is
  kept, but the Room window does not. `--in-place` asks first and stashes, as before. `--discard-edits` is no longer
  needed. Only a replay worktree of an older version still throws edits away with it.
- **The file view cannot write.** No route changes a file in the repository or the replay copy. The one
  file that the page writes is the notes file. **Save** in the notes column sends one section to
  `POST /api/notes`. The server writes only that section, and refuses if the section changed in the file
  since the page read it. The terminals write files as any terminal does.
- **Localhost by default, a new token at each start, and other Host headers refused.** Every page, API
  call, socket and static asset needs the token. `--host` (for a cloud machine) listens beyond this machine.
  timewalk then warns at start and accepts any Host header, so the token is the only guard. Keep the
  default. In the docs, keep the advice to use an SSH tunnel and not `--host 0.0.0.0`.

## Layout

| File | What it is |
|---|---|
| `src/timewalk/__init__.py` | The whole server, one module: `git()`, `Repo` (steps, the replay clone and its fetches, moves, saved branches, diffs, reads), `select_steps` (a small walk's steps, each with its `start`, the commit before it, and the title of its entry) and `walk_notes` (the notes of each of its stops), `own_stops`, `stop_range` and `expand_stop` (a walk with notes of its own: its intro and `##` stops, the place that a stop's `{{include ...}}` lines make, and the stop with its includes put in), notes and slides parsers, `loosely_same` (a `pyproject.toml` or `uv.lock` that `loose` names matches by package names), `Terminal` (a pty), `Hub` (events to pages), `make_app` (Starlette routes) |
| `src/timewalk/walks.py` | Several walks: `load_toc` reads `toc.toml` (with `select`, whose entries can have a title, and `intro` for a small walk, `shared` for a walk with notes of its own, whose `own_select` makes its `select` from its stops, and `loose` for a tutorial), `check` checks notes and slides against the steps (`selection_problems` for a walk that selects, `own_problems` for a walk with notes of its own), and the commands `timewalk-check` and `timewalk-notes` (a tutorial's notes: a `## step-NN Title` section for each step, and drafts of its `### step-NN.k` sections: comments for the text of both modes, a `patch$` line for each file, a `do` and a `watch` block, the check, and last a `files:` item for every file of the commit) |
| `src/timewalk/export.py` | The command `timewalk-export`: copies of a walk to read without timewalk, one for each mode of a tutorial, with only that mode's `::: do` or `::: watch` blocks and without the private lines. `--markdown` writes `TUTORIAL-do.md` and `TUTORIAL-watch.md` (a narrative: `TUTORIAL.md`), and `--site` one website (smalldocs, or Quarto with `--quarto`) with a Watch and a Do section (a narrative: one). `--json` writes the facts of the walk (see `facts.py`). It writes over only the files that it made |
| `src/timewalk/facts.py` | The facts of one walk, for `timewalk-export --json` (`--private` keeps the presenter's lines): the walk, its intro, its stops, each move's text as parts for each mode (`prose`, `command`, `item`, `patch`), and each file of each change with its whole diff, the names it changes, the `do$` line that writes it (`by`) and its `patch`. Each stop and move has its `number`. The notes' text is kept byte for byte. A class makes its own pages from them, as walk-kit does. The same inputs give the same bytes |
| `src/timewalk/observe.py` | Where each file of a move comes from: `observe` runs a move's `do$` lines at the commit before it in a scratch `git clone --shared` (`Scratch`), and notes what each writes; `sources` says for each file the `do$` line that writes it or `patch`, and what is changed by hand after. Results go to a cache, `~/.cache/timewalk/observed`, unless a line fails. `timewalk-check` and `timewalk-export --json` run it. timewalk itself runs it in the background only for a strict walk's moves with a `do$` line of uv, for `facts.takes`; `--no-run` and `TIMEWALK_NO_RUN=1` run nothing |
| `src/timewalk/facts.schema.json` | The layout of the facts file, as a JSON Schema. Raise its `format` for a change that a renderer must know of, and keep the table in `docs/reference.md` true |
| `src/timewalk/slides_pdf.py` | The command `timewalk-pdf`: the PDF of the slides and, with `--with-notes`, the notes, drawn by Chrome or Edge through Playwright. `--brand DIR` gives it the look of a brand folder (font, colours, cover, dividers); only this command and `print.js` know brands, and the page does not. The **PDF** button calls it through `/api/pdf`, with no brand |
| `src/timewalk/static/` | `index.html`/`app.js` (the page, at `/`), `print.*` (for the PDF), `common.js`, `app.css` |
| `src/timewalk/static/vendor/` | ghostty-web, highlight.js, marked, each with its licence. Vendored: do not edit |
| `tests/test_timewalk.py` | The git layer, notes and slides, the guards of the app, a real terminal |
| `demo/timewalk-demo` | A git submodule: the sample repository, five tagged steps, at github.com/rahuldave/timewalk-demo. Its replay copy, `demo/timewalk-demo-replay`, is ignored |
| `docs/` | The site, built by smalldocs with the theme Atelier: one Markdown page per topic (`tour*.md` is a tour of a real class, cheesecracker; `authoring.md` lists what a narrative and a tutorial need, for the skills that build classes), `_smalldocs.yml` (the pages, and the sections and order of the sidebar), `_extensions/atelier/` (the theme, from `smalldocs add rahuldave/smalldocs-atelier@TAG --force`), `styles.css`, `screenshots.py`, `images/`. `_site/` is built and ignored. `.github/workflows/pages.yml` publishes it |
| `demo/notes.md`, `demo/slides/`, `demo/toc.toml`, `demo/walks/` | The notes and slides of the demo, and a second walk. They stay here, outside the sample, as in a class kit |

timewalk is a package, `src/timewalk`, built with hatchling from `pyproject.toml`. It gives five commands,
`timewalk`, `timewalk-pdf`, `timewalk-check`, `timewalk-notes` and `timewalk-export`. `timewalk-export --json` writes the
facts of a walk, which walk-kit makes the pages of a class from. Its dependencies are Starlette, uvicorn,
websockets, Playwright and pypdf. The tests use the `dev` group. In a clone, run `uv run timewalk`.

`uvx --from PATH timewalk`, with PATH a clone, rebuilds after a new commit or an edit in `src/`, because of
`cache-keys` in `[tool.uv]`. Before, uv looked only at `pyproject.toml`, and ran the old build from its cache until
the version changed.

timewalk is not on PyPI, by Rahul's choice. Users run it from GitHub with
`uvx --from git+https://github.com/rahuldave/timewalk@v1.4.6 timewalk`. To get a change to users, raise the
version in `pyproject.toml` and tag the commit `vX.Y.Z`. Then push the tag, and update the pin in the docs and
in the kits.

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
just site            # build the site with smalldocs, and serve it on this machine
just style           # check the prose against the writing guide; -v also shows the soft notes
```

If port 8765 is in use, timewalk stops with a message. Give `--port` with another number.

A terminal test once hung while it waited for output from `/api/type`. The terminal tests type through the
socket.

## Conventions

- **Look for prior art before you build.** Before a new feature or fix, read the code of timewalk and walk-kit that
  does the same job, and use it or call it. walk-kit's `git check` compared `pyproject.toml` with a `sed` pattern and
  patched it as text, while timewalk's `package_names` already read it with a TOML library. Days of fixes went to the
  copy. Say which code you read, and why it does not serve, before you write new code.

- **Make every prompt for another agent copyable.** Write it to a file in the scratchpad, put it on the clipboard with
  `pbcopy`, and give its path. Also show it whole, in a code block that opens
  with four backticks and `text`.

- **Answer Rahul succinctly, in precise language.** Short lines, one idea each. Name the actor and the thing. Write "the
  learner's file", not "theirs", and "the move's version", not "it". No vague words such as "there", "this", "stuff",
  "and so on". A list beats an essay.

- **Write so that a child of 12 can follow.** Rahul could not follow Claude's answers about the 1.4.1
  release. Keep these rules, which come from those answers:
  - **Explain each name of the code the first time you use it.** Do not write "the facts" alone, or `loose`, or "a step
    of one commit". For the first, write "the JSON file that `timewalk-export --json` writes, which walk-kit makes the pages from".
  - **Give the problem first, as the student or the author meets it. Then give the fix.** Label them **Problem** and
    **Fix**.
  - **Say what a command does, step by step.** For example, do not write "`-I` hides the version
    lines". Say what `git diff -I` compares, what it prints, and which lines it leaves out.
  - **Show one real case.** For example: "The student runs `uv add ruff` a month later, and gets a newer ruff.
    The page still says Your files match, because the walk's `loose` setting lets `uv.lock` match by the names of
    its packages."
  - **Say each rule exactly.** "Lines that are only version numbers" and "lines that differ only by version" are
    two different rules. Write the one that the code does.
  - **Call something a problem only if it breaks the purpose of the feature.** `loose` exists to let a student
    have a newer version of a package. A newer version that passes is the feature working, not a problem.
  - **Do not let a reviewer polish prose for many rounds.** Rules of style are defaults, not laws. Twenty rounds of
    review turned `docs/reference.md` into a chain of definitions. Stop at three rounds, and rewrite by hand.
  - **Give one topic at a time.** Do not mix a report of the work, the risks and two decisions in one answer.
  - **Describe each problem in two to four lines.** If Rahul asks again about an answer, that answer was too
    abstract. Make the next answer more concrete, not longer.

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
- **Keep the page's elements consistent.** A button that does something has a bordered look, an icon and a verb.
  Examples are `↺ Restart step`, `Show ▶`, `± See diff <path>`, `▤ See file <path>` and `Next step: step-NN ▶`.
  The toggles of the bar, such as Notes, Shell, Dark and the layouts, are nouns and need no icon. A button keeps one
  shape. Do not split its label into a button and loose text, or turn a button into a band.
- **Use the page's fonts and colours.** The words of the page are in its font, and paths, commands and code are in
  the font of the code. Before a change to how something looks, find its kind (button, band, label, bar), and match
  the others of that kind. The colours come from one theme, GitHub's, in `app.css` and `TERM_THEMES`. Do not add
  colours of your own.
- A new kind of notes line or slide entry needs four things. They are the parser, a test, the table in the
  site, and the code of the page that uses it.

## Checking a change

1. Run `just test` and `just lint`.
2. Run `cd ~/Projects/timewalk-test && TIMEWALK_LOCAL=~/Projects/timewalk just test`. It runs the suite of
   timewalk-test against this copy, and drives every walk, step, move and slide in two windows. For anything in
   `src/timewalk/static/`, also drive the
   page in headless Chrome, in two windows, through a throwaway clone
   of the demo. Run `git clone ~/Projects/timewalk-demo` into the scratchpad. Do not use
   `demo/timewalk-demo-replay`, which can hold edits of Rahul.
3. Go through every step and slide, and type in a terminal. Playwright with `channel="chrome"` works.
   Nobody checks Safari or Firefox.
4. **An adversarial review by an Opus subagent before every commit.** When you have built the change and its
   checks pass, and before you ask Rahul to commit, start a subagent with the model `opus`. Ask it to break the
   change with cases such as these:
   - wrong state in a second window
   - a restart
   - an edit or untracked file in the way
   - a tag that moved, a slow git, or odd paths
   - the rules above

   Fix what it finds, with a test for each finding, and run the checks again. Before anything goes onto `main`, as
   a release or a merge of a branch, run one more review over the whole change. Fix its findings first.

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

- **The port probe in `main()` sets `SO_REUSEADDR`, as uvicorn does.** Without it, a timewalk that had stopped a moment before
  blocked a restart on the same port for a minute, through connections in TIME_WAIT.
- **Run the checks so that a failure stops you.** `just lint | tail -1` and `just style > /dev/null && ...; next`
  let failures through, and two releases went out with a lint error or a doc that failed the guide. Run
  `just test`, `uvx ruff check .`, `just style` and the link check as separate commands, or under
  `set -o pipefail`. Read each result before you commit.
- **A Python script that edits files must fail loudly.** Assert that each old text is found once, and stop
  the release if it is not. One release shipped without its docs because an edit missed a table row.
- **Quote heredocs** (`<<'EOF'`). Without quotes, the shell runs every `` `backtick` `` inside the text.
- **In `app.js`, a `const` that `refresh()` uses must be declared near the top.** `refresh()` runs during the
  module's first top-level `await`, before later declarations exist.
- **Of two CSS rules of equal specificity, the later one wins.** A rule that overrides a general one must come
  after it, or be more specific. The slide command buttons were once 24px for this reason.
- **Per window or per browser.** `sessionStorage` is per tab (the Notes and Cues toggles). `localStorage` is per
  browser (theme, size, pane widths, run on click). The docs must say which.
- **`app.js` uses internals of ghostty-web.** `startRenderLoop()` and `animationFrameId` stop a hidden terminal
  from drawing. `setDrawing` checks that they exist, and does nothing if they do not. `app.js` rounds
  `term.renderer.devicePixelRatio` up to a whole number. At a fractional ratio (a zoomed window, a scaled screen),
  the renderer made a new canvas on every frame, in every terminal of every window. After an update of the vendored ghostty-web, run
  timewalk-test's `test_drawing.py`, which checks both.
- **ghostty-web's `fit.fit()` skips a size equal to the last one it fitted**, though a Room size may have come
  between. `fitTerminal` compares `proposeDimensions()` with the terminal's real size instead.
- **What the server shares.** The server keeps this state, one copy that every window shares:
  - walk, step and move, slide, layout, Shell, open file and view
  - `of` and `at`, the move whose change, or whose version of the file, the reader shows
  - tab and clock
  - in a tutorial, the mode (`do` or `watch`) and the count of moves marked done (`showing`)
  - a memory of slide and scroll for each step (`memory`)

  The server relays scrolls between windows over `/ws/events`, as fractions, and a terminal's scroll as lines. In
  Shell mode each shell keeps the Room's size (`Terminal.room_size`). A window that asks for another size gets the
  Room's size again. A shell gets an Enter after a move only when it is idle at an empty line
  (`Terminal.refresh_prompt`). The server relays the lines that an item of the notes marks (`marks`) in the `show`
  event only. It does not keep them in `showing`, so a window opened later shows the file without them.
- **An events socket says `hello` once it is on the list of windows.** The test client returns from
  `websocket_connect` when the server accepts, a moment before the server adds the socket to `hub.pages`. A test that
  then posted and waited for the event missed it, and `receive_json()` waited for ever. The page had the same gap. A move made while a window drew itself sent that window no event. So a
  window calls `refresh()` when it gets `hello`. The unit tests hung, now and
  then, for hours. Wait for `{"type": "hello"}` first. `pytest-timeout` (60 s here, 120 s in timewalk-test) turns any other
  hang into a failure with a traceback.
- **`/api/match` runs often.** Every window in do mode asks every two seconds. It runs `Repo.differ_from` off the event
  loop (`asyncio.to_thread`), so a slow git does not hold up the sockets. Read git paths with `-z` (`status --porcelain -z`,
  `diff --name-only -z`, `ls-tree -z`). Without it, git quotes a path with a space or an accent.
