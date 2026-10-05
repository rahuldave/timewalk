# timewalk

timewalk lets you teach a project by replaying how it was built. Each step of the project is a git tag.
For every step it shows the slides you wrote for it, the files as they were then, what that step changed,
and real terminals in the repository at that step. A second page, on your own screen, is the same page
with your private notes beside it and a clock across the top; whatever you do there happens on the
projector too.

![The projector page at the demo's second step: a slide, the files, the file the step added, and a terminal at that step](images/projector.png)

## Why replay a history

A finished project hides how it got there. Read in order, its history shows each decision when it was
made: a file arrives, then its tests, then a formatter, then types. timewalk makes that history something
you can stand in. Move to a step and the files, the slides and the terminals are all at that step. Run
the step's commands live. Then move on.

## Quick start

You need `uv`, `git`, and Chrome, Edge, or another recent browser.

```
git clone https://github.com/rahuldave/timewalk
cd timewalk
just demo
```

`just demo` fetches the sample project (a submodule) and opens it. Without `just`, run what the recipe runs:

```
git submodule update --init demo/timewalk-demo
uv run timewalk.py demo/timewalk-demo --notes demo/notes.md --slides demo/slides/slides.toml --discard-edits
```

timewalk prints two addresses:

```
timewalk: 5 steps in .../demo/timewalk-demo
timewalk: stepping in .../demo/timewalk-demo-replay
timewalk: audience   http://127.0.0.1:8765/?t=...
timewalk: presenter  http://127.0.0.1:8765/presenter?t=...
```

Open the first on the projector and the second on your own screen. Then take [the tour of the
demo](demo.md).

On your own project:

```
uv run timewalk.py ~/code/project --notes notes.md --slides slides/slides.toml
```

## What is where

| Page | What it covers |
|---|---|
| [A tour of the demo](demo.md) | The sample project, step by step, with what to try at each |
| [How it works](model.md) | The folders, the worktree, what every button does where, what is kept where |
| [The projector page](projector.md) | Steps, slides, files, the reader's three views, recipes, the keyboard |
| [The presenter page](presenter.md) | The same page, with your notes, the clock and one-click commands |
| [Live edits](edits.md) | A command changes files at a step, and the page shows what it did as it happens |
| [The terminals](terminals.md) | What each tab is, where it starts, long runs, focus |
| [Making the steps](steps.md) | Tags, their notes, and how to build a history worth walking |
| [Slides and documents](slides.md) | The manifest, Markdown slides, pictures, PDFs, documents, the PDF handout |
| [Presenter notes](notes.md) | The notes file: titles, planned times, commands |
| [The replay copy](replay.md) | The second working copy timewalk moves, and what it never does to yours |
| [Python projects](python.md) | Environments when every step has its own lockfile |
| [Safety](safety.md) | A terminal in a web page, and how it is kept to you |
| [Reference](reference.md) | Every option, recipe and file format in one place |
| [Development](development.md) | The code, the tests, the screenshots and this site |

## Keep your class material out of the project

What you present with, the slides, the notes and timewalk itself, lives outside the repository your
students clone. The repository holds only the project. timewalk was built for a class kept this way: one
repository whose history is the lesson, one folder beside it with the notes, the slides and a `justfile`
whose `present` recipe starts timewalk. The demo is shaped the same way: its history is its own
repository, and its notes and slides are in timewalk's `demo/` folder.
