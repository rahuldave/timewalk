# timewalk

timewalk lets you teach a project through a replay of how its authors built it. Each step of the project is a git
tag, a name that git keeps for one commit. At every step, timewalk shows the slides that you wrote for the
step. It also shows the files as they were then, the changes that the step made, and real terminals in
the repository at that step.

A second page, the presenter page, goes on your own screen. It is the projector page with your private
notes beside it and a clock across the top. What you do on the presenter page happens on the projector
page too.

![The projector page at the second step of the demo, with a slide, the files, the file that the step added, and a terminal at that step](images/projector.png)

## Why replay a history

A finished project does not show how its authors built it. Its history, read in order, shows each
decision at the time somebody made it. For example, a file arrives, then its tests, then a formatter,
then types.

timewalk shows the project at one step of that history at a time. When you move to a step, the files,
the slides and the terminals all go to that step. Run the commands of the step live, and then move on.

## Quick start

You need these programs:

- `uv`
- `git`
- Chrome, Edge or another recent browser

```
git clone https://github.com/rahuldave/timewalk
cd timewalk
just demo
```

`just demo` gets the sample project and opens it. The sample project is a git submodule. If you do not have `just`,
run the commands of the recipe yourself:

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

Open the first address on the projector. Open the second address on your own screen. Then take [the tour of the
demo](demo.md).

To use timewalk on your own project, run this command:

```
uv run timewalk.py ~/code/project --notes notes.md --slides slides/slides.toml
```

## What is where

| Page | What it covers |
|---|---|
| [A tour of the demo](demo.md) | The sample project, one step at a time, with what to try at each step |
| [How it works](model.md) | The folders, the worktree, what each button does in which folder, and where timewalk keeps each thing |
| [The projector page](projector.md) | Steps, slides, files, the three views of the reader, recipes and the keyboard |
| [The presenter page](presenter.md) | The projector page with your notes, the clock and commands that run with one click |
| [Live edits](edits.md) | A command changes files at a step, and the page shows each change when it occurs |
| [The terminals](terminals.md) | What each tab is, where it starts, long runs and focus |
| [Making the steps](steps.md) | Tags, their notes, and how to make a history that is good to walk through |
| [Slides and documents](slides.md) | The manifest, Markdown slides, pictures, PDFs, documents and the PDF handout |
| [Presenter notes](notes.md) | The notes file, with titles, planned times and commands |
| [The replay copy](replay.md) | The second working copy that timewalk moves, and what timewalk never does to your repository |
| [Python projects](python.md) | Environments when each step has its own lockfile |
| [Safety](safety.md) | A terminal in a web page, and how timewalk keeps it to you |
| [Reference](reference.md) | Every option, recipe and file format in one place |
| [Development](development.md) | The code, the tests, the screenshots and this site |

## Keep your class material out of the project

Keep the material that you present with outside the repository that your students clone. The material
is the slides, the notes and timewalk itself. The repository holds only the project. The author made
timewalk for a class with this layout:

- one repository, whose history is the lesson
- one class folder beside it, with the notes, the slides and a `justfile`
- a `present` recipe in that `justfile`, which starts timewalk

The demo has the same layout. Its history is its own repository, and its notes and slides are in the
`demo/` folder of timewalk.
