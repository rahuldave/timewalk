// The page: step bar, slides, files, reader, terminals, and, with --notes, the notes beside them; with --clock, a clock
// band on top. Every window that opens the page shows the same step, slide, layout, open file and terminal tab,
// because the server keeps them. Each window keeps its own theme, text size, notes toggle and run-on-click setting.
import { init, Terminal, FitAddon } from "/static/vendor/ghostty-web/ghostty-web.js";
import { api, socket, onEvents, escapeHtml, settings, stepLabel, drawSlide, isDoc, renderMarkdown } from "/static/common.js";

const $ = (id) => document.getElementById(id);
const PAGE = Math.random().toString(36).slice(2);   // tells this window's own requests apart from another window's
// The window for the class, opened by the Room button, or by an address with room=1: no cues, no clock band.
const ROOM = new URLSearchParams(location.search).get("room") === "1";
// The panes whose scroll every window shares, and the time until which a pane's scrolls come from elsewhere.
const SCROLLERS = { slide: "slide", file: "file-body", notes: "notes-body" };
const following = {};
const LANGUAGES = { py: "python", toml: "ini", cfg: "ini", ini: "ini", yaml: "yaml", yml: "yaml", json: "json", jsonl: "json", md: "markdown",
  qmd: "markdown", sh: "bash", zsh: "bash", js: "javascript", ts: "typescript", html: "xml", css: "css", lua: "lua", lock: "ini" };
const TERM_THEMES = {
  light: { background: "#fbfbfc", foreground: "#17202b", cursor: "#2456b5", selectionBackground: "#cfdcf5",
    black: "#17202b", red: "#b3372f", green: "#1d7a4c", yellow: "#946200", blue: "#2456b5", magenta: "#8a3ea8", cyan: "#0f7b86", white: "#6f7b8b",
    brightBlack: "#4d5969", brightRed: "#c9453c", brightGreen: "#22915a", brightYellow: "#a85b00", brightBlue: "#3468cf", brightMagenta: "#a14fc0", brightCyan: "#12909c", brightWhite: "#17202b" },
  dark: { background: "#0f141b", foreground: "#e7ebf1", cursor: "#8db0f7", selectionBackground: "#2b3f66",
    black: "#11161d", red: "#f0827a", green: "#58c48e", yellow: "#f0c05a", blue: "#8db0f7", magenta: "#d59cf0", cyan: "#6ed3de", white: "#b0bac8",
    brightBlack: "#8d99aa", brightRed: "#ff9d95", brightGreen: "#7ddcaa", brightYellow: "#ffd77a", brightBlue: "#aac5ff", brightMagenta: "#e6b8fb", brightCyan: "#8fe6ef", brightWhite: "#ffffff" },
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
  notes: {},              // the notes of every step, when timewalk has a notes file
  editing: null,          // while the notes are edited: the step, and its section as it was read
  notesPath: null,
  skew: 0,                // the server's clock minus this one's
};

// ---------- steps ----------

async function refresh() {
  ui.state = await api("/api/state");
  ui.layout = ui.state.layout;
  ui.skew = ui.state.now - Date.now() / 1000;
  drawSteps();
  const drawn = drawSlides();
  await Promise.all([loadTree(), loadRecipes(), ui.state.has_notes ? loadNotes() : null]);
  if (ui.open) await openFile(ui.open, ui.view, false);
  drawNotes();
  drawBand();
  drawTools();
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
  if (typeof places.notes === "number") put("notes", places.notes);
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
  $("step-name").textContent = here ? here.name : "between steps";
  $("step-subject").textContent = here ? here.subject : "The working copy is not at one of the steps. Choose a step to return.";
  $("note").hidden = !(here && here.note);
  $("note").textContent = here ? here.note : "";
  $("prev").disabled = current === null || current === 0;
  $("next").disabled = current !== null && current === steps.length - 1;
  document.title = (here ? `${here.name} | ` : "") + "timewalk";
  list.querySelector(".here")?.scrollIntoView({ block: "nearest", inline: "center" });
}

async function move(to, setAside = false) {
  if (to < 0 || to >= ui.state.steps.length) return;
  hideNotice();
  try {
    await api("/api/move", { to, set_aside: setAside });
    // the server tells every page it moved, this one included; refresh() runs from the event
  } catch (error) {
    if (error.body?.edits?.length && error.message === "uncommitted edits") askAboutEdits(to, error.body.edits);
    else showNotice(error.message, true);
  }
}

function askAboutEdits(to, edits) {
  const notice = $("notice");
  notice.className = "notice";
  notice.replaceChildren();
  const text = document.createElement("span");
  text.textContent = `${edits.length} file${edits.length === 1 ? " has" : "s have"} edits that are not committed: ${edits.slice(0, 4).join(", ")}${edits.length > 4 ? ", ..." : ""}.`;
  const go = document.createElement("button");
  go.textContent = "Set the edits aside and move";
  go.title = "Runs git stash, so the edits can be brought back with git stash pop";
  go.onclick = () => move(to, true);
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
  for (const entry of ui.terms.values()) if (!entry.el.hidden) requestAnimationFrame(() => entry.fit.fit());
  return drawSlide($("slide"), slides[slide], { command: commandButton });   // a $ line on a slide is a button, as in the notes
}

