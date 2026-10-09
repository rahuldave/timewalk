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
| walks.md | The checks |
| walks.md | Tutorial walks |
| walks.md | Slides for moves |
| walks.md | Do mode and watch mode |
| walks.md | Moves on the page |
| walks.md | Anchor commands |
| walks.md | Draft the notes of the moves |
| build-walks.md | Commits for a tutorial |
| build-walks.md | Moves with jj |
| authoring.md | How to check a walk |
| authoring.md | Run the commands of a walk |
| walks.md | The table of contents |
| steps.md | How to change an earlier step |
| steps.md | Where the class material goes |
| class.md | A check the evening before |
| slides.md | A PDF of the slides |
| class.md | If a window stops answering |
| walks.md | The items under files |
| walks.md | The reader in a tutorial |
| page.md | The reader |
| page.md | The status band |
| page.md | The theme |

## Glossary

Use these terms, and use each one the same way on every page. Define a term on a page where a reader of
only that page could not know it.

| Term | Means |
|---|---|
| timewalk | The tool |
| step | A point in the history. By default, a tag that matches `step-*` |
| tag, commit, branch, HEAD | Git terms. Define them where a page needs them |
| worktree | A second working folder attached to the same git repository. Older versions made the replay copy as one |
| clone | A repository of its own, made from another one with `git clone` |
| stash | Git keeps edits aside, to bring them back later |
| the page | The one page that timewalk serves, at the address that it prints. Do not write "projector page" or "presenter page" |
| a window | One browser window on the page, for example a window on the projector or on your own screen. The windows share the step |
| the presenter | The person who presents. Use "you" when the page speaks to the reader |
| the replay copy | The second working copy, `<repo>-replay`, a clone of your repository on the branch `timewalk/replay` |
| saved branch | A branch `timewalk/saved/<step or move>` in the replay copy, where a move keeps the commits of a learner |
| your repository | The repository that you give to timewalk |
| the class folder | The folder with the notes and the slides |
| the step bar | The row of step buttons at the top of the page |
| the file list | The tree of files. Do not write "file tree" |
| the reader | The pane that shows one file |
| the clock band | The band at the top of the page, with `--clock` |
| the status band | The band under the step bar: what to do now, the description of the walk at its first step, and the message of the tag |
| description | The `description` of a walk in `toc.toml`: what the walk is about, in a sentence or two |
| the notes column | The notes on the right of the page, with `--notes` |
| edit (noun) | A change to a tracked file that is not committed |
| tracked file, untracked file | A file that git records, and a file that git does not record |
| terminal | One terminal tab and its shell |
| tab | A terminal tab: At this step, Runs, Main, Claude or + |
| shell | The program that runs in a terminal, for example zsh or bash |
| slide, document | One slide, or one longer Markdown page shown instead of slides |
| manifest | The TOML file that says which slides go with which step |
| brand | A folder with a `brand.toml`, a logo and fonts, that sets the look of a PDF |
| divider | A page of one colour before the slides of a step, in a branded PDF |
| cover | The first page of a branded PDF, with the title |
| notes file | The Markdown file of notes, the script of each step. It lives outside your repository |
| layout | Slides, Both or Code |
| recipe | A task in a `justfile`, run with `just` |
| the demo | The sample project, timewalk-demo |
| the edits watcher | The part of the server that looks for edits each second |
| walk | A replay of the history of a project, with notes and slides, that students work through. One class can have several walks |
| table of contents | `toc.toml`, the file in the class folder that lists the walks of a class |
| the walk menu | The menu at the left of the step bar that changes the walk, with `--toc` |
| narrative | A walk of tagged steps, with nothing between them |
| tutorial | A walk of tagged steps and the small commits between them |
| move | One small commit of a step in a tutorial, named `step-NN.k`. Its notes are a `### step-NN.k` section |
| do mode | The mode of a tutorial where the learner makes each move by hand from the notes, and marks it done. The code does not move |
| watch mode | The mode of a tutorial where timewalk checks out the commit of each move when asked |
| item | A line under `files:` in the notes, ``- diff `path`: words`` or ``- file `path`: words``. On the page, a button to a change or a file, with its words |
| excerpt | A small, read-only part of the real diff that the notes draw for a `show:` line: the line, with two lines of context or its whole short hunk |
| Last change, Next change | Two views of the reader. Last change is the change of the step or move just made, and Next change is the change of the next one |
| Your file, Your edits | Two views of the reader: the file on the disk, and its edits since the commit that the replay copy stands on |
| Apply | The bar in Next change, in do mode. Its buttons type a git command into the shell that makes the change of the next move as edits |
| files match | In do mode, the answer of timewalk when the files of the learner are the same as the commit of the move: "Your files match step-02.3 ✓" |
| anchor command | One of the last commands of a move section. It shows what the move did, and the notes say what it gives |
| artifact | A file that a step needs and that git does not hold. `just setup` makes it in an ignored folder when it is missing |
| kit, walk kit | The repository of a walk, for example `bla-walk`: notes, slides, `justfile` and `.gitignore` |
| `repo/`, `worktree/` | The two folders of a kit that git ignores: the clone of the project, and the replay copy. The replay copy keeps the folder name `worktree/`, though it is a clone |
| class repository | Your own material for a class, for example `bla-class`, kept private |
