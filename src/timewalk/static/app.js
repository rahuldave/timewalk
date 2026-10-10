// The page: step bar, slides, files, reader, terminals, and, with --notes, the notes beside them; with --clock, a clock
// band on top. Every window that opens the page shows the same step, slide, layout, open file and terminal tab,
// because the server keeps them. Each window keeps its own theme, text size, notes toggle and run-on-click setting.
import { init, Terminal, FitAddon } from "/static/vendor/ghostty-web/ghostty-web.js";
import { api, socket, onEvents, escapeHtml, settings, stepLabel, drawSlide, isDoc, renderMarkdown } from "/static/common.js";

const $ = (id) => document.getElementById(id);
const PAGE = Math.random().toString(36).slice(2);   // tells this window's own requests apart from another window's
// The window for the class, opened by the Room button, or by an address with room=1: no cues, no clock band.
const ROOM = new URLSearchParams(location.search).get("room") === "1";
let refreshing = null;     // the refresh() that runs now; declared here, as refresh() runs during the first await
let refreshAgain = false;  // another refresh() was asked for while it ran
// The panes whose scroll every window shares, and the time until which a pane's scrolls come from elsewhere.
const SCROLLERS = { slide: "slide", file: "file-body", notes: "notes-body" };
const following = {};
const LANGUAGES = { py: "python", toml: "ini", cfg: "ini", ini: "ini", yaml: "yaml", yml: "yaml", json: "json", jsonl: "json", md: "markdown",
  qmd: "markdown", sh: "bash", zsh: "bash", js: "javascript", ts: "typescript", html: "xml", css: "css", lua: "lua", lock: "ini" };
// The terminal colours of the same theme as the page: GitHub Light Default and GitHub Dark Default (GitHub's VS Code theme).
const TERM_THEMES = {
  light: { background: "#ffffff", foreground: "#1f2328", cursor: "#0969da", selectionBackground: "#b6e3ff",
    black: "#24292f", red: "#cf222e", green: "#116329", yellow: "#4d2d00", blue: "#0969da", magenta: "#8250df", cyan: "#1b7c83", white: "#6e7781",
    brightBlack: "#57606a", brightRed: "#a40e26", brightGreen: "#1a7f37", brightYellow: "#633c01", brightBlue: "#218bff", brightMagenta: "#a475f9", brightCyan: "#3192aa", brightWhite: "#8c959f" },
  dark: { background: "#0d1117", foreground: "#e6edf3", cursor: "#2f81f7", selectionBackground: "#264f78",
    black: "#484f58", red: "#ff7b72", green: "#3fb950", yellow: "#d29922", blue: "#58a6ff", magenta: "#bc8cff", cyan: "#39c5cf", white: "#b1bac4",
    brightBlack: "#6e7681", brightRed: "#ffa198", brightGreen: "#56d364", brightYellow: "#e3b341", brightBlue: "#79c0ff", brightMagenta: "#d2a8ff", brightCyan: "#56d4dd", brightWhite: "#ffffff" },
};

const ui = {
  state: null,            // what /api/state returned
  tree: null,             // what /api/tree returned
  open: null,             // path of the file being read
  view: "file",           // "file", "diff" (what the step changed) or "edits" (what was edited since the step)
  closedDirs: new Set(),
  knownRecipes: null,     // recipe names seen at the previous step, to mark new ones
  recipes: { replay: [], main: [] },
  tabs: [{ id: "replay", label: "At this step", hint: "A shell in the working copy at this step" },
         { id: "runs", label: "Runs", hint: "A second shell at this step, for commands that take a while, such as a training run" },
         { id: "main", label: "Main", hint: "A shell in the repository you started from" },
         { id: "assistant", label: "Claude", hint: "Starts Claude Code at this step. Ask it what the code is at this commit" }],
  active: "replay",
  keysToTerminal: false,  // whether a terminal may hold the keyboard: after a click in it, or when asked for
  terms: new Map(),       // tab id -> { term, fit, ws, el }
  theme: settings.get("theme", "light"),
  size: settings.get("size", 14),
  layout: "split",        // "slides", "split" or "code", as the server says; only used when there are slides
  shell: false,           // the Shell toggle, as the server says: the terminals take the space of the slides and the files
  roomSized: false,       // in Shell mode, whether a Room window has sized the shells; your window then follows it
  notes: {},              // the notes of every step, when timewalk has a notes file
  editing: null,          // while the notes are edited: the step, and its section as it was read
  notesPath: null,
  skew: 0,                // the server's clock minus this one's
  askedMove: null,        // in a tutorial, the move this window last asked for, until the page redraws
  askedDone: null,        // in do mode, the count of moves done that this window last asked for, until the page redraws
  of: null,               // the move whose change the reader shows, or null for the step's or the move's own
  autoDone: new Set(),    // the moves this window marked done because the files matched: once each
  excerpts: new Map(),    // the diffs that excerpts are drawn from, by move and path: a commit's change never changes
  askedSlide: null,       // the slide this window last asked for, until the server's answer is drawn
  shellToggledHere: false, // this window turned Shell on or off: it gives the shells its size
};

// ---------- steps ----------

