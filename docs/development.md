# Development

timewalk is a few files, and it has no build step. The server is one Python file that you run with
`uv run`. `uv run` reads the dependencies of the server from the file itself. They are Starlette, uvicorn,
websockets, and Playwright and pypdf for the PDF. The pages are plain HTML, CSS and JavaScript modules.

| File | What it is |
|---|---|
| `timewalk.py` | The server. It holds the git layer (`Repo`), the readers for notes and slides, the terminals, the edits watcher and the web application |
| `slides_pdf.py` | The PDF export. The **PDF** button of the page uses it too |
| `static/` | The page (`index.html`, `app.js`), served at `/`. The old address `/presenter` sends the browser on to `/`. Also the print page, `common.js` and `app.css` |
| `static/vendor/` | ghostty-web, highlight.js and marked, each with its licence |
| `tests/test_timewalk.py` | The tests |
| `demo/` | The demo submodule, with its notes and slides |
| `docs/` | This site. It holds the pages, `build.py`, `screenshots.py`, `images/` and `style/` |

## Tests and lint

```
just test            # the git layer, notes and slides, the web application's guards, the terminals
just test -k slides  # extra arguments go to pytest
just lint            # ruff
```

The tests build small repositories of their own. They check that timewalk never does these things:

- move the repository that you give it
- delete an untracked file
- discard an edit, if `--discard-edits` does not ask for it
- write through the file view
- answer a request without the token

To check a change to the page, drive it in headless Chrome against a clone of the demo. Headless Chrome is
Chrome that runs without a window. Open the page in two windows, and check that they agree. Throw the
clone away after the check.

## The site

```
just screenshots     # take every picture again, from a clone of the demo
just site            # build docs/_site and serve it at http://127.0.0.1:8000
```

The pages are Markdown files in `docs/`. `build.py` renders them with one layout and the sidebar, in the
order of its `PAGES` list. A GitHub Action builds the site on every push to `main`. Then it publishes the
site to GitHub Pages.

`screenshots.py` clones the demo into a temporary folder and serves the clone with timewalk. Then it drives
the page in headless Chrome. It uses bash and a short prompt, so nothing personal shows. When the pages
change, take the pictures again.

## The writing guide

Every page of the site follows the writing guide, `docs/style/GUIDE.md`. The glossary of terms is in
`docs/style/README.md`, with the headings that other pages link to. Check the pages with the recipe:

```
just style                                    # every page
python3 docs/style/check.py -v docs/edits.md  # one page, with the soft notes
```

Fix each hard problem. Then read the page, and build the site with `uv run docs/build.py`.

## The demo

The history of the demo is its own repository. This repository holds it as a submodule, which is a git
repository kept inside another one at a fixed commit. To change the demo, do these steps in order:

1. Work in the submodule.
2. Keep one commit for each step, with an annotated `step-NN` tag. An annotated tag stores a message with the tag.
3. Push its `main` and its tags.
4. Commit the new submodule pointer here.
5. Check `demo/notes.md` and `demo/slides/slides.toml` against the steps.

## Licence

timewalk uses the MIT licence. The libraries in `static/vendor/` keep their own licences. ghostty-web
uses MIT, highlight.js uses BSD 3-Clause and marked uses MIT.
