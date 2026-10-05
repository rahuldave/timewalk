# /// script
# requires-python = ">=3.11"
# dependencies = ["starlette>=0.40", "uvicorn>=0.30", "websockets>=13", "playwright>=1.45", "pypdf>=5"]
# ///
"""Take the site's screenshots from the demo, so they can be taken again whenever the pages change.

    uv run docs/screenshots.py            writes docs/images/*.png

It clones the demo (demo/timewalk-demo) into a temporary folder, serves it with timewalk, and drives both
pages in headless Chrome or Edge. The shells use bash with a short prompt and a temporary home, so no
personal prompt or path shows. Nothing in the real demo or its replay copy is touched.
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
sys.path.insert(0, str(ROOT))
import timewalk  # noqa: E402

OUT = DOCS / "images"
TOKEN = "screenshots"
WIDE = {"width": 1440, "height": 900}


def neutral_shell(
    home: Path,  # A temporary home folder for the shells
) -> None:
    "Make the shells timewalk starts plain: bash, a prompt showing only the folder, and the real uv cache."
    cache = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True).stdout.strip()
    (home / ".bash_profile").write_text("export PS1='\\W $ '\nexport BASH_SILENCE_DEPRECATION_WARNING=1 GIT_PAGER=cat PAGER=cat\n")
    os.environ.update({"SHELL": "/bin/bash", "HOME": str(home), "UV_CACHE_DIR": cache, "GIT_CONFIG_NOSYSTEM": "1"})


def serve(
    repo: timewalk.Repo,  # The demo clone
    port: int,  # Where to listen
) -> uvicorn.Server:  # The running server
    "Serve the demo with its notes and slides, as `just demo` does, in a thread."
    app = timewalk.make_app(repo, TOKEN, port, ROOT / "demo" / "notes.md", "", ROOT / "demo" / "slides" / "slides.toml", show_clock=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


def main() -> None:
    "Take every screenshot the site uses."
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "home").mkdir()
        neutral_shell(tmp / "home")
        subprocess.run(["git", "clone", "--quiet", str(ROOT / "demo" / "timewalk-demo"), str(tmp / "timewalk-demo")], check=True)
        subprocess.run(["git", "-C", str(tmp / "timewalk-demo"), "remote", "remove", "origin"], check=True)
        repo = timewalk.Repo(tmp / "timewalk-demo")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = serve(repo, port)
        base = f"http://127.0.0.1:{port}"
        try:
            with sync_playwright() as p:
                browser = None
                for channel in ("chrome", "msedge", None):
                    try:
                        browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                        break
                    except Exception:
                        continue
                if browser is None:
                    raise SystemExit("screenshots: no Chrome, Edge or Chromium. Run `uvx playwright install chromium` once.")
                shoot(browser, base, repo)
                browser.close()
        finally:
            server.should_exit = True
    print(f"screenshots: wrote {len(list(OUT.glob('*.png')))} pictures to {OUT}")


def shoot(browser, base: str, repo: timewalk.Repo) -> None:
    "Drive the page through the demo, saving a picture at each point the docs show."
    errors: list[str] = []
    page = browser.new_page(viewport=WIDE, device_scale_factor=1)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base}/?t={TOKEN}")
    page.wait_for_selector("#step-list button")
    page.wait_for_timeout(1500)

    def post(path: str, body: dict) -> None:
        page.evaluate("(a) => fetch(a[0], {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(a[1])})", [path, body])

    def at(index: int) -> None:
        post("/api/move", {"to": index})
        page.wait_for_function(f"document.getElementById('step-name').textContent === {repo.steps[index].name!r}")
        page.wait_for_timeout(700)

    def layout(name: str) -> None:
        page.locator(f"#layouts button[data-layout={name}]").click()
        page.wait_for_timeout(300)

    def open_file(name: str, view: str = "file") -> None:
        page.locator("#tree .file button").filter(has_text=__import__("re").compile("^" + name)).first.click()
        page.wait_for_timeout(300)
        if view != "file":
            page.locator(f"#view-{view}").click()
            page.wait_for_timeout(300)

    def typed(text: str, wait: int = 1200) -> None:
        page.keyboard.type(text)
        page.keyboard.press("Enter")
        page.wait_for_timeout(wait)

    def tab(label: str) -> None:
        page.locator("#tabs button", has_text=label).first.click()
        page.wait_for_timeout(600)

    def save(name: str, clip: str | None = None, focused: bool = False) -> None:
        if not focused:
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.wait_for_timeout(150)
        page.mouse.move(2, 2)
        target = page.locator(clip) if clip else page
        target.screenshot(path=str(OUT / f"{name}.png"))

    # The whole page, as `just demo` shows it: the clock band, slides beside the code, a file open, the terminal
    # at this step, and the notes on the right. The clock runs, so the band shows real times.
    page.locator("#clock-start").click()
    at(1)
    layout("split")
    open_file("greet.py")
    tab("At this step")
    typed("clear; git log --oneline --decorate")
    save("page")

    # The three layouts.
    layout("slides")
    save("layout-slides")
    layout("code")
    save("layout-code")

    # What a step changed: step-04 types the functions.
    at(4)
    open_file("greet.py", "diff")
    save("reader-changes", ".file-pane")
    page.locator("#only-changed").check()
    page.wait_for_timeout(300)
    save("tree-changed", ".tree-pane")
    page.locator("#only-changed").uncheck()

    # Live edits: at step-02, ruff formats the untidy file, and the reader shows what it did.
    at(2)
    open_file("greet.py")
    page.locator("#terms .term:not([hidden])").click()
    typed("clear; uvx ruff format", 4000)
    page.wait_for_selector("#tree .badge.E")
    save("edits-tree", ".tree-pane")
    open_file("greet.py", "edits")
    save("edits")
    # Moving with edits asks first.
    page.locator("#file-body").click()
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("#notice:not([hidden])")
    page.wait_for_timeout(300)
    save("move-with-edits", "#notice")
    page.locator("#notice button", has_text="Stay here").click()
    page.locator("#terms .term:not([hidden])").click()
    typed("git restore .", 1500)

    # From here on, as `just demo` runs: --discard-edits. The step bar warns, and a move drops the edits without asking.
    repo.discard = True
    page.reload()
    page.wait_for_selector("#step-list button")
    page.wait_for_timeout(1500)
    page.locator("#terms .term:not([hidden])").click()
    typed("clear; uvx ruff format", 4000)
    page.wait_for_selector("#mode.warn")
    save("discard-warning", ".bar")
    page.locator("#file-body").click()
    page.keyboard.press("ArrowRight")
    page.wait_for_function(f"document.getElementById('step-name').textContent === {repo.steps[3].name!r}")
    page.wait_for_timeout(1200)
    save("discard-moved", ".bar")

    # A document instead of slides.
    at(3)
    layout("slides")
    save("document")
    layout("split")

    # The terminals: the same command at a step and in Main.
    at(1)
    page.locator("#terms .term:not([hidden])").click()
    typed("clear; git log --oneline -1; cat src/greet.py")
    save("terminal-step", ".term-pane")
    tab("Main")
    typed("clear; git log --oneline -1; cat src/greet.py")
    save("terminal-main", ".term-pane")
    tab("Runs")
    typed("clear; for epoch in 1 2 3; do echo \"epoch $epoch\"; sleep 1; done; echo done", 400)
    tab("At this step")
    page.wait_for_timeout(3500)
    save("terminal-runs-dot", ".term-pane .pane-head")
    page.locator("#file-body").click()
    page.wait_for_timeout(300)
    save("terminal-unfocused", ".term-pane")
    page.locator("#terms .term:not([hidden])").click()
    page.wait_for_timeout(300)
    save("terminal-focused", ".term-pane", focused=True)

    # The notes column at step-02: cues in their own shade, the prose, and the commands.
    at(2)
    save("notes", "#notes-pane")
    save("band", "#band")
    # Editing the notes of the step.
    page.locator("#notes-edit").click()
    page.wait_for_selector("#notes-text")
    page.locator("#notes-text").evaluate("e => e.setSelectionRange(0, 0)")
    save("notes-edit", "#notes-pane", focused=True)
    page.locator("#notes-cancel").click()
    page.wait_for_timeout(300)

    # Dark, for the overview.
    at(1)
    page.locator("#theme").click()
    page.wait_for_timeout(1200)
    open_file("greet.py")
    save("page-dark")
    page.locator("#theme").click()
    page.wait_for_timeout(800)
    at(0)
    if errors:
        raise SystemExit("screenshots: the pages reported errors: " + "; ".join(errors))


if __name__ == "__main__":
    main()
