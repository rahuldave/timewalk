# Live edits

At a step, some commands only look (`pytest`, `git log`) and some change files (`ruff format`,
`uv lock`, an editor). timewalk shows the second kind as it happens, beside what the step itself changed,
and never throws the changes away.

## What you see

Run a command that rewrites a tracked file in a terminal at this step. Within about a second:

- The file is marked **edited** in the file list, and the count at the top says how many are.
- The reader offers a third view, **Edits since the step**: the file's diff against the step's commit.
  Clicking an edited file opens it there.
- The presenter page shows the same, and a file opened there opens on the projector too.

![The file list after ruff format at step-02: one file edited](images/edits-tree.png)

![Edits since the step: what ruff changed, against the step's commit](images/edits.png)

Run it again and the view follows. Undo the edits (`git restore .`) and the marks go, and the reader goes
back to the file.

## Three views, two kinds of change

| View | Compares | Answers |
|---|---|---|
| Changes in this step | the step before, and this step's commit | What did the history do here? |
| Edits since the step | this step's commit, and the files on disk | What did we just do in the room? |
| File | nothing | What is in the file now? |

At step-03 of the demo the two meet: **Changes in this step** on `greet.py` is the same change the class
watched ruff make live at step-02.

## Moving with edits

Edits belong to the step where they were made, so moving asks first.

![Moving with uncommitted edits asks first](images/move-with-edits.png)

- **Stay here** leaves everything as it is.
- **Set the edits aside and move** runs `git stash`, labelled with the step, `timewalk: edits made at
  step-02`, and then moves. `git stash list` and `git stash pop` in a terminal bring them back.

Nothing is discarded unless you discard it, for example with `git restore .` in a terminal.

## What counts as an edit

A change to a **tracked** file: one the step's commit has. New files a command writes, such as a
`.venv`, a database or a run's output, are untracked. They are not edits, are not shown, and are never
touched by a move. See [The replay copy](replay.md).

Edits in the **Main** tab's repository are not watched: the page never shows that repository. See
[The terminals](terminals.md).

## How it works

The server looks at the replay copy once a second (`git status`, plus each edited file's size and time),
and tells both pages when anything changed. The diff is `git diff HEAD -- <file>` in the replay copy.