/** Draw the page from the server's state. Calls that come while one runs are folded into one more run after it. */
function refresh() {
  if (refreshing) { refreshAgain = true; return refreshing; }
  refreshing = (async () => {
    try {
      do { refreshAgain = false; await drawFromState(); } while (refreshAgain);
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function drawFromState() {
  ui.state = await api("/api/state");
  ui.askedMove = null;
  ui.askedDone = null;
  ui.layout = ui.state.layout;
  applyShell(ui.state.shell);
  ui.skew = ui.state.now - Date.now() / 1000;
  drawSteps();
  drawMoves();
  drawWalks();
  const drawn = drawSlides();
  if (!ui.state.has_notes) ui.notes = {};   // a walk without notes shows none, not the last walk's
  await Promise.all([loadTree(), loadRecipes(), ui.state.has_notes ? loadNotes() : null]);
  if (ui.open) await openFile(ui.open, ui.view, false, ui.of, ui.at, ui.marks);
  drawNotes();
  drawBand();
  drawTools();
  checkMatch();
  await drawn;
  restorePlaces(ui.state.restore);
}

/** Put the slide, the notes and the open file back where this step was left, as the server remembers it. */
function restorePlaces(places = {}) {
  const put = (pane, at) => {
    const el = $(SCROLLERS[pane]);
    following[pane] = Date.now() + 300;   // a scroll that we make here is not sent on to the other windows
    el.scrollTop = at * (el.scrollHeight - el.clientHeight);
  };
  if (places.slide && places.slide[0] === ui.state.slide) put("slide", places.slide[1]);
  // When the notes just scrolled to a new move (or to the top, at the Start), that place wins over the remembered one.
  if (typeof places.notes === "number" && !ui.notesMoved) put("notes", places.notes);
  ui.notesMoved = false;
  if (places.file && places.file[0] && places.file[0] === ui.open) put("file", places.file[1]);
}

/** Ask the server to change what both pages show. The change comes back to this page as an event too. */
function show(what) {
  return api("/api/show", { ...what, from: PAGE }).catch((error) => showNotice(error.message, true));
}

function drawSteps() {
  const { steps, current } = ui.state;
  const list = $("step-list");
  list.replaceChildren(...steps.map((step) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = stepLabel(step);
    button.title = `${step.name}: ${step.subject}`;
    button.className = step.index === current ? "here" : current !== null && step.index < current ? "done" : "";
    if (step.index === current) button.setAttribute("aria-current", "step");
    button.onclick = () => move(step.index);
    item.append(button);
    return item;
  }));
  const here = current === null ? null : steps[current];
  const made = ui.state.move ? ui.state.moves[ui.state.move - 1] : null;   // in a tutorial, the last move made
  $("step-name").textContent = here ? (made ? made.name : here.name) : "between steps";
  // At Start, none of the step's moves is made: the code is still the step before's, so say that, not the step's subject.
  const atStart = here && ui.state.moves?.length && ui.state.move === 0;
  $("step-subject").textContent = !here ? "The working copy is not at one of the steps. Choose a step to return."
    : made ? made.subject : atStart ? `before its first move, with the code of ${ui.state.steps[ui.state.current - 1]?.name ?? "the step before"}` : here.subject;
  drawStatus();
  $("prev").disabled = current === null || current === 0;
  $("next").disabled = current !== null && current === steps.length - 1;
  document.title = (here ? `${here.name} | ` : "") + "timewalk";
  list.querySelector(".here")?.scrollIntoView({ block: "nearest", inline: "center" });
}

async function move(to, setAside = false) {
  if (to < 0 || to >= ui.state.steps.length) return;
  hideNotice();
  try {
    await api("/api/move", { to, name: ui.state.steps[to].name, set_aside: setAside });
    // the server tells every page it moved, this one included; refresh() runs from the event
  } catch (error) {
    if (error.body?.edits?.length && error.message === "uncommitted edits") askAboutEdits(() => move(to, true), error.body.edits);
    else showNotice(error.message, true);
  }
}

/** A tutorial: the moves of the step, as a row of buttons. Start is before the first move. Only the next move is open. */
/** In a tutorial: the move the page points at. In do mode, the one the learner works on (one past the moves marked done);
 *  in watch mode, the one whose commit the code is at. 0 is Start, before the first move. */
function moveOnShow() {
  const { moves = [], move = 0, done = 0, mode } = ui.state;
  return mode === "watch" ? move ?? 0 : Math.min(done + 1, moves.length);
}

/** The moves of a step, under the step bar: a switch between do and watch, arrows, and a button for each move. */
function drawMoves() {
  const { moves = [], move, done = 0, mode } = ui.state;
  document.body.classList.toggle("has-moves", moves.length > 0);
  const row = $("moves");
  row.hidden = !moves.length;
  if (!moves.length) return;
  const watch = mode === "watch";
  const reached = movesReached();
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = "Moves";
  // Back goes only to the step's Start, where just setup makes the environment again; forward goes one move at a time.
  const back = document.createElement("button");
  back.className = "nav";
  back.innerHTML = "&#9664;";
  back.title = "Back to the step's Start, then run just setup (Shift+Left)";
  back.disabled = !reached && (watch ? !move : true);
  back.onclick = () => toStart();
  const forward = document.createElement("button");
  forward.className = "nav";
  forward.innerHTML = "&#9654;";
  forward.title = watch ? "Show the next move's commit (Shift+Right)" : "Mark this move done, and go to the next (Shift+Right)";
  forward.disabled = reached >= moves.length;
  forward.onclick = () => nextMove();
  const buttons = [{ name: "Start", subject: "The step's start: back here, run just setup" }, ...moves].map((m, number) => {
    const button = document.createElement("button");
    button.textContent = number ? String(number) : "Start";
    button.className = number === 0 ? (reached === 0 ? "here" : "") : number <= reached ? "done" : number === reached + 1 ? "next" : "";
    if (watch && number === move && number) button.className = "here";
    if (button.className === "here") button.setAttribute("aria-current", "step");
    // Only Start and the next move can be reached: the moves go in order, and back is the Start. The move on show
    // stays bright: it is where you are, not a place you cannot go.
    const current = button.className === "here";
    button.disabled = number !== 0 && number !== reached + 1 && !current;
    button.title = number === 0 ? m.subject : number === reached + 1 ? `${m.name}: ${m.subject}`
      : number <= reached ? `${m.name}: made. To go back, go to Start` : `${m.name}: after ${moves[reached].name}`;
    button.onclick = () => (current ? null : number === 0 ? toStart() : nextMove());
    return button;
  });
  const subject = document.createElement("span");
  subject.className = "move-subject";
  const edited = (ui.tree?.edits || ui.state.edits || []).length;
  subject.textContent = reached >= moves.length ? "Every move is made"
    : `Next: ${moves[reached].name}, ${moves[reached].subject.replace(/^[^:]*:\s*/, "")}`
      + (watch && edited ? ". Your edits will be kept on a branch at the next Show" : "");
  row.replaceChildren(label, back, ...buttons, forward, subject);
}

/** What each kind of walk asks of you, said once, at the walk's first step. */
const STYLES = {
  narrative: "A narrative goes from tag to tag: each step is one commit of the history, and you go on to the next step when you are ready.",
  do: "A tutorial has one or more steps, and each step has zero or more moves: small commits between the tags. In do mode, you make each move "
    + "by hand from the notes, run the command at the end of its section, and press Done.",
  watch: "A tutorial has one or more steps, and each step has zero or more moves: small commits between the tags. In watch mode, Show checks "
    + "out each move's commit for you, and you run the command at the end of its section.",
};

/** What to do now: where you are (the kind of walk, the step, the moves made) and the next thing to do. The band shows
 *  both, and the notes show what to do. */
function direction() {
  const { walk, steps = [], current, moves = [], mode, has_notes: hasNotes } = ui.state;
  if (current === null) return { where: "", todo: "" };
  const last = current === steps.length - 1;
  const step = `${steps[current].name}, step ${current + 1} of ${steps.length}`;
  // Name a button that is on the screen: Next step ends the notes, when this window shows them; otherwise the step bar's.
  const button = hasNotes && !notesHidden.get() ? "Next step, at the end of the notes" : "\u25B6 in the step bar";
  const go = last ? "This is the last step." : `Press ${button}, or the Right arrow, for the next step.`;
  if (walk?.kind !== "tutorial") return { where: (walk ? "Narrative \u00b7 " : "") + step, todo: go };
  const watch = mode === "watch";
  const kind = `Tutorial, ${watch ? "watch" : "do"} mode \u00b7 ${step}`;
  if (!moves.length) return { where: `${kind} \u00b7 no moves`, todo: `Run just setup${hasNotes ? " and the commands in the notes" : ""}. ${go}` };
  const reached = movesReached();
  const where = `${kind} \u00b7 ${reached} of ${moves.length} moves made`;
  if (reached >= moves.length) return { where, todo: `Every move is made. ${go}` };
  const next = moves[reached].name;
  return { where, todo: (reached ? "" : "Run just setup. Then ") + (watch
    ? `press Show on ${next} (Shift+Right), and run the command at the end of its section.`
    : `make ${next} by hand, run the command at the end of its section, and press Done.`).replace(/^./, (c) => (reached ? c.toUpperCase() : c)) };
}

/** The status band under the step bar: what to do now; at the walk's first step, what the walk is; and the step's tag. */
function drawStatus() {
  const { walk, steps = [], current, mode } = ui.state;
  const here = current === null ? null : steps[current];
  $("note").hidden = !here;
  if (!here) return;
  const { where, todo } = direction();
  $("status").textContent = `${where} \u00b7 ${todo}`;
  const style = walk ? STYLES[walk.kind === "tutorial" ? mode || "do" : "narrative"] : "";
  $("about").hidden = current !== 0 || !walk;
  $("about").textContent = [walk?.description, style].filter(Boolean).join(" ");
  // The tag's message, compact: its first line in bold, and the rest after it, without the blank line between.
  const [title, ...rest] = (here.note || "").trim().split(/\n\s*\n/);
  const tag = $("tag");
  tag.replaceChildren();
  if (title) {
    const strong = document.createElement("strong");
    strong.textContent = title;
    tag.append(strong, rest.length ? " \u2014 " + rest.join("\n") : "");
  }
}

/** How many of the step's moves are made: in watch mode the move on show, in do mode the moves marked done. */
function movesReached() {
  const { move = 0, done = 0, mode } = ui.state;
  return mode === "watch" ? move ?? 0 : done;
}

/** Make the next move: in watch mode show its commit; in do mode mark the move worked on done. */
function nextMove() {
  const { moves = [] } = ui.state;
  const reached = ui.askedMove?.step === ui.state.current ? ui.askedMove.move : ui.askedDone?.step === ui.state.current ? ui.askedDone.done : movesReached();
  if (reached >= moves.length) return;
  return ui.state.mode === "watch" ? goMove(reached + 1) : markDone(reached + 1);
}

/** Back to the step's Start: the code goes back to the step before, edits are kept on a branch, and just setup is the next thing. */
function toStart() {
  return goMove(0);   // the notes' hint at the Start says to run just setup
}

/** A tutorial's mode, beside the walk's kind, at every step: Do, the learner makes each move by hand; Watch, timewalk
 *  shows each move's commit. The Room window names the mode in its title instead. */
function drawModes() {
  const tutorial = ui.state.walk?.kind === "tutorial";
  const modes = $("move-modes");
  modes.hidden = ROOM || !tutorial;
  if (modes.hidden) return;
  for (const button of modes.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === (ui.state.mode || "do")));
  }
}

/** Do mode: mark how many of the step's moves are done. The code does not move. */
let doneQueue = Promise.resolve();
function markDone(count) {
  const { current } = ui.state;
  if (current === null) return Promise.resolve();
  ui.askedDone = { step: current, done: count };
  doneQueue = doneQueue.then(() => api("/api/done", { step: ui.state.steps[current].name, done: count })
    .catch((error) => { ui.askedDone = null; showNotice(error.message, true); }));
  return doneQueue;
}

/** Moves asked for one after another reach the server in that order, one at a time. */
let moveQueue = Promise.resolve();
function goMove(number, setAside = false) {
  moveQueue = moveQueue.then(() => makeMove(number, setAside));
  return moveQueue;
}

async function makeMove(number, setAside) {
  const { moves = [], current } = ui.state;
  if (!moves.length || current === null || number < 0 || number > moves.length) return;
  hideNotice();
  ui.askedMove = { step: current, move: number };   // a second key press before the page redraws counts from here
  try {
    await api("/api/move", { to: current, name: ui.state.steps[current].name, move: number, set_aside: setAside });
  } catch (error) {
    ui.askedMove = null;
    if (error.body?.edits?.length && error.message === "uncommitted edits") askAboutEdits(() => goMove(number, true), error.body.edits);
    else showNotice(error.message, true);
  }
}

function askAboutEdits(goOn, edits) {
  const notice = $("notice");
  notice.className = "notice";
  notice.replaceChildren();
  const text = document.createElement("span");
  text.textContent = `${edits.length} file${edits.length === 1 ? " has" : "s have"} edits that are not committed: ${edits.slice(0, 4).join(", ")}${edits.length > 4 ? ", ..." : ""}.`;
  const go = document.createElement("button");
  go.textContent = "Set the edits aside and move";
  go.title = "Runs git stash, so the edits can be brought back with git stash pop";
  go.onclick = goOn;
  const stay = document.createElement("button");
  stay.textContent = "Stay here";
  stay.onclick = hideNotice;
  notice.append(text, go, stay);
  notice.hidden = false;
}

function showNotice(message, isError = false) {
  const notice = $("notice");
  notice.className = isError ? "notice error" : "notice";
  notice.textContent = message;
  notice.hidden = false;
}

function hideNotice() { $("notice").hidden = true; }

/** After a move that kept the learner's work on a branch: say where, and how to get it back. Not in the Room window. */
function showKept(branches) {
  if (ROOM) return;
  const name = branches[0];   // the server names the branch with the edits first
  const notice = $("notice");
  notice.className = "notice";
  // A command keeps one line: it is in the font of the code, and does not break at its spaces.
  const code = (text) => Object.assign(document.createElement("code"), { textContent: text, className: "nowrap" });
  const text = document.createElement("span");   // one item of the notice's row, so that it wraps as a sentence
  text.append(`Your work is kept on ${branches.length > 1 ? "the branches" : "the branch"} `, ...branches.flatMap(
    (branch, i) => [...(i ? [", "] : []), code(branch)]), ". To see it: ", code(`git show ${name}`), ". To bring a file back: ",
  code(`git restore --source ${name} -- <file>`), ".");
  notice.replaceChildren(text);
  notice.hidden = false;
  const close = document.createElement("button");
  close.textContent = "\u2715 Close";
  close.onclick = hideNotice;
  notice.append(close);
}

// ---------- slides ----------

function drawSlides() {
  const { slides = [], slide = 0, has_slides: hasSlides } = ui.state;
  $("layouts").hidden = !hasSlides;
  // A step without slides shows its code, whatever the chosen layout.
  document.body.dataset.layout = hasSlides && slides.length ? ui.layout : "code";
  document.body.classList.toggle("no-slides-here", hasSlides && !slides.length);
  for (const button of $("layouts").querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.layout === ui.layout));
  const doc = isDoc(slides[slide]);
  // A document scrolls instead of paging, so a step that is one document has no slide arrows.
  $("slide-arrows").hidden = doc && slides.length === 1;
  $("slide-count").textContent = doc ? slides[slide].replace(/#doc$/, "") : slides.length ? `Slide ${slide + 1} of ${slides.length}` : "No slides for this step";
  $("slide-prev").disabled = slide <= 0;
  $("slide-first").hidden = slide <= 0;   // shown once you are past the first slide
  $("slide-next").disabled = slide >= slides.length - 1;
  // At the last slide of a step whose moves are all made: the next step, as at the end of the notes.
  const { steps = [], current, moves = [] } = ui.state;
  const after = current !== null && steps[current + 1];
  $("slide-step").hidden = !after || !slides.length || slide < slides.length - 1 || movesReached() < moves.length;
  if (after) $("slide-step").textContent = `Next step: ${after.name} \u25B6`;
  for (const entry of ui.terms.values()) requestAnimationFrame(() => fitTerminal(entry));
  return drawSlide($("slide"), slides[slide], { command: commandButton });   // a $ line on a slide is a button, as in the notes
}

function applyLayout(layout) {
  ui.layout = layout;
  drawSlides();
}

// ---------- the Shell toggle ----------
// Shell gives the terminals the space of the slides, the file list and the reader, in every window; the server keeps
// it, as it keeps the layout. A shell has one size. In Shell mode the Room window sets it, since the projector is what
// the class reads, and your window shows the same rows and columns, with its font scaled to fit.

function applyShell(on) {
  const was = ui.shell;
  ui.shell = !!on;
  document.body.classList.toggle("shell-mode", ui.shell);
  $("shell-toggle").setAttribute("aria-pressed", String(ui.shell));
  if (was === ui.shell) return;
  if (!ui.shell) {
    ui.roomSized = false;
    for (const entry of ui.terms.values()) { entry.adopted = null; entry.term.options.fontSize = ui.size; }
  }
  // On, the Room sizes each shell; the window that turned it on gives its size until a Room does. Off, the window that
  // turned it off gives the size again. Only that one, so that two windows do not race.
  const mine = ui.shellToggledHere;
  ui.shellToggledHere = false;
  for (const entry of ui.terms.values()) requestAnimationFrame(() => { fitTerminal(entry); if (ROOM ? ui.shell : mine) entry.sendSize(true); });
}

/** Fit a terminal to its pane; or, when it follows the Room's size, take those rows and columns and scale the font to fit. */
function fitTerminal(entry) {
  if (entry.el.hidden) return;
  if (!entry.adopted) {
    // Not fit.fit(): it skips a size equal to the last one it fitted, though the Room's size may have come between.
    const size = entry.fit.proposeDimensions();
    if (size && (size.cols !== entry.term.cols || size.rows !== entry.term.rows)) entry.term.resize(size.cols, size.rows);
    return;
  }
  const { rows, cols } = entry.adopted;
  entry.term.options.fontSize = ui.size;
  const room = entry.fit.proposeDimensions();
  if (room) entry.term.options.fontSize = Math.max(6, Math.floor(ui.size * Math.min(room.cols / cols, room.rows / rows) * 2) / 2);
  if (entry.term.cols !== cols || entry.term.rows !== rows) entry.term.resize(cols, rows);
}

/** The Room window that sized a shell closed: this window fits the shell to itself again, and gives it its size. */
function dropRoomSize(event) {
  if (ROOM) return;
  const entry = ui.terms.get(event.track);
  if (!entry) return;
  entry.adopted = null;
  entry.term.options.fontSize = ui.size;
  ui.roomSized = [...ui.terms.values()].some((other) => other.adopted);
  requestAnimationFrame(() => { fitTerminal(entry); entry.sendSize(true); });
}

/** The Room window sized a shell, in Shell mode: this window shows it at the same rows and columns. */
function followSize(event) {
  if (ROOM || !ui.shell) return;
  ui.roomSized = true;
  const entry = ui.terms.get(event.track);
  if (!entry) return;
  entry.adopted = { rows: event.rows, cols: event.cols };
  fitTerminal(entry);
}

/** The slide this window asks for next: one past the one it last asked for, so that quick presses all count. */
function slideFrom() {
  // Only for a moment: after that the server's answer is drawn, and another window may have changed the slide since.
  const fresh = ui.askedSlide && ui.askedSlide.step === ui.state.current && Date.now() - ui.askedSlide.at < 1500;
  return fresh ? ui.askedSlide.slide : ui.state.slide;
}

/** Slide changes asked for one after another reach the server in that order, one at a time. */
let slideQueue = Promise.resolve();
function showSlide(to, setAside = false) {
  ui.askedSlide = { step: ui.state.current, slide: Math.min(Math.max(to, 0), Math.max((ui.state.slides?.length || 1) - 1, 0)), at: Date.now() };
  slideQueue = slideQueue.then(() => askSlide(to, setAside));
  return slideQueue;
}

async function askSlide(to, setAside) {
  try {
    const here = ui.state.current === null ? null : ui.state.steps[ui.state.current]?.name;
    await api("/api/slide", { to, step: here, set_aside: setAside });
  } catch (error) {
    // In a tutorial with sync, a slide can make a move: with edits, ask first, as a move does.
    ui.askedSlide = null;
    if (error.body?.edits?.length && error.message === "uncommitted edits") askAboutEdits(() => showSlide(to, true), error.body.edits);
    else showNotice(error.message, true);
  }
}

// ---------- files ----------

async function loadTree() {
  ui.tree = await api("/api/tree");
  drawTree();
}

function drawTree() {
  const onlyChanged = $("only-changed").checked;
  const edited = new Set(ui.tree.edits);
  const files = ui.tree.files.filter((f) => !onlyChanged || f.status || edited.has(f.path));
  const changed = ui.tree.files.filter((f) => f.status).length;
  $("tree-summary").textContent = `${ui.tree.files.length} files` + (changed ? `, ${changed} changed` : "") + (edited.size ? `, ${edited.size} edited` : "");
  $("tree-summary").title = ui.tree.summary || "";

  const root = { dirs: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [], changed: false });
      node = node.dirs.get(part);
      if (file.status || edited.has(file.path)) node.changed = true;
    }
    node.files.push({ ...file, name: parts.at(-1) });
  }
  // On a large tree, start with folders that hold no change closed. The user's own toggles win after that.
  if (ui.closedDirs.size === 0 && ui.tree.files.length > 60) {
    const seed = (node, prefix) => node.dirs.forEach((child, name) => {
      const path = prefix + name;
      if (!child.changed) ui.closedDirs.add(path);
      seed(child, path + "/");
    });
    seed(root, "");
  }

  const build = (node, prefix, depth) => {
    const list = document.createElement("ul");
    for (const [name, child] of [...node.dirs].sort(([a], [b]) => a.localeCompare(b))) {
      const path = prefix + name;
      const item = document.createElement("li");
      item.className = "dir" + (ui.closedDirs.has(path) ? " closed" : "");
      const button = document.createElement("button");
      button.style.setProperty("--depth", depth);
      button.textContent = name + "/";
      button.onclick = () => {
        item.classList.toggle("closed");
        if (item.classList.contains("closed")) ui.closedDirs.add(path); else ui.closedDirs.delete(path);
      };
      item.append(button, build(child, path + "/", depth + 1));
      list.append(item);
    }
    for (const file of node.files.sort((a, b) => a.name.localeCompare(b.name))) {
      const item = document.createElement("li");
      item.className = "file" + (file.path === ui.open ? " open" : "");
      const button = document.createElement("button");
      button.style.setProperty("--depth", depth);
      button.append(file.name);
      if (file.status) {
        const badge = document.createElement("span");
        badge.className = "badge " + file.status;
        badge.textContent = file.status === "A" ? "new" : "changed";
        button.append(badge);
      }
      if (edited.has(file.path)) {
        const badge = document.createElement("span");
        badge.className = "badge E";
        badge.textContent = "edited";
        badge.title = "Edited since the step's commit, by a command run here" + (ui.state?.discard ? ". Discarded on the next move" : "");
        button.append(badge);
      }
      button.onclick = () => show({ path: file.path, view: edited.has(file.path) ? "edits" : file.status === "M" && ui.view === "diff" ? "diff" : "file" });
      item.append(button);
      list.append(item);
    }
    return list;
  };

  const tree = $("tree");
  tree.replaceChildren(build(root, "", 0));
  drawMode();
  if (ui.tree.deleted.length) {
    const gone = document.createElement("p");
    gone.className = "gone";
    gone.textContent = "Removed in this step: " + ui.tree.deleted.join(", ");
    tree.append(gone);
  }
}

