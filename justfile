# timewalk: browse a repository one commit at a time. Run `just` to see this list.


[private]
default:
    @just --list --unsorted

# Browse a repository. Extra arguments go to timewalk: just walk ~/code/project --notes notes.md --slides slides/slides.toml
[positional-arguments]
walk repo *args:
    uv run timewalk "$@"

# Browse the sample repository, the submodule demo/timewalk-demo, with its notes and slides. Fetches it the first time.
# A move never asks: edits made in the replay copy are kept on a branch timewalk/saved/<place>
demo:
    git submodule update --init demo/timewalk-demo
    uv run timewalk demo/timewalk-demo --notes demo/notes.md --slides demo/slides/slides.toml --clock

# Browse the sample repository with its two walks, from demo/toc.toml. The dropdown in the step bar changes the walk
demo-walks:
    git submodule update --init demo/timewalk-demo
    uv run timewalk demo/timewalk-demo --toc demo/toc.toml --clock

# Make a PDF of the slides, the notes, or both: just pdf slides/slides.toml --notes notes.md --with-notes
[positional-arguments]
pdf *args:
    uv run timewalk-pdf "$@"

# Run the tests. Extra arguments go to pytest: just test -k slides
[positional-arguments]
test *args:
    uv run pytest -q tests "$@"

# Lint the Python
lint:
    uvx ruff check .

# Take the site's screenshots again, from a throwaway clone of the demo
screenshots:
    uv run docs/screenshots.py

# Build the site into docs/_site and serve it at http://127.0.0.1:8000
site:
    uv run docs/build.py --serve

# Check the site's pages against the writing guide (docs/style/GUIDE.md). -v also shows the soft notes
style *args:
    python3 docs/style/check.py docs/*.md {{ args }}
