# Presenter notes

The notes file is a Markdown file with one section for each step. Give it to timewalk with
`--notes notes.md`. Only the presenter page asks for the notes file, and it shows the notes in the notes
column on its right. The server reads the file again each time the presenter page asks for it. So you can
edit the notes while you present.

```markdown
## step-07 Training
time: 0:37

Start the run first, then read the code while it runs.

runs$ just train configs/cheese.yaml
$ cat configs/cheese.yaml
main$ git log --oneline
```

| Line | Does |
|---|---|
| `## step-name Title` | Starts the section of a step. The title replaces the subject of the commit on the presenter page. The PDF also uses the title in its footer |
| `time: 0:37` | The planned start of the step, in minutes and seconds from the start of the session, for the clock |
| `$ command` | A command for the **At this step** tab |
| `runs$ command` | The same, in the **Runs** tab |
| `runs2$ command` to `runs9$ command` | The same, in one more Runs tab, which opens the first time a line uses it. Use it for a second long command while the first command still runs |
| `main$ command` | The same, in the **Main** tab, in your repository |
| other lines | Markdown text for you to read |

Each command becomes a button under the notes on the presenter page. One click types the command into its
tab and presses Enter. The click also brings the tab to the front on both pages. The terminal on the
projector page then gets the keyboard, so you can type the next command there.

## How to write good notes

- **Write what to say, not what is on the slide.** The audience reads the slide. The notes are for you.
- **Put the commands in the order you will run them.** Also put in commands that undo changes. For
  example, after a command that changes files, add `git restore .`. Then the next move does not need to
  ask about edits. With `--discard-edits`, the move discards the edits, and you need no undo command.
- **Give every step a time** when the plan for the session is clear. Then the clock tells you at every step
  if you are early or late.
- **Try every command at its step before the class.** A command that worked at the last step can fail at
  this step, because the files are different.

## Text between commands

The page takes the commands out of the text and shows them as buttons. So do not end a sentence with a
colon to point at a command. Write "The last command undoes them", and not "Undo them with:".
