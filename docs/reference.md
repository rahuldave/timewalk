# Reference

## timewalk

```
uv run timewalk.py [repo] [options]
```

| Option | Does |
|---|---|
| `repo` | The repository to browse. Default: the current folder |
| `--notes FILE` | A Markdown file of presenter notes, one `## step-name` section per step |
| `--slides FILE` | A TOML manifest of slides and documents |
| `--tags GLOB` | The tags that mark steps. Default: `step-*` |
| `--commits` | Step through the commits of the current branch instead of tags |
| `--in-place` | Move the repository itself instead of a second working copy |
| `--discard-edits` | A move throws edits to tracked files away instead of asking and stashing them. For a throwaway replay copy; refused with `--in-place`. See [Live edits](edits.md#with---discard-edits) |
| `--assistant CMD` | The command the Claude tab starts. Default: `claude`. `''` for a plain shell |
| `--port N` | Default: 8765 |
| `--no-open` | Do not open the browser |

## slides_pdf

```
uv run slides_pdf.py MANIFEST [-o OUT.pdf] [--title TEXT] [--notes FILE]
```

| Option | Does |
|---|---|
| `MANIFEST` | The slides manifest |
| `-o`, `--output` | The PDF to write. Default: `slides.pdf` beside the manifest |
| `--title` | A title for the footer of every page |
| `--notes` | A notes file, read only for each step's title |

## Recipes

In the timewalk folder, `just` lists these:

| Recipe | Does |
|---|---|
| `just demo` | Fetch the demo submodule if needed, and open it with its notes and slides, with `--discard-edits` |
| `just walk REPO ...` | Run timewalk on a repository; extra arguments go to timewalk |
| `just pdf MANIFEST ...` | Make a PDF of every slide in a manifest |
| `just test ...` | Run the tests; extra arguments go to pytest |
| `just lint` | Lint the Python |
| `just screenshots` | Take the site's screenshots again, from the demo |
| `just site` | Build this site into `docs/_site` and serve it locally |

## The slides manifest

| Key | Holds |
|---|---|
| `deck` | Optional. The deck bare numbers refer to |
| `[slides]` | `step-name = [entries]`: Markdown slides, pictures, PDF pages, HTML deck slides |
| `[docs]` | `step-name = "file.md"`: one whole document instead of slides |

Entries are listed in [Slides and documents](slides.md#the-manifest).

## The notes file

| Line | Does |
|---|---|
| `## step-name Title` | Starts a step's section |
| `time: m:ss` | The planned start |
| `$ cmd`, `runs$ cmd`, `runs2$ cmd` to `runs9$ cmd`, `main$ cmd` | A command for that tab |

## Keys

| Page | Key | Does |
|---|---|---|
| Both pages | Left, Right | Previous or next step |
| Both pages | Up, Down | Previous or next slide, when there is more than one |
| Both pages | Alt with an arrow | The same, even inside a terminal |

## Requirements

`uv`, `git`, and a browser. `just` for the recipe buttons and the recipes above. `claude` on the path
for the Claude tab. Chrome or Edge for the PDF export and the screenshots. Checked in Chrome; Safari and
Firefox are not checked.
