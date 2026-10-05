# Python projects

In a Python project that uses [uv](https://docs.astral.sh/uv/), every step has its own `pyproject.toml`
and `uv.lock`, because they are tracked files. The lockfile, `uv.lock`, records the exact version of each
package. The `.venv` is not tracked, so it does not change when you move. It belongs to the replay copy and
not to your repository, and a move never changes it.

## Moves forward with `uv run`

`uv run` makes the environment match the lockfile of the step before it runs a command. A `just` recipe
that calls `uv run` does the same. If you move to a step that adds a package, run `uv run pytest` or
`just test`. uv installs the package first.

## Problems with `uv run`

- **A move backwards leaves later packages installed.** `uv run` adds and updates packages, but it does
  not remove them. Go back from a step that added a package, and the package stays. Code at the earlier
  step can import it by mistake and still work, and nobody sees the mistake.
- **Bare commands do not sync.** To sync is to make the environment match the lockfile. `python`, `pytest`
  and other commands that run straight from `.venv/bin` use the packages that were last installed.
- **A plain `uv run` writes the lockfile again when it does not match `pyproject.toml`.** The new
  lockfile is an edit to a tracked file, so the next move asks about it. `uv run --locked` stops with an
  error instead.

## The exact environment at every step

Run `uv sync --locked` when you arrive at a step. It installs what the step adds and removes what the step
does not list. It also refuses a lockfile that does not match. With a warm cache, it takes about a second.
Make it the first command of each step in your notes:

```markdown
## step-05 The model
time: 0:21

$ uv sync --locked
$ just test
```

## The Python of timewalk stays out

timewalk itself runs with `uv run`, in an environment of its own. The shells that timewalk starts take
that environment off `PATH`. So `python` in a shell of the class is the Python of the project, and never
the Python of timewalk. See [The terminals](terminals.md#the-shells-environment).
