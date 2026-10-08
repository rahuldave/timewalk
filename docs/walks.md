# Several walks

A walk is one path through the history of your repository, with its own notes and slides. One class can
have several walks. For example, one walk tells the whole story of the project, step by step, and a second
walk covers only the tests. A table of contents lists the walks, and a menu at the left of the step bar
changes the walk in every window.

A walk is a narrative or a tutorial. A narrative goes from tag to tag. A tutorial also stops at each small
commit between two tags, and the class makes the step one move at a time.

To build a class with several walks, see [Build a class with several walks](build-walks.md).
[What a narrative and a tutorial need](authoring.md) lists what each kind of walk needs, as checklists.

Try it on the demo. In a clone of timewalk, run `just demo-walks`. The demo has two walks. "The demo, step
by step" has all five steps, and "Only the checks" has two of them. The class timewalk-test has five
walks, two of them tutorials.

![The step bar of timewalk-test in a tutorial: the walk menu, the label Tutorial, the switch between Do and Watch, and the row of moves](images/walks.png)

## The table of contents

The table of contents is a TOML file, `toc.toml`, in the class folder. It holds one `[[walk]]` table for
each walk. The first walk is the default.

```toml
[[walk]]
id = "story"
title = "The project, step by step"
kind = "narrative"
notes = "notes.md"
slides = "slides/slides.toml"

[[walk]]
id = "tests"
title = "Only the tests"
kind = "narrative"
folder = "walks/tests"            # its notes.md, and slides/slides.toml, are in this folder
steps = ["step-01", "step-02", "step-05"]
```