function applyLayout(layout) {
  ui.layout = layout;
  drawSlides();
}

async function showSlide(to) {
  try { await api("/api/slide", { to }); } catch (error) { showNotice(error.message, true); }
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

async function openFile(path, view = "file", scrollTop = true) {
  const file = await api("/api/file?path=" + encodeURIComponent(path));
  // A view with nothing to show falls back to the file, for example once edits are stashed or undone.
  if ((view === "diff" && !file.diff) || (view === "edits" && !file.edits)) view = "file";
  ui.open = path;
  ui.view = view;
  $("file-path").textContent = path;
  for (const name of ["file", "diff", "edits"]) $("view-" + name).setAttribute("aria-selected", String(view === name));
  $("view-diff").disabled = !file.diff;
  $("view-edits").disabled = !file.edits;
  $("view-edits").hidden = !file.edits;
  const body = $("file-body");
  if (view === "edits") body.replaceChildren(drawDiff(file.edits));
  else if (file.missing) body.innerHTML = `<p class="empty">This file does not exist at this step.</p>`;
  else if (file.skipped) body.innerHTML = `<p class="empty">Not shown: ${escapeHtml(file.skipped)}.</p>`;
  else if (view === "diff" && file.diff) body.replaceChildren(drawDiff(file.diff));
  else body.replaceChildren(drawCode(path, file.text));
  if (scrollTop) body.scrollTop = 0;
  document.querySelectorAll("#tree .file").forEach((item) => item.classList.remove("open"));
  drawTree();
}

function drawCode(path, text) {
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
  gutter.textContent = Array.from({ length: Math.max(count, 1) }, (_, i) => i + 1).join("\n");
  const code = document.createElement("pre");
  code.className = "source hljs";
  code.innerHTML = html;
  wrap.append(gutter, code);
  return wrap;
}

function drawDiff(diff) {
  const pre = document.createElement("pre");
  pre.className = "diff";
  for (const line of diff.split("\n")) {
    if (/^(diff --git|index |--- |\+\+\+ )/.test(line)) continue;
    const row = document.createElement("span");
    row.className = "line" + (line.startsWith("@@") ? " hunk" : line.startsWith("+") ? " add" : line.startsWith("-") ? " del" : "");
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
  drawRecipes();
  if (focus) ui.keysToTerminal = true;
  requestAnimationFrame(() => { entry.fit.fit(); if (focus) { entry.term.focus(); entry.sendSize(true); } });
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
  const ws = socket("/ws/term/" + id);
  ws.binaryType = "arraybuffer";
  // A shell has one size. The window you last typed in sets it; a window that only shows the shell leaves it alone,
  // so two windows of different sizes do not fight over it.
  const sendSize = (force = false) => (force || ui.keysToTerminal) && ws.readyState === WebSocket.OPEN &&
    ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
  ws.onopen = () => { fit.fit(); sendSize(true); };
  ws.onmessage = (event) => {
    term.write(typeof event.data === "string" ? event.data : new Uint8Array(event.data));
    // A tab that printed something while another was in front gets a dot, so a finished run is noticed.
    const tab = ui.tabs.find((candidate) => candidate.id === id);
    if (tab && ui.active !== id && !tab.unseen && opened) { tab.unseen = true; drawTabs(); }
  };
  let opened = false;
  setTimeout(() => { opened = true; }, 1500);   // the replay of earlier output on connecting is not news
  ws.onclose = () => term.write("\r\n[disconnected from timewalk]\r\n");
  term.onData((data) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "input", data })));
  term.onResize(sendSize);
  new ResizeObserver(() => { if (!el.hidden) fit.fit(); }).observe(el);
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
  for (const { term, fit, el } of ui.terms.values()) { term.options.fontSize = ui.size; if (!el.hidden) fit.fit(); }
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
  $("slide-prev").onclick = () => showSlide(ui.state.slide - 1);
  $("slide-first").onclick = () => showSlide(0);
  $("slide-next").onclick = () => showSlide(ui.state.slide + 1);
  for (const button of $("layouts").querySelectorAll("button")) button.onclick = () => show({ layout: button.dataset.layout });
  for (const view of ["file", "diff", "edits"]) $("view-" + view).onclick = () => ui.open && show({ path: ui.open, view });
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
    if (event.key === "ArrowRight") { event.preventDefault(); $("next").click(); }
    if (event.key === "ArrowLeft") { event.preventDefault(); $("prev").click(); }
    // With one slide or a document, plain Up and Down are left to scroll.
    // On a document, plain Up and Down scroll it, even when the step has other slides. Alt with them changes slide.
    const onDoc = isDoc(ui.state?.slides?.[ui.state?.slide]);
    const changeSlide = event.altKey || (manySlides && !onDoc);
    if (event.key === "ArrowDown" && changeSlide) { event.preventDefault(); $("slide-next").click(); }
    if (event.key === "ArrowUp" && changeSlide) { event.preventDefault(); $("slide-prev").click(); }
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