// With --discard-edits a move throws edits away. Both pages say so in the step bar, and warn when there are some.
function drawMode() {
  const mode = $("mode");
  $("view-edits").title = "What commands run here, such as a formatter, have changed in this file since the step's commit. Not committed" +
    (ui.state?.discard ? ". Discarded on the next move" : "");
  mode.hidden = !ui.state?.discard;
  if (mode.hidden) return;
  const count = ui.tree?.edits?.length || 0;
  mode.textContent = count ? `${count} edited file${count === 1 ? "" : "s"}, discarded on the next move` : "Moves discard edits";
  mode.classList.toggle("warn", count > 0);
}

/** Open a file in the reader. Views: "file" (your file), "at" (as a move leaves it), "diff" (Last change), "next"
 *  (Next change), "edits" (your edits). `of` names a move whose change to show; `marks` are lines to highlight. */
async function openFile(path, view = "file", scrollTop = true, of = null, at = null, marks = null) {
  const query = "/api/file?path=" + encodeURIComponent(path) + (of ? "&of=" + encodeURIComponent(of) : "") + (at ? "&at=" + encodeURIComponent(at) : "");
  const file = await api(query);
  ui.of = file.of || null;
  ui.at = file.at || null;
  ui.marks = marks || [];
  $("view-diff").textContent = ui.of ? `Changes in ${ui.of}` : file.last_name ? `Last change: ${file.last_name}` : "Last change";
  $("view-next").textContent = file.next_name ? `Next change: ${file.next_name}` : "Next change";
  $("view-at").hidden = !ui.at;
  $("view-at").textContent = ui.at ? `At ${ui.at}` : "At";
  // A view with nothing to show falls back to the file, for example once edits are stashed or undone.
  const empty = { diff: !file.diff, next: !file.next, edits: !file.edits, at: !ui.at };
  if (empty[view]) view = "file";
  ui.open = path;
  ui.view = view;
  $("file-path").textContent = path;
  for (const name of ["file", "at", "diff", "next", "edits"]) $("view-" + name).setAttribute("aria-selected", String(view === name));
  $("view-diff").disabled = !file.diff;
  $("view-next").disabled = !file.next;
  $("view-edits").disabled = !file.edits;
  $("view-edits").hidden = !file.edits;
  const body = $("file-body");
  // A move's change shows even where the file does not exist yet: in do mode, before the learner makes it.
  const shown = view === "at" ? file.at_text : file;
  if (view === "edits") body.replaceChildren(drawDiff(file.edits, ui.marks));
  else if (view === "diff") body.replaceChildren(drawDiff(file.diff, ui.marks));
  else if (view === "next") body.replaceChildren(drawDiff(file.next, ui.marks));
  else if (shown.missing) body.innerHTML = `<p class="empty">This file does not exist ${view === "at" ? "at " + escapeHtml(ui.at) : "at this step"}.</p>`;
  else if (shown.skipped) body.innerHTML = `<p class="empty">Not shown: ${escapeHtml(shown.skipped)}.</p>`;
  else body.replaceChildren(drawCode(path, shown.text, ui.marks));
  if (scrollTop) {
    const mark = body.querySelector(".mark");
    // Measured from the reader itself: offsetTop is measured from a positioned ancestor, which the reader is not.
    body.scrollTop = mark ? Math.max(mark.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 60, 0) : 0;
  }
  drawEditorLink(view === "file" && !file.missing && !file.skipped);
  drawApply(path, view, file);
  document.querySelectorAll("#tree .file").forEach((item) => item.classList.remove("open"));
  drawTree();
}

/** Whether a line of a file or a diff is one of the lines to mark: its text, without the diff's sign, holds a mark. */
function marked(line, marks) {
  const text = line.replace(/^[+\- ]/, "").trim();
  return marks.some((mark) => mark.trim() && text && text.includes(mark.trim()));
}

