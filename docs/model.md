# How timewalk works

timewalk is one server process, two web pages, a set of shells, and two working copies of your
repository. This page says what each piece is and where it is. It also says what each button does in each
folder. Then you know what happens before you click.

![The pieces of timewalk, with two pages, one server, your class folder, your repository and its replay copy](images/model.svg)

## The folders

| Folder | What it is | What works in it | Does it move? |
|---|---|---|---|
| **Your repository**, `~/code/project` | The repository that you give to timewalk, on its current branch | The **Main** tab. The recipe buttons, when Main is in front | Never, unless you give `--in-place` |
| **The replay copy**, `~/code/project-replay` | A git worktree of your repository, on a detached HEAD at the current step | **At this step**, **Runs**, **Runs 2** to **9**, **Claude** and **+**. The file list, the reader, the edits watcher and the recipe buttons | Yes. Each move checks out a step here |
| **Your class folder** | The folder with your notes and slides. timewalk does not use it as a repository | The server reads `notes.md` and the slide files from it again each time | No. timewalk writes nothing there |
| **The folder of timewalk** | The code. `uv run` runs it in its own environment, which uv keeps in its cache | Nothing. The shells remove that environment from their `PATH` | No |

A worktree is a second working folder attached to the same git repository. HEAD is the commit that a
working folder is at. A detached HEAD is at a commit and not on a branch.

## Where worktrees are used, and where not

**Used: the replay copy.** timewalk makes one replay copy for each repository that you browse. The first
start makes it with this command:

```
git worktree add --detach ~/code/project-replay <the first step's commit>
```

Each later start finds the replay copy in `git worktree list`, and uses it again from where it stopped.
The `.git` of your repository holds the record of the replay copy, in `.git/worktrees/project-replay`.
The `.git` of the replay copy is a file of one line that points there. See [The replay copy](replay.md).

**Not used:**

- **Your repository** is the main working copy. The server reads its tags, and adds the record of the
  replay copy to its `.git`. The server does nothing else to it.
- **With `--in-place`**, timewalk makes no replay copy. Moves check out steps in your repository, and
  every tab, Main too, is in it. The server refuses `--discard-edits` with `--in-place`, so a move cannot
  drop your real work.
- **Your class folder** is never a working copy of anything.
- **The PDF export**, `slides_pdf.py`, uses no git. It reads the manifest and the slide files.

**The demo** adds one layer. `demo/timewalk-demo` is a git submodule of timewalk. A submodule is a
repository of its own inside another repository. Git keeps the data of this submodule in
`.git/modules/demo/timewalk-demo` of timewalk. The replay copy of the demo, `demo/timewalk-demo-replay`, is
a worktree of the submodule, and the git of timewalk ignores it.

**The tests and the screenshots** never touch your repositories or the replay copy of the demo. The
tests make small repositories in temporary folders. `docs/screenshots.py` clones the demo into a
temporary folder, and timewalk makes the replay copy of that clone beside it there.

## What each control does

