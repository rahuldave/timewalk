# Development

timewalk is a few files with no build step. The server is one Python file run with `uv run`, which
reads its dependencies from the file itself; the pages are plain HTML, CSS and JavaScript modules.

| File | What it is |
|---|---|
| `timewalk.py` | The server: the git layer (`Repo`), the notes and slides readers, the terminals, the edits watcher, the web application |
| `slides_pdf.py` | The PDF export |
| `static/` | The projector page (`index.html`, `app.js`), the presenter page (`presenter.html`, `presenter.js`), the print page, `common.js`, `app.css` |
| `static/vendor/` | ghostty-web, highlight.js and marked, each with its licence |
| `tests/test_timewalk.py` | The tests |
| `demo/` | The demo submodule, its notes and slides |
| `docs/` | This site: the pages, `build.py`, `screenshots.py`, `images/` |

## Tests and lint

```
just test            # the git layer, notes and slides, the web application's guards, the terminals
just test -k slides  # extra arguments go to pytest
just lint            # ruff
```

The tests build small repositories of their own and check what timewalk must never do: move the
repository it is pointed at, delete an untracked file, discard an edit, write through the file view,
answer a request without the token. A change to the pages is checked by driving both pages in headless
Chrome against a throwaway clone of the demo.

## The site

```
just screenshots     # take every picture again, from a clone of the demo
just site            # build docs/_site and serve it at http://127.0.0.1:8000
```

The pages are Markdown in `docs/`. `build.py` renders them with one layout and the sidebar, in the order
of its `PAGES` list. A GitHub Action builds the site on every push to `main` and publishes it to GitHub
Pages.

`screenshots.py` clones the demo into a temporary folder, serves it with timewalk, and drives both pages
in headless Chrome, with bash and a short prompt so nothing personal shows. Take the pictures again
whenever the pages change.

## The demo

The demo's history is its own repository, held here as a submodule. To change it, work in the
submodule, keep one commit per step with an annotated `step-NN` tag, push its `main` and tags, then
commit the new submodule pointer here and check `demo/notes.md` and `demo/slides/slides.toml` against
the steps.

## Licence

MIT. The libraries in `static/vendor/` keep their own licences: ghostty-web (MIT), highlight.js (BSD
3-Clause) and marked (MIT).
