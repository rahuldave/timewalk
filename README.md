# timewalk

Browse a git repository one commit at a time, with slides for each step, a terminal that runs in the
repository as it was at that step, and a private page of presenter notes. Built for teaching a project
by replaying how it was developed.

```
uv run timewalk.py ~/code/project --notes notes.md --slides slides/slides.toml
```

It prints two addresses. Open the first on the projector and the second on your own screen.

## Try it

```
just demo
```

builds a four-step sample repository in `demo/sample` and opens it with the demo's notes and slides.

## What is on each page

**The projector page** (`/`)

| Part | What it shows |
|---|---|
| Step bar | One chip per step. Click one, or use the arrows, or Alt+Left and Alt+Right |
| Slides | The slides for this step, to the left of the files. Alt+Up and Alt+Down change slide. The Slides, Both and Code buttons choose what is shown |
| Files | The tracked files at this step. Files the step added or changed are marked, and can be listed alone |
| Reader | A file as it is at this step, or what this step changed in it. Files cannot be edited here |
| Terminals | Tabs, each a real shell built on Ghostty's terminal core. **At this step**: in the repository at this step. **Runs**: a second shell there, for commands that take a while. **Main**: in the repository you started from. **Claude**: starts Claude Code at this step, to ask what the code is at this commit. **+** opens more |
| Recipes | The `just` recipes that exist at this step, as buttons. Ones new at this step are highlighted |

**The presenter page** (`/presenter`), for your screen only

| Part | What it shows |
|---|---|
| Notes | Your notes for this step, from the notes file |
| Clock | Time since you pressed start, time left in this step, and how far over you are |
| Slides | A preview, the list, and which of Slides, Both or Code the projector shows |
| Commands | The commands from your notes. One click types and runs one on the projector |
| Files | The files this step changed. One click opens one on the projector, as the file or as its changes |
| Next | The step that follows, and when it is due |

The Right arrow moves to the next slide, then to the next step. Shift+Right moves a whole step.
Everything done on the presenter page happens on the projector page too.

## Steps

By default the steps are the tags matching `step-*`, in name order. An annotated tag's message is shown
to the audience as the step's note.

| Option | Steps are |
|---|---|
| (none) | tags matching `step-*` |
| `--tags 'v*'` | tags matching another pattern |
| `--commits` | every commit on the current branch, oldest first |

## Slides

Slides are Markdown. A manifest, a TOML file, says which slides go with which step.

```toml
[slides]
step-00 = ["opening.md"]                       # every slide in that file
step-01 = ["tools.md#1-3", "pictures/a.svg"]   # slides 1 to 3 of a file, then a picture
step-02 = ["handout.pdf#page=2"]               # one page of a PDF
```

| Entry | Means |
|---|---|
| `"talk.md"` | every slide in the file, in order |
| `"talk.md#2"` | its second slide |
| `"talk.md#2-4"` | its second to fourth |
| `"picture.svg"`, `.png`, `.jpg` | a picture as a slide |
| `"deck.pdf#page=3"` | a page of a PDF |
| `"deck.html#/3"` | a slide of an HTML deck, with whatever fragment that deck uses |
| `7`, `"8-10"` | with `deck = "talk.md"` at the top of the manifest, those slides of that deck |

In a Markdown file, **a line that is exactly `---` starts the next slide**. Everything else is ordinary
Markdown: headings, lists, tables, links, bold, `code`, fenced code (highlighted when the fence names a
language), and pictures as `![description](path)` with the path relative to the slide file. For a
horizontal rule inside a slide, write `***`.

Paths in the manifest are relative to the manifest. The manifest and the slide files are read afresh
whenever a slide is shown, so they can be edited while presenting. A step with no entry shows the code
alone.

### A PDF of the slides

```
uv run slides_pdf.py slides/slides.toml -o handout.pdf --title "My talk" --notes notes.md
```

writes every slide in the manifest's order, one per page, with a footer naming the step. Markdown slides
and pictures are drawn as the browser draws them; pages of a PDF deck are copied from that PDF. A slide
with too much on it is shrunk to fit its page. `--notes` is read only for each step's title. It needs
Chrome or Edge, which it finds if installed; otherwise run `uvx playwright install chromium` once.

## Notes

A Markdown file with one section per step:

```markdown
## step-07 Training
time: 0:37

Start the run first, then read the code while it runs.

runs$ just train modal configs/cheese.yaml
$ cat configs/cheese.yaml
main$ git log --oneline
```

| Line | Does |
|---|---|
| `## step-name Title` | Starts a step's notes. The title replaces the commit subject on the presenter page |
| `time: 0:37` | The planned start, minutes:seconds into the session |
| `$ command` | A command for the terminal at this step |
| `runs$ command` | The same, in the Runs tab |
| `runs2$ command` | The same, in a second Runs tab (up to `runs9$`), opened when first used: for a second long command while the first is still going |
| `main$ command` | The same, in the repository you started from |
| anything else | Shown to you as Markdown |

Notes are served only to the presenter page.

## What it will and will not do to your repository

- **It never moves the repository you point it at.** Stepping happens in a second working copy,
  `<repo>-replay`, made beside it with `git worktree`. `--in-place` steps the repository itself.
- **It never deletes an untracked file.** Whatever a command wrote at one step (an environment, a
  database, a run's output) is still there at the next. If a later step has a file where an untracked
  one sits, the move is refused and the file is named.
- **It never discards an edit.** If tracked files were edited, moving asks first, and then sets the edits
  aside with `git stash`, labelled with the step they were made at.
- **The file view cannot write.** There is no route that changes a file. The terminals can, as any
  terminal can.

## Safety

A terminal in a web page is a way to run commands on your machine, so:

- The server listens on `127.0.0.1` only.
- Every page, API call, socket, slide and script needs the token in the printed address. A new token is
  made at each start.
- A request addressed to any other host name is refused, which stops a hostile web page from reaching
  the server by pointing a name at localhost.

Do not put the printed address on a slide or in a recording: anyone on your machine who has it can run
commands as you while the server is running.

## Requirements

`uv`, `git`, and a browser. `just` if the recipe buttons are wanted, and `claude` on the path for the
Claude tab (`--assistant ''` gives a plain shell there instead, `--assistant aider` another tool).

## Development

```
just test      # 44 tests: the git layer, notes and slides, the web application's guards, a real terminal
just lint
```

| File | What it is |
|---|---|
| `timewalk.py` | The server: the git layer, notes and slides readers, terminals, the web application |
| `slides_pdf.py` | The PDF export |
| `static/` | The two pages, the print page, and the vendored libraries |
| `static/vendor/` | ghostty-web (the terminal), highlight.js, marked. Each with its licence |
| `demo/` | The sample repository's builder, notes and slides |

Checked by driving both pages in headless Chrome through every step and slide, including typing in the
terminal. Not checked: Safari and Firefox.

## Licence

MIT: see [`LICENSE`](LICENSE). The libraries in `static/vendor/` keep their own licences, each in its
folder: ghostty-web (MIT), highlight.js (BSD 3-Clause) and marked (MIT).
