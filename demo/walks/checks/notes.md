# Notes for the walk "Only the checks"

A short walk through two steps of the demo: the tests, and the formatter.

## step-02 The tests
time: 0:00

Four tests check the two functions. Run them, then let ruff format the code, and run them again.

$ uvx pytest -q
$ uvx ruff format
$ uvx pytest -q

## step-03 The formatter's rule
time: 0:03

The rule of the formatter is now in `pyproject.toml`, so every later change keeps the same layout.

$ uvx ruff format --check
