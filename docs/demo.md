# A tour of the demo

The demo is a small project, [timewalk-demo](https://github.com/rahuldave/timewalk-demo), with five
steps. In the demo, two Python functions arrive untidy. Then they get tests, then formatting, then types
and documentation. timewalk holds the demo as a git submodule in `demo/timewalk-demo`. The notes of the
demo are in `demo/notes.md`, and its slides are in `demo/slides/`.

```
just demo
```

`just demo` starts timewalk with `--discard-edits`, so you do not need to undo anything that you type in
the tour. Open the presenter address on your own screen, and the projector address on the projector.
Each step below says what to look at and what to try.

## step-00: an empty project

The project has a README and a `.gitignore`. The step bar shows five step buttons. The note under the
step bar is the message of the tag.

- Press **Right** to go to the next step. Press **Left** to come back.
- Open the **Main** tab, and run `git log --oneline --decorate`.

The log shows the whole history of the repository that you started from. The replay copy stays at
step-00.

## step-01: a file arrives

`src/greet.py` arrives in its first form, with odd spacing, mixed quotes and a function on one line. The
file list marks it **new**.

- The step has two slides. Press **Down** and **Up** to move between them.
- Click `greet.py` to read it.
- Try **Slides**, **Both** and **Code** at the top right.

## step-02: its tests, and a formatter run live

The step adds four tests and a `pyproject.toml`, which tells pytest where the code is. The code is still
untidy.

- On the presenter page, click `uvx pytest -q`. The command runs in the terminal on the projector.
- Click `uvx ruff format`. Within one second, the file list marks `src/greet.py` **edited**.
- Look at the reader. It now offers **Edits since the step**, which shows exactly what ruff changed. See
  [Live edits](edits.md).
- Look at the step bar. It warns that the next move discards the edited file.
- Press **Right**. The move drops the edit and does not ask. See
  [Live edits](edits.md#moving-with-edits).

The demo runs with `--discard-edits` because nobody needs the edits in its replay copy. Without the flag,
timewalk asks first, and then stashes the edit. To stash is to keep edits aside in git and bring them
back later.

![What ruff changed at step-02, shown live in the reader](images/edits.png)

## step-03: the formatting, committed

The history now has the change that ruff made live, with its rule in `pyproject.toml`. The step shows a
**document** instead of slides. A document is one longer Markdown page that scrolls. See
[Slides and documents](slides.md#a-document-instead-of-slides).

- Open `src/greet.py`, and choose **Changes in this step**.

The changes match what the class saw ruff do at step-02.

## step-04: types and docs

Each parameter gets a type and a comment. Each function gets a docstring of one line.

- Open `greet.py`, and choose **Changes in this step**. The view shows how the signature grows.
- On the presenter page, click `uvx pytest -q` again. The tests show that the behaviour did not change.

## What the demo is made of

| Part | Where |
|---|---|
| The history | [github.com/rahuldave/timewalk-demo](https://github.com/rahuldave/timewalk-demo), with five commits and five annotated tags |
| The notes | `demo/notes.md`, with one section for each step, and times and commands |
| The slides | `demo/slides/talk.md`, a picture, and `formatting.md`, which is the document at step-03 |
| The manifest | `demo/slides/slides.toml`, which says which slides go with which step |

The demo has no `justfile`, so the recipe buttons say "no justfile here". The commands of the demo use
`uvx`. `uvx` runs ruff and pytest, and you do not need to install them.