/** "Open in your editor": a link that opens the file in the editor this browser chose, at the first marked line. */
function drawEditorLink(show) {
  const link = $("open-editor");
  // Only on this machine: with --host, the path would be the other machine's.
  link.hidden = !show || ROOM || !ui.state?.work || !["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
  if (link.hidden) return;
  const editor = settings.get("editor", "vscode");
  const line = (() => { const mark = $("file-body").querySelector(".gutter .mark"); return mark ? Number(mark.textContent) : 1; })();
  const full = `${ui.state.work}/${ui.open}`;
  link.href = `${editor}://file${full.startsWith("/") ? "" : "/"}${encodeURI(full).replace(/#/g, "%23").replace(/\?/g, "%3F")}:${line}`;
  link.textContent = `Open in ${{ vscode: "VS Code", zed: "Zed", cursor: "Cursor" }[editor] || editor}`;
  link.title = "Open this file in your own editor. Alt+click to choose the editor: VS Code, Zed or Cursor";
  link.onclick = (event) => {
    if (!event.altKey) return;
    event.preventDefault();
    const order = ["vscode", "zed", "cursor"];
    settings.set("editor", order[(order.indexOf(editor) + 1) % order.length]);
    drawEditorLink(true);
  };
}

/** Apply, in the Next change tab, in a tutorial's do mode: type into the shell the command that applies this file's
 *  change, or the whole move's, as edits. Disabled where the learner has already changed a file it touches. */
function drawApply(path, view, file) {
  const bar = $("apply-bar");
  const next = ui.state?.changes?.next;
  const moves = ui.state?.moves || [];
  const move = next && moves.find((m) => m.name === next.name);
  bar.hidden = ROOM || view !== "next" || ui.state?.mode !== "do" || !move || !file.next;
  if (bar.hidden) return;
  const edited = new Set(ui.tree?.edits || ui.state.edits || []);   // the file list's edits are the freshest
  const touched = move.files.map((f) => f.path);
  // Files that already match the move were applied, or typed, and are no collision: the move is about to be done.
  const applied = ui.match?.match && ui.match.move === move.name;
  const fileClash = !applied && edited.has(path);
  const moveClash = applied ? [] : touched.filter((p) => edited.has(p));
  $("apply-file").hidden = $("apply-move").hidden = !!applied;
  $("apply-note").textContent = applied ? `Your files match ${move.name}: it is made` : fileClash || moveClash.length
    ? `You have changed ${fileClash ? path : moveClash.join(", ")}. Compare it with Next change, or use Catch me up.`
    : `${move.name}: apply its change to your files, as edits`;
  $("apply-file").disabled = fileClash;
  $("apply-move").disabled = moveClash.length > 0;
  // From the top of the copy: the learner's shell may be in a folder of it, where the path would not be found.
  $("apply-file").onclick = () => typeCommand(`(cd "$(git rev-parse --show-toplevel)" && git diff ${next.before.slice(0, 12)} ${next.after.slice(0, 12)} -- ${shellQuote(path)} | git apply --3way)`);
  $("apply-move").onclick = () => typeCommand(`git cherry-pick --no-commit ${next.after.slice(0, 12)}`);
}

function shellQuote(text) {
  return /^[\w./-]+$/.test(text) ? text : `'${text.replace(/'/g, "'\\''")}'`;
}

/** Type a command into the shell at the step, and run it, as a command of the notes does with run on click. */
function typeCommand(text) {
  return api("/api/type", { track: "replay", text, enter: true, from: PAGE }).catch((error) => showNotice(error.message, true));
}

function drawCode(path, text, marks = []) {
  const name = path.split("/").at(-1);
  const ext = name.includes(".") ? name.split(".").at(-1).toLowerCase() : "";
  const language = name === "Dockerfile" || ext === "dockerfile" ? "dockerfile" : name === "justfile" ? "makefile" : LANGUAGES[ext];
  let html;
  try {
    html = language && window.hljs?.getLanguage(language) ? window.hljs.highlight(text, { language }).value : escapeHtml(text);
  } catch { html = escapeHtml(text); }
  const count = text.endsWith("\n") ? text.split("\n").length - 1 : text.split("\n").length;
  const wrap = document.createElement("div");
  wrap.className = "code";
  const gutter = document.createElement("pre");
  gutter.className = "gutter";
  gutter.setAttribute("aria-hidden", "true");
  // The gutter's numbers, each a span, so that a marked line's number can be marked
  const lines = text.split("\n");
  gutter.replaceChildren(...Array.from({ length: Math.max(count, 1) }, (_, i) => {
    const number = document.createElement("span");
    number.textContent = String(i + 1) + "\n";
    if (marks.length && marked(lines[i] || "", marks)) number.className = "mark";
    return number;
  }));
  const code = document.createElement("pre");
  code.className = "source hljs";
  code.innerHTML = html;
  wrap.append(gutter, code);
  return wrap;
}

function drawDiff(diff, marks = []) {
  const pre = document.createElement("pre");
  pre.className = "diff";
  for (const line of diff.split("\n")) {
    if (/^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode)/.test(line)) continue;
    const row = document.createElement("span");
    row.className = "line" + (line.startsWith("@@") ? " hunk" : line.startsWith("+") ? " add" : line.startsWith("-") ? " del" : "")
      + (marks.length && !line.startsWith("@@") && marked(line, marks) ? " mark" : "");
    row.textContent = line || " ";
    pre.append(row);
  }
  return pre;
}

// ---------- just recipes ----------

async function loadRecipes() {
  ui.recipes = await api("/api/recipes");
  drawRecipes();
  ui.knownRecipes = new Set(ui.recipes.replay.map((r) => r.name));
}

function drawRecipes() {
  const track = ui.active === "main" ? "main" : "replay";   // which working copy's justfile the chips list
  const list = ui.recipes[track] || [];
  const holder = $("recipes");
  holder.replaceChildren();
  if (!list.length) return;   // no justfile in this folder: no recipe buttons, and no label
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = "just";
  holder.append(label);
  for (const recipe of list) {
    const button = document.createElement("button");
    button.textContent = recipe.name;
    const isNew = track === "replay" && ui.knownRecipes && !ui.knownRecipes.has(recipe.name);
    if (isNew) button.className = "new";
    button.title = (recipe.doc || "just " + recipe.name) + (recipe.needs.length ? `\nNeeds: ${recipe.needs.join(", ")}` : "") + (isNew ? "\nNew at this step" : "");
    button.onclick = () => typeInto(ui.active, "just " + recipe.name + (recipe.needs.length ? " " : "\r"));
    holder.append(button);
  }
}

// ---------- terminals ----------

function drawTabs() {
  $("tabs").replaceChildren(...ui.tabs.map((tab) => {
    const button = document.createElement("button");
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(tab.id === ui.active));
    button.textContent = tab.label;
    if (tab.unseen) { button.classList.add("unseen"); button.setAttribute("aria-label", tab.label + ", new output"); }
    button.title = tab.hint;
    button.onclick = () => { selectTab(tab.id, true); show({ track: tab.id }); };
    return button;
  }));
}

// The page keeps the keyboard, for the arrows, until a terminal is clicked or asked for: `focus` gives it the keys.
function selectTab(id, focus = false) {
  // A command sent to "runs2" (up to "runs9") opens that tab the first time it is used, and a shell opened with +
  // on one page appears on the other.
  if (/^runs[2-9]$/.test(id) && !ui.tabs.some((tab) => tab.id === id)) {
    const after = ui.tabs.findLastIndex((tab) => tab.id.startsWith("runs"));
    ui.tabs.splice(after + 1, 0, { id, label: "Runs " + id.slice(4), hint: "Another shell at this step, for a second long command" });
  }
  if (/^extra-\d$/.test(id) && !ui.tabs.some((tab) => tab.id === id)) {
    ui.tabs.push({ id, label: "Shell " + id.slice(6), hint: "Another shell at this step" });
  }
  if (!ui.tabs.some((tab) => tab.id === id)) return;
  ui.active = id;
  ui.tabs.find((tab) => tab.id === id).unseen = false;
  drawTabs();
  const entry = ui.terms.get(id) || startTerminal(id);
  for (const [other, { el }] of ui.terms) el.hidden = other !== id;
  drawOnlyWhatShows();
  drawRecipes();
  if (focus) ui.keysToTerminal = true;
  requestAnimationFrame(() => { fitTerminal(entry); if (focus) { entry.term.focus(); entry.sendSize(true); } });
}

function startTerminal(id) {
  const el = document.createElement("div");
  el.className = "term";
  $("terms").append(el);
  const term = new Terminal({ fontSize: ui.size, fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, "Symbols Nerd Font Mono", "Symbols Nerd Font", monospace',
    theme: TERM_THEMES[ui.theme], cursorBlink: true, scrollback: 8000 });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(el);
  // At a fractional device pixel ratio (a zoomed window, a scaled screen), ghostty-web's renderer never finds its canvas
  // the right size, and makes a new canvas on every frame. A whole ratio, rounded up, stops that and keeps text sharp.
  // It uses an internal of ghostty-web: check it after an update.
  if (term.renderer && typeof term.renderer.devicePixelRatio === "number" && !Number.isInteger(term.renderer.devicePixelRatio)) {
    term.renderer.devicePixelRatio = Math.ceil(term.renderer.devicePixelRatio);
  }
  const ws = socket("/ws/term/" + id);
  ws.binaryType = "arraybuffer";
  // A shell has one size. The window you last typed in sets it; a window that only shows the shell leaves it alone,
  // so two windows of different sizes do not fight over it. In Shell mode the Room window sets it instead, and the
  // server keeps that size while Shell is on.
  const sendSize = (force = false, opening = false) => {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (ui.shell && ROOM) return ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols, room: true }));
    if (ui.shell && ui.roomSized && !force) return;   // asked anyway, the server answers with the Room's size
    if (force || ui.keysToTerminal) ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols, opening }));
  };
  ws.onopen = () => {
    // A window opened, or reloaded, in Shell mode takes the size the Room gave this shell, if it gave one.
    const given = !ROOM && ui.shell && ui.state?.room_sizes?.[id];
    if (given) { ui.roomSized = true; entry.adopted = { rows: given[0], cols: given[1] }; }
    fitTerminal(entry);
    sendSize(!ROOM, true);   // a size for a new shell; the server keeps the size a shell already has. The Room: only in Shell mode
  };
  ws.onmessage = (event) => {
    term.write(typeof event.data === "string" ? event.data : new Uint8Array(event.data));
    // A tab that printed something while another was in front gets a dot, so a finished run is noticed.
    const tab = ui.tabs.find((candidate) => candidate.id === id);
    if (tab && ui.active !== id && !tab.unseen && opened) { tab.unseen = true; drawTabs(); }
  };
  let opened = false;
  setTimeout(() => { opened = true; }, 1500);   // the replay of earlier output on connecting is not news
  ws.onclose = (event) => {
    entry.lost = true;
    if (event.code === 4403) { term.write("\r\n[timewalk refused this window: if it restarted, open the new address it printed]\r\n"); return; }
    term.write("\r\n[disconnected from timewalk; trying again]\r\n");
    setTimeout(() => { if (ui.terms.get(id) === entry) restartTerminals(); }, 2000);   // the server may still be there
  };
  term.onData((data) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "input", data })));
  term.onResize(() => sendSize());
  new ResizeObserver(() => fitTerminal(entry)).observe(el);
  // A scroll back in this terminal: the other windows scroll the same shell back as many lines.
  let scrollQueued = false;
  term.onScroll(() => {
    if (Date.now() < (following["term:" + id] || 0) || scrollQueued) return;
    scrollQueued = true;
    requestAnimationFrame(() => {
      scrollQueued = false;
      // `events` is declared after the first terminal starts; a scroll before then has no one to tell.
      try { events.send({ type: "scroll", pane: "term", track: id, lines: Math.round(term.viewportY) }); } catch { /* not connected yet */ }
    });
  });
  const entry = { term, fit, ws, el, sendSize };
  ui.terms.set(id, entry);
  return entry;
}

