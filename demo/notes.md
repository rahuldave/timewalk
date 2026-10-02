# Presenter notes for the demo repository

Private: only the presenter page shows these.

## step-00 Where everything starts
time: 0:00

- Say what the project will become before showing any code.
- Point at the step bar: four steps, we are at the first.

$ ls -la
$ git log --oneline

## step-01 A file arrives
time: 0:02

The function works. **Nothing checks it**, and nothing says what `name` is.

- Open `src/greet.py` on the projector.
- Run it once.

$ just run

## step-02 Its tests
time: 0:04

Three tests. Ask: what would you test that is missing?

$ just test
runs$ for epoch in 1 2 3 4 5; do echo "epoch $epoch"; sleep 2; done
main$ git status --short

## step-03 Its types and docs
time: 0:06

Show the changes view for `src/greet.py`. The behaviour is identical; the signature now explains itself.

$ just test
