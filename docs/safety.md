# Safety

A terminal in a web page is a way to run commands on your machine. timewalk keeps it to you.

- **It listens on `127.0.0.1` only.** Nothing on your network can reach it.
- **Every request needs the token** in the address it prints: every page, API call, socket, slide and
  script. A new token is made at each start. The pages carry it in a cookie once opened, so you only
  paste the address once.
- **Requests addressed to any other host name are refused.** That stops a hostile web page from reaching
  the server by pointing a name of its own at `127.0.0.1`.
- **The projector page never asks for the notes.** Only the presenter page requests them, through
  `/api/notes`, which needs the token like everything else.
- **Nothing writes files except the terminals.** The file view has no route that changes a file.

## What you should do

- **Do not put the printed address on a slide or in a recording.** Anyone on your machine who has it
  can run commands as you while the server is running.
- **Stop timewalk when the class is over.** With it stopped, the address no longer answers.
- **Share the projector by mirroring a screen,** not by opening the address on another machine. It is not
  reachable from one, by design.