function typeInto(id, text) {
  const entry = ui.terms.get(id) || startTerminal(id);
  ui.keysToTerminal = true;
  const send = () => entry.ws.send(JSON.stringify({ type: "input", data: text }));
  if (entry.ws.readyState === WebSocket.OPEN) send(); else entry.ws.addEventListener("open", send, { once: true });
  entry.term.focus();
}

function resizeTerminalText() {
  // The text size changes on the live terminal. Making a new one would replay old output at a new width, which garbles it.
  for (const entry of ui.terms.values()) { entry.term.options.fontSize = ui.size; fitTerminal(entry); }
}

// ---------- drawing only what shows ----------
// ghostty-web draws every open terminal on every frame, shown or not. A terminal whose tab is not in front stops
// drawing, and so does every terminal while the window is hidden. Its output is still read; it draws again, whole,
// the moment it shows. This reaches into ghostty-web's render loop, `startRenderLoop` and `animationFrameId`, which it
// does not publish. If a later version renames them, the terminals keep drawing as they did before.

function setDrawing(entry, on) {
  const term = entry.term;
  if (typeof term.startRenderLoop !== "function") return;
  if (!on && !entry.paused) {
    cancelAnimationFrame(term.animationFrameId);
    term.animationFrameId = undefined;
    entry.paused = true;
  } else if (on && entry.paused) {
    entry.paused = false;
    try { term.renderer.render(term.wasmTerm, true, term.viewportY, term, term.scrollbarOpacity); } catch { /* the loop draws it next frame */ }
    term.startRenderLoop();
  }
}

function drawOnlyWhatShows() {
  const visible = document.visibilityState === "visible";
  for (const entry of ui.terms.values()) setDrawing(entry, visible && !entry.el.hidden);
}

function restartTerminals() {
  // The theme is fixed when a terminal is made. The server replays recent output, so nothing is lost.
  for (const { term, ws, el } of ui.terms.values()) { ws.onclose = null; ws.close(); term.dispose(); el.remove(); }
  ui.terms.clear();
  selectTab(ui.active);
}

// ---------- appearance ----------

function applyAppearance() {
  document.documentElement.dataset.theme = ui.theme;
  document.documentElement.style.setProperty("--size", ui.size + "px");
  document.documentElement.style.setProperty("--scale", String(ui.size / 14));   // 14px is the default size: the slides scale from it
  $("hl-light").disabled = ui.theme === "dark";
  $("hl-dark").disabled = ui.theme !== "dark";
  $("theme").textContent = ui.theme === "dark" ? "Light" : "Dark";
  settings.set("theme", ui.theme);
  settings.set("size", ui.size);
}

// The terminal focuses itself when it opens and again a moment later. Unless a terminal was clicked or asked
// for, the page takes the keys back, so the arrows move steps and slides.
function guardKeys() {
  document.addEventListener("mousedown", (event) => {
    ui.keysToTerminal = event.target instanceof Element && !!event.target.closest(".term-pane");
    // The window you type in sets the size of the shell, so a click in a terminal claims it.
    if (ui.keysToTerminal) ui.terms.get(ui.active)?.sendSize(true);
  }, true);
  $("terms").addEventListener("focusin", () => {
    if (!ui.keysToTerminal) requestAnimationFrame(() => document.activeElement?.closest(".term-pane") && document.activeElement.blur());
  });
}

function wireControls() {
  $("prev").onclick = () => move((ui.state.current ?? 1) - 1);
  $("next").onclick = () => move((ui.state.current ?? -1) + 1);
  $("only-changed").onchange = drawTree;
  $("slide-prev").onclick = () => showSlide(slideFrom() - 1);
  $("slide-first").onclick = () => showSlide(0);
  $("slide-next").onclick = () => showSlide(slideFrom() + 1);
  $("slide-step").onclick = () => move(ui.state.current + 1);
  for (const button of $("layouts").querySelectorAll("button")) button.onclick = () => show({ layout: button.dataset.layout });
  $("shell-toggle").onclick = () => { ui.shellToggledHere = true; show({ shell: !ui.shell }); };
  for (const view of ["file", "at", "diff", "next", "edits"]) {
    $("view-" + view).onclick = () => ui.open && show({ path: ui.open, view, of: view === "diff" ? ui.of : null, at: ui.at, marks: ui.marks });
  }
  $("theme").onclick = () => { ui.theme = ui.theme === "dark" ? "light" : "dark"; applyAppearance(); restartTerminals(); };
  $("larger").onclick = () => { ui.size = Math.min(ui.size + 1, 26); applyAppearance(); resizeTerminalText(); };
  $("smaller").onclick = () => { ui.size = Math.max(ui.size - 1, 10); applyAppearance(); resizeTerminalText(); };
  $("add-tab").onclick = () => {
    const n = ui.tabs.filter((tab) => tab.id.startsWith("extra-")).length + 1;
    if (n > 9) return;
    selectTab("extra-" + n, true);
    show({ track: "extra-" + n });
  };
  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey) return;
    // Alt with Enter, 1, 2, 3, ` or \: the toggles. Read by the key's place, so that a Mac's Option characters do not
    // get in the way, and with Alt, so that they work even when a terminal has the keys.
    if (event.altKey && !event.shiftKey) {
      const layouts = { Digit1: "slides", Digit2: "split", Digit3: "code" };
      let used = true;
      if (event.code === "Enter") { ui.shellToggledHere = true; show({ shell: !ui.shell }); }
      else if (layouts[event.code]) { if (ui.state?.has_slides) show({ layout: layouts[event.code] }); }
      else if (event.code === "Backquote") { if (ui.state?.has_slides) show({ layout: ui.layout === "slides" ? "code" : "slides" }); }
      else if (event.code === "Backslash") { if (!$("notes-toggle").hidden) $("notes-toggle").click(); }   // not N: Option+N types ~ on some Macs
      else used = false;
      if (used) { event.preventDefault(); event.stopPropagation(); return; }
    }
    // Shift+Right and Shift+Left: the next and the previous move of a tutorial. With Alt too, inside a terminal.
    if (event.shiftKey && (event.key === "ArrowRight" || event.key === "ArrowLeft") && ui.state?.moves?.length) {
      const target = event.target instanceof Element ? event.target : document.body;
      // A text field keeps Shift+arrows (and Option+Shift+arrows, which select words); a terminal gives them up with Alt.
      // (A terminal takes its keys through a textarea of its own: that one is the terminal, not a text field.)
      const inTerminal = !!target.closest(".term-pane");
      if (!inTerminal && target.closest("textarea, select, [contenteditable], input:not([type=checkbox]):not([type=radio])")) return;
      if (!event.altKey && inTerminal) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      if (event.key === "ArrowRight") nextMove();
      else toStart();   // back is the step's Start, where just setup makes the environment again
      return;
    }
    // Shift+Up and Shift+Down: the first and the last slide of the step. Other keys with Shift are left alone.
    if (event.shiftKey) {
      const target = event.target instanceof Element ? event.target : document.body;
      if (target.closest(".term-pane") || target.closest("textarea, select, [contenteditable], input:not([type=checkbox]):not([type=radio])")) return;
      const count = ui.state?.slides?.length || 0;
      if (event.key === "ArrowUp" && count > 1) { event.preventDefault(); event.stopPropagation(); showSlide(0); }
      if (event.key === "ArrowDown" && count > 1) { event.preventDefault(); event.stopPropagation(); showSlide(count - 1); }
      return;
    }
    // Alt+arrows work everywhere, a terminal included. Plain arrows work unless a terminal or a text field has the keys.
    if (!event.altKey) {
      const target = event.target instanceof Element ? event.target : document.body;
      if (target.closest(".term-pane") || target.closest("textarea, select, [contenteditable], input:not([type=checkbox]):not([type=radio])")) return;
    }
    const manySlides = (ui.state?.slides?.length || 0) > 1;
    // Home and End: the top or the bottom of the slide, file or notes under the mouse.
    if ((event.key === "Home" || event.key === "End") && !event.altKey) {
      const pane = paneUnderMouse();
      if (pane) { event.preventDefault(); scrollPane(pane, event.key === "Home" ? "top" : "bottom"); }
    }
    // A held key repeats: one move per press, so a held arrow does not queue a run of checkouts.
    if (event.key === "ArrowRight") { event.preventDefault(); if (!event.repeat) $("next").click(); }
    if (event.key === "ArrowLeft") { event.preventDefault(); if (!event.repeat) $("prev").click(); }
    // With one slide or a document, plain Up and Down are left to scroll.
    // On a document, plain Up and Down scroll it, even when the step has other slides. Alt with them changes slide.
    const onDoc = isDoc(ui.state?.slides?.[ui.state?.slide]);
    const changeSlide = event.altKey || (manySlides && !onDoc);
    // In a tutorial with sync a slide can make a move: one per press there, as with Left and Right.
    const once = !(event.repeat && ui.state?.moves?.length && ui.state?.sync);
    if (event.key === "ArrowDown" && changeSlide) { event.preventDefault(); if (once) $("slide-next").click(); }
    if (event.key === "ArrowUp" && changeSlide) { event.preventDefault(); if (once) $("slide-prev").click(); }
    if (event.defaultPrevented) event.stopPropagation();   // a key used here is not also typed into a terminal
  }, true);   // capture: seen before the terminal, which keeps the keys it handles to itself
  // drag the bar between the reader and the terminal
  // drag the handles between the slides, the file list and the reader. Each browser keeps the widths.
  const panes = document.querySelector(".panes");
  for (const [id, pane, name] of [["resize-slides", "slide-pane", "slides-width"], ["resize-files", "tree-pane", "files-width"]]) {
    const handle = $(id);
    const saved = settings.get(name, null);
    if (saved) panes.style.setProperty("--" + name, saved + "px");
    handle.addEventListener("pointerdown", (down) => {
      down.preventDefault();
      handle.setPointerCapture(down.pointerId);
      handle.classList.add("dragging");
      const left = $(pane).getBoundingClientRect().left;
      const onMove = (e) => {
        const width = Math.round(Math.min(Math.max(e.clientX - left, 120), panes.clientWidth - 260));
        panes.style.setProperty("--" + name, width + "px");
        settings.set(name, width);
      };
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", () => { handle.removeEventListener("pointermove", onMove); handle.classList.remove("dragging"); }, { once: true });
    });
    handle.addEventListener("dblclick", () => { panes.style.removeProperty("--" + name); settings.set(name, null); });
  }

  // drag the handle on the edge of the notes: their width, or on a narrow screen, where they sit under the
  // terminals, their height. Each browser keeps the size; a double-click resets it.
  const notesHandle = $("resize-notes");
  for (const name of ["notes-width", "notes-height"]) {
    const saved = settings.get(name, null);
    if (saved) document.body.style.setProperty("--" + name, saved + "px");
  }
  notesHandle.addEventListener("pointerdown", (down) => {
    down.preventDefault();
    notesHandle.setPointerCapture(down.pointerId);
    notesHandle.classList.add("dragging");
    const across = getComputedStyle(notesHandle).cursor !== "row-resize";
    const name = across ? "notes-width" : "notes-height";
    const onMove = (e) => {
      const size = across ? Math.min(Math.max(window.innerWidth - e.clientX, 220), window.innerWidth - 420)
                          : Math.min(Math.max(window.innerHeight - e.clientY, 120), window.innerHeight - 200);
      document.body.style.setProperty("--" + name, Math.round(size) + "px");
      settings.set(name, Math.round(size));
    };
    notesHandle.addEventListener("pointermove", onMove);
    notesHandle.addEventListener("pointerup", () => { notesHandle.removeEventListener("pointermove", onMove); notesHandle.classList.remove("dragging"); }, { once: true });
  });
  notesHandle.addEventListener("dblclick", () => {
    const name = getComputedStyle(notesHandle).cursor !== "row-resize" ? "notes-width" : "notes-height";
    document.body.style.removeProperty("--" + name);
    settings.set(name, null);
  });

  const divider = $("divider");
  divider.addEventListener("pointerdown", (down) => {
    divider.setPointerCapture(down.pointerId);
    const onMove = (e) => {
      const height = Math.min(Math.max(window.innerHeight - e.clientY, 120), window.innerHeight - 220);
      document.documentElement.style.setProperty("--term-height", height + "px");
      settings.set("term-height", height);
    };
    divider.addEventListener("pointermove", onMove);
    divider.addEventListener("pointerup", () => divider.removeEventListener("pointermove", onMove), { once: true });
  });
  const saved = settings.get("term-height", null);
  if (saved) document.documentElement.style.setProperty("--term-height", saved + "px");
}

