# timewalk: browse a repository one commit at a time. Run `just` to see this list.

deps := '--with pytest --with httpx --with "starlette>=0.40" --with "uvicorn>=0.30" --with "websockets>=13"'

[private]
default:
    @just --list --unsorted

# Browse a repository. Extra arguments go to timewalk: just walk ~/code/project --notes notes.md --slides slides/slides.toml
walk repo *args:
    uv run timewalk.py {{ repo }} {{ args }}

# Build the small sample repository and browse it, with its notes and slides
demo:
    python3 demo/make_demo.py
    uv run timewalk.py demo/sample --notes demo/notes.md --slides demo/slides/slides.toml

# Make one PDF of every slide in a manifest: just pdf slides/slides.toml -o handout.pdf --title "My talk"
pdf manifest *args:
    uv run slides_pdf.py {{ manifest }} {{ args }}

# Run the tests. Extra arguments go to pytest: just test -k slides
test *args:
    uv run --no-project {{ deps }} pytest -q tests {{ args }}

# Lint the Python
lint:
    uvx ruff check .
