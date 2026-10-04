# The replay copy

timewalk never moves the repository you point it at. It steps a second working copy, made beside it,
called `<repo>-replay`.

```
timewalk: stepping in /home/you/code/project-replay
```

## A git worktree

The replay copy is neither a clone nor a branch. It is a **git worktree**: a second working folder
attached to the same repository. Its `.git` is a one-line file pointing back into your repository's
`.git`, and `git worktree list` in your repository shows it.

```
$ git worktree list
/home/you/code/project          ca6f48d [main]
/home/you/code/project-replay   e6b4b02 (detached HEAD)
```

- **It shares everything committed.** Commits, tags and objects are the repository's own, so nothing is
  copied or fetched, and every tag is there at once.
- **It has its own HEAD, index and untracked files.** It stands on a step's commit with a detached HEAD.
  Moving is `git checkout --detach` to the next step's commit. Its `.venv`, outputs and stashes are its
  own.
- **It is built from commits only.** Uncommitted edits, untracked files, and an uncommitted `uv lock` in
  your repository never reach it. Tags are read when timewalk starts, so restart it after re-tagging.
- **It is made once and reused.** The next start finds it. Remove it with
  `git worktree remove <repo>-replay`.

`--in-place` steps your repository itself instead. Use it only on a copy you do not mind moving.

## What timewalk will and will not do

- **It never moves the repository you point it at.** Unless you pass `--in-place`.
- **It never deletes an untracked file.** Whatever a command wrote at one step (an environment, a
  database, a run's output) is still there at the next. If a later step has a tracked file where an
  untracked one sits, the move is refused and the file is named.
- **It never discards an edit.** Edits to tracked files are shown as they happen. Moving with edits asks
  first, then sets them aside with `git stash`, labelled with the step they were made at. See
  [Live edits](edits.md).
- **The file view cannot write.** There is no route that changes a file. The terminals can, as any
  terminal can.

## Things that live in the replay copy

Because untracked files survive moves, the replay copy collects what the class's commands make: a
`.venv`, caches, databases, run outputs. That is usually what you want: a training run's results are
still there two steps later. To start clean, remove the worktree and let timewalk make a new one.
