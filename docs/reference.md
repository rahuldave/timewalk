# Reference

## Install

Run timewalk from GitHub with uv, with no clone and no install:

```
uvx --from git+https://github.com/rahuldave/timewalk@v1.0.9 timewalk [repo] [options]
uvx --from git+https://github.com/rahuldave/timewalk@v1.0.9 timewalk-pdf [options]
```

Or install the two commands once with `uv tool install git+https://github.com/rahuldave/timewalk@v1.0.9`. In a clone of timewalk, run them with
`uv run timewalk` and `uv run timewalk-pdf`.

## timewalk

```
timewalk [repo] [options]
```

| Option | Does |
|---|---|
| `repo` | The repository to browse. Default: the current folder |
| `--notes FILE` | A Markdown file of notes, the script of each step, with one `## step-name` section per step. The page shows them in a column on the right. timewalk refuses a notes file inside your repository or its replay copy. See [Notes](notes.md) |
| `--slides FILE` | A TOML manifest of slides and documents |
| `--tags GLOB` | The tags that mark steps. Default: `step-*` |
| `--commits` | Step through the commits of the current branch instead of tags |
| `--replay PATH` | Put the replay copy at `PATH`, for example `worktree`, instead of `<repo>-replay` beside the repository. timewalk refuses a path inside the repository |
| `--in-place` | Move your repository itself instead of the replay copy |
| `--host ADDRESS` | The address to listen on. Default: `127.0.0.1`, this machine only. `0.0.0.0` listens on every network, for a cloud machine; timewalk then prints a warning, and accepts every host name. See [Safety](safety.md#run-timewalk-on-a-cloud-machine) |
| `--clock` | Show the clock band on the page, in every window. The band shows the clock, the planned times and the next step. Off by default |
| `--discard-edits` | A move drops edits to tracked files, and does not ask or stash them first. Use it for a replay copy that you throw away. timewalk refuses it with `--in-place`. See [Live edits](edits.md#with---discard-edits) |
| `--assistant CMD` | The command the Claude tab starts. Default: `claude`. `''` for a plain shell |
| `--port N` | The port to listen on. Default: 8765. If the port is in use, timewalk stops and says so. Then give another port |
| `--no-open` | Do not open the browser |

## timewalk-pdf

```
timewalk-pdf [MANIFEST] [-o OUT.pdf] [--title TEXT] [--notes FILE] [--with-notes]
```

| Option | Does |
|---|---|
| `MANIFEST` | The slides manifest. Optional with `--with-notes`. Without a manifest, the PDF holds only the notes |
| `-o`, `--output` | The PDF to write. Default: `slides.pdf` beside the manifest, or `notes.pdf` beside the notes file when there is no manifest |
| `--title` | A title for the footer of every page |
| `--notes` | A notes file. Without `--with-notes`, `slides_pdf` reads only the title of each step from it, for the footer |
| `--with-notes` | Put the notes of each step after its slides: the prose, the cues in their own shade, and the commands in code blocks. The `time:` lines stay out |

The **PDF** button on the page makes the same PDF, with the notes. See
[Slides and documents](slides.md#a-pdf-of-the-slides).

## Recipes

In the timewalk folder, `just` lists these:

| Recipe | Does |
|---|---|
| `just demo` | Fetch the demo submodule if needed. Then open it with its notes and slides, with `--discard-edits` and `--clock` |
| `just walk REPO ...` | Run timewalk on a repository. More arguments go to timewalk |
| `just pdf MANIFEST ...` | Make a PDF of every slide in a manifest |
| `just test ...` | Run the tests. More arguments go to pytest |
| `just lint` | Lint the Python |
| `just screenshots` | Take the site's screenshots again, from the demo |
| `just site` | Build this site into `docs/_site` and serve it locally |
| `just style ...` | Check the pages of the site against the writing guide, `docs/style/GUIDE.md`. Add `-v` to also show the soft notes |

## The slides manifest

| Key | Holds |
|---|---|
| `deck` | Optional. The deck that bare numbers refer to |
| `[slides]` | `step-name = [entries]`: Markdown slides, pictures, PDF pages, HTML deck slides |
| `[docs]` | `step-name = "file.md"`: one whole document instead of slides |

[Slides and documents](slides.md#the-manifest) lists the entries.

## The notes file

| Line | Does |
|---|---|
| `## step-name Title` | Starts a step's section |
| `time: m:ss` | The planned start, for the clock band. The notes column and the PDF leave it out |
| `> text` | A cue. Every window shows it, in its own shade. Remove private cues before you give the notes to students |
| `$ cmd`, `runs$ cmd`, `runs2$ cmd` to `runs9$ cmd`, `main$ cmd` | A command for that tab |

## Address options

Add these to the address that timewalk prints, after the token, with `&`.

| Option | Does |
|---|---|
| `cues=off` | The window starts with the cues hidden. timewalk prints this address for the class |
| `room=1` | The window for the class: no cues, no clock band, and no PDF or Edit buttons. The **Room** button opens it |

## Keys

| Key | Does |
|---|---|
| Left, Right | Previous or next step |
| Up, Down | Previous or next slide, when there is more than one. On a document, or with one slide, they scroll |
| Alt with an arrow | Moves the step or the slide, even inside a terminal or on a document |

## Requirements

- **`uv`, `git` and a browser** for timewalk itself.
- **`just`** for the recipe buttons and the recipes above.
- **`claude` on the path** for the Claude tab.
- **Chrome or Edge** for the PDF export, the **PDF** button and the screenshots. If you have neither, run
  `uvx playwright install chromium` one time.

The author checks timewalk in Chrome, and not in Safari or Firefox.
