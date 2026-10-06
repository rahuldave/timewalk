# Brand a PDF

A brand gives the PDF of the slides the look of a course, a school or a talk. A brand can set the font,
the colours, the footer, a cover page and a divider. A divider is a page of one colour before each step,
so that a reader can tell the steps apart. A brand changes only the PDF that `timewalk-pdf` makes. The page
and its **PDF** button do not use a brand.

Without a brand, the PDF looks as it always has.

## A brand is a folder

A brand is a folder with a file `brand.toml` and the files that it names, for example a logo and a font
file. Every path in `brand.toml` is relative to the folder. To make a new brand, copy a folder and change
it.

Keep the brands in the class folder, beside the notes and the slides. One class folder can hold several
brands:

```
my-class/
  notes.md
  slides/slides.toml
  brand/school/brand.toml
  brand/school/shield.png
  brand/school/Karla.ttf
  brand/mytalk/brand.toml
```

Give the folder to the command with `--brand`:

```
timewalk-pdf slides/slides.toml --notes notes.md --title "Testing" --brand brand/school
timewalk-pdf slides/slides.toml --notes notes.md --title "Testing" --brand brand/mytalk -o build/talk.pdf
```

`--brand` also takes the path of the `brand.toml` itself. The two commands above make two PDFs from the
same slides, each with its own look.

## Two examples

The first brand copies the look of the slides of a school course. It has a cover page, the school's
shield on the cover, and a maroon divider with the title of each step:

```toml
[brand]
font = "Karla.ttf"
text_color = "#404040"
title_color = "#1F497D"
accent = "#951026"
footer = "Protopapas"
logo = "shield.png"
logo_on = "cover"

[cover]
box = "AC215/E115"
lines = ["Pavlos Protopapas", "SEAS/Harvard"]

[divider]
color = "#951026"
title = true
```

The second brand is for a talk. It uses a font that is on the machine, puts no cover first, and has a plain
green divider with no title on it:

```toml
[brand]
font = "Georgia"
title_color = "#2a6f4e"
footer = "Rahul Dave"

[divider]
color = "#2a6f4e"
title = false
```

A brand can set one key only. For example, a `brand.toml` with only `[divider]` and `color` adds the
dividers and changes nothing else.

## The keys

Every key is optional. A key that a brand leaves out keeps that part of the PDF as it is without a brand.

| Key | What it sets | Without it |
|---|---|---|
| `[brand]` `font` | The font of the slides and the footer: the name of a font on this machine, or a font file in the folder (`.ttf`, `.otf`, `.woff`, `.woff2`) | The font of the page |
| `[brand]` `text_color` | The colour of the text of the slides | Dark grey |
| `[brand]` `title_color` | The colour of the titles of the slides. The first title of a slide also gets a thin line under it, across the page | The colour of the page, no line |
| `[brand]` `accent` | The colour of the box on the cover | Dark grey |
| `[brand]` `footer` | Small text at the bottom left of each slide, in small capitals, in place of the title of the deck | The title of the deck |
| `[brand]` `logo` | A picture in the folder, for example a PNG with a transparent background | No logo |
| `[brand]` `logo_on` | `"cover"`: the logo at the bottom left of the cover. `"every"`: also small at the bottom left of each slide | `"cover"` |
| `[cover]` | A cover page before the first slide. The table can be empty | No cover |
| `[cover]` `title` | The title on the cover | The title of the deck, from `--title` |
| `[cover]` `box` | One line in a box of the `accent` colour, for example the code of a course | No box |
| `[cover]` `lines` | A list of lines under the box. The first line is larger, for example the name of the speaker | No lines |
| `[divider]` `color` | A page of this colour before each step that has slides | No dividers |
| `[divider]` `title` | `true`: the name and the title of the step, in white, on the divider. `false`: an empty page | `true` |

Write a colour as `"#RRGGBB"`, for example `"#951026"`. The title of a step comes from the notes file, so
give `--notes` for the titles on the dividers and in the footer.

## Fonts

Give the font as a file in the brand folder if you can. The command loads the file to draw the PDF, so the
PDF looks the same on every machine. Many fonts are free to copy with their licence, for example the fonts
of Google Fonts. Put the licence file beside the font.

If the font is a name, the font must be on the machine that makes the PDF. If it is not, the command stops
and says which font is missing. It does not change to another font without a message.

## Mistakes in a brand

If a brand has a mistake, the command stops before it draws a page. The message names the file and the
key. The command stops for these mistakes:

- a table or a key that a brand does not have, for example a misspelt key
- a logo or a font file that is not in the folder
- a colour that is not written as `"#RRGGBB"`
- a font file that the browser cannot read, or a font name that is not on the machine

## The pages of a branded PDF

A branded PDF has one page for each slide, as before. A brand with a `[cover]` adds one page at the start.
A brand with a divider adds one page before each step that has slides. A step with no slides gets no
divider. A document continues over its pages as before, and its step gets a divider.

The page numbers in the footer count the slides, and not the cover or the dividers.
