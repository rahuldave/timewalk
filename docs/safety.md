# Safety

A terminal in a web page can run commands on your machine. timewalk lets only you use it.

- **It listens on `127.0.0.1` only.** Nothing on your network can reach it.
- **Every request needs the token.** The token is a secret string in the address that timewalk prints.
  Every page, API call, socket, slide and script needs it. timewalk makes a new token at each start. After
  you open the address, the pages keep the token in a cookie, so you paste the address only once.
- **The server refuses requests addressed to any other host name.** So a hostile web page cannot point a
  name of its own at `127.0.0.1` and reach the server.
- **The projector page never asks for the notes.** Only the presenter page asks for them, through
  `/api/notes`, and `/api/notes` needs the token as every other request does.
- **Only the terminals write files.** The file view has no route that changes a file.

## What you must do

- **Do not put the printed address on a slide or in a recording.** While the server runs, anyone
  on your machine with the address can run commands as you.
- **Stop timewalk at the end of the class.** When timewalk stops, the address does not answer.
- **Mirror a screen to share the projector page.** Do not open the address on another machine. The
  server does not let another machine reach it, and that is intentional.
