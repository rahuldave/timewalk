# What timewalk does with git

This page lists each thing that timewalk does with git, and says why. It is for an author, a teacher,
or a program that builds a class. Other pages give the details, and this page links to them.

Some git terms come first:

- **A commit** is one saved state of the files. **A branch** is a name for a line of commits, and it
  moves when you commit. **A tag** is a name that stays on one commit.
- **A ref** is any such name. Git keeps refs in folders, for example `refs/heads/` for branches and
  `refs/tags/` for tags. A folder of refs is a namespace.
- **HEAD** is the commit that a working copy stands on. On a detached HEAD, the working copy stands on a
  commit directly, and not on a branch.
- **The index** is what `git add` stages, the list of changes that the next commit holds.

## Your repository and the replay copy

timewalk never moves your repository, and never writes to it. It writes no config, hook, branch, tag or
stash there. It moves a second copy instead, the replay copy, `<repo>-replay`. The replay copy is a clone
of your repository on the branch `timewalk/replay`. A clone is a repository of its own. So what a learner
does in the replay copy stays there.

### How timewalk makes the replay copy

The first start makes the replay copy in a folder `.<repo>-replay.making`, and then renames it:

```
git clone --no-checkout --origin home <your repository> .<repo>-replay.making
git checkout -B timewalk/replay <the commit of the first step>
```

- **Your repository is the remote `home`.** A remote is a name for another repository that git fetches
  from.
- **The clone has no other branch.** timewalk deletes the copies of your branches that `git clone` made.
- **A half made copy never stays.** If a start stops before the rename, the next start deletes the
  `.making` folder and begins again.
- **The objects of the clone are its own.** Git links their files from your repository where it can, and
  copies them on another disk. Alternates are a list of other repositories that git reads objects from.
  The clone has none, unless your repository has them itself, for example after `git clone --shared`. So
  your repository can move or lose objects, and the replay copy still works.

