# Making the steps

A step is a point in the history that timewalk can stand on. By default the steps are the tags matching
`step-*`, in name order. Nothing else about the repository needs to change.

## Tags and their notes

```
git tag --annotate step-03 -m "The code after ruff format. Only its layout changed."
```

An **annotated** tag's message is shown to the audience, in the band under the step bar, as the step's
note. A lightweight tag (`git tag step-03`) works too, with no note. The commit's subject is shown beside
the step's name.

| Option | Steps are |
|---|---|
| (none) | tags matching `step-*`, in name order |
| `--tags 'v*'` | tags matching another pattern |
| `--commits` | every commit on the current branch's first-parent history, oldest first. The note is the rest of the commit message |

Name steps so they sort: `step-00` to `step-09`, then `step-10`. A step can carry a letter, `step-07a`
and `step-07b`, and still sort in place.

## A history worth walking

A history made for teaching is a history made on purpose. What works:

- **One commit per step, one idea per commit.** The file, then its tests, then the formatter, then
  types. The step's diff is what the class reads, so keep it about one thing.
- **Annotated tags with a sentence for the room.** The commit message is for developers; the tag's
  message is what the audience sees.
- **A straight line.** Steps on one branch, no merges.
- **Steps that are unfinished on purpose.** Two failing tests at one step and their fix at the next is a
  lesson. Say so in the tag's note.
- **Run outputs never tracked.** Put `.venv`, databases, checkpoints and run folders in the first step's
  `.gitignore`. Untracked files survive every move.

## Changing an earlier step

Before anyone has cloned, a history can be rewritten: fix an early file in its own step, rebase the later
steps on it, and point the tags at the new commits. A tag keeps pointing at the old commit until you move
it:

```
git tag --annotate --force step-03 <new commit> -F step-03.tag.txt
```

Keeping each tag's message in a file beside your notes makes this repeatable. timewalk reads the tags
when it starts, so restart it after re-tagging.

## Where the class material lives

Keep the slides, notes and anything about the class outside the project, in a folder of their own with a
`justfile` such as:

```just
timewalk := home_directory() / "code/timewalk"
repo := home_directory() / "code/project"

# Open the step browser on the project with these notes and slides
present *args:
    uv run {{ timewalk }}/timewalk.py {{ repo }} --notes notes.md --slides slides/slides.toml {{ args }}

# Make the handout: every slide, one per page
handout:
    uv run {{ timewalk }}/slides_pdf.py slides/slides.toml -o handout.pdf --title "My class" --notes notes.md
```
