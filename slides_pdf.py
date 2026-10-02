# /// script
# requires-python = ">=3.11"
# dependencies = ["starlette>=0.40", "uvicorn>=0.30", "websockets>=13", "playwright>=1.45", "pypdf>=5"]
# ///
"""Make one PDF of every slide in a timewalk slides manifest, to hand out.

    uv run slides_pdf.py slides.toml                         writes slides.pdf beside the manifest
    uv run slides_pdf.py slides.toml -o handout.pdf --title "babykev" --notes notes.md

Slides come out in the manifest's order, one per page, whatever they are written in: Markdown slides and
pictures are drawn exactly as the step browser draws them, and pages of a PDF deck are copied from that PDF.
Each drawn page has a footer with the deck title, the step it belongs to and a page number. With --notes, the
footer also carries each step's title from the notes file's `## step-name Title` headings; nothing else is read
from the notes, which stay private.

It needs a Chromium-family browser to do the drawing and uses the Google Chrome or Microsoft Edge already
installed. If there is neither, run `uvx playwright install chromium` once.
"""

import argparse
import io
import socket
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from timewalk import HERE, load_slides, parse_notes


def deck_of(
    manifest: Path,  # The slides manifest
    notes: Path | None,  # The presenter's notes, read only for step titles
    title: str,  # The deck's title, for the footer
) -> dict:  # The deck: its title and, per step, the step's title and slides
    "Describe the whole deck in the manifest's order."
    titles = {name: entry.get("title", "") for name, entry in parse_notes(notes.read_text(encoding="utf-8")).items()} if notes else {}
    steps = [{"name": name, "title": titles.get(name, ""), "slides": slides} for name, slides in load_slides(manifest).items()]
    return {"title": title, "steps": steps}


def serve(
    deck: dict,  # What `deck_of` returned
    folder: Path,  # The manifest's folder, where the slide files are
) -> tuple[uvicorn.Server, int]:  # The running server and its port
    "Serve the print page on a free local port, for the few seconds the export takes."
    async def print_page(request: Request) -> FileResponse:
        return FileResponse(HERE / "static" / "print.html")

    async def deck_json(request: Request) -> JSONResponse:
        return JSONResponse(deck)

    app = Starlette(routes=[
        Route("/print", print_page),
        Route("/api/deck", deck_json),
        Mount("/static", StaticFiles(directory=HERE / "static")),
        Mount("/slides", StaticFiles(directory=folder)),
    ])
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, port


def draw(
    port: int,  # Where the print page is served
) -> bytes:  # A PDF with one page for every slide that is not itself a PDF page
    "Open the print page in a headless browser and print it."
    with sync_playwright() as p:
        browser = None
        for channel in ("chrome", "msedge", None):
            try:
                browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                break
            except BrowserError:
                continue
        if browser is None:
            raise SystemExit("slides_pdf: no Chrome, Edge or Chromium found. Run `uvx playwright install chromium` once.")
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        problems: list[str] = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}/print")
        page.wait_for_selector("body[data-ready]", timeout=120_000)
        if problems:
            raise SystemExit("slides_pdf: the print page failed: " + "; ".join(problems))
        pdf = page.pdf(width="13.333in", height="7.5in", print_background=True, prefer_css_page_size=True)
        browser.close()
        return pdf


def assemble(
    deck: dict,  # What `deck_of` returned
    drawn: bytes,  # The PDF from `draw`
    folder: Path,  # The manifest's folder
) -> PdfWriter:  # The handout, with every slide in the manifest's order
    "Interleave the drawn pages with pages copied from PDF decks."
    pages = iter(PdfReader(io.BytesIO(drawn)).pages)
    out = PdfWriter()
    sources: dict[str, PdfReader] = {}
    for step in deck["steps"]:
        for entry in step["slides"]:
            path, _, fragment = entry.partition("#")
            if not path.lower().endswith(".pdf"):
                page = next(pages, None)
                if page is None:
                    raise SystemExit(f"slides_pdf: the browser drew fewer pages than there are slides; stopped at {entry}")
                out.add_page(page)
                continue
            source = sources.setdefault(path, PdfReader(folder / path))
            wanted = dict(part.split("=", 1) for part in fragment.split("&") if "=" in part).get("page")
            for index in ([int(wanted) - 1] if wanted else range(len(source.pages))):
                if not 0 <= index < len(source.pages):
                    raise SystemExit(f"slides_pdf: {path} has {len(source.pages)} pages; the manifest asks for page {wanted}")
                out.add_page(source.pages[index])
    if next(pages, None) is not None:
        raise SystemExit("slides_pdf: the browser drew more pages than there are slides; a slide may have spilled onto a second page")
    return out


def main() -> None:
    "Parse the command line and write the PDF."
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", type=Path, help="the slides manifest, a TOML file")
    parser.add_argument("-o", "--output", type=Path, help="the PDF to write (default: slides.pdf beside the manifest)")
    parser.add_argument("--notes", type=Path, help="a notes file; only its step titles are used, in the footer")
    parser.add_argument("--title", default="", help="a title for the footer of every page")
    args = parser.parse_args()

    manifest = args.manifest.resolve()
    if not manifest.is_file():
        raise SystemExit(f"slides_pdf: {manifest} does not exist")
    deck = deck_of(manifest, args.notes, args.title)
    count = sum(len(step["slides"]) for step in deck["steps"])
    if not count:
        raise SystemExit(f"slides_pdf: {manifest} lists no slides under [slides]")
    server, port = serve(deck, manifest.parent)
    try:
        handout = assemble(deck, draw(port), manifest.parent)
    finally:
        server.should_exit = True
    output = (args.output or manifest.with_name("slides.pdf")).resolve()
    handout.add_metadata({"/Title": args.title or manifest.parent.name})
    with output.open("wb") as file:
        handout.write(file)
    print(f"slides_pdf: wrote {len(handout.pages)} pages for {len(deck['steps'])} steps to {output}")


if __name__ == "__main__":
    main()