// ---------- start ----------

// ---------- the notes, the PDF button and the clock ----------

/** Show the Notes and PDF buttons when there is something for them, and the notes column if this window wants it. */
// Whether this window hides the notes. Kept per tab, not per browser, so a projector window and your own window
// in the same browser can differ.
function perWindow(key) {
  return {
    get() { try { return sessionStorage.getItem("timewalk." + key) === "1"; } catch { return false; } },
    set(value) { try { sessionStorage.setItem("timewalk." + key, value ? "1" : "0"); } catch { /* not remembered */ } },
  };
}
const notesHidden = perWindow("notes-hidden");
// Whether this window hides the cues, the "> " lines of the notes, for example in the window that the class sees.
// An address with cues=off starts the window with them hidden.
const cuesHidden = perWindow("cues-hidden");
if (ROOM || /^(off|hide|hidden|no)$/i.test(new URLSearchParams(location.search).get("cues") || "")) cuesHidden.set(true);

/** Open the window for the class. Where the browser can place windows (Chrome, Edge), it goes on the other screen,
 *  at the full size of that screen. Elsewhere it opens as a new window, to drag to the projector. */
async function openRoom() {
  const url = new URL(location.href);
  url.searchParams.set("room", "1");
  url.searchParams.set("cues", "off");
  let features = "popup,width=1280,height=800";
  let note = "";
  try {
    if ("getScreenDetails" in window) {
      const details = await window.getScreenDetails();
      const other = details.screens.find((screen) => screen !== details.currentScreen);
      if (other) features = `popup,left=${other.availLeft},top=${other.availTop},width=${other.availWidth},height=${other.availHeight}`;
      else note = "Only one screen was found. Drag the room window to the projector, or make the projector an extended display.";
    } else {
      note = "This browser cannot place a window on another screen. Drag the room window to the projector.";
    }
  } catch {
    note = "The browser did not allow timewalk to place windows. Drag the room window to the projector.";
  }
  const room = window.open(url.toString(), "timewalk-room", features);
  if (!room) showNotice("The browser blocked the room window. Allow pop-ups for this address, then click Room again.", true);
  else if (note) showNotice(note);
}

/** The walks of a table of contents: a dropdown in your window, the walk's title in the Room window. */
function drawWalks() {
  const { walks = [], walk } = ui.state;
  const picker = $("walk-picker");
  picker.hidden = ROOM || walks.length < 2;
  $("walk-title").hidden = !ROOM || !walk;
  document.body.classList.toggle("walks", walks.length > 1);
  // Every walk is a narrative (tag to tag) or a tutorial (with the small commits between the tags): say which.
  const KINDS = { narrative: "Narrative", tutorial: "Tutorial" };
  const kind = (KINDS[walk?.kind] || walk?.kind || "").toLowerCase();
  $("walk-title").textContent = walk ? `${walk.title} (${kind}${walk.kind === "tutorial" ? `, ${ui.state.mode === "watch" ? "watch" : "do"} mode` : ""})` : "";
  $("walk-kind").hidden = picker.hidden || !walk;
  $("walk-kind").textContent = walk ? KINDS[walk.kind] || walk.kind : "";
  $("walk-kind").title = walk?.kind === "tutorial" ? "A tutorial stops at every small commit between the tags: Shift+Right makes the next move"
    : "A narrative goes from tag to tag, with nothing between them";
  drawModes();
  if (picker.hidden) return;
  const option = (w) => new Option(w.title, w.id, false, w.id === walk?.id);
  const kinds = [...new Set(walks.map((w) => w.kind))];
  if (kinds.length < 2) { picker.replaceChildren(...walks.map(option)); return; }
  // Both kinds: one group for each, narratives first, each walk in the order of the table.
  picker.replaceChildren(...kinds.sort((a, b) => (a === "narrative" ? -1 : b === "narrative" ? 1 : 0)).map((kind) => {
    const group = document.createElement("optgroup");
    group.label = `${KINDS[kind] || kind}s`;
    group.append(...walks.filter((w) => w.kind === kind).map(option));
    return group;
  }));
}

async function changeWalk(id, setAside = false) {
  if (ui.editing) {
    showNotice("Save or cancel your edit of the notes before you change the walk.", true);
    $("walk-picker").value = ui.state.walk?.id;
    return;
  }
  hideNotice();
  try {
    await api("/api/walk", { id, set_aside: setAside });   // the server tells every window; refresh() runs from the event
  } catch (error) {
    $("walk-picker").value = ui.state.walk?.id;
    if (error.body?.edits?.length && error.message === "uncommitted edits") askAboutEdits(() => changeWalk(id, true), error.body.edits);
    else showNotice(error.message, true);
  }
}

function drawTools() {
  const { has_notes: hasNotes, has_slides: hasSlides } = ui.state;
  const shown = hasNotes && !notesHidden.get();
  $("notes-toggle").hidden = !hasNotes;
  $("notes-toggle").setAttribute("aria-pressed", String(shown));
  $("pdf").hidden = !hasSlides;
  $("notes-pane").hidden = !shown;
  document.body.classList.toggle("with-notes", shown);
  $("cues-toggle").hidden = !shown;
  $("cues-toggle").setAttribute("aria-pressed", String(!cuesHidden.get()));
  document.body.classList.toggle("hide-cues", cuesHidden.get());
  $("run-on-click").checked = settings.get("run-on-click", false);
  for (const entry of ui.terms.values()) requestAnimationFrame(() => fitTerminal(entry));
}

