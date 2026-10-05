# Safety

A terminal in a web page can run commands on your machine. timewalk lets only you use it.

- **It listens on `127.0.0.1` by default.** Nothing on your network can reach it. The option `--host`
  changes this, for a cloud machine. See [Run timewalk on a cloud machine](#run-timewalk-on-a-cloud-machine).
- **Every request needs the token.** The token is a secret string in the address that timewalk prints.
  Every page, API call, socket, slide and script needs it. timewalk makes a new token at each start. After
  you open the address, the pages keep the token in a cookie, so you paste the address only once.
- **The server refuses requests addressed to any other host name.** So a hostile web page cannot point a
  name of its own at `127.0.0.1` and reach the server. With `--host`, the server accepts every host name,
  because people reach it by an IP address or a DNS name. The token is then the only guard.
- **The page writes one file, the notes file.** **Save** in the notes column sends one section through
  `/api/notes`, and the server writes that section of the notes file. The request needs the token, as
  every other request does. timewalk refuses a notes file inside your repository or its replay copy.
- **Apart from the notes file, only the terminals write files.** The file view has no route that changes
  a file.

## What you must do

- **Do not put the printed address on a slide or in a recording.** While the server runs, anyone
  on your machine with the address can run commands as you.
- **Stop timewalk at the end of the class.** When timewalk stops, the address does not answer.
- **Open the page in a window on the projector, from your own machine.** Do not open the address on
  another machine. By default, the server does not let another machine reach it, and that is
  intentional.
- **Hide the notes in the window on the projector if they hold private cues.** Every window can show the
  notes, cues included. The **Notes** button hides them in one window.

## Run timewalk on a cloud machine

You can run timewalk on a cloud machine, for example a virtual machine on Google Cloud (GCP), and open the
page on your laptop. Use an SSH tunnel if you can. An SSH tunnel carries the connection inside SSH, which
encrypts it, and it opens no port to the internet.

1. On the cloud machine, start timewalk as usual. It listens on `127.0.0.1`.
2. On your laptop, open the tunnel. The command below uses GCP, with the name of your machine:

   ```
   gcloud compute ssh my-vm -- -L 8765:localhost:8765
   ```

3. On your laptop, open the address that timewalk printed on the cloud machine.

If a tunnel is not possible, start timewalk with `--host 0.0.0.0`. timewalk then listens on every network
of the machine, and it prints a warning. Then open the port in the firewall of the cloud, for your own IP
address only:

```
uv run timewalk.py ~/code/project --notes notes.md --host 0.0.0.0 --port 8765
gcloud compute firewall-rules create timewalk --allow tcp:8765 --source-ranges <your-ip>/32
```

Do not open the port to every address. The connection is not encrypted, so anyone between you and the
machine can read the token. Anyone with the token can run commands on the machine. Close the port and stop
timewalk at the end of the class.
