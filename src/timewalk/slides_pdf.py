"""Make one PDF of the slides and, if asked, the notes of every step, to hand out.

    timewalk-pdf slides.toml                                    writes slides.pdf beside the manifest
    timewalk-pdf slides.toml -o handout.pdf --title "babykev" --notes notes.md
    timewalk-pdf slides.toml --notes notes.md --with-notes      each step's notes after its slides
    timewalk-pdf --notes notes.md --with-notes                  the notes alone, as a runbook

Steps come out in step order. Within a step, the slides come in the manifest's order, one per page, whatever they
are written in: Markdown slides and pictures are drawn as the page draws them, and pages of a PDF deck are copied
from that PDF. A document (a step under the manifest's [docs], or an entry ending in `#doc`) is printed whole,
over as many pages as it needs. With --with-notes, each step's notes follow its slides, over as many pages as
they need: the prose, the `>` cues in their own shade, and the commands in code blocks. Each drawn slide has a
footer with the deck title, the step and a page number; with --notes the footer also has each step's title.

The page in timewalk has a PDF button that makes the same PDF, with the notes.

It needs a Chromium-family browser to do the drawing and uses the Google Chrome or Microsoft Edge already
installed. If there is neither, run `uvx playwright install chromium` once.
"""

import argparse
import io
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import quote

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

COMMAND = re.compile(r"^\s*(main|runs[2-9]?)?\$\s+(.+)$")


def notes_markdown(
    raw: str,  # A step's section of the notes, as written
) -> str:  # The same as Markdown for the page: commands in code blocks, no planned time
    "Turn a section of the notes into Markdown to print: each run of command lines becomes one code block."
    out: list[str] = []
    block: list[str] = []
    for line in raw.split("\n"):
        if re.match(r"^time:\s*\d+:\d\d\s*$", line.strip()):
            continue
        if COMMAND.match(line):
            block.append(line.strip())
            continue
        if block:
            out += ["```", *block, "```"]
            block = []
        out.append(line)
    if block:
        out += ["```", *block, "```"]
    return "\n".join(out).strip()


def deck_of(
    manifest: Path | None,  # The slides manifest, if there is one
    notes: Path | None,  # The notes file, if there is one: its step titles, and with `with_notes` its text
    title: str,  # The deck's title, for the footer
    with_notes: bool = False,  # Print each step's notes after its slides
) -> dict:  # The deck: its title and, per step, the step's title, slides and notes
    "Describe the whole deck, step by step."
    parsed = parse_notes(notes.read_text(encoding="utf-8")) if notes and notes.is_file() else {}
    slides = load_slides(manifest) if manifest else {}
    names = set(slides) | (set(parsed) if with_notes else set())
    # In step-name order, as timewalk orders the steps, so a step under [docs] falls among the [slides] steps.
    steps = [{"name": name, "title": parsed.get(name, {}).get("title", ""), "slides": slides.get(name, []),
              "notes": notes_markdown(parsed[name]["raw"]) if with_notes and name in parsed else ""} for name in sorted(names)]
    return {"title": title, "steps": steps}


def serve(
    deck: dict,  # What `deck_of` returned
    folder: Path | None,  # The manifest's folder, where the slide files are, if there is a manifest
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
        *([Mount("/slides", StaticFiles(directory=folder))] if folder else []),
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
    docs: list[str],  # The document entries of the deck, each printed on its own
    notes: list[str] = (),  # The steps whose notes are printed, each on its own
    draws_slides: bool = True,  # Whether any slide needs drawing; with none, the empty print is skipped
) -> tuple[bytes, dict[str, bytes], dict[str, bytes]]:  # The slides' PDF, a PDF per document, a PDF per step's notes
    "Open the print page in a headless browser and print it, then each document and each step's notes over as many pages as they need."
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

        def printed(address: str) -> bytes:  # The PDF of one print page
            page.goto(address)
            # "attached", not visible: a print page with nothing on it, notes alone, has an empty body.
            page.wait_for_selector("body[data-ready]", state="attached", timeout=120_000)
            if problems:
                raise SystemExit("slides_pdf: the print page failed: " + "; ".join(problems))
            return page.pdf(width="13.333in", height="7.5in", print_background=True, prefer_css_page_size=True)

        slides = printed(f"http://127.0.0.1:{port}/print") if draws_slides else b""
        documents = {doc: printed(f"http://127.0.0.1:{port}/print?doc={quote(doc)}") for doc in dict.fromkeys(docs)}
        written = {step: printed(f"http://127.0.0.1:{port}/print?notes={quote(step)}") for step in notes}
        browser.close()
        return slides, documents, written


