# The terminals

Every tab is an ordinary login shell, your own `$SHELL`, running in a pseudo-terminal on your machine
and drawn by ghostty-web, built on Ghostty's terminal core. The tabs differ only in the folder they
start in.

| Tab | Starts in | Its edits show in the file view |
|---|---|---|
| **At this step** | the replay copy, at the current step | yes, live, as **edited** |
| **Runs**, **Runs 2** to **Runs 9** | the replay copy | yes |
| **Claude** | the replay copy, running `claude` | yes |
| **+** (Shell 1 to 9) | the replay copy | yes |
| **Main** | the repository you started timewalk on, on its own branch | no |

A shell is started the first time its tab is opened, and keeps running while you move between steps and
reload the page. A page that reconnects gets the shell's recent output again.

The presenter page shows the same shells, not copies: typing on either page types into the one shell,
and both show its output. The tab in front is shared too. The projector sets each shell's size; the
presenter page draws it in whatever room it has, so a long line can wrap differently there.

## At this step and Main

The same command in the two tabs, with the replay copy at step-01:

![At this step: the replay copy at step-01, greet.py as first written](images/terminal-step.png)

![Main: the repository you started from, at its latest commit](images/terminal-main.png)

**Main** is there to show the finished project while the walk stands earlier: its log, its tests, the
file as it ends up. Anything you change in it changes your own repository, and the page never shows it.

## Runs

**Runs** is for a command that takes a while, such as a training run, so it keeps going while you work in
**At this step**. A `runs$` line in the notes sends its command there. `runs2$` to `runs9$` open further
Runs tabs, for a second or third long command at once.

A tab that prints while another is in front gets a dot, so a finished run is noticed.

![Runs has printed since it was last in front](images/terminal-runs-dot.png)

### A long command and a move

A command keeps running when you move to another step, and the files change under it. What the program
has already loaded stays as it was. What it reads later comes from the new step: a config file, a module
imported late, a script a recipe starts. Move on while a run is going only once it has read everything it
needs. The Main tab's files do not move, but it runs the repository's current code, not the step's.

### A long command and timewalk stopping

When timewalk stops, its shells end, and so does whatever runs in them in the ordinary way. A command
started with `nohup` and `&` is the exception: it lets go of the shell and keeps running.

| Started in a timewalk tab as | When timewalk stops |
|---|---|
| `just train` | Stops |
| `sleep 600 &` | Stops |
| `nohup sleep 600 &` | Keeps running |
| `nohup sleep 600 > run.log 2>&1 < /dev/null &` | Keeps running |

To make a run that outlives the class, start it detached, from a recipe if you like:

```just
# Start training in the background; it keeps running if the terminal or timewalk goes away
train-detached config:
    nohup just train {{ config }} > runs/train.log 2>&1 < /dev/null &
```

**Redirect all three streams.** Without them the recipe still returns at once in a timewalk tab, because
a tab is a terminal. But wherever the recipe's output goes to a pipe, as in `just train-detached x | tee`,
a script or CI, the background process holds that pipe open and the recipe waits for it to finish. With
the redirects it returns at once everywhere. Follow the run with `tail -f runs/train.log`.

A detached run is no longer tied to any tab: no tab gets a dot when it finishes, and stopping it takes
`kill`. Its files still come from the folder it started in, so moving steps changes them under it, as
above.

## Claude

**Claude** starts [Claude Code](https://claude.com/claude-code) in the replay copy, so you can ask what
the code is at this commit. `--assistant aider` starts another tool there; `--assistant ''` gives a plain
shell.

## Which tab has the keyboard

The page keeps the arrow keys, for steps and slides, until you click into a terminal. That terminal then
has every key, and a blue edge says so. Click anywhere else to give the keys back. Switching tabs, or a
command sent from the presenter page, also gives that terminal the keys. Alt with an arrow moves steps
and slides even from inside a terminal.

![No edge: the page has the keys](images/terminal-unfocused.png)

![A blue edge: the terminal has the keys](images/terminal-focused.png)

## The shells' environment

The shells get your environment, with `TERM=xterm-256color` and `TIMEWALK=1` added, and without
timewalk's own Python. `uv run timewalk.py` puts the script's environment first on `PATH` and names it in
`VIRTUAL_ENV`; the shells take both off, so `python`, `pytest` and the rest are the project's or yours,
never timewalk's.

A shell runs your startup files, so your prompt, aliases and tools are there. A tool that reads
`TIMEWALK` can tell it is running inside timewalk.
