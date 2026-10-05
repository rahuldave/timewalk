# How the site is written

Every page of the site follows `GUIDE.md`, the nossaifd writing guide. `GUIDE.md` and `check.py` are copies
from the nossaifd repository, https://github.com/rahuldave/nossaifd. If the guide changes there, copy both
files again.

Check the pages with the recipe, and fix each hard problem:

```
just style                                    # every page
python3 docs/style/check.py -v docs/edits.md  # one page, with the soft notes
```

Then read the page, and build the site with `uv run docs/build.py`.

## Headings that other pages link to

Keep the text of these headings. A link from another page uses each one as an anchor.

| Page | Heading |
|---|---|
| edits.md | Moving with edits |
| edits.md | With `--discard-edits` |
| slides.md | A document instead of slides |
| slides.md | The manifest |
| terminals.md | A long command and a move |
| terminals.md | The shells' environment |
| page.md | Several windows |
| slides.md | A PDF of the slides |

## Glossary

Use these terms, and use each one the same way on every page. Define a term on a page where a reader of
only that page could not know it.

| Term | Means |
|---|---|
| timewalk | The tool |
| step | A point in the history. By default, a tag that matches `step-*` |
| tag, commit, branch, HEAD | Git terms. Define them where a page needs them |
| worktree | A second working folder attached to the same git repository |
| stash | Git keeps edits aside, to bring them back later |
| the page | The one page that timewalk serves, at the address that it prints. Do not write "projector page" or "presenter page" |
| a window | One browser window on the page, for example a window on the projector or on your own screen. The windows share the step |
| the presenter | The person who presents. Use "you" when the page speaks to the reader |
| the replay copy | The second working copy, `<repo>-replay`, which is a worktree |
| your repository | The repository that you give to timewalk |
| the class folder | The folder with the notes and the slides |
| the step bar | The row of step buttons at the top of the page |
| the file list | The tree of files. Do not write "file tree" |
| the reader | The pane that shows one file |
| the clock band | The band at the top of the page, with `--clock` |
| the notes column | The notes on the right of the page, with `--notes` |
| edit (noun) | A change to a tracked file that is not committed |
| tracked file, untracked file | A file that git records, and a file that git does not record |
| terminal | One terminal tab and its shell |
| tab | A terminal tab: At this step, Runs, Main, Claude or + |
| shell | The program that runs in a terminal, for example zsh or bash |
| slide, document | One slide, or one longer Markdown page shown instead of slides |
| manifest | The TOML file that says which slides go with which step |
| notes file | The Markdown file of notes, the script of each step. It lives outside your repository |
| layout | Slides, Both or Code |
| recipe | A task in a `justfile`, run with `just` |
| the demo | The sample project, timewalk-demo |
| the edits watcher | The part of the server that looks for edits each second |
