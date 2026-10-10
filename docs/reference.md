# Reference

## Install

Run timewalk from GitHub with uv, with no clone and no install:

```
uvx --from git+https://github.com/rahuldave/timewalk@v1.0.14 timewalk [repo] [options]
uvx --from git+https://github.com/rahuldave/timewalk@v1.0.14 timewalk-pdf [options]
```

Or install the commands `timewalk`, `timewalk-pdf`, `timewalk-check` and `timewalk-notes` once with `uv tool install git+https://github.com/rahuldave/timewalk@v1.0.14`. In a clone of timewalk, run them with
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
| `--toc FILE` | A table of contents, `toc.toml`, of several walks, each with its own notes and slides. Give it in place of `--notes`, `--slides` and `--tags`. See [Several walks](walks.md) |
| `--walk ID` | With `--toc`: the walk to start on. Default: the first walk of the table |
| `--tags GLOB` | The tags that mark steps. Default: `step-*` |
| `--commits` | Step through the commits of the current branch instead of tags |
| `--replay PATH` | Put the replay copy at `PATH`, for example `worktree`, instead of `<repo>-replay` beside the repository. The replay copy is a clone on the branch `timewalk/replay`. timewalk refuses a path inside the repository. See [The replay copy](replay.md) |
| `--in-place` | Move your repository itself instead of the replay copy |
| `--host ADDRESS` | The address to listen on. Default: `127.0.0.1`, this machine only. `0.0.0.0` listens on every network, for a cloud machine; timewalk then prints a warning, and accepts every host name. See [Safety](safety.md#run-timewalk-on-a-cloud-machine) |
| `--clock` | Show the clock band on the page, in every window. The band shows the clock, the planned times and the next step. Off by default |
| `--discard-edits` | No longer needed. A move keeps edits on a saved branch, `timewalk/saved/<step>`, and does not ask. With a replay copy that is a clone, timewalk prints a line that says so. Only on a replay copy that an older version made as a worktree does a move still drop edits with it. timewalk refuses it with `--in-place`. See [Live edits](edits.md#with---discard-edits) |
| `--assistant CMD` | The command the Claude tab starts. Default: `claude`. `''` for a plain shell |
| `--port N` | The port to listen on. Default: 8765. If another program listens on the port, timewalk stops and says so. Then give another port. A timewalk that you just stopped does not block the port |
| `--no-open` | Do not open the browser |

## timewalk-pdf

```
timewalk-pdf [MANIFEST] [-o OUT.pdf] [--title TEXT] [--notes FILE] [--with-notes] [--brand DIR]
timewalk-pdf --toc toc.toml [--walk ID] [-o OUT.pdf] [--with-notes] [--brand DIR]
```

| Option | Does |
|---|---|
| `MANIFEST` | The slides manifest. Optional with `--with-notes`. Without a manifest, the PDF holds only the notes |
| `-o`, `--output` | The PDF to write. Default: `build/slides.pdf` in the current folder, or `build/notes.pdf` when there is no manifest. The folder `build` is made if it is missing |
| `--title` | A title for the footer of every page |
| `--notes` | A notes file. Without `--with-notes`, `slides_pdf` reads only the title of each step from it, for the footer |
| `--with-notes` | Put the notes of each step after its slides: the prose, the cues in their own shade, and the commands in code blocks. The `time:` lines stay out |
| `--brand` | A brand: a folder with a `brand.toml`, or the `brand.toml` itself. It sets the font, colours, footer, cover and dividers. See [Brand a PDF](brand.md) |
| `--toc`, `--walk` | Take the manifest and the notes of one walk from a table of contents, in place of `MANIFEST` and `--notes`. Default: the first walk. The title of the walk goes in the footer, unless you give `--title` |

The **PDF** button on the page makes the same PDF, without the notes and without a brand. See
[Slides and documents](slides.md#a-pdf-of-the-slides).

## timewalk-check

```
timewalk-check REPO --toc toc.toml
timewalk-check REPO --notes notes.md --slides slides/slides.toml
```

Checks the notes and the slides of every walk against the steps, and against each other. It prints
one line for each problem, and exits with code 1 when it finds an error. timewalk runs the same checks when it
starts with `--toc`. See [Several walks](walks.md#the-checks), and the full list in
[What a narrative and a tutorial need](authoring.md#how-to-check-a-walk).

In a tutorial, it also checks the items under `files:` against the commits. Each of these problems is a
warning:

- A `diff` item names a file that its move does not change.
- A `file` item names a file that is not in the commit of its move.
- A `show:` line is no longer in the change of its item.
- An item names a move that is not in the walk.

## timewalk-notes

```
timewalk-notes REPO --toc toc.toml --walk ID [--write]
```

| Option | Does |
|---|---|
| `REPO` | The repository that the class walks through |
| `--toc` | The table of contents |
| `--walk` | The `id` of a tutorial walk with a notes file |
| `--write` | Write the drafts into the notes file. Without it, print only the drafts |

Drafts a `### step-NN.k` section for each move that the notes do not have yet, at the end of its step. A
draft has the subject of the commit as its title, and a list "What changed:" from the diff. It ends with a
comment that asks for a command. Sections already in the notes stay as they are.

The draft also has a line `files:`, with one item for each file of the commit. A new file gets a `file`
item, and a changed file a `diff` item. You write the words of each item. The draft has no `show:` lines. See
[Draft the notes of the moves](walks.md#draft-the-notes-of-the-moves).

## Recipes

In the timewalk folder, `just` lists these:

| Recipe | Does |
|---|---|
| `just demo` | Fetch the demo submodule if needed. Then open it with its notes and slides, with `--clock` |
| `just demo-walks` | The same, with the two walks of `demo/toc.toml` |
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
| `"step-name.N"` | In a tutorial, under `[slides]`: the slides of one move. Quote the name |
| `sync` | In a tutorial, at the top: `false` keeps the slides and the moves apart. It wins over `sync` in `toc.toml` |

[Slides and documents](slides.md#the-manifest) lists the entries.

## The table of contents

| Key | Holds |
|---|---|
| `[[walk]]` | One table for each walk. The first is the default |
| `id`, `title`, `kind` | The name, the title in the menu, and `"narrative"` or `"tutorial"` |
| `description` | What the walk is about, in a sentence or two. The status band shows it at the first step of the walk |
| `notes`, `slides`, `folder` | The notes file and the manifest, or a folder that holds `notes.md` and `slides/slides.toml` |
| `steps`, `tags` | Some of the steps by name, or a glob for tags of its own |
| `moves` | In a tutorial: the mode it starts in, `"do"` or `"watch"`. Default: `"do"` |
| `sync` | In a tutorial: whether the slides and the moves follow each other. Default: `true` |

[Several walks](walks.md#the-table-of-contents) lists every key.

## The notes file

| Line | Does |
|---|---|
| `## step-name Title` | Starts a step's section |
| `time: m:ss` | The planned start, for the clock band. The notes column and the PDF leave it out |
| `> text` | A cue. Every window shows it, in its own shade. Remove private cues before you give the notes to students |
| `$ cmd`, `runs$ cmd`, `runs2$ cmd` to `runs9$ cmd`, `main$ cmd` | A command for that tab |
| `### step-NN.k Title` | In a tutorial: starts the section of move `k` of step `NN`. Write the sections in the order of the commits |
| `files:` | Starts a list of items. With no items: a **± See diff** button for each file that the commit of the move added or changed |
| ``- diff `path`: words`` | An item: a button **± See diff path** to the change of the move, or of the step, to that file. Its words go under the button |
| ``- file `path`: words`` | An item: a button **▤ See file path** to the file. In do mode, for a move not made yet, the file as the move leaves it |
| ``- diff step-02.1 `path`: words`` | An item of another move |
| ``  show: `a line` `` | Under an item: a line of the change, drawn as an excerpt in the notes and marked in the reader. A click on the excerpt opens the change |
| The last commands of a move section | The anchor commands of the move, under "After this move, run:". They show what the move did. `timewalk-check` warns of a move section with no command |

## The views of the reader

| View | Shows |
|---|---|
| **Your file** | The file on the disk, in the replay copy |
| **At step-02.3** | In do mode: the file as a move not made yet leaves it, read only. A `file` item opens it |
| **Last change: name** | The step before, or the move before, to this one |
| **Next change: name** | This step or move, to the next one. In do mode, it has the **Apply** bar |
| **Changes in step-02.1** | The change of another move, opened from the notes |
| **Your edits** | The edits since the commit that the replay copy stands on |

In do mode, **Last change** and **Next change** follow the moves marked done. In watch mode, they follow
the move on show. On **Your file**, **Open in VS Code** opens the file in your editor, on this machine
only. Alt+click changes the editor. See [The reader](page.md#the-reader) and
[The reader in a tutorial](walks.md#the-reader-in-a-tutorial).

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
| Shift+Up, Shift+Down | The first or the last slide of the step |
| Shift+Right, Shift+Left | In a tutorial: the next move (do mode: marks it done, and the code does not move; watch mode: checks out its commit), or back to the step's Start, which checks out the tag before the step |
| Alt+Shift+Right, Alt+Shift+Left | The same, inside a terminal |
| Home, End | The top or the bottom of the slide, the file or the notes under the mouse |
| Alt+Enter | **Shell** on or off, in every window |
| Alt+1, Alt+2, Alt+3 | **Slides**, **Both**, **Code**, in every window |
| Alt+\` | **Slides** or **Code**: from one to the other |
| Alt+\\ | Shows or hides the notes, in this window. Not Alt+N, which types ~ on some Mac keyboards |

## Requirements

- **`uv`, `git` and a browser** for timewalk itself.
- **`just`** for the recipe buttons and the recipes above.
- **`claude` on the path** for the Claude tab.
- **Chrome or Edge** for the PDF export, the **PDF** button and the screenshots. If you have neither, run
  `uvx playwright install chromium` one time.

The author checks timewalk in Chrome, and not in Safari or Firefox.
