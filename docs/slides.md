# Slides and documents

Slides are Markdown files, pictures, pages of a PDF, or slides of an HTML deck. A manifest, a TOML file,
says which go with which step. A step can show one longer Markdown document instead.

## The manifest

```toml
deck = "talk.md"                               # optional: the deck that bare numbers refer to

[slides]
step-00 = ["opening.md"]                       # every slide in that file
step-01 = ["tools.md#1-3", "pictures/a.svg"]   # slides 1 to 3 of a file, then a picture
step-02 = ["handout.pdf#page=2"]               # one page of a PDF
step-04 = [7, "8-10"]                          # slides of the default deck

[docs]
step-03 = "walkthrough.md"                     # one whole Markdown document instead of slides
```

Start timewalk with `--slides slides/slides.toml`. Paths are relative to the manifest.

| Entry | Means |
|---|---|
| `"talk.md"` | every slide in the file, in order |
| `"talk.md#2"` | its second slide |
| `"talk.md#2-4"` | its second to fourth |
| `"picture.svg"`, `.png`, `.jpg`, `.gif`, `.webp` | a picture as a slide |
| `"deck.pdf#page=3"` | a page of a PDF |
| `"deck.html#/3"` | a slide of an HTML deck, with whatever fragment that deck uses |
| `7`, `"8-10"` | with `deck = "talk.md"`, those slides of that deck (pages, if the deck is a PDF) |
| `"guide.md#doc"` | the whole file as one document, as under `[docs]` |

A step with no entry shows the code alone.

## Writing Markdown slides

**A line that is exactly `---` starts the next slide.** Everything else is ordinary Markdown: headings,
lists, tables, links, bold, `code`, fenced code (highlighted when the fence names a language), and
pictures as `![description](path)`, with the path relative to the slide file. For a horizontal rule
inside a slide, write `***`.

```markdown
## The title of the first slide

- A point
- Another, with `code` and **bold**

---

## The second slide

![What the picture shows](pictures/shape.svg)
```

Keep a slide to about six bullets or one table. On the projector a long slide scrolls; in the PDF it is
shrunk to fit its page.

The manifest and the slide files are read afresh whenever a slide is shown, so you can edit them while
presenting.

## A document instead of slides

A step listed under `[docs]` shows one Markdown file in the slide pane, whole:

- It is not split at `---`; there, `---` is an ordinary rule.
- It keeps the slides' text size, and the pane keeps its size: the document scrolls.
- The slide arrows are hidden on both pages, and Up and Down scroll.
- It replaces any `[slides]` entry for that step.

Use it for a walkthrough, a reading, a comparison, anything longer than a slide. The demo's step-03 is
one: `demo/slides/formatting.md`.

![A document in the slide pane: it scrolls, and has no slide arrows](images/document.png)

## A PDF of the slides

```
uv run slides_pdf.py slides/slides.toml -o handout.pdf --title "My talk" --notes notes.md
```

writes every slide in step order, one per page, with a footer naming the deck, the step and the page.

- Markdown slides and pictures are drawn as the browser draws them.
- A slide with too much on it is shrunk to fit its page.
- Pages of a PDF deck are copied from that PDF.
- A document runs over as many pages as it needs, headed by its step.
- `--notes` is read only for each step's title, for the footer. Nothing else from the notes is used.

It needs Chrome or Edge, which it finds if installed. Otherwise run `uvx playwright install chromium`
once. From the timewalk folder, `just pdf slides/slides.toml -o handout.pdf` does the same.
