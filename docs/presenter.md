# The presenter page

The page at the second address, ending in `/presenter`. It is for your screen only: it holds your notes,
which the projector page never receives. Everything you do here happens on the projector.

![The presenter page at step-02, with the clock running](images/presenter.png)

## What is on it

| Part | What it shows |
|---|---|
| Step | The step's name and title, with **◀ Step** and **Step ▶** to move |
| Clock | Time since you pressed **Start the clock**, time left in this step, and how far over you are |
| Your notes for this step | The step's section of the notes file, as Markdown |
| Slides for this step | A preview of the slide on the projector, the step's list of slides, and **Slides**, **Both**, **Code** for the projector's layout |
| Commands | The commands from your notes. One click types one into the projector's terminal and runs it |
| Files | The files this step changed, and files edited since. **Show** opens one on the projector; **Changes** and **Edits** open it in those views |
| What the audience sees | The tag's message, as shown in the band on the projector |
| Next | The next step's title, and when it is planned |

## The clock

Each step's notes can give a planned start, `time: 0:37`, in minutes and seconds from the start of the
session. Once the clock is started, the page shows the time left before the next step is due, and turns
red with how far over you are. **Reset** stops it.

## Commands

A line in your notes that starts with `$ ` is a command for the terminal at this step; `runs$ ` sends it
to the Runs tab, `runs2$ ` to `runs9$ ` to further Runs tabs, and `main$ ` to the Main tab. Each is a
button here. One click types it on the projector and presses Enter, switches the projector to that tab,
and gives that terminal the keyboard. See [Presenter notes](notes.md).

## The keyboard

| Key | Does |
|---|---|
| Right | The next slide; after the last slide, the next step |
| Left | The previous slide; before the first, the previous step |
| Shift with Right or Left | A whole step, whatever the slide |

## Two screens, one state

Both pages follow the server. Moving, changing slide, opening a file or running a command on either page
is sent to every open page. A page that is reloaded comes back where the others are, and a terminal that
reconnects replays its recent output.
