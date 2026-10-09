# Notes for the demo

The script of each step: what to read and which commands to run. Lines that start with "> " are cues for
a presenter. timewalk shows them in their own shade.

## step-00 Where everything starts
time: 0:00

> Say: what the project will become, before you show any code.

The project starts with a README and nothing else. The step bar shows five steps, and this is the first.

$ ls -la
main$ git log --oneline --decorate

## step-01 A file arrives
time: 0:02

The two functions work, but nothing checks them, and nobody has tidied them. Nothing says what `name` is.
Open `src/greet.py` and read it.

> Ask: what bothers you about this file?

$ python3 -c "import sys; sys.path.insert(0, 'src'); from greet import shout; print(shout('class'))"

## step-02 Its tests
time: 0:04

Four tests arrive. Run them first. Then format the code with ruff. The file list marks `src/greet.py` as
edited, and the reader's "Your edits" view shows what ruff changed. Run the tests again: they
still pass.

> Ask: what would you test that is missing?

$ uvx pytest -q
$ uvx ruff format
$ uvx pytest -q
runs$ for epoch in 1 2 3 4 5; do echo "epoch $epoch"; sleep 2; done

> Note: the demo runs with `--discard-edits`, so the step bar warns about the edit, and the next move
> throws it away without asking.

## step-03 Formatting, committed
time: 0:07

The change that ruff made at the last step is now in the history, and its rule is in `pyproject.toml`.
Open `src/greet.py` in "Last change", and compare it with what ruff did.

$ uvx ruff format --check
$ uvx pytest -q

## step-04 Its types and docs
time: 0:09

Each parameter now has a type and a comment, and each function has a docstring. Open `src/greet.py` in
"Last change". The behaviour is the same, and the signature now explains itself.

> Say: run the tests once more, and point out that nothing broke.

$ uvx pytest -q
