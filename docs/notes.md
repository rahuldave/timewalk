# Presenter notes

A Markdown file with one section per step, passed with `--notes notes.md`. Only the presenter page
asks for it, and shows it in the column on its right. It is read afresh whenever the presenter page asks, so it can be edited while presenting.

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
| `## step-name Title` | Starts a step's section. The title replaces the commit's subject on the presenter page, and is used in the PDF's footer |
| `time: 0:37` | The planned start, minutes:seconds into the session, for the clock |
| `$ command` | A command for the terminal at this step |
| `runs$ command` | The same, in the Runs tab |
| `runs2$ command` to `runs9$ command` | The same, in a further Runs tab, opened when first used: for a second long command while the first is still going |
| `main$ command` | The same, in the Main tab, in the repository you started from |
| anything else | Shown to you as Markdown |

Each command is a button under the notes on the presenter page. One click types it into that tab,
presses Enter, and brings the tab to the front on both pages. The projector's terminal gets the keyboard,
so you can type a follow-up there.

## Writing good notes

- **Write what to say, not what is on the slide.** The audience reads the slide; the notes are for you.
- **Put the commands in the order you will run them.** Undo commands too: `git restore .` after a
  command that changed files, so the next move does not have to ask.
- **Give every step a time** once the session has a shape. The clock then tells you, at every step,
  whether you are early or late.
- **Rehearse every command at its step.** A command that worked at the last step can fail at this one:
  the files are different.

## Prose between commands

The commands are lifted out of the text and shown as buttons, so a sentence should not lead into one with
a colon. Write "The last command undoes them", not "Undo them with:".
