# Notes

The notes are the script of each step. They say what to read, what to say and which commands to run. They
live in one Markdown file, with one section for each step. Give the file to timewalk with `--notes notes.md`. The
page then shows the notes of the current step in a column on its right.

![The notes column at step-02 of the demo, with a cue in its own shade, the prose and the commands](images/notes.png)

## Where the notes file goes

Keep the notes file outside the repository that the class walks through. timewalk refuses a notes file
inside that repository or inside its replay copy. A move would change a file there.

A good place is the folder or repository that holds the class material, beside the slides. If a student
forks that repository, the edits of the student show in the git of the fork, ready to commit.

## What a notes file holds

```markdown
## step-07 Training
time: 0:37

> Say: start the run first, then read the code while it runs.

The training reads its configuration from one file.

runs$ just train configs/cheese.yaml
$ cat configs/cheese.yaml
main$ git log --oneline
```

| Line | Does |
|---|---|
| `## step-name Title` | Starts the section of a step. The title shows at the top of the notes column, and the PDF uses it in its footer |
| `time: 0:37` | The planned start of the step, in minutes and seconds from the start of the session. The clock band uses it, and shows only with `--clock` |
| `> text` | A cue, for example `> Say: ...` or `> Note: ...`. The notes column shows it on a soft background with a grey left border, apart from the prose |
| `$ command` | A command for the **At this step** tab |
| `runs$ command` | The same, in the **Runs** tab |
| `runs2$ command` to `runs9$ command` | The same, in one more Runs tab, which opens the first time a line uses it. Use it for a second long command while the first command still runs |
| `main$ command` | The same, in the **Main** tab, in your repository |
| `do$ command` | In a tutorial walk: a command that makes the move, for example `do$ uv add --group test pytest`. In do mode, the same as `$ command`. In watch mode and in a narrative, no button, and the line "The move ran:" with the command. Never an anchor command. Put it outside the blocks. See [Write for both modes](authoring.md#write-for-both-modes) |
| `### step-NN.k Title` | In a tutorial walk: starts the section of move `k` of step `NN`, for example `### step-02.1 A test of counting`. Write the sections in the order of the commits. See [Tutorial walks](walks.md#tutorial-walks) |
| `files:` | Starts a list of items, the files to look at. If the line has no items, the page shows a **± See diff** button for each file that the commit of the move added or changed |
| ``- diff `path`: words`` | Under `files:`, an item: a button **± See diff path** that opens the change of the move, or of the step, to that file, and its words. The words can go on in an indented line under it |
| ``- file `path`: words`` | An item: a button **▤ See file path** that opens the file itself. In do mode, for a move not made yet, it opens the file as the move leaves it |
| ``- diff step-02.1 `path`: words`` | An item for another move, named before the path |
| ``  show: `a line` `` | Under an item: one line of the change. The notes draw it as an excerpt of the real diff, with the line shaded, and the reader marks the line. For a line with a backtick in it, use two backticks: ```  show: ``a `b` c`` ```. See [Quote a line with backticks](walks.md#quote-a-line-with-backticks) |
| `::: do` | In a tutorial walk: opens a block for do mode. The page shows the lines up to the next `:::` only in do mode. See [Do mode and watch mode](walks.md#do-mode-and-watch-mode) |
| `::: watch` | The same, for watch mode. A narrative shows the `watch` blocks |
| `:::` | Closes a block. A block holds no heading and no other block |
| `{{include step-NN.k}}` | Only in the notes of a walk with notes of its own: puts in a move section of the shared notes. See [A walk with notes of its own](walks.md#a-walk-with-notes-of-its-own) |
| other lines | Markdown text to read |

timewalk shows every line of the notes, cues included. If some cues are only for you, remove them from the
copy of the notes that you give to students.

## The notes of a move

In a tutorial walk, each move of a step has a section of its own, under the `## step` section. A move is
one small commit between two tags. A move section says what problem the change solves, gives the
change, and gives the commands that check it. It ends with a line `files:` and its items.
`timewalk-notes` drafts the sections from the commits. See
[Draft the notes of the moves](walks.md#draft-the-notes-of-the-moves).

The text of a step before its first move section is about the whole step. Put `$ just setup` there, and
say what the step builds, and why.

Write the notes for a reader who knows the program, and not timewalk. Do not write "step", "move" or
"commit" in the text. See
[Write for a reader who has never heard of timewalk](authoring.md#write-for-a-reader-who-has-never-heard-of-timewalk).

````markdown
### step-02.2 A test of an empty text

An empty file is a text with no words. `count` must give an empty dictionary for it, and not fail. A
test in a file of its own, `tests/test_empty.py`, says so, and keeps it true after later changes.

::: do
Make the file `tests/test_empty.py`, with these lines:

```python
import unittest

from tally import count


class Empty(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(count(""), {})
```
:::

::: watch
The new file `tests/test_empty.py` holds one test, `test_empty`. It gives `count` the empty text `""`,
and expects the empty dictionary `{}`.
:::

$ uv run python -m unittest discover -s tests -q

The new test passes at once. `split()` finds no words in an empty text, so `count` gives `{}`.

files:
- diff `tests/test_empty.py`: an empty text counts nothing.
  show: `self.assertEqual(count(""), {})`
````

An item is one file to look at, with words that say why. On the page, each item is one button, **± See
diff** or **▤ See file** with the path, and the words go on the line under it.

A `show:` line quotes one line of the change. The notes column draws it as an excerpt, a small part of the
real diff, read only. A click on the excerpt opens the change, as **± See diff** does. The excerpt comes
from the commits each time, so the notes never hold a copy of the diff. See
[The items under files](walks.md#the-items-under-files).

The last commands of a move section are its anchor commands. The notes column shows them under the label
"Check it". An anchor command shows what the move did, and the notes say what it gives. Make
it safe to run again, and keep it from changing tracked files. A test that fails is a good anchor command,
if the notes say that it fails. See [Anchor commands](walks.md#anchor-commands).

A move section can hold blocks for one mode. The shared text first says what problem the change solves,
and what the change is. Then the `do` block gives the exact lines to type, and the `watch` block says what
the change does and where to see it. The page shows only the blocks of the mode on show. Keep the
explanation, the anchor commands and the items outside the blocks. See [What a narrative and a tutorial need](authoring.md#write-for-both-modes).

A command that makes the move, for example `uv add`, goes in a `do$` line in the shared text. Do mode
runs it, and watch mode shows it as what the move ran. A `do$` line is never an anchor command. See
[Write for both modes](authoring.md#write-for-both-modes).

## The notes column

The top of the column shows the name of the step in bold, and the title from the notes, on one line. If
the column is too narrow, the title ends in "...", and a hover over it shows the whole title. The first line
of the notes says what to do now, in the font of the code, as the status band does. See
[The status band](page.md#the-status-band).

Below that line, the column shows the notes in the order of the file. Each command is a button with a border, at the
place where the notes have it, between the prose around it. A click on a button types the command into
its terminal and brings that tab to the front in every window. The terminal in the window where you clicked gets the keyboard.

A `$ ` line inside a fenced code block is code to read, and not a command. Use a code block to show a
command that the class must not run from the page.

By default, a click only types the command. You can look at the command, and then press Enter to
run it. To run each command with one click, turn on **run on click** at the top of the column. The
browser remembers this setting.

The **Notes** button in the step bar shows or hides the column in one window. The **Cues** button shows
or hides only the cues, the lines that start with `>`, in one window. The prose and the commands stay. In
a class, the window on the projector hides the cues, and your own window shows them. See
[Teach a class](class.md).

![The notes as the class sees them: the prose and the commands, without the cues](images/notes-room.png)

Code in a sentence has the font of the code, and no shade. A fenced code block, and an excerpt of a
diff, is a band across the whole width of the column, with no border. The notes and the reader colour the
code with the GitHub theme of highlight.js, light or dark, as the page is. highlight.js is the library
that colours the code. See [The theme](page.md#the-theme).

When the step has no moves, or when every move of the step is made, a green **Next step** button ends the
notes. It names the next step, and goes to it in every window. The last step has no such button.

If the screen is less than about 1000 pixels wide, the notes column moves under the terminals.

### The notes of a step with moves

In a tutorial, the notes column draws a step with moves in sections:

- **The title of the step.** The text before the first move section is a section of its own, under the
  title of the step. The title comes from the `##` heading, or else from the subject of the step's
  commit. The text is about the whole step.
- **A section for each move.** The sections have no boxes. Each one runs from edge to edge of the column,
  with a bar on its left. The bar is dark for the move on show and blue for the next move. A move made
  has a grey bar and a check. The page fades the moves after the next one.
- **The button of each move.** The next move has **Show ▶** in watch mode, or **Done ✓** in do mode. A
  move already made keeps a grey button that does nothing, **Shown ✓** or **Made ✓**.
- **↺ Restart step.** Once a move is made, each section of a move made ends with this button, and so does
  the section of the move on show. It goes back to the step's **Start** in every window, with the code of
  the step before. If the replay copy has edits, the move keeps them on a saved branch first. The notes
  then go to the top, where `just setup` is.
- **Next step.** When every move is made, the green **Next step** button ends the notes.

See [Moves on the page](walks.md#moves-on-the-page).

![The notes of step-01 in watch mode, with both moves shown: the text of the step under its title, two moves with Shown and Restart step, and Next step at the end](images/tutorial-next.png)

## Edit the notes

To change the notes of the current step, click **✎ Edit** at the top of the column. A text box shows the
section of the step as it is in the file. Change the text, and then click **Save**. timewalk writes that
section of the notes file, and leaves every other section as it is. A file with Windows line ends keeps
them.

Every window shows the new notes at once. If somebody changed the same section in the file after you
clicked **✎ Edit**, timewalk refuses to save, so that it does not overwrite their change. Copy your text,
reload the page, and edit again.

A walk with notes of its own has no **✎ Edit** button. Its sections come from two files, so edit them in
your editor. See [A walk with notes of its own](walks.md#edit-the-notes-of-a-walk-with-notes-of-its-own).

![The notes of step-02 in the text box, ready to save](images/notes-edit.png)

The page also reloads the notes by itself. When you save the notes file in your editor, or a `git pull`
changes it, every window shows the new notes within about a second. A section that you are editing in the
text box stays as it is until you save it.

## The clock band

If you start timewalk with `--clock`, a band across the top of the page shows a clock against the planned
times. Without the flag, the page has no clock band.

| Part | Shows |
|---|---|
| The clock | Time since you pressed **▶ Start the clock**. **↺ Reset** stops it, and sets it back to 0:00 |
| The plan | Time left before the next step is due, or in red, how far over you are |
| Next | The name and title of the next step, and its planned time |

The planned times come from the `time:` lines of the notes file. Without them, the band shows only the
clock.

![The clock band across the top of the page](images/band.png)

## For a student who works alone

A student who works alone needs only the page. It has the slides, the files, the terminals and the notes.
The notes then work as a runbook, which is a written script of each step with its prose and commands.
Start timewalk without `--clock`, so that the page has no clock band.

## How to write good notes

- **Write what to read and what to do.** The slide shows the idea. The notes give the steps.
- **Put the commands in the order of use.** Also put in commands that undo changes. For example, after a
  command that changes files, add `git restore .`. Then the next move has no edits to keep on a saved
  branch, and the notice about the branch does not show.
- **Give every step a time** when the plan for the session is clear. Then, with `--clock`, the clock band
  tells you at every step if you are early or late.
- **Try every command at its step before the class.** A command that worked at the last step can fail at
  this step, because the files are different.

## Text between commands

The page takes the commands out of the text and shows them as buttons. So do not end a sentence with a
colon to point at a command. Write "The last command undoes them", and not "Undo them with:".
