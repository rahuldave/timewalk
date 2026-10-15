# Reference

## Install

Run timewalk from GitHub with uv, with no clone and no install:

```
uvx --from git+https://github.com/rahuldave/timewalk@v1.4.5 timewalk [repo] [options]
uvx --from git+https://github.com/rahuldave/timewalk@v1.4.5 timewalk-pdf [options]
```

Or install the commands `timewalk`, `timewalk-pdf`, `timewalk-check`, `timewalk-notes` and `timewalk-export` once with `uv tool install git+https://github.com/rahuldave/timewalk@v1.4.5`. In a clone of timewalk, run them with
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
| `--no-run` | In a strict tutorial, do not run the `do$` lines of `uv` to see which files they write. Do mode then gives the `uv` command itself. `TIMEWALK_NO_RUN=1` does the same. See [A strict walk](walks.md#a-strict-walk) |

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
| `--with-notes` | Put the notes of each step after its slides: the prose, the cues in their own shade, and the commands in code blocks. The `time:` lines stay out. Each block for one mode prints under "In do mode:" or "In watch mode:" |
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

In a tutorial, it also runs the `do$` lines of each move, to see where each file of the move comes from. A
file gets its content from a `do$` line that writes it, or from a patch that a `patch$` line names. It
runs the `do$` lines of a move in order, at the commit before the move, in a scratch clone of the project.
It notes the files that each one writes. The clone is a `git clone --shared` in a folder of its own, made
afresh for each move, with no remote. So nothing is written in your repository.

The result of each move goes into a cache, `~/.cache/timewalk/observed`, so a
second check runs nothing. A move with a `do$` line that failed runs again. Each of these problems is a warning:

- No `do$` line writes a file of the move, and no `patch$` line names it. The warning ends "add patch$
  PATH".
- A `do$` line writes a file, and the move changes it after that, and no `patch$` line names it.
- A `do$` line writes a file that the move does not change.
- A `do$` line fails at the commit before the move. The lines after it do not run. One case gets no warning. The
  move's last `do$` line writes a file of the move that no `patch$` line names. Then it ends with an error code from 1
  to 125. And the move has no other warning or error. An example is the linter ruff, as `ruff check --fix`. It writes its
  fixes into the move's files. Then it ends with an error code, because some faults in the code are ones that it cannot
  fix. Its fixes are the move's change, so the check does not warn. A line that runs out of time, is killed, or is not
  found is always a warning. The result of a line that fails is not kept, so such a move runs its `do$` lines at each check.

A file that the walk's `loose` names, and that matches by the names of its packages, has no change after
its `do$` line. So a `uv.lock` with newer versions is the `do$` line's. Give `--no-run`, or set
`TIMEWALK_NO_RUN=1`, to run nothing. A move with `do$` lines is then not checked. timewalk itself runs
nothing, unless a strict walk has a `do$` line of `uv`.

In a strict walk, timewalk runs the `do$` lines of a move with a `do$` line of `uv`, in the
background, with the same cache. See [A strict walk](walks.md#a-strict-walk).

A `patch$` line that names a file that its move does not change is an error. A `patch$` line before the
first move of a step, or in a block for one mode, is a warning.

A step after the first can be a single commit, right after the tag of the step before. Such a step has no
moves. In a tutorial that is not a small walk, `timewalk-check` treats its commit as one move. Give a `patch$`
line for each file that the commit changes. A file with no `patch$` line is a warning, and a `patch$` line for a
file that the commit does not change is an error.

A `do$` line in such a step is a warning, because the step has no move for it to make. Write the command as a
`$` line. In a tutorial that is not a small walk, a `patch$` line in the first step is a warning too. The learner's
clone already has every file of that step, so the line has nothing to do.

A sentence that asks for **Apply this file** or **Apply the whole move** is a warning. timewalk no longer
has these buttons. Write a line `patch$ PATH` for each file that the move changes by a patch.

In any walk, a `show:` line that quotes a line with a backtick in it in one backtick is a warning. Write
it in two backticks. See [Quote a line with backticks](walks.md#quote-a-line-with-backticks).

A public cue, for example `> Try:`, in the blockquote of a private cue, for example `> Say:`, is an error.
The check reads the notes and the intro file of a walk, and gives the file and the line, for example
`notes.md:6: '> Try: this' is a public cue in the blockquote of the cue '> Say: a secret'; put a blank line
between the two`.

It reads the items and the commands once for each mode, as the reader of that mode sees them. A warning
for one mode names it, for example "step-02.1 has no command in watch mode". A `do$` line does not count
as a command for this warning.

A walk with `select` is checked on the sections that it takes. A section that it takes and the notes do
not have is an error, for example "step-05.3 to step-05.6 takes the section ### step-05.4, and notes.md
has none". See [The checks of a small walk](walks.md#the-checks-of-a-small-walk).

A walk with notes of its own is checked on its stops and their include lines. See
[The checks of a walk with notes of its own](walks.md#the-checks-of-a-walk-with-notes-of-its-own).

The blocks for one mode give these problems:

- **An error:** a block `::: do` or `::: watch` that is not closed, a heading in a block, or a block in a
  block.
- **A warning:** a fenced div of another name, for example `::: callout-note`, or a line `:::` that closes
  no block.
- **A warning:** a `do$` line in a block, before the first move of a step, or after the commands that
  check its move. A `do$` line shows in both modes, so put it outside the blocks, in the section of the move
  that it makes, before the checks. In a narrative, every `do$` line is a warning, as a narrative has no moves
  to make.

## timewalk-notes

```
timewalk-notes REPO --toc toc.toml --walk ID [--write] [--blocks]
```

| Option | Does |
|---|---|
| `REPO` | The repository that the class walks through |
| `--toc` | The table of contents |
| `--walk` | The `id` of a tutorial walk with a notes file, or with a folder |
| `--write` | Write the drafts into the notes file. Without it, print only the drafts |
| `--blocks` | Kept for old scripts. It does nothing, because every draft has both blocks |

Drafts a `### step-NN.k` section for each move that the notes do not have yet, at the end of its step. A
draft has the subject of the commit as its title. Then it has these parts, each a comment for you to
replace:

- the problem that the change solves, and the change, for both modes
- a line `patch$ PATH` for each file of the commit. Put a `do$` line in place of the `patch$` lines of the
  files that a command writes
- a block `::: do`, for what to read in each patch, and where in the file. It copies no code
- a block `::: watch`, for what the change does and where to see it
- the check, a command and what it prints

Sections already in the notes stay as they are.

A step without a section gets a heading `## step-NN Title`, with the first line of its tag's message as
the title, steps without moves too. A walk whose folder has no `notes.md` yet gets one. For a walk with
`select`, it drafts only the sections that the walk takes, into the notes of the whole tutorial, in order.
For a walk with notes of its own, it drafts the move sections that its stops include, into its `shared` notes.

The draft ends with a line `files:`, with one item for each file of the commit, `uv.lock` too. A new file
gets a `file` item, and every other file a `diff` item. You write the words of each item. The draft has no `show:` lines. See
[Draft the notes of the moves](walks.md#draft-the-notes-of-the-moves).

## timewalk-export

```
timewalk-export REPO --toc toc.toml [--walk ID] (--markdown DIR | --site DIR | --json FILE) [options]
timewalk-export REPO --notes notes.md [--tags GLOB] (--markdown DIR | --site DIR | --json FILE) [options]
```

| Option | Does |
|---|---|
| `REPO` | The project, with its step tags. Its commits give the links |
| `--toc` | The table of contents. Give `--toc` or `--notes`, not both |
| `--walk` | With `--toc`: the `id` of a walk with notes, a tutorial or a narrative. Default: the first tutorial of the table |
| `--notes` | The notes file of a tutorial, in place of `--toc` |
| `--tags` | With `--notes`: the glob of the step tags. Default: `step-*` |
| `--github` | The address of the project on GitHub, `https://github.com/OWNER/NAME`, for the links. Default: the remote `origin` of the project. If `origin` is not on GitHub, give it |
| `--title` | The title. Default: the `#` heading of the notes, or else the title of the walk. For a walk with `select`: the title of the walk |
| `--intro` | A Markdown file to put first, under the title |
| `--no-opening` | Leave out the text of the notes before the first step. A walk with `intro` in `toc.toml` always puts its intro there in place of that text |
| `--markdown` | The folder for `TUTORIAL-do.md` and `TUTORIAL-watch.md`. For a narrative: `TUTORIAL.md` |
| `--site` | The folder for the website: `index.qmd`, `watch/step-NN.qmd`, `do/step-NN.qmd`, `_smalldocs.yml` and `styles.css`. For a narrative: `step-NN.qmd` at the top of the folder, in place of `watch/` and `do/`. A small step's page has its name, for example `watch/step-05.3-to-step-05.6.qmd` |
| `--quarto` | With `--site`: write `_quarto.yml` in place of `_smalldocs.yml` |
| `--json` | The file for the facts of the walk, in JSON, for a class to make its own pages from. See [The facts file](#the-facts-file) |
| `--private` | With `--json`: keep the private lines, for a copy for the presenter. Without it, the facts leave them out, as the copies do |
| `--no-run` | Run no `do$` line. With `--json`, the facts then say `"observed": false`, and give no `writes`, `by`, `patch` or `take` for a move with `do$` lines. The do copy of a strict walk then gives each `uv` command as it is, and `timewalk-export` warns that a reader on a later day gets newer versions than the author's. `TIMEWALK_NO_RUN=1` does the same. See [A strict walk](walks.md#a-strict-walk) |

Give one or more of `--markdown`, `--site` and `--json`. Each copy of a tutorial has the text outside the blocks and the blocks
of one mode, without the private lines. A narrative has one copy, with the `watch` blocks. Each step, move
and item links to the code on GitHub. In a tutorial, a small step, and a whole step with moves, link to the code at
their **Start**, the commit before the first move. Build the website with `smalldocs render DIR`, or with `quarto render DIR` after `--quarto`.

The command writes over only the files that it made. It refuses a folder inside the project or inside its
replay copy. See [Copies of a tutorial for GitHub and a website](walks.md#copies-of-a-tutorial-for-github-and-a-website).

### The facts file

`timewalk-export --json FILE` writes the facts of one walk, its history and its notes, in one JSON file.
A class makes its own pages from the file, for example with the `walk` skill of walk-kit. The file holds
no words or layout of timewalk. The exact layout is a JSON Schema, `src/timewalk/facts.schema.json` in the
timewalk repository. The same notes and history give the same bytes. The file has no time of day, and no
path of your machine.

The text of the notes is in it as written, every character, blank lines too. Only the private lines are
left out.

| Field | Holds |
|---|---|
| `format` | The version of the layout, now `4`. A change that a renderer must know of raises it. Version 2 added `observed`, `number`, `writes`, `by` and the field `patch` of a file. Version 3 added the part `patch`, and removed the part `apply`. timewalk 1.4.3 added the field `take` to the part `command`. Version 4, from timewalk 1.4.4, added `after` to `take`. It also gives a `take` to the `do$` lines of a step with no moves after the first step, whose commit comes right after the step before it |
| `timewalk` | The version of timewalk that wrote the file |
| `made` | The line "Made by timewalk-export". The command writes over only a file with this line |
| `observed` | Whether the `do$` lines ran. See [timewalk-check](#timewalk-check) |
| `walk` | `id`, `title`, `description`, `kind` (`tutorial` or `narrative`), `mode` (the mode that a tutorial starts in, and `watch` for a narrative), `github` (the address of the project) and `loose`, the list of files from the walk's `loose` setting, for example `["pyproject.toml", "uv.lock"]`. In do mode, a `pyproject.toml` or `uv.lock` on the list matches the move's copy by the names of its packages, not by their versions. See [Files that match by the names of their packages](walks.md#files-that-match-by-the-names-of-their-packages). A facts file that timewalk 1.4.0 wrote has no `loose` |
| `intro` | The parts of the text before the first stop |
| `stops` | The stops of the walk, in order |
| `slides` | `null`, or the path of the manifest from the class folder, its `deck`, and its `slides` and `docs` for the stops and moves of the walk |
| `history` | The commit of every step and move of the whole history, by name. An item that names another part links to the file at that commit |
| `errors`, `warnings` | What `timewalk-check` reports for this walk, with paths from the class folder. In a strict walk, `errors` also names each change whose `do$` lines failed, so that its `take` is not known |

A stop has these fields:

| Field | Holds |
|---|---|
| `name`, `title` | The name of the stop, for example `step-02` or `step-05.3 to step-05.6`, and its title |
| `number` | The number that a reader sees: the name without the prefix of the tags and the zeros in front, for example `2`, `8b` or `5.3 to 5.6` |
| `tag` | For a whole step, its tag: `name`, `title` (the first line of the message) and `text` (the rest). `null` for any other stop, for example a small step |
| `commit`, `start` | The commit of the stop, and the commit where it starts: the tag before it, or the commit before its first move. `start` is `null` at the first commit of the history |
| `own` | In a walk with notes of its own: the `title` and the `text` of the section of the stop, with its includes put in. Else `null` |
| `parts` | The text of the stop before its first move |
| `moves` | The moves of the stop, in order. Empty in a narrative, and in a step without moves |
| `files` | For a stop with no moves, each file of its change from `start`. Empty for a stop with moves |

A move has a `name`, a `number` (for example `8b.1`), a `title`, its `commit`, the commit before it (`parent`), its
`parts` and its `files`.

The text of a stop or a move is a list of parts, in the order of the notes. Each part has a `kind`, and a
`mode`, which is `shared`, `do` or `watch`.

| Kind | Holds |
|---|---|
| `prose` | `text`: Markdown, as written, every character. Fenced code stays whole |
| `command` | `text`, the command. `track` is `""` for the shell at the step, `main`, or `runs` to `runs9`. `makes` is true for a `do$` line. `writes` lists the files that a `do$` line adds, changes or removes, when it ran. It is `null` for any other command. `take` is the command that do mode gives for a `do$` line of `uv` in a strict walk, in a move or in a step with no moves: `lines`, the places from 0 among the change's `do$` lines whose files the command takes, this line first; `files`, the files that those lines write and the change keeps; `deletes`, the files that they delete, and the old paths of those that they rename; `command`, the command that takes them from the commit, for example `git restore --source 1a2b3c4d5e6f -- pyproject.toml uv.lock && uv sync --locked --all-groups --all-packages`; and `after`. With `after` false, this line runs only `uv add`, `uv remove`, `uv lock`, `uv init`, `uv version` or `uv sync`, and the command takes the place of this line. With `after` true, this line runs a program, for example `uv run`. The learner runs this line, and then the command. In both cases, the learner does not run the other lines in `lines`. A later line that joins the take, and is not run, has empty `lines`, `files` and `deletes`, and `command` `""`. `take` is `null` for any other command, and when the `do$` lines did not run. See [A strict walk](walks.md#a-strict-walk) |
| `item` | An item under `files:`. `item` is `diff` or `file`. It also has `path`, `move` (the move that the item names, or `null` for its own move), `words`, and `show`, the lines that its `show:` lines quote |
| `patch` | A `patch$` line. `path` is the file that the move changes by a patch, the diff of the move's commit. Do not mix it up with the field `patch` of a file |

A file of a change has these fields:

| Field | Holds |
|---|---|
| `path`, `old_path` | The path after the change, and for a rename the path before it. Else `old_path` is `null` |
| `status` | `added`, `modified`, `deleted` or `renamed` |
| `added`, `removed`, `binary` | The count of lines added and removed, and whether git sees the file as binary |
| `diff` | The whole diff of the file, from its first hunk, never cut. `""` for a binary file, or for a change of its mode alone |
| `names` | `added`, `removed` and `changed`: the Python functions and classes that the change adds, removes or changes, and the recipes that it adds to a `justfile` |
| `by` | Where the file comes from: the place, from 0, among the `do$` lines of the move, of the last one that writes it. `"patch"` when no `do$` line writes it. `null` when the `do$` lines did not run. In a step with no moves of a tutorial, after the first step, the place among the step's own `do$` lines. Any other stop with no moves has `"patch"` for every file |
| `patch` | The field `patch` of a file: what a person changes by hand, from its first hunk. For a patch, the whole diff. For a file of a `do$` line, the diff from what the line left to the commit, and `""` when there is none. `null` when the `do$` lines did not run |

Without `--private`, the facts leave out the private lines. A private line is a `time:` line, a cue in an
HTML comment, or a cue `> Say:` or `> Note:`.

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
| `just style ...` | Check the pages of the site against the writing guide, `docs/style/GUIDE.md`. Add `-v` to also show the soft notes. Then Claude Code reviews the prose that changed since the last commit, and a problem that it finds fails the check. Each file has a report in `reports/writing/` |

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
| `select` | The steps, moves and ranges of moves that the walk takes from a bigger walk, in the order of the history, for example `["step-04", "step-05.3..step-05.6", "step-10.3"]`. An entry `{ range = "step-05.3..step-05.6", title = "Types" }` gives its stop a title. Not with `steps` |
| `shared` | In a tutorial with notes of its own: the notes of the big tutorial, where its include lines take the sections of its moves. Its `notes` then hold its intro and a `## Title` section for each stop. Not with `select`, `steps` or `intro` |
| `intro` | A Markdown file about the walk. The notes column shows it at the first stop, under "About this walk". The copies of `timewalk-export` put it before the first step, in place of the text of the notes before it |
| `loose` | In a tutorial: the files, or globs, that do mode compares by the names of their packages, for example `["pyproject.toml", "uv.lock"]`. Another file that it names is not compared by its contents, but must be there when the move makes it |
| `moves` | In a tutorial: the mode it starts in, `"do"` or `"watch"`. Default: `"do"` |
| `sync` | In a tutorial: whether the slides and the moves follow each other. Default: `true` |
| `setup` | `"manual"`: you run `just setup` yourself at each step. The only value for now |

[Several walks](walks.md#the-table-of-contents) lists every key.

## The notes file

| Line | Does |
|---|---|
| `## step-name Title` | Starts a step's section |
| `time: m:ss` | The planned start, for the clock band. The notes column and the PDF leave it out |
| `> text` | A cue. Every window shows it, in its own shade. Remove private cues before you give the notes to students |
| `$ cmd`, `runs$ cmd`, `runs2$ cmd` to `runs9$ cmd`, `main$ cmd` | A command for that tab |
| `do$ cmd` | In a tutorial: a command that makes the move. In do mode, a command for the **At this step** tab. In watch mode and in a narrative, the line "The move ran:" and the command, with no button. Never an anchor command. The do copy of `timewalk-export` shows it as a command, or in a strict walk as do mode does, and the watch copy as "The move ran:". The PDF prints it as written |
| `patch$ PATH` | In a tutorial, in the shared text of a move: the move changes `PATH` by a patch, the diff of its commit. Write a path with a space in backticks. In do mode, on the move being made, **⇣ Apply patch PATH** or **⇄ Replace with the move's file PATH**, and **± See diff PATH**. In watch mode, "A patch to PATH" and **± See diff PATH**. See [patch$ buttons](walks.md#patch-buttons) |
| `### step-NN.k Title` | In a tutorial: starts the section of move `k` of step `NN`. Write the sections in the order of the commits |
| `files:` | Starts a list of items. With no items: a **± See diff** button for each file that the commit of the move added or changed |
| ``- diff `path`: words`` | An item: a button **± See diff path** to the change of the move, or of the step, to that file. Its words go under the button |
| ``- file `path`: words`` | An item: a button **▤ See file path** to the file. In do mode, for a move not made yet, the file as the move leaves it |
| ``- diff step-02.1 `path`: words`` | An item of another move |
| ``  show: `a line` `` | Under an item: a line of the change, drawn as an excerpt in the notes and marked in the reader. A click on the excerpt opens the change |
| ```  show: ``a `b` c`` ``` | The same, for a line with a backtick in it. See [Quote a line with backticks](walks.md#quote-a-line-with-backticks) |
| `## Title` | In the notes of a walk with notes of its own: starts a stop. The text before the first one is the intro of the walk |
| `{{include step-NN.k}}` | In the notes of a walk with notes of its own, on a line of its own: puts in the section of a move of the shared notes. `{{include step-NN}}` puts in a whole step, and `{{include step-NN.j..step-NN.k}}` a range of moves. Not in a block `::: do` or `::: watch`. See [A walk with notes of its own](walks.md#a-walk-with-notes-of-its-own) |
| `::: do`, `::: watch` | In a tutorial: opens a block for one mode, up to a line `:::`. The page shows it only in that mode. A narrative shows the `watch` blocks |
| The last commands of a move section | The anchor commands of the move, under "Check it". They show what the move did. A `do$` line is never one. `timewalk-check` warns of a move section with no command |

## The views of the reader

| View | Shows |
|---|---|
| **Your file** | The file on the disk, in the replay copy |
| **At step-02.3** | In do mode: the file as a move not made yet leaves it, read only. A `file` item opens it |
| **Last change: name** | The step before, or the move before, to this one |
| **Next change: name** | This step or move, to the next one |
| **Yours and step-02.3** | In do mode: your file, against the file as the move that you make leaves it. Lines with `-` are yours, and lines with `+` are the move's. A file of the walk's `loose` that matches by the names of its packages says so |
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
