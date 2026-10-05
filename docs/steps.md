# Making the steps

A step is a point in the history of your repository where timewalk can stand. A commit is one saved
state of the files, and a tag is a name that points at one commit. By default, the steps are the tags
that match `step-*`, in the order of their names. You do not need to change anything else in your
repository.

## Tags and their notes

```
git tag --annotate step-03 -m "The code after ruff format. Only its layout changed."
```

An annotated tag is a tag that stores a message. For an **annotated** tag, the audience sees the message
as the note of the step, in the band under the step bar. A lightweight tag (`git tag step-03`) has no
message. It also works as a step, with no note. The page shows the subject of the commit next to the name
of the step.

| Option | Steps are |
|---|---|
| (none) | tags that match `step-*`, in the order of their names |
| `--tags 'v*'` | tags that match a different pattern |
| `--commits` | every commit in the first-parent history of the current branch, oldest first. The first-parent history leaves out the commits that a merge brought in. The note is the rest of the commit message |

Give the steps names that sort in the correct order, for example `step-00` to `step-09`, and then
`step-10`. A step name can have a letter at the end, as in `step-07a` and `step-07b`, and still sort in its
place.

## A history for a class

Make the history for a class on purpose. These rules give a history that a class can follow:

- **One commit per step, one idea per commit.** For example, add the file, then its tests, then the
  formatter, then the types. The class reads the diff of each step, so keep each diff about one thing.
- **Annotated tags with a sentence for the room.** The commit message is for developers. The message of
  the tag is what the audience sees.
- **A straight line.** Put all the steps on one branch, with no merges.
- **Steps that you leave unfinished on purpose.** Two tests that fail at one step and their fix at the next step
  make a lesson. Say so in the note of the tag.
- **No tracked run outputs.** Put `.venv`, databases, checkpoints and run folders in the `.gitignore` of
  the first step. Untracked files, which git does not record, stay the same through every move.

## How to change an earlier step

Before anyone clones (copies) your repository, you can rewrite its history. Follow these steps:

1. Fix the early file in a step of its own.
2. Rebase the later steps on the new commit. A rebase copies the later commits on top of a different commit.
3. Point the tags at the new commits.

A tag continues to point at the old commit until you move it:

```
git tag --annotate --force step-03 <new commit> -F step-03.tag.txt
```

Keep the message of each tag in a file next to your notes. Then you can move the tags again later. The
server of timewalk reads the tags when it starts. After you move a tag, restart timewalk.

## Where the class material goes

Keep the slides, the notes and other material for the class outside the project. timewalk refuses a
notes file inside the project or its replay copy. Put the material in a folder of its own, a kit, with
two folders that git ignores:

```
my-walk/
├── notes.md       the notes, tracked in the kit
├── slides/        the slides and the manifest, tracked in the kit
├── repo/          ignored: a clone of the project
└── worktree/      ignored: the replay copy
```

The `.gitignore` of the kit lists `repo/` and `worktree/`, so the names never change from kit to kit. A
`justfile` like this one runs timewalk from GitHub on the clone:

```just
timewalk := "uvx --from git+https://github.com/rahuldave/timewalk@v1.0.5"

# Open the page on the project, with these notes and slides
present *args:
    {{ timewalk }} timewalk repo --replay worktree --notes notes.md --slides slides/slides.toml {{ args }}

# Make the handout: every slide, with the notes of each step
handout:
    {{ timewalk }} timewalk-pdf slides/slides.toml --notes notes.md --with-notes -o handout.pdf
```
