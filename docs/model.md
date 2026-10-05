# How timewalk works

timewalk is one server process, two web pages, a set of shells, and two working copies of your
repository. This page says what each piece is, where it lives, and what every button does to which
folder, so you can predict what will happen before you click.

![The pieces: two pages, one server, your class folder, your repository and its replay copy](images/model.svg)

## The folders

| Folder | What it is | Who works in it | Does it move? |
|---|---|---|---|
| **Your repository**, `~/code/project` | The repository you point timewalk at, on whatever branch it is on | The **Main** tab. The recipe buttons, when Main is in front | Never, unless you pass `--in-place` |
| **The replay copy**, `~/code/project-replay` | A git worktree of your repository, on a detached HEAD at the current step | **At this step**, **Runs**, **Runs 2** to **9**, **Claude**, **+**. The file view, the reader, the edits watcher, the recipe buttons | Yes: every move checks out a step here |
| **Your class folder** | Wherever your notes and slides are. Not a repository as far as timewalk is concerned | The server reads `notes.md` and the slide files from it, afresh each time | No: nothing is written there |
| **timewalk's folder** | The code. `uv run` runs it in an environment of its own, kept in uv's cache | Nobody: the shells take that environment off their `PATH` | No |

## Where worktrees are used, and where not

**Used: the replay copy.** One per repository you browse. The first start makes it with:

```
git worktree add --detach ~/code/project-replay <the first step's commit>
```

Every later start finds it in `git worktree list` and reuses it, wherever it was left. Your repository's
`.git` holds its record, in `.git/worktrees/project-replay`. The replay copy's own `.git` is a one-line
file pointing there. See [The replay copy](replay.md).

**Not used:**

- **Your repository** is the main working copy. timewalk reads its tags and adds the replay copy's record
  to its `.git`, and does nothing else to it.
- **With `--in-place`** there is no replay copy. Moves check out steps in your repository, and every tab,
  Main included, is in it. `--discard-edits` is refused in place, so a move can never drop your real work.
- **Your class folder** is never a working copy of anything.
- **The PDF export**, `slides_pdf.py`, uses no git at all: it reads the manifest and the slide files.

**The demo** adds one layer. `demo/timewalk-demo` is a git submodule of timewalk: a repository of its own,
whose git data is kept in timewalk's `.git/modules/demo/timewalk-demo`. Its replay copy,
`demo/timewalk-demo-replay`, is a worktree of the submodule, and timewalk's own git ignores it.

**The tests and the screenshots** never touch your repositories or the demo's replay copy. The tests
build small repositories in temporary folders. `docs/screenshots.py` clones the demo into a temporary
folder, and timewalk makes that clone's replay copy beside it there.

## What each control does

| Control | What happens | Git, in which folder |
|---|---|---|
| A step chip, **Left**, **Right**, the step arrows | Checks for edits, refuses if an untracked file is in the way, moves, tells both pages | `git status`, `git ls-files --others`, `git ls-tree`, then `git checkout --detach <step>`, in the replay copy |
| **Set the edits aside and move** | Stashes the edits, labelled with the step, then moves | `git stash push -m "timewalk: edits made at step-NN"`, in the replay copy |
| A move, with `--discard-edits` | Moves without asking; edits to tracked files are thrown away | `git checkout --force --detach <step>`, in the replay copy |
| **Up**, **Down**, the slide arrows | Changes the shared slide number | None |
| **Slides**, **Both**, **Code** | Changes the shared layout, on both pages | None |
| A file in the tree, **File** | Reads the file from disk, refusing paths outside the copy | None: a file read, in the replay copy |
| **Changes in this step** | The step's diff for that file | `git diff <step before> <step> -- <file>`, in the replay copy |
| **Edits since the step** | What was edited since the step's commit | `git diff HEAD -- <file>`, in the replay copy |
| **At this step** | A login shell | Starts in the replay copy |
| **Runs**, **Runs 2** to **9**, **+** | Further login shells, each its own process | Start in the replay copy |
| **Claude** | A login shell that types `claude` (or `--assistant`) when it starts | Starts in the replay copy |
| **Main** | A login shell | Starts in your repository |
| A recipe button | Types `just <recipe>` into the tab in front | The recipes are listed with `just --dump`, in the replay copy, or in your repository when Main is in front |
| A command in the notes | Types the command into its tab and presses Enter; that tab comes to the front on both pages | Whatever the command does, in that tab's folder |
| **Start the clock** | Records the start time | None |
| (nothing: every second) | The edits watcher looks for edits and tells both pages when they change | `git status`, in the replay copy |

The shells start in their folder. A `cd` in one moves that shell and nothing else: the file view stays
on the replay copy.

## What is kept where

| Kept | Where | Lost when |
|---|---|---|
| The steps: names, commits, notes | Read from the tags when timewalk starts | Re-read at the next start; restart after re-tagging |
| The current step | Git: the replay copy's HEAD. Asked afresh each time | Never: a restart finds the replay copy where it was |
| Slide, layout, open file, view, tab in front, clock | The server's memory, shared by both pages | timewalk stops |
| Each shell, and its last 256 KB of output | The server: one process per tab, on a pseudo-terminal | timewalk stops: the shells end, and so does what runs in them, unless it was started with `nohup` and `&` |
| Theme, text size, terminal height | Each browser's local storage | You clear it |
| Edits to tracked files | On disk in the replay copy, or in a stash | You discard them, or the next move does, with `--discard-edits` |
| Untracked files: `.venv`, outputs, databases | On disk in the replay copy | You delete them, or remove the replay copy |
| Notes and slides | Your class folder, read afresh | Never written by timewalk |

If a terminal checks out another commit, the replay copy is no longer at a step and both pages say
"between steps". Choose a step to return.

## One move, in order

1. A page asks the server to move to a step. With `--discard-edits`, the server runs
   `git checkout --force --detach` to the step's commit and goes straight to step 6.
2. The server asks git for edits to tracked files in the replay copy. If there are some, and the page did
   not say to set them aside, it refuses and names them. The page asks you.
3. Asked to set them aside, it runs `git stash push` with the step's name.
4. It lists the untracked files, and the files the new step tracks. If any path is in both, it refuses
   and names it: a checkout would overwrite your file.
5. It runs `git checkout --detach` to the step's commit.
6. It resets the slide to the first, and tells both pages.
7. Each page reads the new state, the tree, the recipes, and, on the presenter page, the notes. An open
   file stays open, read again at the new step.

The shells are not touched. A command running in **Runs** keeps running, and the files change under it.
See [The terminals](terminals.md#a-long-command-and-a-move).

## The two pages

Both pages are the same HTML and script. At `/presenter` the script adds the clock band and the notes
column. Each page holds a socket to the server for events. When either page changes what is shown, it
tells the server, and the server tells both. Each shell has one size, set by the projector; the
presenter page draws the same shell in its own space.

## Security, in one line

The server listens on `127.0.0.1` only, and every page, request, socket and file needs the token in the
printed address. See [Safety](safety.md).
