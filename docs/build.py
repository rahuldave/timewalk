# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "pygments>=2.17"]
# ///
"""Build the timewalk site from the Markdown pages in this folder.

    uv run docs/build.py            writes docs/_site
    uv run docs/build.py --serve    and serves it at http://127.0.0.1:8000 to look at

Each page is Markdown. Links between pages are written as `page.md`, so they also work when the files are
read on GitHub; the build turns them into `page.html`. Pictures are in `images/`, made by screenshots.py.
"""

import argparse
import functools
import html
import http.server
import re
import shutil
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

HERE = Path(__file__).resolve().parent
OUT = HERE / "_site"

# The pages, in the order of the sidebar: file, title in the sidebar.
PAGES = [
    ("index.md", "Overview"),
    ("demo.md", "A tour of the demo"),
    ("class.md", "Teach a class"),
    ("walk.md", "Make a walk"),
    ("model.md", "How it works"),
    ("page.md", "The page"),
    ("notes.md", "Notes"),
    ("edits.md", "Live edits"),
    ("terminals.md", "The terminals"),
    ("steps.md", "Making the steps"),
    ("slides.md", "Slides and documents"),
    ("walks.md", "Several walks"),
    ("build-walks.md", "Build a class with several walks"),
    ("authoring.md", "What a narrative and a tutorial need"),
    ("brand.md", "Brand a PDF"),
    ("replay.md", "The replay copy"),
    ("python.md", "Python projects"),
    ("safety.md", "Safety"),
    ("reference.md", "Reference"),
    ("development.md", "Development"),
]


def code_block(
    code: str,  # The text inside a fence
    language: str,  # The fence's language, or empty
    attrs: str,  # Unused fence attributes
) -> str:  # Highlighted HTML, or empty to let markdown-it escape it plainly
    "Highlight a fenced block with Pygments when it names a language Pygments knows."
    try:
        lexer = get_lexer_by_name(language) if language else None
    except ClassNotFound:
        lexer = None
    if lexer is None:
        return ""
    return highlight(code, lexer, HtmlFormatter(nowrap=True))


def slug(
    text: str,  # A heading's text
) -> str:  # An id for it
    "Make an id from a heading, as GitHub does: lower case, words joined by hyphens."
    text = re.sub(r"<[^>]+>", "", html.unescape(text)).lower()
    return re.sub(r"[^\w\- ]", "", text).strip().replace(" ", "-")


def render(
    text: str,  # A page's Markdown
) -> tuple[str, str]:  # Its title (the first heading) and its HTML
    "Turn one page into HTML: tables, highlighted code, ids on headings, links to .html."
    md = MarkdownIt("commonmark", {"html": True, "highlight": code_block}).enable("table")
    body = md.render(text)
    body = re.sub(r'href="([\w\-]+)\.md(#[^"]*)?"', lambda m: f'href="{m.group(1)}.html{m.group(2) or ""}"', body)
    body = re.sub(r"<(h[23])>(.*?)</\1>", lambda m: f'<{m.group(1)} id="{slug(m.group(2))}"><a class="anchor" href="#{slug(m.group(2))}">{m.group(2)}</a></{m.group(1)}>', body)
    body = re.sub(r'<p><img src="([^"]+)" alt="([^"]*)"\s*/?></p>', r'<figure><a href="\1"><img src="\1" alt="\2" loading="lazy"></a><figcaption>\2</figcaption></figure>', body)
    found = re.search(r"<h1>(.*?)</h1>", body)
    return (re.sub(r"<[^>]+>", "", found.group(1)) if found else "timewalk"), body


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="site.css">
</head>
<body>
<header class="top"><a class="brand" href="index.html">timewalk</a><span class="tag">replay a repository's history, one step at a time</span>
<a class="gh" href="https://github.com/rahuldave/timewalk">GitHub</a></header>
<div class="wrap">
<nav class="side" aria-label="Pages"><ul>{nav}</ul></nav>
<main>{body}<footer class="pager">{pager}</footer></main>
</div>
</body>
</html>
"""


def build() -> int:  # How many pages were written
    "Write every page, the stylesheet and the pictures to _site."
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(HERE / "images", OUT / "images")
    light = HtmlFormatter(style="friendly").get_style_defs(".hl")
    css = (HERE / "site.css").read_text(encoding="utf-8")
    dark_media = "@media (prefers-color-scheme: dark) {\n" + HtmlFormatter(style="github-dark").get_style_defs(".hl") + "\n}"
    (OUT / "site.css").write_text(css + "\n/* code, from Pygments */\n" + light + "\n" + dark_media + "\n", encoding="utf-8")
    for index, (name, _label) in enumerate(PAGES):
        title, body = render((HERE / name).read_text(encoding="utf-8"))
        body = body.replace("<pre><code", '<pre class="hl"><code')
        out = name.replace(".md", ".html")
        nav = "".join(f'<li><a href="{n.replace(".md", ".html")}"{" aria-current=page" if n == name else ""}>{html.escape(t)}</a></li>' for n, t in PAGES)
        before = PAGES[index - 1] if index > 0 else None
        after = PAGES[index + 1] if index + 1 < len(PAGES) else None
        pager = (f'<a class="prev" href="{before[0].replace(".md", ".html")}">&larr; {html.escape(before[1])}</a>' if before else "<span></span>") + \
                (f'<a class="next" href="{after[0].replace(".md", ".html")}">{html.escape(after[1])} &rarr;</a>' if after else "<span></span>")
        page_title = "timewalk" if name == "index.md" else f"{title} | timewalk"
        (OUT / out).write_text(TEMPLATE.format(title=html.escape(page_title), nav=nav, body=body, pager=pager), encoding="utf-8")
    return len(PAGES)


def main() -> None:
    "Build the site, and serve it if asked."
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--serve", action="store_true", help="serve the built site at http://127.0.0.1:8000")
    args = parser.parse_args()
    print(f"build: wrote {build()} pages to {OUT}")
    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
        print("build: serving at http://127.0.0.1:8000 (Ctrl+C to stop)")
        http.server.ThreadingHTTPServer(("127.0.0.1", 8000), handler).serve_forever()


if __name__ == "__main__":
    main()