| Key | Holds |
|---|---|
| `id` | The name of the walk, for `--walk`. Letters, digits, `-` and `_` |
| `title` | What the menu shows. Default: the `id` |
| `kind` | `"narrative"`: the tagged steps, and nothing between them. `"tutorial"`: the tagged steps, and the small commits between them. See [Tutorial walks](#tutorial-walks). Default: `"narrative"` |
| `notes` | The notes file of the walk |
| `slides` | The slides manifest of the walk |
| `folder` | A folder for the walk. Its notes are `notes.md`, and its manifest is `slides/slides.toml`, in this folder |
| `steps` | Optional. Some of the steps, by name. Without it, the walk has every step. A tutorial cannot have `steps` |
| `tags` | Optional. A glob for tags of its own, for example `"parts-*"` on another branch. Give `steps` or `tags`, not both |
| `setup` | `"manual"`: you run `just setup` yourself at each step. The only value for now |
| `moves` | In a tutorial, the mode that it starts in. `"do"`: the learner makes each move by hand. `"watch"`: timewalk checks out the commit of each move. See [Do mode and watch mode](#do-mode-and-watch-mode). Default: `"do"` |
| `sync` | In a tutorial, `true` or `false`. With `true`, the slides and the moves follow each other. Default: `true` |

timewalk reads every path from the folder of `toc.toml`. Give a walk `folder`, or give it `notes` and
`slides`. A walk can also have no notes, or no slides. In a folder, `notes.md` and `slides/slides.toml` are
each optional.

## A class folder with several walks

Keep the files of each walk in its own folder. The default walk can stay at the top, as in a class with
one walk:

```
my-class/
  toc.toml
  notes.md                    the default walk
  slides/slides.toml          its slides, and the slide files
  walks/
    tests/
      notes.md
      slides/slides.toml      it can name ../../../slides/talk.md#3
```

A manifest of one walk can use the slide files of another. Write the path from the folder of the
manifest, for example `../../../slides/talk.md#3`. The page serves slides only from the slides folders of
the walks.

Put every slide file there, and every picture that a slide shows. Keep each manifest in a slides
folder of its own, not at the top of the class folder. Keep the notes out of the slides folders. The page
never serves the notes or `toc.toml`, and the checks report a layout that would. See [Slides and documents](slides.md#the-manifest) for the entries.

## Start timewalk with a table of contents

Give `--toc` in place of `--notes` and `--slides`:

```
timewalk ~/code/project --toc my-class/toc.toml
timewalk ~/code/project --toc my-class/toc.toml --walk tests
```

timewalk starts on the first walk, or on the walk that `--walk` names. Each walk names its own tags, so
`--toc` does not go with `--tags` or `--commits`.

## Change the walk

Choose a walk in the menu at the left of the step bar. Every window changes to the steps, the notes and
the slides of that walk.

- **The menu** shows only when the table has two walks or more. If the table has both kinds of walk, the
  menu puts them in two groups, **Narratives** and **Tutorials**. Each group keeps the order of the table.
- **A label** beside the menu says the kind of the walk on show, **Narrative** or **Tutorial**.
- **In a tutorial,** a switch beside the label changes the mode, **Do** or **Watch**.
- **The Room window** has no menu. It shows the title of the walk, with its kind, and for a tutorial its
  mode, for example "(tutorial, watch mode)".

If the bar has no room for the menu, its buttons move to a second row.

A change of walk moves the replay copy, by the same rules as a move between steps:

- **If the replay copy has edits,** the page asks first. Then it stashes the edits, with the name of the
  step on show. With `--discard-edits`, the move drops them. See [Live edits](edits.md#moving-with-edits).
- **timewalk remembers each walk.** It keeps the step, the move, the slide and the scrolls of each walk.
  When you come back to a walk, the page shows the place where you left it.

**Save** in the notes column writes the notes file of the walk on show. The **PDF** button makes the PDF of
the walk on show.

## The checks

timewalk checks every walk when it starts. An error stops timewalk, and a warning prints and does not.
Run the same checks alone with `timewalk-check`:

```
timewalk-check ~/code/project --toc my-class/toc.toml
timewalk-check ~/code/project --notes notes.md --slides slides/slides.toml
```

The second form checks a class with one walk and no table. `timewalk-check` prints one line for each
problem, and exits with code 1 when it finds an error.

| Problem | Gives |
|---|---|
| A notes section or a slides entry for a step that is not in the walk | An error |
| In a tutorial, a move whose subject does not start with its name, for example `step-02.1:` | An error |
| In a tutorial, a move with no `###` section, a `###` section for no move, or sections out of the order of the commits | An error |
| A `moves` value other than `"do"` or `"watch"`, and other mistakes in `toc.toml` | An error |
| A step with no entry in `slides.toml`. Give each step one slide or more | An error |
| A slide file that does not exist, or that is not in a slides folder of a walk | An error |
| A picture that a Markdown slide shows from outside the slides folders | An error |
| A manifest at the top of the class folder, or notes in a slides folder | An error |
| A slide number past the end of its Markdown file, or a page past the end of its PDF | An error |
| A notes file or a manifest inside your repository or its replay copy | An error |
| A step with no section in the notes | A warning |
| In a tutorial, a move whose section has no command | A warning |
| A slide file in a slides folder that no walk uses | A warning |

A picture that a Markdown slide shows counts as used.

## Tutorial walks

A tutorial walk stops at every small commit between two steps. Each small commit is a move. The class
makes the moves of a step one at a time, and reads the notes of each move.

The moves of a step are the commits after the step before it, up to the commit of the step, on the
first-parent line. The first-parent line is the line of commits that git follows back from a commit
through the first parent of each merge. The last move is the commit of the step, with its tag. A step of
one commit has no moves, and the first step has none.

Write the subject of each commit with the name of its move, for example `step-02.1: a test of counting`.
The commit of the step keeps its own subject, for example `step-02: the tests pass`. A subject with the
wrong name is an error in the checks.

```
step-01: count the words              tag step-01
step-02.1: a test of counting         move step-02.1
step-02.2: a test of an empty text    move step-02.2
step-02: the tests pass               tag step-02, move step-02.3
```

A tutorial takes every tag of its glob, so it cannot have `steps`. For a tutorial on one part of the
history, give the part tags of its own on a branch, and name them with `tags`.

### The notes of a tutorial

Give each move a `### step-NN.k Title` section, in the order of the commits. Here `NN` is the number of
the step and `k` is the number of the move. A move section holds these parts:

- **The instructions** to make the move by hand, with the code to type.
- **What changed,** a short list of the files and the functions that the commit changed.
  `timewalk-notes` drafts it from the diff. See [Draft the notes of the moves](#draft-the-notes-of-the-moves).
- **A line `files:`.** The page shows the files that the commit of the move added or changed. Each file
  is a button that opens the change of the move in the reader.
- **One or more commands at the end,** with the result to expect. They are the anchor of the move.

````markdown
## step-02 Tests, one at a time

$ just setup

We add the tests one file at a time, and run them after each.

### step-02.1 A test of counting

Make `tests/test_count.py` with two tests:

```python
...
```

What changed:

- `tests/test_count.py`: new file, 11 lines; adds `class Count`, `test_words` and `test_case`.

files:

$ uv run python -m unittest discover -s tests -q

One test fails: `test_case` expects `{"a": 2}`.
````

### Anchor commands

The last commands of a move section are its anchor commands. An anchor command shows what the move did,
for example a test run or a run of the program. The notes column shows them under the label "After this
move, run:".

- **Make each anchor command safe to run again.** It must not change files that git tracks.
- **Say what the command gives.** A test that fails is a good anchor, if the notes say that it fails and
  why.

`timewalk-check` warns of a move whose section has no command.

### Do mode and watch mode

A tutorial has two modes. Every window shares the mode. The switch **Do** or **Watch** beside the label
of the walk changes it, at any step and at any time. The key `moves` in `toc.toml` sets the mode that the
tutorial starts in.

- **Do mode** is the default. The learner makes each move by hand from its notes. The code does not move
  until the learner asks.
- **Watch mode** checks out the commit of each move when you ask. The learner reads the change, and runs
  the anchor commands.

A move to a tagged step always starts that step at the tag before it, with no move made. So in both modes,
each step starts from the code of the step before.

![A tutorial in do mode at the start of step-02: the moves row, the first move with Done and Catch me up, its anchor command, and its change in the reader before the file exists](images/tutorial-do.png)

In do mode, each move section has these buttons:

| Button | Does |
|---|---|
| **Done ✓** | On the move that the learner works on: marks it done, and goes on to the next move. The code does not move |
| **Not done** | On a move marked done: marks it, and the moves after it, as not done |
| **Catch me up** | Checks out the commit at the end of that move, in every window. If the replay copy has edits, the page asks first, and then stashes them. It never discards them |

A button of a file under `files:` opens the change of that move in the reader, even before the file
exists. The button of the view in the reader then says, for example, **Changes in step-02.1**.

![A tutorial in watch mode after Show on the first move: the commit of step-02.1 checked out, the hint at the top of the notes, and the slide of the move](images/tutorial-watch.png)

In watch mode, each move section that is not on show has a **Show ▶** button. It checks out the commit of
that move, in every window. Any move can be reached, and no move is locked.

### Moves on the page

When a step has moves, a row of moves shows under the step bar, in every window. It has the arrows ◀ and
▶, and a button for each place. The buttons are **Start**, then **1**, **2** and so on. **Start** is the place before the
first move. Beside the buttons, the row shows the move on show and its subject.

| Control | In do mode | In watch mode |
|---|---|---|
| **▶**, or Shift+Right | Marks the move done, and goes to the next | Checks out the commit of the next move |
| **◀**, or Shift+Left | Marks the last move done as not done | Checks out the commit of the move before |
| **1**, **2** | Works on that move: the moves before it count as done | Checks out the commit of that move |
| **Start** | Marks no move done | Checks out the tag before the step |

- **In a terminal,** use Alt with Shift+Right and Shift+Left.
- **Right and Left** go to the next or the previous step, as in a narrative.
- **A hint** at the top of the notes says how to go on in the mode on show.
- **The notes** mark the section of the move on show with a line at its left, and scroll to it in every
  window.
- **What changed** in the reader shows what the move on show changed.

timewalk writes down the move on show in the git folder of the replay copy. After a restart, the page
comes back at the same move.

### Slides for moves

A move can have slides of its own in `slides.toml`. Quote its name, because it holds a dot:

```toml
[slides]
step-02 = ["talk.md#3"]
"step-02.1" = ["moves.md#1"]
"step-02.3" = ["moves.md#2"]
```

The slides of a step are its own slides, then the slides of each move, in order. With `sync`, the slides
follow the moves:

- **A move shows its first slide.** A move without slides keeps the slide before it.
- **In do mode,** **Done ✓** shows the first slide of the next move, if that move has slides.
- **In watch mode,** a slide of another move also checks out that move. A slide of the next move makes
  that move, and a slide of an earlier move goes back to it. A slide that is further ahead makes only the
  next move.

Write `sync = false` at the top of `slides.toml`, before `[slides]`, to keep the slides and the moves
apart. The key `sync` in `toc.toml` does the same for the walk, and the manifest wins.

### Draft the notes of the moves

`timewalk-notes` writes a first draft of the section of each move that the notes do not have yet:

```
timewalk-notes ~/code/project --toc my-class/toc.toml --walk tutorial
timewalk-notes ~/code/project --toc my-class/toc.toml --walk tutorial --write
```

Without `--write`, it prints only the drafts. With `--write`, it writes them into the
notes file of the walk. A section that is already in the notes stays as it is. Each draft has these
parts:

- **A heading** `### step-NN.k`, with the subject of the commit after its name as the title.
- **"What changed:"**, a list with one item for each file of the commit. An item says if the file is new
  or removed, or how many lines the commit added and removed. For Python, it names the functions and
  classes that the commit added, removed or changed. For a `justfile`, it names the recipes that the
  commit added.
- **A line `files:`**.
- **A comment** that asks for a command, for you to replace with an anchor command.

The list names only what the diff shows. Finish each draft by hand. Write the instructions, say which
change in the list is the one to look at, and add the anchor commands.

## A PDF of one walk

`timewalk-pdf` takes the slides and the notes of one walk from the table:

```
timewalk-pdf --toc my-class/toc.toml --walk tests -o tests.pdf
```

Without `--walk`, it makes the PDF of the first walk. The title of the walk goes in the footer, unless you give `--title`. `--brand` and `--with-notes` work as for one walk. See
[Slides and documents](slides.md#a-pdf-of-the-slides).