See [How timewalk makes the replay copy](replay.md#how-timewalk-makes-the-replay-copy).

### The refs in the replay copy

timewalk fetches from your repository into refs of its own:

| Ref in the replay copy | Holds |
|---|---|
| `refs/remotes/home/*` | Your branches, as `home/main` and so on |
| `refs/timewalk/home-tags/*` | Your tags, as last fetched |
| `refs/timewalk/home-head` | Your HEAD, for `--commits` |
| `refs/tags/*` | The tags that a learner sees. timewalk copies your tags here, with the rule below |
| `refs/heads/timewalk/replay` | The branch that each move resets to the commit of the step |
| `refs/heads/timewalk/saved/*` | The saved branches, where a move keeps the work of a learner |
| `refs/timewalk/placed` | The commit where timewalk last put the branch. A later commit is a commit of the learner |

Your tags go into a namespace of their own because a learner can make tags too. timewalk copies a tag of
yours to `refs/tags/` only in two cases:

- The learner has no tag of that name.
- The tag of that name is the copy that timewalk made before.

So a tag that you moved moves in the replay copy too, and a tag that you deleted goes. A tag that the
learner made is never moved or deleted.

### When timewalk fetches

timewalk fetches at each start, at each change of walk, and before a move to a commit that the clone
does not have yet. So a tag that you made or moved between sessions, for example with `just retag`, is in
the replay copy at the next start. timewalk reads the list of steps from the tags of your repository when
it starts, and again when you change the walk. Restart timewalk after you tag again.

### The place across restarts

The current step is the HEAD of the replay copy, so a restart finds it. In a tutorial, one commit can be
two places. The commit of a step is the last move of that step, and also the **Start** of the next step.
So timewalk writes the place in a file in the git folder of the replay copy, `timewalk-place.json`. That
folder is outside the files of the copy. So git does not track the file, and the file list does not show
it.

### A replay copy from an older version

Older versions made the replay copy as a git worktree. A worktree is a second working folder attached to
your repository. It shares your config, branches and stashes. timewalk still uses such a copy, on a
detached HEAD, and writes no saved branch in it. A move with edits asks first, and then runs `git stash`.
See [A replay copy from an older version](replay.md#a-replay-copy-from-an-older-version).

### With `--in-place`

`--in-place` is the one exception to the rule. timewalk makes no replay copy, and moves your repository
itself. It writes no saved branch there, because the branches of your repository are your own. So a
move does these things:

1. If an untracked file is in the way of the step, the move stops and names the file.
2. If you have edits, the page asks first. If you agree, timewalk runs
   `git stash push --message "timewalk: edits made at step-NN"`.
3. timewalk runs `git checkout --detach` to the commit of the step.

An untracked file is a file that git does not record. An edit is a change to a tracked file that nobody
committed. timewalk refuses `--discard-edits` with `--in-place`. In a tutorial, timewalk also writes
`timewalk-place.json` into the git folder of your repository. See
[With `--in-place`](edits.md#with---in-place).

## Steps and moves as git

A step is a tag. By default, the steps are the tags that match `step-*`, in the order of their names.

- **An annotated tag** stores a message. The message is the note of the step, which the audience sees in
  the status band.
- **A lightweight tag** has no message. It works as a step with no note.
- **`--tags 'v*'`** takes the tags that match another pattern.
- **`--commits`** takes every commit on the first-parent line of the current branch of your repository.
  The first-parent line is the line of commits that git follows back through the first parent of each
  merge. The note is the body of the commit message.

See [Making the steps](steps.md).

### Moves of a tutorial

A move is one small commit between two tags. The moves of a step are the commits on the first-parent
line after the tag before it, up to the tag of the step. A step of one commit has no moves, and the first
step has none.

```
step-01: count the words              tag step-01
step-02.1: a test of counting         move step-02.1
step-02.2: a test of an empty text    move step-02.2
step-02: the tests pass               tag step-02, move step-02.3
```

- **The subject of each move starts with its name,** `step-NN.k:`. `timewalk-check` reports a subject with
  the wrong name.
- **The last commit keeps the subject of the step,** `step-NN:`, and carries the tag.

See [Commits for a tutorial](build-walks.md#commits-for-a-tutorial).

### Walks on some steps, or on one part

A walk in `toc.toml` names a glob of tags, and can name some of its steps. timewalk then keeps only those
steps, in their order. A tutorial takes every tag of its glob, because its moves are the commits between
two tags. For a tutorial on one part of the history, give the part tags of its own on a branch, for
example `hooks-00` to `hooks-02`. The replay copy fetches every tag, so the commits of that branch are
in it too. See [Several walks](walks.md).

## What is checked out, and when

timewalk checks out a commit at four times. The first is when it makes the replay copy. The others are
when a window asks for a move, or for another walk. The last is in watch mode with sync on, when a window
shows a slide of the next move. A move resets the branch `timewalk/replay` to the commit:

```
git checkout -B timewalk/replay <commit>
```

| Walk | A step button, Left or Right | A move button |
|---|---|---|
| Narrative | Checks out the tag of the step | None |
| Tutorial, do mode | Checks out the tag before the step, the **Start** of the step. The first step, and a step of one commit, check out their own tag | **Catch me up** checks out the commit of the move that the learner works on. **Done** checks out nothing |
| Tutorial, watch mode | Checks out the tag before the step, as in do mode | **Show** checks out the commit of the next move |

### Do mode

In do mode, the learner makes each move by hand from the notes. The code does not move until the
learner presses **Catch me up**, **Start**, **↺ Restart step**, or a step button, or another walk is chosen. So the working copy
holds the commit of the step before, with the work of the learner on top as edits and untracked files.

**Done** changes only a count that the server keeps. "Your files match" compares the files on the disk
with the commit of the move, by content, and changes nothing. When the learner goes on to the next step,
the move keeps the work of the learner on a saved branch named after the step. Then it checks out the
tag. So each step starts from the code of the authors, and not from the copy of the learner.

### Watch mode

In watch mode, **Show** checks out the commit of each move in turn. The learner reads the change and runs
the anchor commands. An anchor command is one of the last commands of a move, and it shows what the move
did. See [Do mode and watch mode](walks.md#do-mode-and-watch-mode).

### Why the moves go in order

The moves go forward one at a time, in both modes. The way back is only the **Start** of the step. The
reason is the environment, and not git.

The notes of each step start with `just setup`, so any step is a safe place to land. A move has no
`just setup` of its own. It builds on what the moves before it did to the environment, for example a
package that a move added. A skip would miss that, and a move back would leave it in place. So after
**Start**, run `just setup` again.

### Apply

In do mode, the **Apply** bar in **Next change** types a git command into the **At this step** tab, and
runs it:

| Button | Types |
|---|---|
| **⇣ Apply this file** | ``(cd "$(git rev-parse --show-toplevel)" && git diff A B -- path \| git apply --3way)`` |
| **⇣ Apply the whole move** | `git cherry-pick --no-commit B` |

Here `A` is the commit before the move, and `B` is the commit of the move. `git apply --3way` merges the
change into the file when the file differs. A cherry-pick copies the change of a commit, and
`--no-commit` leaves it as edits. HEAD does not move.

The page itself never writes a file. The shell runs the command, and the learner sees it. See
[Apply](walks.md#apply).

## How a move keeps the learner's work

In the replay copy, a move never asks, and never discards work. It keeps the work on a saved branch
first, `timewalk/saved/<place>`, named after the step or the move where the learner did it. A second save
at the same place gets `-2`, then `-3`. A move does these things, in this order:

1. **It finds the untracked files in the way.** A file is in the way when HEAD or the new commit has a
   file of its name, or a folder of its name. A file is also in the way when one of those commits has a
   file where its path has a folder. timewalk checks both commits, because `git reset --hard` puts back
   the files of HEAD. Ignored files count too.
2. **It folds case where git does.** If `core.ignorecase` is true, as on a Mac, `Notes.md` and `notes.md`
   are one name.
3. **It refuses a nested repository in the way.** A git repository inside the replay copy cannot go on a
   branch. The move stops and names the folder. Move the folder out, and move again.
4. **It waits for the index.** If another git holds `index.lock`, the move waits for about one and a half
   seconds. Then it refuses, before it saves anything.
5. **It commits what the learner staged,** if that differs from HEAD. timewalk copies the index to a
   temporary file, and runs `git write-tree` and `git commit-tree` with the copy.
6. **It commits the edits and the files in the way,** on top, from a second copy of the index. It first
   clears the flags that hide edits from git, assume-unchanged and skip-worktree. It reads each path
   literally, so a file named `*.out` is that one file.
7. **It names the work.** `git branch timewalk/saved/<place>` points at the top commit. The commits of the
   learner since `refs/timewalk/placed` are under it, on `timewalk/replay` or on a detached HEAD. A branch
   that the learner made keeps its own commits, so timewalk does not copy them.
8. **It ends a half done command.** If a rebase, `git am`, a cherry-pick or a revert is half done, timewalk
   runs it with `--quit`. The work is already on the saved branch.
9. **It deletes only what it kept.** It deletes the files in the way, and then the folders that they
   leave empty.
10. **It moves.** It runs `git reset --hard`, and then a plain `git checkout -B timewalk/replay <commit>`.
    It never uses `--force`. If timewalk missed a file, git refuses, and nothing is overwritten.

If the delete or the checkout fails, timewalk puts the kept work back into the files, with
`git restore --source <the commit of the work> --worktree`. The message names the saved branch.

Until the move, the files, the index and the stash of the learner do not change. Steps 5 to 7 use copies
of the index, so they write nothing in the working copy. See
[Moving with edits](edits.md#moving-with-edits) and [Saved branches](replay.md#saved-branches).

### Get the work back

Every window except the Room window names the saved branch after a move. Run these commands in a
terminal at the step:

```
git branch --list 'timewalk/saved/*'                   # list the saved branches
git show timewalk/saved/step-02                        # see the last commit of one
git log --stat timewalk/saved/step-02                  # see all of its commits
git restore --source timewalk/saved/step-02 -- src/greet.py   # bring one file back, as an edit
git log --all --oneline --graph                        # see every line of commits, saved ones too
```

To take the work into your repository, fetch the branch from the replay copy, for example
`git fetch ../project-replay timewalk/saved/step-03:my-step-03`.

### What a move does not save

- **Untracked files that nothing replaces** are not on a saved branch. They stay on the disk, untouched,
  through every move.
- **Ignored files,** for example `.venv`, databases and the outputs of runs, stay on the disk. A move
  touches one only when it is in the way, and then keeps it on the saved branch first.
- **Stashes, config and other branches of the learner** stay in the replay copy, as they are.

## `just setup` and git

The presenter or the learner runs `just setup` at the start of each step, in the replay copy, from the
first command of the notes. timewalk does not run it. It makes the environment and the
artifacts of the step. An artifact is a file that a step needs and that git does not hold, for example a
corpus or a report.

- **`just setup` never touches git.** It makes no commit, branch or tag, and changes no config or hook.
- **A change to git is a recipe of its own.** For example, a step that sets `core.hooksPath` to install a
  hook gives the learner a separate recipe to run. A hook is a program that git runs at a fixed point,
  for example before a commit.
- **Artifacts go in folders that git ignores.** So they are untracked, and they stay through every move.

See [The recipe `just setup`](authoring.md#the-recipe-just-setup).

## Git problems that timewalk handles

- **A lock on the index.** The edits watcher runs `git status` each second, and a shell prompt can run git
  too. Each one holds `index.lock` for a moment. A git command of timewalk that meets the lock waits, and
  runs again up to three times. timewalk never runs `git stash` twice. A move waits, and then refuses
  before it saves anything.
- **Paths with spaces or accents.** Without `-z`, git quotes such a path. The commands of a move, the
  edits watcher and "Your files match" read paths with `-z`, for example `git status --porcelain -z`.
- **Tags that move between sessions.** Each start fetches your tags again. It copies a moved tag to the
  replay copy if the learner did not make a tag of that name.
- **Commits of a learner on a detached HEAD.** A move keeps them on a saved branch, as it keeps commits on
  `timewalk/replay`.
- **No git identity.** If the replay copy has no `user.name` or `user.email`, timewalk names itself as the
  author of its commits, `timewalk <timewalk@localhost>`.
- **Signed commits.** `git commit-tree` does not read `commit.gpgsign`. So a saved branch never waits for a
  key or a password.
- **Hooks.** `git commit-tree` runs no commit hooks, so no `pre-commit` or `commit-msg` hook runs. A new
  replay copy has only the hooks of your git template folder, `init.templateDir`, if you set one. A hook
  there, a hook that the learner installs in the replay copy, or a global `core.hooksPath` can run during a
  move. Examples are `post-checkout` and `reference-transaction`.
- **Alternates.** The replay copy has none, unless your repository has them, so its objects do not depend
  on your repository.

## The commands that timewalk runs

[How timewalk works](model.md) lists the git commands of each control, in which folder, in
[What each control does](model.md#what-each-control-does). It also gives
[One move, in order](model.md#one-move-in-order).