function drawTools() {
  const { has_notes: hasNotes, has_slides: hasSlides } = ui.state;
  const shown = hasNotes && !notesHidden.get();
  $("notes-toggle").hidden = !hasNotes;
  $("notes-toggle").setAttribute("aria-pressed", String(shown));
  $("pdf").hidden = !(hasNotes || hasSlides);
  $("notes-pane").hidden = !shown;
  document.body.classList.toggle("with-notes", shown);
  $("cues-toggle").hidden = !shown;
  $("cues-toggle").setAttribute("aria-pressed", String(!cuesHidden.get()));
  document.body.classList.toggle("hide-cues", cuesHidden.get());
  $("run-on-click").checked = settings.get("run-on-click", false);
  for (const entry of ui.terms.values()) if (!entry.el.hidden) requestAnimationFrame(() => entry.fit.fit());
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
  ui.editing = { step: here.name, base: ui.notes[here.name]?.raw || "" };
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
  const { step, base } = ui.editing;
  try {
    await api("/api/notes", { step, base, text: $("notes-text").value });
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
  $("notes-title").textContent = here ? `${here.name}, ${mine?.title || here.subject}` : "between steps";
  $("notes-edit").hidden = !!ui.editing || !here;
  // The prose and the commands, in the order of the notes file: each command is a button where it is written.
  const box = $("notes");
  box.replaceChildren();
  let group = null;   // the buttons of a run of commands, one after another in the file
  for (const part of mine?.parts || []) {
    if (part.kind === "text") {
      const prose = document.createElement("div");
      prose.innerHTML = renderMarkdown(part.text);
      box.append(prose);
      group = null;
      continue;
    }
    if (!group) {
      group = document.createElement("div");
      group.className = "p-commands";
      box.append(group);
    }
    group.append(commandButton(part));
  }
  if (!box.children.length) box.innerHTML = `<p class="p-empty">${!here ? "" : ui.notesPath
    ? `No notes for ${escapeHtml(here.name)} in ${escapeHtml(ui.notesPath.split("/").pop())}. Click Edit to write them, or add a section headed "## ${escapeHtml(here.name)}".`
    : "No notes file. Start timewalk with --notes notes.md, with one \"## step-name\" section per step."}</p>`;
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
  $("clock-start").textContent = started ? "Reset" : "Start the clock";
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
$("notes-toggle").onclick = () => { notesHidden.set(!notesHidden.get()); drawTools(); };
$("cues-toggle").onclick = () => { cuesHidden.set(!cuesHidden.get()); drawTools(); };
$("room").hidden = ROOM;
$("room").onclick = openRoom;
$("fullscreen").hidden = !ROOM || !document.fullscreenEnabled;
$("fullscreen").onclick = () => document.documentElement.requestFullscreen().catch(() => {});
document.addEventListener("fullscreenchange", () => { $("fullscreen").hidden = !ROOM || !!document.fullscreenElement; });
if (ROOM) { document.title = "timewalk room"; document.body.classList.add("room"); }
$("pdf").onclick = makePdf;
$("run-on-click").onchange = () => settings.set("run-on-click", $("run-on-click").checked);
$("notes-edit").onclick = startEditing;
$("notes-cancel").onclick = stopEditing;
$("notes-save").onclick = saveNotes;
setInterval(drawBand, 1000);
applyAppearance();
guardKeys();
wireControls();
await init();
await refresh();
selectTab(ui.state.track || "replay");
if (ui.state.path) { await openFile(ui.state.path, ui.state.view); restorePlaces(ui.state.restore); }
const events = onEvents(async (event) => {
  if (event.type === "moved") { hideNotice(); await refresh(); }
  if (event.type === "edits") {
    await loadTree();
    if (ui.open) await openFile(ui.open, ui.view, false);
  }
  if (event.type === "scroll") { followScroll(event); return; }
  if (event.type === "content") { await reloadContent(); return; }
  if (event.type === "slide") { ui.state = await api("/api/state"); drawSlides(); }
  if (event.type === "notes" && !ui.editing) { await loadNotes(); drawNotes(); drawBand(); }
  if (event.type === "clock") { ui.state = await api("/api/state"); ui.skew = ui.state.now - Date.now() / 1000; drawBand(); }
  if (event.type === "show") {
    if (event.layout && event.layout !== ui.layout) applyLayout(event.layout);
    if (event.path) await openFile(event.path, event.view || "file");
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
