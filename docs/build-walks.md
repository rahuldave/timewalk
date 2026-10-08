# Build a class with several walks

This page builds a class kit with several walks, from the history of the project to `just present`. A walk
is one path through the history, with its own notes and slides. [Several walks](walks.md) describes each
part in detail. Here the parts come together, in the order that you make them.

The repository timewalk-test, at github.com/rahuldave/timewalk-test, is a complete example. It has five
walks. Two are narratives on every step or on some steps, one is a narrative on tags of its own, and two
are tutorials. Clone
it and run `just present` to see each feature. Its README is a tour.

![A tutorial walk: the moves of step-02 under the step bar, the move on show in the notes, and the file that the move added](images/tutorial.png)

## The parts of a class

A class with several walks has these parts:

| Part | Where | What it holds |
|---|---|---|
| The project | Its own repository | The history: tagged steps, and for a tutorial the small commits between them |
| The table of contents | `toc.toml` in the kit | The list of walks, the default first |
| The default walk | `notes.md` and `slides/` in the kit | The notes and the slides of the first walk |
| Every other walk | `walks/NAME/` in the kit | Its `notes.md`, and `slides/slides.toml` with its slide files |
| The recipes | `justfile` in the kit | `just present`, `just check` and `just pdf` |

The kit is the folder of [Making the steps](steps.md#where-the-class-material-goes), with a table of contents
and a folder for each walk:

```
my-class/
├── toc.toml
├── notes.md                  the default walk
├── slides/slides.toml        its slides
├── walks/
│   ├── tests/
│   │   ├── notes.md
│   │   └── slides/slides.toml
│   └── tutorial/
│       ├── notes.md
│       └── slides/slides.toml
├── justfile
├── repo/                     ignored: a clone of the project
└── worktree/                 ignored: the replay copy
```

## Make the history

Make the steps as for one walk, with one annotated tag for each step, on one branch. See
[Making the steps](steps.md). Each kind of walk then needs a little more:

- **A walk on some of the steps** needs nothing more. Name its steps in `toc.toml`.
- **A walk on one part, with steps of its own,** needs a branch with tags of its own, for example
  `hooks-00` to `hooks-02`. Start the branch at the step where the part begins.
- **A tutorial** needs small commits between the tags. Each small commit is a move.

### Commits for a tutorial

Make each move a commit of its own, and name it in its subject. The last commit of the step keeps the
subject of the step, and carries the tag:

```
git commit -m "step-02.1: a test of counting"
git commit -m "step-02.2: a test of an empty text"
git commit -m "step-02: case does not matter, and the tests pass"
git tag -a step-02 -m "Tests

Two tests, one failing, and the fix."
```

A step of one commit has no moves, and works as in a narrative.

### Split a step that is one commit

If the history has one commit for a step, split it into moves. First take each group of files from the
tag, one commit at a time:

```
git switch --detach step-01
git checkout step-02 -- tests/test_count.py
git commit -m "step-02.1: a test of counting"
git checkout step-02 -- tests/test_empty.py
git commit -m "step-02.2: a test of an empty text"
git restore --source step-02 --staged --worktree .
git commit -m "step-02: case does not matter, and the tests pass"
```

The last command takes the rest of the step, and removes the files that the step removed. So the last
commit has the same files as the old step-02. Then do these steps:

1. Rebase the later steps on the new commit.
2. Point the tags at the new commits again, as in [How to change an earlier step](steps.md#how-to-change-an-earlier-step).
3. Restart timewalk, which reads the tags when it starts.

## Write the table of contents

List the walks in `toc.toml`. The first walk is the default:

```toml
[[walk]]
id = "narrative"
title = "The project, step by step"
notes = "notes.md"
slides = "slides/slides.toml"

[[walk]]
id = "tests"
title = "Only the tests"
folder = "walks/tests"
steps = ["step-01", "step-02", "step-05"]

[[walk]]
id = "hooks"
title = "A git hook, one move at a time"
kind = "tutorial"
folder = "walks/hooks"
tags = "hooks-*"
```

[The table of contents](walks.md#the-table-of-contents) lists every key.

## Write the notes and the slides

Write the notes of each walk as for one walk, with one `## step-name` section for each step. See
[Notes](notes.md). In a tutorial, give each move a `### step-name.N` section, in the order of the commits.
A line `files:` in a move lists the files of its commit.

Write a manifest for each walk, with an entry for each step, even of one slide. A manifest can use the
slide files of another walk, with a path from its own folder:

```toml
[slides]
step-01 = ["../../../slides/talk.md#2", "tests.md#1"]
"step-02.1" = ["moves.md#1"]
```

Keep every slide file in a slides folder of a walk. The page serves slides from those folders only. See
[Slides for moves](walks.md#slides-for-moves).

## Add the recipes

These recipes run timewalk from GitHub on the clone of the project. `just present` checks the walks first,
and stops if a check finds an error:

```just
timewalk := "uvx --from git+https://github.com/rahuldave/timewalk@v1.0.14"

# Open the walks: just present, or just present tutorial for another walk
present walk="" *args:
    {{ timewalk }} timewalk-check repo --replay worktree --toc toc.toml
    {{ timewalk }} timewalk repo --replay worktree --toc toc.toml {{ if walk == "" { "" } else { "--walk " + walk } }} {{ args }}

# Check the notes and slides of every walk against the steps
check:
    {{ timewalk }} timewalk-check repo --replay worktree --toc toc.toml

# Make the PDF of one walk's slides: just pdf tests
pdf walk="":
    {{ timewalk }} timewalk-pdf --toc toc.toml {{ if walk == "" { "" } else { "--walk " + walk } }} -o build/slides.pdf
```

## Check and present

1. Run `just check`. It names each problem, for example a step with no slides, a move with no notes, or a
   slide that does not exist. [The checks](walks.md#the-checks) lists them.
2. Run `just present`. Open the **Room** window for the class. Change the walk with the menu in the step bar.
3. Before the class, go through every walk, step and move in two windows. See
   [A check the evening before](class.md#a-check-the-evening-before).
