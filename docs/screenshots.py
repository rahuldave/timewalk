# /// script
# requires-python = ">=3.11"
# dependencies = ["starlette>=0.40", "uvicorn>=0.30", "websockets>=13", "playwright>=1.45", "pypdf>=5"]
# ///
"""Take the site's screenshots from the demo, so they can be taken again whenever the pages change.

    uv run docs/screenshots.py            writes docs/images/*.png

It clones the demo (demo/timewalk-demo) into a temporary folder, serves it with timewalk, and drives both
pages in headless Chrome or Edge. The pictures of a tutorial, and of the walk menu, come from timewalk-test: a copy of
../timewalk-test when it is there, or else a clone from GitHub. The shells use bash with a short prompt and a temporary home, so no
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
sys.path.insert(0, str(ROOT / "src"))
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
    walks: bool = False,  # Serve the walks of demo/toc.toml, as `just demo-walks` does
) -> uvicorn.Server:  # The running server
    "Serve the demo with its notes and slides, as `just demo` does, in a thread."
    if walks:
        from timewalk.walks import load_toc

        app = timewalk.make_app(repo, TOKEN, port, None, "", None, show_clock=True, walks=load_toc(ROOT / "demo" / "toc.toml"), root=ROOT / "demo")
    else:
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
                server.should_exit = True
                shoot_tutorial(browser, tmp)
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

    # The handles between the panes: the slides made narrow, to show more of the code.
    box = page.locator("#resize-slides").bounding_box()
    page.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] - 120, box["y"] + box["height"] / 2, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)
    save("handles", ".panes")
    page.locator("#resize-slides").dblclick()
    page.wait_for_timeout(300)

    # The three layouts.
    layout("slides")
    save("layout-slides")
    layout("code")
    save("layout-code")

    # Shell: the terminals take the space of the slides and the files; the notes stay.
    page.locator("#shell-toggle").click()
    page.wait_for_timeout(600)
    save("shell")
    page.locator("#shell-toggle").click()
    page.wait_for_timeout(600)

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
    # The window for the class, as the Room button opens it: the same step, the notes without the cues, no clock.
    room = browser.new_page(viewport=WIDE, device_scale_factor=1)
    room.on("pageerror", lambda error: errors.append(str(error)))
    room.goto(f"{base}/?t={TOKEN}&room=1")
    room.wait_for_selector("#notes .p-commands button")
    room.wait_for_timeout(1500)
    room.mouse.move(2, 2)
    room.screenshot(path=str(OUT / "room-window.png"))
    room.locator("#notes-pane").screenshot(path=str(OUT / "notes-room.png"))
    room.close()
    page.locator("#slide").evaluate("e => e.scrollTop = e.scrollHeight")   # the command is at the end of the slide
    page.wait_for_timeout(200)
    save("slide-command", "#slide-pane")
    page.locator("#slide").evaluate("e => e.scrollTop = 0")
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


def shoot_tutorial(browser, tmp: Path) -> None:
    "Take the tutorial walk of timewalk-test: the band at its first step, step-02 in do mode and in watch mode, step-01 with its moves all shown, the step bar with the walk menu, the items and Apply."
    from timewalk.walks import load_toc

    local = ROOT.parent / "timewalk-test"
    kit = tmp / "timewalk-test"
    if (local / "history" / "build.py").is_file():
        # A copy of the files, edits included, as the class there stands; not what it built or ignores.
        import shutil

        shutil.copytree(local, kit, ignore=shutil.ignore_patterns(".git", "repo", "repo-replay", ".venv", "build", "__pycache__", ".pytest_cache"))
    else:
        subprocess.run(["git", "clone", "--quiet", "https://github.com/rahuldave/timewalk-test", str(kit)], check=True)
    subprocess.run([sys.executable, str(kit / "history" / "build.py"), str(kit / "repo")], check=True, capture_output=True)
    walks = load_toc(kit / "toc.toml")
    tutorial = next(w for w in walks if w.id == "tutorial")
    repo = timewalk.Repo(kit / "repo")
    repo.select(tutorial.tags, tutorial.steps, tutorial=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = timewalk.make_app(repo, TOKEN, port, None, "", None, walks=walks, root=kit, start_walk="tutorial")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        errors: list[str] = []
        page = browser.new_page(viewport=WIDE, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}/?t={TOKEN}")
        page.wait_for_selector("#tabs button")
        page.wait_for_timeout(1000)

        def post(path: str, body: dict) -> None:
            page.evaluate("(a) => fetch(a[0], {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(a[1])})", [path, body])

        def section(number: int):  # the notes of one move
            return page.locator(f'#notes .p-move[data-move="{number}"]')

        def save(name: str, clip: str | None = None) -> None:
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.mouse.move(2, 2)
            page.wait_for_timeout(300)
            (page.locator(clip) if clip else page).screenshot(path=str(OUT / f"{name}.png"))

        # The walk's first step, step-00: the band under the step bar says what to do, then the walk's description and
        # what a tutorial in do mode asks, then the message of the tag.
        post("/api/move", {"to": 0, "name": "step-00"})
        page.wait_for_function("document.getElementById('step-name').textContent === 'step-00'")
        page.wait_for_selector("#note:not([hidden]) #about:not([hidden])")
        page.wait_for_timeout(800)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.mouse.move(2, 2)
        top = page.locator("header.bar").bounding_box()
        band = page.locator("#note").bounding_box()
        page.screenshot(path=str(OUT / "tutorial-band.png"), clip={"x": 0, "y": top["y"], "width": WIDE["width"],
                                                                   "height": band["y"] + band["height"] - top["y"]})

        # Do mode, the walk's own: step-02 at Start, before its first move, with the code of step-01. The first
        # move's file opens as that move's change, though the file does not exist yet.
        post("/api/move", {"to": 2, "name": "step-02"})
        page.wait_for_function("document.getElementById('step-name').textContent === 'step-02'")
        page.wait_for_selector("#moves:not([hidden])")
        page.wait_for_selector('#notes .p-move.here[data-move="1"]')
        section(1).locator(".p-files button").first.click()
        page.wait_for_function("document.getElementById('view-next').textContent === 'Next change: step-02.1'")
        page.wait_for_timeout(800)
        save("tutorial-do")
        # The step bar: the walk menu, the label of the kind, the switch between Do and Watch, and the row of moves.
        save("walks", "header.bar")

        # Do mode, one move later: move 1 marked done, the notes at move 2, and the change of move 2 in the reader.
        page.locator("#moves button.nav").last.click()
        page.wait_for_selector('#notes .p-move.here[data-move="2"]')
        section(2).locator(".p-files button").first.click()
        page.wait_for_function("document.getElementById('view-next').textContent === 'Next change: step-02.2'")
        page.wait_for_timeout(800)
        save("tutorial")

        # Watch mode: Show on move 1 checks out its commit, in every window; its file opens as this move's change.
        page.locator('#move-modes button[data-mode="watch"]').click()
        page.wait_for_selector('#move-modes button[data-mode="watch"][aria-pressed="true"]')
        section(1).locator(".p-move-actions button", has_text="Show").click()
        page.wait_for_function("document.getElementById('step-name').textContent === 'step-02.1'")
        page.wait_for_selector('#notes .p-move.here[data-move="1"]')
        section(1).locator(".p-files button").first.click()
        page.wait_for_function("document.getElementById('view-diff').textContent === 'Last change: step-02.1'")
        page.wait_for_timeout(800)
        save("tutorial-watch")

        # Watch mode at step-01, with both of its moves shown: the text before the moves in a section of its own, each
        # shown move with Shown and Restart step, and Next step at the end. A tall window, so that the whole notes fit.
        post("/api/move", {"to": 1, "name": "step-01"})
        page.wait_for_function("document.getElementById('step-name').textContent === 'step-01'")
        page.wait_for_selector('#notes .p-move.next[data-move="1"] button:has-text("Show")')
        section(1).locator(".p-move-actions button", has_text="Show").click()
        page.wait_for_selector('#notes .p-move.next[data-move="2"] button:has-text("Show")')
        section(2).locator(".p-move-actions button", has_text="Show").click()
        page.wait_for_selector("#notes .p-next-step button")
        tall = browser.new_page(viewport={"width": WIDE["width"], "height": 1900}, device_scale_factor=1)
        tall.on("pageerror", lambda error: errors.append(str(error)))
        tall.add_init_script("localStorage.setItem('timewalk.notes-width', '520');")
        tall.goto(f"http://127.0.0.1:{port}/?t={TOKEN}")
        tall.wait_for_selector("#notes .p-next-step button")
        tall.wait_for_timeout(1000)
        tall.locator("#notes-body").evaluate("e => e.scrollTop = 0")
        tall.mouse.move(2, 2)
        tall.wait_for_timeout(300)
        pane = tall.locator("#notes-pane").bounding_box()
        end = tall.locator("#notes .p-next-step").bounding_box()
        tall.screenshot(path=str(OUT / "tutorial-next.png"), clip={"x": pane["x"], "y": pane["y"], "width": pane["width"],
                                                                  "height": end["y"] + end["height"] + 16 - pane["y"]})
        tall.close()

        # Back to do mode, at the start of step-02, in a new window with wider notes, so that an item, its words and its
        # excerpt fit, and lower terminals, so that the marked line of the reader shows. The Code layout gives the reader room.
        page.locator('#move-modes button[data-mode="do"]').click()
        page.wait_for_selector('#move-modes button[data-mode="do"][aria-pressed="true"]')
        post("/api/move", {"to": 2, "name": "step-02"})
        page.wait_for_function("document.getElementById('step-name').textContent === 'step-02'")
        page.close()
        page = browser.new_page(viewport=WIDE, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('timewalk.notes-width', '560'); localStorage.setItem('timewalk.term-height', '200');")
        page.goto(f"http://127.0.0.1:{port}/?t={TOKEN}")
        page.wait_for_selector('#notes .p-move.here[data-move="1"]')
        page.wait_for_timeout(1000)
        page.locator("#layouts button[data-layout=code]").click()

        # The items of move 1: a button with its words, and the show: line as a small excerpt of the real diff. The
        # item opens Next change, with the same line marked, and the Apply bar with what the files match.
        section(1).locator(".p-item button").first.click()
        page.wait_for_function("document.getElementById('view-next').textContent === 'Next change: step-02.1'")
        page.wait_for_selector("#apply-bar:not([hidden])")
        page.wait_for_selector("#file-body .line.mark")
        page.wait_for_function("document.getElementById('apply-match').textContent.includes('step-02.1')")
        section(1).locator(".p-items").scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        save("tutorial-items")

        # Apply the whole move: the command goes into the shell at the step, the files match step-02.1, and the move is
        # marked done by itself. The item of move 2 then opens its Next change, with the Apply bar for that move.
        page.locator("#apply-move").click()
        page.wait_for_selector('#notes .p-move.here[data-move="2"]', timeout=15000)
        section(2).locator(".p-item button").first.click()
        page.wait_for_function("document.getElementById('view-next').textContent === 'Next change: step-02.2'")
        page.wait_for_function("document.getElementById('apply-match').textContent.includes('step-02.2')")
        page.wait_for_timeout(800)
        save("tutorial-apply")
        page.close()
        if errors:
            raise SystemExit("screenshots: the tutorial reported errors: " + "; ".join(errors))
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
