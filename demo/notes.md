# Presenter notes for the demo repository

Private: only the presenter page shows these.

## step-00 Where everything starts
time: 0:00

- Say what the project will become before showing any code.
- Point at the step bar: five steps, we are at the first.

$ ls -la
main$ git log --oneline --decorate

## step-01 A file arrives
time: 0:02

The functions work. **Nothing checks them**, nobody has tidied them, and nothing says what `name` is.

- Open `src/greet.py` on the projector.
- Ask what bothers people about it.

$ python3 -c "import sys; sys.path.insert(0, 'src'); from greet import shout; print(shout('class'))"

## step-02 Its tests
time: 0:04

Four tests. Ask: what would you test that is missing?

Then format the code **live**. The file list marks `src/greet.py` as edited, and the reader's
"Edits since the step" view shows what ruff changed. The tests still pass.

$ uvx pytest -q
$ uvx ruff format
$ uvx pytest -q
runs$ for epoch in 1 2 3 4 5; do echo "epoch $epoch"; sleep 2; done

The demo runs with `--discard-edits`, so the step bar warns that the edit will be discarded, and moving
on throws it away without asking. Without that flag a move asks first and stashes the edits.

## step-03 Formatting, committed
time: 0:07

The same change ruff made live at the last step, now in the history, with its rule in `pyproject.toml`.
Show the changes view for `src/greet.py` and compare it with what the class saw.

$ uvx ruff format --check
$ uvx pytest -q

## step-04 Its types and docs
time: 0:09

Show the changes view for `src/greet.py`. The behaviour is identical; the signature now explains itself.

$ uvx pytest -q
