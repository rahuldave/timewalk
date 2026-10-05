# The replay copy

timewalk never moves the repository that you give it. It moves a second working copy instead. timewalk
makes the copy beside your repository and names it `<repo>-replay`.

```
timewalk: stepping in /home/you/code/project-replay
```

## A git worktree

The replay copy is not a clone and it is not a branch. It is a **git worktree**, a second working folder
attached to the same git repository. Its `.git` is a file of one line that points back into the `.git` of
your repository. Also, `git worktree list` in your repository shows it.

```
$ git worktree list
/home/you/code/project          ca6f48d [main]
/home/you/code/project-replay   e6b4b02 (detached HEAD)
```

A commit is a saved state of the files, and a tag is a name for one commit. HEAD is the commit that a
working copy stands on. A detached HEAD stands on a commit directly, and not on a branch.

- **It shares everything committed.** The commits, tags and objects belong to your repository. So timewalk
  copies and fetches nothing, and every tag is there at once.
- **It has its own HEAD, index and untracked files.** It stands on the commit of a step, with a detached
  HEAD. A move runs `git checkout --detach` with the commit of the next step. Its `.venv`, outputs and
  stashes belong to it. A stash is a set of edits that git keeps aside, to bring back later.
- **It gets only commits.** Edits, untracked files and a `uv lock` that you did not commit in your
  repository never reach it. Restart timewalk after you tag again, because it reads the tags when it starts.
- **timewalk makes it once and uses it again.** The next start finds it. To remove it, run
  `git worktree remove <repo>-replay`.

With `--in-place`, timewalk moves your repository itself. Use `--in-place` only on a copy that you can
let timewalk move.

## What timewalk does and does not do

- **It never moves the repository that you give it.** The exception is when you pass `--in-place`.
- **It never deletes an untracked file.** A command at one step can write an environment, a database or
  the output of a run. That file is still there at the next step. A later step can have a tracked file
  where an untracked file is. Then timewalk refuses the move and names the file.
- **It never discards an edit.** The page shows edits to tracked files as they happen. A move with edits
  asks first. Then it keeps the edits aside with `git stash`, with a label that names their step. See
  [Live edits](edits.md).
- **The `--discard-edits` flag is the one exception, and you must ask for it by name.** Use it for a
  replay copy where all that you type is for one use only. With the flag, a move runs `git checkout -f`.
  It drops edits without a question. It replaces an untracked file only where the step has a file of the
  same name.
- **The file view cannot write.** No route of the server changes a file in your repository or the replay
  copy. The one route that writes is **Save** in the notes column, and it writes only the notes file. The
  notes file must be outside both copies. The terminals can change files, as any terminal can.

## Files that stay in the replay copy

Untracked files stay through moves. So the replay copy keeps what the commands of the class make, for
example a `.venv`, caches, databases and the outputs of runs. You usually want these files. For example,
the results of a training run are still there two steps later. To start clean, remove the worktree. Then
timewalk makes a new one.