| Control | What happens | Git, in which folder |
|---|---|---|
| A step button, **Left**, **Right**, the step arrows | Looks for edits, refuses if an untracked file is in the way, moves, and tells both pages | `git status`, `git ls-files --others`, `git ls-tree`, then `git checkout --detach <step>`, in the replay copy |
| **Set the edits aside and move** | Stashes the edits with the name of the step, then moves | `git stash push -m "timewalk: edits made at step-NN"`, in the replay copy |
| A move, with `--discard-edits` | Moves and does not ask. Edits to tracked files are lost | `git checkout --force --detach <step>`, in the replay copy |
| **Up**, **Down**, the slide arrows | Changes the shared slide number | None |
| **Slides**, **Both**, **Code** | Changes the shared layout, on both pages | None |
| A file in the file list, **File** | Reads the file from disk, and refuses paths outside the copy | None. It reads a file in the replay copy |
| **Changes in this step** | The diff of the step for that file | `git diff <step before> <step> -- <file>`, in the replay copy |
| **Edits since the step** | The edits since the commit of the step | `git diff HEAD -- <file>`, in the replay copy |
| **At this step** | A login shell | Starts in the replay copy |
| **Runs**, **Runs 2** to **9**, **+** | More login shells, each with its own process | Start in the replay copy |
| **Claude** | A login shell that types `claude` (or `--assistant`) when it starts | Starts in the replay copy |
| **Main** | A login shell | Starts in your repository |
| A recipe button | Types `just <recipe>` into the tab in front | `just --dump` lists the recipes, in the replay copy, or in your repository when Main is in front |
| A command in the notes | Types the command into its tab and presses Enter. That tab comes to the front on both pages | The work of the command, in the folder of that tab |
| **Start the clock**, with `--clock` | Records the start time | None |
| (no control, each second) | The edits watcher looks for edits, and tells both pages when they change | `git status`, in the replay copy |

Each shell starts in its folder. A `cd` in one shell moves that shell and nothing else. The file list
and the reader stay on the replay copy.

## What is kept where

| Kept | Where | Lost when |
|---|---|---|
| The steps, with names, commits and notes | timewalk reads them from the tags when it starts | timewalk reads them again at the next start. Restart after you change the tags |
| The current step | Git, as the HEAD of the replay copy. timewalk asks git again each time | Never. A restart finds the replay copy where it was |
| Slide, layout, open file, view, tab in front, clock | The memory of the server, shared by both pages | timewalk stops |
| Each shell, and the last 256 KB of its output | The server, with one process for each tab, on a pseudo-terminal | timewalk stops. The shells end, and their commands end too, unless you started a command with `nohup` and `&` |
| Theme, text size, terminal height | The local storage of each browser | You clear it |
| Edits to tracked files | On disk in the replay copy, or in a stash | You discard them, or the next move discards them, with `--discard-edits` |
| Untracked files, for example `.venv`, outputs and databases | On disk in the replay copy | You delete them, or you remove the replay copy |
| Notes and slides | Your class folder, which timewalk reads again each time | Never. timewalk does not write them |

A pseudo-terminal is the device that the operating system gives a shell in place of a real terminal.
A tracked file is a file that git records, and an untracked file is a file that git does not record.

If a terminal checks out another commit, the replay copy is no longer at a step. Both pages then say
"between steps". To go back, choose a step.

## One move, in order

1. A page asks the server to move to a step. With `--discard-edits`, the server runs
   `git checkout --force --detach` to the commit of the step, and goes to step 6.
2. The server asks git for edits to tracked files in the replay copy. If there are edits, and the page did
   not ask to set them aside, the server refuses and names them. The page then asks you.
3. If the page asked to set the edits aside, the server runs `git stash push` with the name of the step.
4. The server lists the untracked files, and the files that the new step tracks. If a path is in both
   lists, the server refuses and names it. A checkout would write over your file.
5. The server runs `git checkout --detach` to the commit of the step.
6. The server sets the slide back to the first slide, and tells both pages.
7. Each page reads the new state, the file list, the recipes, and on the presenter page, the notes. An
   open file stays open, and the page reads it again at the new step.

A move does not touch the shells. A command that runs in **Runs** continues, and the files change under
it. See [The terminals](terminals.md#a-long-command-and-a-move).

## The two pages

The projector page and the presenter page use the same HTML and script. At `/presenter`, the script adds
the notes column, and with `--clock` the clock band. Each page keeps a socket to the server for events. A socket is an
open connection between the page and the server, which carries messages both ways.

When a page changes what it shows, it
tells the server, and the server tells both pages. Each shell has one size, which the projector page
sets. The presenter page draws the same shell in its own space.

## Security, in one line

The server listens on `127.0.0.1` only. Each page, request, socket and file needs the token in the
printed address. The token is the secret value after `?t=`. See [Safety](safety.md).