async function makePdf() {
  const button = $("pdf");
  button.disabled = true;
  button.textContent = "Making the PDF...";
  try {
    const response = await fetch("/api/pdf", { cache: "no-store" });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || response.statusText);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = (response.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || "timewalk.pdf";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  } catch (error) {
    showNotice("The PDF could not be made: " + error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "PDF";
  }
}

function startEditing() {
  const here = ui.state.current === null ? null : ui.state.steps[ui.state.current];
  if (!here) return;
  ui.editing = { step: here.name, base: ui.notes[here.name]?.raw || "", walk: ui.state.walk?.id ?? null };
  $("notes-text").value = ui.editing.base;
  $("notes-file").textContent = (ui.notesPath || "").split("/").pop();
  $("notes-file").title = ui.notesPath || "";
  $("notes-editor").hidden = false;
  $("notes-body").hidden = true;
  $("notes-edit").hidden = true;
  $("notes-text").focus();
}

function stopEditing() {
  ui.editing = null;
  $("notes-editor").hidden = true;
  $("notes-body").hidden = false;
  $("notes-edit").hidden = false;
}

async function saveNotes() {
  const { step, base, walk } = ui.editing;
  try {
    await api("/api/notes", { step, base, walk, text: $("notes-text").value });
    stopEditing();
    await loadNotes();
    drawNotes();
    drawBand();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function loadNotes() {
  const notes = await api("/api/notes");
  ui.notes = notes.notes;
  ui.notesPath = notes.path;
}

function clockText(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function drawNotes() {
  const { steps, current } = ui.state;
  const here = current === null ? null : steps[current];
  const mine = here ? ui.notes[here.name] : null;
  const title = $("notes-title");
  title.replaceChildren();
  if (here) {
    const name = document.createElement("strong");
    name.textContent = here.name;
    title.append(name, " " + (mine?.title || here.subject));
    title.title = `${here.name} ${mine?.title || here.subject}`;   // the whole title, when the column cuts it
  } else title.textContent = "between steps";
  $("notes-edit").hidden = !!ui.editing || !here;
  // The prose and the commands, in the order of the notes file: each command is a button where it is written.
  const box = $("notes");
  box.replaceChildren();
  // In a tutorial, each move has a section, and the moves go in order: a move has no just setup of its own, so it builds
  // on what the moves before it did. The moves after the next one are greyed and do nothing; back is the step's Start.
  // The commands at the end of a section anchor the move.
  const { moves = [], move: made = 0, done = 0, mode } = ui.state;
  const watch = mode === "watch";
  const onShow = moveOnShow();
  if (here) {
    const hint = document.createElement("p");
    hint.className = "p-hint";
    hint.textContent = direction().todo;
    box.append(hint);
  }
  // In a step with moves, the text before the first move is about the whole step: it gets a section of its own.
  const parts = mine?.parts || [];
  let into = box;       // where the parts go: the notes, or the section of a move
  if (moves.length && parts.length && parts[0].kind !== "move") {
    into = document.createElement("section");
    into.className = "p-bridge";
    const label = document.createElement("div");
    label.className = "p-bridge-label";
    label.textContent = "Before the moves";
    into.append(label);
    box.append(into);
  }
  let files = [];       // the files of that move's commit, for a files: line
  let owner = 0;        // the move whose section it is
  let group = null;     // the buttons of a run of commands, one after another in the file
  /** The last commands of a move's section are its anchor: what to run to see what the move did. */
  // A made move, or the one on show, ends with Restart step: back to the step's Start, where just setup comes again.
  const markAnchor = (section) => {
    if (!section?.classList.contains("p-move")) return;
    const last = [...section.querySelectorAll(":scope > .p-commands")].pop();
    if (last) {
      last.classList.add("p-anchor");
      const label = document.createElement("div");
      label.className = "p-anchor-label";
      label.textContent = "After this move, run:";
      section.insertBefore(label, last);
    }
    if (movesReached() > 0 && (section.classList.contains("done") || section.classList.contains("here"))) {
      const restart = document.createElement("div");
      restart.className = "p-restart";
      const button = document.createElement("button");
      button.textContent = "\u21BA Restart step";
      button.title = "Back to the step's Start, in every window: the code of the step before. Then run just setup (Shift+Left)";
      button.onclick = () => toStart();
      const words = document.createElement("span");
      words.textContent = "back to the start of the step, then just setup";
      restart.append(button, words);
      section.append(restart);
    }
  };
  for (const part of parts) {
    if (part.kind === "move") {
      markAnchor(into);
      const number = moves.findIndex((m) => m.name === part.name) + 1;
      files = number ? moves[number - 1].files : [];
      owner = number;
      into = document.createElement("section");
      const reached = watch ? made : done;
      const state = !number ? "" : watch
        ? (number === made ? "here" : number < made ? "done" : number === made + 1 ? "next" : "later")
        : (number <= done ? "done" : number === done + 1 ? "here" : "later");
      into.className = "p-move " + state;
      into.dataset.move = String(number);
      const head = document.createElement("div");
      head.className = "p-move-head";
      const heading = document.createElement("h3");
      heading.textContent = part.title ? `${part.name} ${part.title}` : part.name;
      head.append(heading);
      if (number) {
        const actions = document.createElement("span");
        actions.className = "p-move-actions";
        const action = (text, title, onclick, primary = false) => {
          const button = document.createElement("button");
          button.textContent = text;
          button.title = title;
          if (primary) button.className = "primary";
          button.onclick = onclick;
          button.disabled = !onclick;
          actions.append(button);
        };
        // Only the next move has buttons that act: Show in watch mode; Done and Catch me up in do mode. The order is the
        // rule. A move already made keeps its button, grayed: Shown, or Made.
        if (watch && number <= reached) action("Shown \u2713", `${part.name} is shown. To go back, press Restart step`, null);
        if (!watch && number <= reached) action("Made \u2713", `${part.name} is made. To go back, press Restart step`, null);
        if (watch && number === reached + 1) action("Show \u25B6", `Check out ${part.name}'s commit, in every window`, () => goMove(number), true);
        if (!watch && state === "here") action("Done \u2713", "I made this move: go to the next", () => markDone(number), true);
        if (!watch && state === "here") action("\u21E5 Catch me up", `Set the code to the end of ${part.name}. Your own try is kept on a branch, timewalk/saved/<place>`, () => goMove(number));
        if (state === "later") {
          const after = document.createElement("span");
          after.className = "p-after";
          after.textContent = `after ${moves[reached]?.name}`;
          actions.append(after);
        }
        head.append(actions);
      }
      into.append(head);
      box.append(into);
      group = null;
      continue;
    }
    if (part.kind === "files") {
      into.append(drawFiles(part.items || [], owner, files));
      group = null;
      continue;
    }
    if (part.kind === "text") {
      const prose = document.createElement("div");
      prose.innerHTML = renderMarkdown(part.text);
      into.append(prose);
      group = null;
      continue;
    }
    if (!group) {
      group = document.createElement("div");
      group.className = "p-commands";
      into.append(group);
    }
    group.append(commandButton(part));
  }
  markAnchor(into);
  if (!parts.length) box.insertAdjacentHTML("beforeend", `<p class="p-empty">${!here ? "" : ui.notesPath
    ? `No notes for ${escapeHtml(here.name)} in ${escapeHtml(ui.notesPath.split("/").pop())}. Click Edit to write them, or add a section headed "## ${escapeHtml(here.name)}".`
    : "No notes file. Start timewalk with --notes notes.md, with one \"## step-name\" section per step."}</p>`);
  // At the end of the notes, once the step's moves are all made: the next step, in its own colour.
  const after = here && steps[current + 1];
  if (after && movesReached() >= moves.length) {
    const next = document.createElement("div");
    next.className = "p-next-step";
    const button = document.createElement("button");
    button.className = "go";
    button.textContent = `Next step: ${after.name} \u25B6`;
    button.title = `Go to ${after.name}, ${after.subject}, in every window (Right arrow)`;
    button.onclick = () => move(current + 1);
    next.append(button);
    box.append(next);
  }
  // A move after the next one waits: its commands, items and buttons do nothing until the moves before it are made.
  for (const button of box.querySelectorAll(".p-move.later button")) button.disabled = true;
  // When the move on show changes, scroll the notes, and only the notes, to its section, in every window alike. A window
  // with the notes hidden does it when they show.
  const step = here?.name ?? null;
  const section = box.querySelector(".p-move.here") || box.querySelector(".p-move.next");
  const scroller = $("notes-body");
  const key = `${step} ${mode} ${watch ? made : onShow}`;
  if (moves.length && section && scroller.offsetParent && ui.drawnMove !== key) {
    following.notes = Date.now() + 300;   // each window scrolls itself, so this scroll is not sent on
    // At the Start, the top of the notes, where just setup is; otherwise the move's section.
    ui.notesMoved = true;
    if (movesReached() === 0) scroller.scrollTop = 0;
    else scroller.scrollTop += section.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 40;
    ui.drawnMove = key;
  }
}

/** Which tab of the reader a move's change opens in: Next change for the move to make next, Last change for the move just
 *  made, and otherwise the move's own change, named. Number 0 is the step itself, in a narrative. */
function changeView(number) {
  const { moves = [], move: made = 0, done = 0, mode } = ui.state;
  if (!moves.length || !number) return { view: "diff", of: null };
  const last = mode === "watch" ? made : done;
  if (number === last + 1) return { view: "next", of: null };
  if (number === last) return { view: "diff", of: null };
  return { view: "diff", of: moves[number - 1].name };
}

/** A files: line of the notes. With items, each is a button to a file or a change, with its words, and its show: lines
 *  drawn as small excerpts of the real diff. Without items, a button for each file of the move's commit. */
function drawFiles(items, owner, files) {
  const { moves = [], done = 0, mode } = ui.state;
  const box = document.createElement("div");
  box.className = "p-files";
  if (!items.length) {
    for (const file of files) {
      const target = changeView(owner);
      const button = document.createElement("button");
      const path = document.createElement("span");
      path.className = "p-item-path";
      path.textContent = file.path;
      button.append("\u00B1 See diff ", path);   // the same button as an item's
      button.title = `What ${moves[owner - 1]?.name || "this step"} changed in this file`;
      button.onclick = () => show({ path: file.path, ...target });
      box.append(button);
    }
    return box;
  }
  box.classList.add("p-items");
  for (const item of items) {
    const here = item.move ? moves.findIndex((m) => m.name === item.move) + 1 : owner;
    const elsewhere = item.move && !here;   // a move of another step: its own change, named, never "ahead"
    const number = elsewhere ? 0 : here;
    const name = elsewhere ? item.move : moves[number - 1]?.name || null;
    const row = document.createElement("div");
    row.className = "p-item";
    const button = document.createElement("button");
    // One button: an icon and a verb, as Restart step and Show have, then the path.
    const path = document.createElement("span");
    path.className = "p-item-path";
    path.textContent = item.path;
    button.append(item.kind === "file" ? "\u25A4 See file " : "\u00B1 See diff ", path);
    button.className = "p-item-" + item.kind;
    let target;
    if (item.kind === "file") {
      // A file of a move not made yet, in do mode: as the move leaves it, read only. Otherwise the learner's own file.
      const ahead = mode === "do" && name && !elsewhere && number > done;
      target = ahead ? { view: "at", at: name } : { view: "file" };
      button.title = ahead ? `The file as ${name} leaves it, read only` : "Your file";
    } else {
      target = elsewhere ? { view: "diff", of: name } : changeView(number);
      button.title = `What ${name || "this step"} changes in this file`;
    }
    const open = () => show({ path: item.path, ...target, marks: item.show });
    button.onclick = open;
    const words = document.createElement("span");
    words.className = "p-item-words";
    words.innerHTML = renderMarkdown(item.text).replace(/^<p>|<\/p>\s*$/g, "").trim();
    row.append(button);
    if (words.innerHTML) row.append(words);   // the author's words, on the line under the button
    box.append(row);
    for (const line of item.show) {
      const excerpt = document.createElement("div");
      excerpt.className = "p-excerpt";
      excerpt.title = button.title;
      // The excerpt opens the change too, except in a move after the next one, whose buttons do nothing yet.
      // A click that ends a selection, to copy a line, is not a click on the excerpt.
      excerpt.onclick = () => { if (getSelection().isCollapsed && !excerpt.closest(".p-move.later")) open(); };
      box.append(excerpt);
      if (item.kind === "diff") fillExcerpt(excerpt, item.path, name, line);
      else excerpt.remove();
    }
  }
  return box;
}

/** In do mode: whether the learner's files match the move being worked on. Shown on its section and in the Apply bar;
 *  a match marks the move done by itself. Asked every two seconds, and after each refresh. */
async function checkMatch() {
  const { moves = [], done = 0, mode } = ui.state || {};
  if (mode !== "do" || !moves.length || done >= moves.length || document.visibilityState === "hidden") {
    ui.match = null;
    drawMatch();
    return;
  }
  ui.match = await api("/api/match").catch(() => null);
  drawMatch();
  // Only your window marks it, never the Room, so that two windows do not both ask.
  // Once per move: a learner who then marks it not done keeps that choice.
  const key = `${ui.state.steps[ui.state.current]?.name} ${ui.match?.move}`;
  if (!ROOM && ui.match?.match && ui.match.number === (ui.state.done ?? 0) + 1 && !ui.autoDone.has(key)) {
    ui.autoDone.add(key);
    markDone(ui.match.number);
  }
}

function drawMatch() {
  const match = ui.match;
  const text = !match?.move ? "" : match.match ? `Your files match ${match.move} \u2713`
    : `${match.differ.length === 1 ? "1 file differs" : `${match.differ.length} files differ`} from ${match.move}: ${match.differ.join(", ")}`;
  for (const old of document.querySelectorAll("#notes .p-match")) old.remove();
  const head = document.querySelector("#notes .p-move.here .p-move-head");
  if (head && text) {
    const badge = document.createElement("span");
    badge.className = "p-match match" + (match.match ? " ok" : "");
    badge.textContent = text;
    head.after(badge);
  }
  $("apply-match").textContent = text;
  $("apply-match").className = "match" + (match?.match ? " ok" : "");
}

/** Draw a small, read-only excerpt of a change in the notes: the line that show: names, with two lines of context, or
 *  its whole hunk when that is short. The change is read from the commits each time, so it is never stale. */
async function fillExcerpt(box, path, move, line) {
  const key = `${move || ui.state.steps[ui.state.current]?.name} ${path}`;
  if (!ui.excerpts.has(key)) {
    const file = await api("/api/file?path=" + encodeURIComponent(path) + (move ? "&of=" + encodeURIComponent(move) : "")).catch(() => null);
    if (file) ui.excerpts.set(key, file.diff || "");
  }
  const diff = ui.excerpts.get(key) || "";
  const rows = diff.split("\n").filter((row) => !/^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode)/.test(row));
  const at = rows.findIndex((row) => !row.startsWith("@@") && marked(row, [line]));
  if (at < 0) {
    box.className = "p-excerpt p-gone";
    box.textContent = `This line is no longer in the change of ${path}: ${line}`;
    return;
  }
  let start = at, end = at;
  while (start > 0 && !rows[start - 1].startsWith("@@")) start--;
  while (end < rows.length - 1 && !rows[end + 1].startsWith("@@")) end++;
  if (end - start + 1 > 8) { start = Math.max(at - 2, start); end = Math.min(at + 2, end); }
  box.replaceChildren(drawDiff(rows.slice(start, end + 1).join("\n"), [line]));
}

/** One command of the notes, as a button that types it into its terminal, and runs it with run on click. */
function commandButton(command) {
  const button = document.createElement("button");
  const text = document.createElement("span");
  text.textContent = command.text;
  const track = document.createElement("span");
  track.className = "track";
  const extra = /^runs([2-9])$/.exec(command.track);
  track.textContent = extra ? `Runs ${extra[1]}` : { main: "Main", runs: "Runs" }[command.track] || "at this step";
  button.append(text, track);
  button.title = "Types this into that terminal. With run on click, it also presses Enter";
  button.onclick = () => api("/api/type", { track: command.track, text: command.text, enter: $("run-on-click").checked, from: PAGE })
    .catch((error) => showNotice(error.message, true));
  return button;
}

function drawBand() {
  if (!ui.state) return;
  // The clock band shows only when timewalk was started with --clock.
  $("band").hidden = !ui.state.show_clock || ROOM;   // the clock is for you, not for the class
  if ($("band").hidden) return;
  const { steps, current, clock: started } = ui.state;
  const here = current === null ? null : steps[current];
  const next = current === null ? null : steps[current + 1];
  const elapsed = started ? Date.now() / 1000 + ui.skew - started : 0;
  $("elapsed").textContent = clockText(elapsed);
  $("clock-start").textContent = started ? "\u21BA Reset" : "\u25B6 Start the clock";
  const planned = here ? ui.notes[here.name]?.time : null;
  const nextPlanned = next ? ui.notes[next.name]?.time : null;
  const plan = $("plan");
  plan.className = "plan";
  if (!started) plan.textContent = planned != null ? `this step is planned at ${clockText(planned)}` : "";
  else if (nextPlanned != null && elapsed > nextPlanned) { plan.className = "plan late"; plan.textContent = `${clockText(elapsed - nextPlanned)} over`; }
  else if (nextPlanned != null) plan.textContent = `${clockText(nextPlanned - elapsed)} left in this step`;
  else plan.textContent = planned != null ? `started at ${clockText(planned)}` : "";
  const nextNotes = next ? ui.notes[next.name] : null;
  $("upcoming").innerHTML = next
    ? `Next: <span class="step-name">${escapeHtml(next.name)}</span> ${escapeHtml(nextNotes?.title || next.subject)}` +
      (nextNotes?.time != null ? `, at ${clockText(nextNotes.time)}` : "")
    : "This is the last step.";
}

// ---------- start ----------

$("clock-start").onclick = () => api("/api/clock", { action: ui.state.clock ? "reset" : "start" });
$("notes-toggle").onclick = () => { notesHidden.set(!notesHidden.get()); drawTools(); if (ui.state) drawSteps(); if (ui.state && !ui.editing) drawNotes(); };
$("cues-toggle").onclick = () => { cuesHidden.set(!cuesHidden.get()); drawTools(); };
$("room").hidden = ROOM;
$("room").onclick = openRoom;
$("fullscreen").hidden = !ROOM || !document.fullscreenEnabled;
$("fullscreen").onclick = () => document.documentElement.requestFullscreen().catch(() => {});
document.addEventListener("fullscreenchange", () => { $("fullscreen").hidden = !ROOM || !!document.fullscreenElement; });
if (ROOM) { document.title = "timewalk room"; document.body.classList.add("room"); }
$("pdf").onclick = makePdf;
$("walk-picker").onchange = () => changeWalk($("walk-picker").value);
for (const button of $("move-modes").querySelectorAll("button")) {
  button.onclick = () => ui.state.mode !== button.dataset.mode && show({ mode: button.dataset.mode });
}
$("run-on-click").onchange = () => settings.set("run-on-click", $("run-on-click").checked);
$("notes-edit").onclick = startEditing;
$("notes-cancel").onclick = stopEditing;
$("notes-save").onclick = saveNotes;
setInterval(drawBand, 1000);
setInterval(checkMatch, 2000);
document.addEventListener("visibilitychange", drawOnlyWhatShows);
applyAppearance();
guardKeys();
wireControls();
await init();
await refresh();
selectTab(ui.state.track || "replay");
if (ui.state.path) { await openFile(ui.state.path, ui.state.view, true, ui.state.of, ui.state.at); restorePlaces(ui.state.restore); }
// For tests that drive the page: what each terminal shows, read only.
window.timewalkTerminals = () => [...ui.terms].map(([id, { term, el, paused }]) => {
  const lines = term.buffer?.active;
  const from = lines ? Math.max(0, lines.length - 40) : 0;
  const tail = lines ? Array.from({ length: lines.length - from }, (_, i) => lines.getLine(from + i)?.translateToString(true) ?? "").join("\n") : "";
  return { id, shown: !el.hidden, drawing: !paused, rows: term.rows, cols: term.cols, fontSize: term.options.fontSize,
           scrolledBack: Math.round(term.viewportY), tail };
});
// For tests: a hash of what one terminal's canvas shows, to tell whether it drew. Costly, so asked for one terminal at a time.
window.timewalkPicture = (id) => {
  const canvas = ui.terms.get(id)?.el.querySelector("canvas");
  if (!canvas) return null;
  let hash = 0;
  for (const c of canvas.toDataURL()) hash = (hash * 31 + c.charCodeAt(0)) | 0;
  return hash;
};
const events = onEvents(async (event) => {
  if (event.type === "walk") ui.excerpts.clear();   // another walk may have steps of the same names
  if (event.type === "moved" || event.type === "walk") { hideNotice(); await refresh(); if (event.kept?.length) showKept(event.kept); }
  if (event.type === "refused") { showNotice("timewalk refused this window. If it restarted, open the new address that it printed.", true); return; }
  if (event.type === "reconnected") {
    // The server came back, or the network did: look again, and open the terminals whose sockets closed.
    if ([...ui.terms.values()].some((entry) => entry.lost)) restartTerminals();
    await refresh();
  }
  if (event.type === "edits") {
    checkMatch();   // edits are often a move being made: no need to wait for the next poll
    await loadTree();
    if (ui.open) await openFile(ui.open, ui.view, false, ui.of, ui.at, ui.marks);
  }
  if (event.type === "scroll") { followScroll(event); return; }
  if (event.type === "content") { await reloadContent(); return; }
  if (event.type === "slide") { ui.state = await api("/api/state"); drawSlides(); }
  if (event.type === "done") {
    ui.state = await api("/api/state");
    ui.askedDone = null;
    drawMoves(); drawNotes(); ui.notesMoved = false; drawSlides(); drawSteps(); checkMatch();
    if (ui.open) await openFile(ui.open, ui.view, false, ui.of, ui.at, ui.marks);   // Next change is now the next move's
  }
  if (event.type === "notes" && !ui.editing) { await loadNotes(); drawNotes(); drawBand(); }
  if (event.type === "clock") { ui.state = await api("/api/state"); ui.skew = ui.state.now - Date.now() / 1000; drawBand(); }
  if (event.type === "size") { followSize(event); return; }
  if (event.type === "unsized") { dropRoomSize(event); return; }
  if (event.type === "show") {
    if (event.layout && event.layout !== ui.layout) applyLayout(event.layout);
    if (typeof event.shell === "boolean") applyShell(event.shell);
    if (event.path) await openFile(event.path, event.view || "file", true, event.of || null, event.at || null, event.marks || null);
    if (event.mode && event.mode !== ui.state.mode) { ui.state.mode = event.mode; drawMoves(); drawNotes(); ui.notesMoved = false; drawSteps(); drawWalks(); drawSlides(); }
    // The window that asked gets the keys: a tab it clicked, or a command it typed from the notes.
    if (event.track) selectTab(event.track, event.from === PAGE);
  }
});

// ---------- shared scrolling ----------
// When you scroll a slide, a file or the notes, every other window scrolls to the same place, as a fraction of the
// whole, since the windows can differ in size. A scroll that came from another window is not sent back.
for (const [pane, id] of Object.entries(SCROLLERS)) {
  const el = $(id);
  let queued = false;
  el.addEventListener("scroll", () => drawTopButtons(), { passive: true });
  el.addEventListener("scroll", () => {
    if (Date.now() < (following[pane] || 0) || queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const room = el.scrollHeight - el.clientHeight;
      events.send({ type: "scroll", pane, at: room > 0 ? el.scrollTop / room : 0 });
    });
  }, { passive: true });
}

function followScroll(event) {
  if (event.pane === "term") {
    const entry = ui.terms.get(event.track);
    if (!entry) return;
    following["term:" + event.track] = Date.now() + 200;
    entry.term.scrollLines(Math.round(entry.term.viewportY) - event.lines);
    return;
  }
  const el = $(SCROLLERS[event.pane] || "");
  if (!el) return;
  following[event.pane] = Date.now() + 200;
  el.scrollTop = event.at * (el.scrollHeight - el.clientHeight);
}

// ---------- live reload ----------
// When the notes file, the manifest or a slide file changes on disk, every window reads it again and draws the
// current slide and notes anew, at the same scroll position. A notes section that you are editing stays as it is.
async function reloadContent() {
  const tops = Object.fromEntries(Object.entries(SCROLLERS).map(([pane, id]) => [pane, $(id).scrollTop]));
  ui.state = await api("/api/state");
  for (const pane of Object.keys(SCROLLERS)) following[pane] = Date.now() + 500;   // not a scroll to send on
  if (!ui.editing && ui.state.has_notes) { await loadNotes(); drawNotes(); drawBand(); }
  await drawSlides();
  for (const [pane, top] of Object.entries(tops)) $(SCROLLERS[pane]).scrollTop = top;
}

// ---------- back to the top ----------
// Each scrolling pane has a Top button in its header, shown only when the pane is scrolled down. Home and End
// scroll the pane under the mouse to its top or bottom. Either is a scroll, so the other windows follow.
let mouse = { x: 0, y: 0 };
document.addEventListener("mousemove", (event) => { mouse = { x: event.clientX, y: event.clientY }; }, { passive: true });

function paneUnderMouse() {
  const under = document.elementFromPoint(mouse.x, mouse.y);
  for (const [pane, id] of Object.entries(SCROLLERS)) if (under && $(id).contains(under)) return pane;
  for (const pane of ["slide", "file"]) if ($(SCROLLERS[pane]).offsetParent) return pane;   // else the first one shown
  return null;
}

function scrollPane(pane, where) {
  const el = $(SCROLLERS[pane]);
  el.scrollTop = where === "top" ? 0 : el.scrollHeight;
}

function drawTopButtons() {
  for (const button of document.querySelectorAll(".to-top")) button.hidden = $(SCROLLERS[button.dataset.pane]).scrollTop < 40;
}

for (const button of document.querySelectorAll(".to-top")) button.onclick = () => scrollPane(button.dataset.pane, "top");
setInterval(drawTopButtons, 1000);   // a redraw puts a pane back at the top without a scroll event
