# A tour of the demo

The demo is a small project, [timewalk-demo](https://github.com/rahuldave/timewalk-demo), with five
steps. Two Python functions arrive untidy, get tests, get formatted, and get types and documentation.
timewalk holds it as a git submodule in `demo/timewalk-demo`, with its notes in `demo/notes.md` and its
slides in `demo/slides/`.

```
just demo
```

`just demo` starts timewalk with `--discard-edits`, so nothing typed during the tour needs undoing. Open
the presenter address on your own screen as well as the projector address. Each step below says
what to look at and what to try.

## step-00: an empty project

A README and a `.gitignore`. The step bar shows five chips; the note under it is the tag's message.

- Press **Right** to move to the next step, **Left** to come back.
- Open the **Main** tab and run `git log --oneline --decorate`: the whole history, from the repository
  you started from, while the replay copy stands at step-00.

## step-01: a file arrives

`src/greet.py` arrives the way it was first written: odd spacing, mixed quotes, a function on one line.
It is marked **new** in the file list.

- The step has two slides. Press **Down** and **Up** to move between them.
- Click `greet.py` to read it. Try **Slides**, **Both** and **Code** at the top right.

## step-02: its tests, and a formatter run live

Four tests and a `pyproject.toml` that tells pytest where the code is. The code is still untidy.

- On the presenter page, click `uvx pytest -q`: it runs on the projector's terminal.
- Click `uvx ruff format`. Within a second `src/greet.py` is marked **edited**, and the reader offers
  **Edits since the step**: exactly what ruff changed. See [Live edits](edits.md).
- Look at the step bar: it warns that the edited file will be discarded on the next move. The demo runs
  with `--discard-edits`, because its replay copy is throwaway.
- Press **Right**. The move drops the edit without asking. Without the flag, timewalk would ask first and
  stash it. See [Live edits](edits.md#moving-with-edits).

![What ruff changed at step-02, shown live in the reader](images/edits.png)

## step-03: the formatting, committed

The same change ruff made live, now in the history, with its rule in `pyproject.toml`. This step shows a
**document** instead of slides: one longer Markdown page that scrolls. See
[Slides and documents](slides.md#a-document-instead-of-slides).

- Open `src/greet.py` and choose **Changes in this step**: it matches what the class watched ruff do.

## step-04: types and docs

Each parameter gets a type and a comment, each function a one-line docstring.

- **Changes in this step** on `greet.py` shows the signature growing.
- Run `uvx pytest -q` again from the presenter page: the behaviour did not change.

## What the demo is made of

| Part | Where |
|---|---|
| The history | [github.com/rahuldave/timewalk-demo](https://github.com/rahuldave/timewalk-demo), five commits, five annotated tags |
| The notes | `demo/notes.md`: a section per step, with times and commands |
| The slides | `demo/slides/talk.md`, a picture, and `formatting.md`, the document at step-03 |
| The manifest | `demo/slides/slides.toml`: which slides go with which step |

The demo has no `justfile`, so the recipe buttons say "no justfile here". Its commands use `uvx`, which
runs ruff and pytest without installing them.
