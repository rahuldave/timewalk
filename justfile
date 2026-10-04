# timewalk: browse a repository one commit at a time. Run `just` to see this list.

deps := '--with pytest --with httpx --with "starlette>=0.40" --with "uvicorn>=0.30" --with "websockets>=13"'

[private]
default:
    @just --list --unsorted

# Browse a repository. Extra arguments go to timewalk: just walk ~/code/project --notes notes.md --slides slides/slides.toml
[positional-arguments]
walk repo *args:
    uv run timewalk.py "$@"

# Browse the sample repository, the submodule demo/timewalk-demo, with its notes and slides. Fetches it the first time
demo:
    git submodule update --init demo/timewalk-demo
    uv run timewalk.py demo/timewalk-demo --notes demo/notes.md --slides demo/slides/slides.toml

# Make one PDF of every slide in a manifest: just pdf slides/slides.toml -o handout.pdf --title "My talk"
[positional-arguments]
pdf manifest *args:
    uv run slides_pdf.py "$@"

# Run the tests. Extra arguments go to pytest: just test -k slides
[positional-arguments]
test *args:
    uv run --no-project {{ deps }} pytest -q tests "$@"

# Lint the Python
lint:
    uvx ruff check .

# Take the site's screenshots again, from a throwaway clone of the demo
screenshots:
    uv run docs/screenshots.py

# Build the site into docs/_site and serve it at http://127.0.0.1:8000
site:
    uv run docs/build.py --serve
