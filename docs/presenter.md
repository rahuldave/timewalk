# The presenter page

The presenter page is the page at the second address, which ends in `/presenter`. It is for your own
screen. The page is the [projector page](projector.md) with two additions:

- **The clock band** goes across the top. It shows the clock and the next step.
- **The notes column** is on the right. It shows your notes for the step and their commands.

![The presenter page, which is the projector page with the clock band on top and the notes column on the right](images/presenter.png)

## What the two pages share

The projector page and the presenter page show the same things. The server keeps the shared state. When
you make a change on either page, the server sends it to both pages.

| Shared by both pages | Each page's own |
|---|---|
| The step | The theme, dark or light |
| The slide | The text size |
| Slides, Both or Code | The height of the terminals |
| The open file, and its view | The scroll position of a file or slide |
| The terminal tab in front | Which terminal has the keyboard |
| The terminals themselves: the same shells | |

So you control the room from your screen. For example:

- Press **Right**, and the projector moves to the next step.
- Choose **Code**, and the projector shows the code.
- Open `greet.py` in **Changes in this step**, and the class sees it.

The room sees what you see, and you also see your notes.

## The clock band

| Part | Shows |
|---|---|
| The clock | Time since you pressed **Start the clock**. **Reset** stops it |
| The plan | Time left before the next step is due, or in red, how far over you are |
| Next | The name and title of the next step, and its planned time |

The planned times come from `time:` lines in your notes file. Without them, the band shows only the clock.

## The notes column

The notes column shows your notes for the step, as Markdown. The name of the step and the title from your
notes go at the top. Below the notes, each command of the step has a button.

A click on a button does these things:

- It types the command into its terminal and runs it.
- It brings that tab to the front on both pages.
- It gives the keyboard to the terminal on the projector page.

See [Presenter notes](notes.md).

The server reads the notes file again at every step. So you can edit the file while you present. The
projector page never asks for the notes.

## The keyboard

The keys are the same as on the projector page. **Left** and **Right** move to the previous or next step.
If a step has more than one slide, **Up** and **Down** change the slide. Alt with an arrow works even
inside a terminal. A terminal has the keys only after you click into it.

## On a narrow screen

If the screen is less than about 1000 pixels wide, the notes column moves under the terminals. So a laptop
screen still has room for the code.
