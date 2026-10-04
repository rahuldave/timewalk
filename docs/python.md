# Python projects

In a Python project managed with [uv](https://docs.astral.sh/uv/), every step has its own
`pyproject.toml` and `uv.lock`, because they are tracked. The `.venv` is not tracked, so it does not
change when you move. It is the replay copy's own, separate from your repository's, and moving never
touches it.

## Through `uv run`, going forward needs nothing

`uv run` syncs the environment to the step's lockfile before it runs, so does a `just` recipe that calls
it. Move to a step that adds a package, run `uv run pytest` or `just test`, and the package is installed
first.

## Three catches

- **Going backwards leaves later packages installed.** `uv run` adds and updates but does not remove.
  Step back from a step that added a package and it stays: code at the earlier step that imports it by
  mistake still works, and nobody notices.
- **Bare commands do not sync.** `python`, `pytest` or anything run straight from `.venv/bin` uses
  whatever was installed last.
- **A lockfile out of step with `pyproject.toml` is rewritten** by a plain `uv run`. That is an edit to a
  tracked file, so the next move asks about it. `uv run --locked` stops with an error instead.

## Exact environments at every step

Run `uv sync --locked` when you arrive at a step. It installs what the step adds, removes what it does
not list, and refuses a stale lockfile. On a warm cache it takes about a second. Make it the first
command of each step in your notes:

```markdown
## step-05 The model
time: 0:21

$ uv sync --locked
$ just test
```

## timewalk's own Python stays out

timewalk itself runs with `uv run`, in an environment of its own. The shells it starts take that
environment off `PATH`, so `python` in a class shell is the project's, never timewalk's. See
[The terminals](terminals.md#the-shells-environment).
