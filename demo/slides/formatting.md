# Formatting, committed

At the last step the class watched `uvx ruff format` rewrite `src/greet.py`. This step is that
same change, committed, with the rule it follows written down. This page is a **document**, not a
deck: it scrolls, and has no slide arrows.

## What the formatter changed

| Before | After |
|---|---|
| `def greet( name,excited = False ):` | `def greet(name, excited=False):` |
| `'Hello, '+name` | `"Hello, " + name` |
| `def shout(name):  return ...` on one line | the body on its own line |
| one blank line between functions | two |

Nothing else. The tests pass before and after, because a formatter only moves layout: it never
changes what the code does.

## Why let a tool decide

- **Review is about meaning.** A diff that mixes layout with logic hides the logic. Once every file
  is formatted, a diff shows only what changed in behaviour.
- **Nobody argues about it.** Quote style, spacing and line breaks are settled by the tool, so a
  team does not spend time on them.
- **It is cheap to keep.** `uvx ruff format --check` says whether anything would change, which is
  what a pre-commit hook or CI runs.

## Where the rule lives

```toml
[tool.ruff]
line-length = 100
```

It is in `pyproject.toml`, so everyone who runs ruff in this project formats the same way, whatever
their editor does.

## Try it

```
uvx ruff format --check
uvx ruff format --diff
```

The first prints nothing to fix. The second shows the changes ruff would make: none, at this step.

---

Next: types and docs, which change what a reader can learn from a signature.
