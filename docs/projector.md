# The projector page

The projector page is the page at the first address, and it is for the room. Everything on it follows the
step of the replay copy. The replay copy is the second working copy that timewalk moves from step to step.
The [presenter page](presenter.md) is the same page, with your notes beside it.

The two pages share the step, the slide, the layout, the open file and the terminal tab in front. So a
change on either page shows on both pages.

![The projector page with the step bar, the slides, the file list, the reader and the terminals](images/projector.png)

## The step bar

The step bar has one chip for each step, with numbers from 00. A step is a point in the history, and by
default it is a git tag that matches `step-*`. The bar fills the chip of the current step, and shades the chips of
earlier steps.

Next to the chips, the bar shows the name of the step and the subject of its commit. Under them, a band
shows the message of the tag, which is the note that the audience sees.

To go to a step, do one of these:

- Click its chip.
- Click an arrow button.
- Press **Left** or **Right** on the keyboard.

Sometimes the replay copy is on a commit that is not a step, for example after someone runs `git checkout`
in a terminal. Then the bar says "between steps". To go back, choose a step.

If you start timewalk with `--discard-edits`, the bar also says "Moves discard edits". When files have
edits, the note becomes a red warning with a count of the edited files. The next move throws those
edits away. See [Live edits](edits.md#with---discard-edits).

## The layouts

The buttons **Slides**, **Both** and **Code**, at the top right, choose what fills the page. A choice
changes both pages at once. If a step has no slides, the page shows the code in every layout.

![The Slides layout, where the slide fills the page](images/layout-slides.png)

![The Code layout, where the file list and the reader fill the page](images/layout-code.png)

Some controls change only the page you use:

- **A−** and **A+** change the size of all the text, and the terminals use the same size.
- **Dark** and **Light** change the theme.
- Drag the bar above the terminals to give them more or less room.

Each page keeps these three settings in its own browser. So the projector and your screen can have
different settings.

![The same step in the dark theme](images/projector-dark.png)

## Slides

The slides pane shows the slides for the step, from the manifest. The manifest is the TOML file that says
which slides go with which step. The top of the pane shows the number of slides and the arrows. If the step
has more than one slide, **Up** and **Down** change the slide.

A step can show one longer Markdown **document** instead. A document scrolls and has no arrows. See
[Slides and documents](slides.md).

## The file list

The file list shows the tracked files of the replay copy as a tree. A tracked file is a file that git
records. The list marks the files that the step added as **new**, and the files that it modified as
**changed**. The list shows the files that the step removed under the tree.

An edit is a change to a tracked file that nobody has committed yet. If a command in a terminal edited a
file after the commit of the step, the list marks the file as **edited**. To see the size of the step in
lines, hover over the count at the top.

The **changed or edited** button shows only those files. On a large project, folders with no changes
start closed.

![The file list with only the files that the step changed](images/tree-changed.png)

## The reader

The reader is the pane that shows one file. To read a file, click it in the file list. The reader has up
to three views:

| View | Shows |
|---|---|
| **File** | The file as it is on the disk now |
| **Changes in this step** | What the commit of the step changed in the file, against the step before |
| **Edits since the step** | The edits to the file since the commit of the step. The view shows only when the file has edits |

You cannot edit a file in the reader. The reader shows new edits within about a second. See
[Live edits](edits.md).

![The Changes in this step view, where step-04 adds types and comments](images/reader-changes.png)

## The terminals

A row of tabs goes along the bottom of the page. Each tab is a terminal with a real shell, the program
that runs your commands.

- **At this step** and **Runs** run in the replay copy.
- **Main** runs in the repository that you gave to timewalk.
- **Claude** starts Claude Code at this step.
- **+** opens more terminals.

See [The terminals](terminals.md).

## Recipes

A recipe is a task in a `justfile`, which you run with `just`. If the step has a `justfile`, its public
recipes show as buttons beside the tabs. A click types `just <recipe>` into the terminal in front and runs
it.

If a recipe needs an argument, the click types the command but does not run it. You then finish the
command. The page highlights the recipes that are new at this step. If **Main** is in front, the buttons
show the recipes of your repository.

## The keyboard

| Key | Does |
|---|---|
| Left, Right | The previous or next step |
| Up, Down | The previous or next slide, when the step has more than one. Otherwise they scroll |
| Alt with an arrow | The same, from anywhere, a terminal included |

The page keeps the arrow keys until you click into a terminal. Then the terminal has the keys, and a blue
edge around it shows this. The arrows then move through the history of your shell.

To give the keys back to the page, click anywhere else. A command from the presenter page also gives its
terminal the keys.

![A terminal with the keys has a blue edge](images/terminal-focused.png)