def assemble(
    deck: dict,  # What `deck_of` returned
    drawn: bytes,  # The slides' PDF from `draw`
    documents: dict[str, bytes],  # The documents' PDFs from `draw`
    written: dict[str, bytes],  # Each step's notes as a PDF, from `draw`
    folder: Path | None,  # The manifest's folder
) -> PdfWriter:  # The handout: each step's slides in the manifest's order, then its notes
    "Interleave the drawn pages with the documents' pages, pages copied from PDF decks, and each step's notes."
    pages = iter(PdfReader(io.BytesIO(drawn)).pages if drawn else [])
    out = PdfWriter()
    sources: dict[str, PdfReader] = {}
    for step in deck["steps"]:
        for entry in step["slides"]:
            path, _, fragment = entry.partition("#")
            if entry in documents:
                for page in PdfReader(io.BytesIO(documents[entry])).pages:
                    out.add_page(page)
                continue
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
        for page in PdfReader(io.BytesIO(written[step["name"]])).pages if step["name"] in written else []:
            out.add_page(page)
    if next(pages, None) is not None:
        raise SystemExit("slides_pdf: the browser drew more pages than there are slides; a slide may have spilled onto a second page")
    return out


def make_pdf(
    manifest: Path | None,  # The slides manifest, if there is one
    notes: Path | None,  # The notes file, if there is one
    title: str,  # A title for the footer of every page
    with_notes: bool = False,  # Print each step's notes after its slides
) -> bytes:  # The PDF
    "Make the whole PDF: draw it in a browser, copy in PDF pages, and join the parts in step order."
    deck = deck_of(manifest, notes, title, with_notes)
    if not any(step["slides"] or step["notes"] for step in deck["steps"]):
        raise SystemExit("slides_pdf: there is nothing to print: no slides, and no notes")
    folder = manifest.parent if manifest else None
    server, port = serve(deck, folder)
    try:
        docs = [entry for step in deck["steps"] for entry in step["slides"] if entry.endswith("#doc")]
        drawn_entries = [entry for step in deck["steps"] for entry in step["slides"]
                         if not entry.split("#")[0].lower().endswith(".pdf") and not entry.endswith("#doc")]
        drawn, documents, written = draw(port, docs, [step["name"] for step in deck["steps"] if step["notes"]], bool(drawn_entries))
        handout = assemble(deck, drawn, documents, written, folder)
    finally:
        server.should_exit = True
    handout.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    handout.write(buffer)
    return buffer.getvalue()


def main() -> None:
    "Parse the command line and write the PDF."
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", type=Path, nargs="?", help="the slides manifest, a TOML file (optional with --with-notes)")
    parser.add_argument("-o", "--output", type=Path, help="the PDF to write (default: slides.pdf beside the manifest, or notes.pdf beside the notes)")
    parser.add_argument("--notes", type=Path, help="a notes file: its step titles go in the footer, and with --with-notes its text is printed")
    parser.add_argument("--with-notes", action="store_true", help="print each step's notes after its slides, cues and commands included")
    parser.add_argument("--title", default="", help="a title for the footer of every page")
    args = parser.parse_args()

    manifest = args.manifest.resolve() if args.manifest else None
    notes = args.notes.resolve() if args.notes else None
    if manifest is not None and not manifest.is_file():
        raise SystemExit(f"slides_pdf: {manifest} does not exist")
    if manifest is None and not (notes and args.with_notes):
        raise SystemExit("slides_pdf: give a slides manifest, or --notes with --with-notes, or both")
    data = make_pdf(manifest, notes, args.title or (manifest or notes).parent.name, args.with_notes)
    output = (args.output or (manifest.with_name("slides.pdf") if manifest else notes.with_name("notes.pdf"))).resolve()
    output.write_bytes(data)
    print(f"slides_pdf: wrote {len(PdfReader(io.BytesIO(data)).pages)} pages to {output}")


if __name__ == "__main__":
    main()
