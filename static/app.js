// The projector page, and the presenter page: the same page, which on /presenter adds a clock band and the notes.
// What both show (step, slide, layout, open file and view, terminal tab) is kept by the server, so they agree.
import { init, Terminal, FitAddon } from "/static/vendor/ghostty-web/ghostty-web.js";
import { api, socket, onEvents, escapeHtml, settings, stepLabel, drawSlide, isDoc, renderMarkdown } from "/static/common.js";

const $ = (id) => document.getElementById(id);
const PRESENTER = location.pathname.startsWith("/presenter");
const PAGE = Math.random().toString(36).slice(2);   // tells this page's own requests apart from the other page's
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
  notes: {},              // the presenter's notes, on the presenter page only
  notesPath: null,
  skew: 0,                // the server's clock minus this one's
};

// ---------- steps ----------

async function refresh() {
  ui.state = await api("/api/state");
  ui.layout = ui.state.layout;
  ui.skew = ui.state.now - Date.now() / 1000;
  drawSteps();
  drawSlides();
  await Promise.all([loadTree(), loadRecipes(), PRESENTER ? loadNotes() : null]);
  if (ui.open) await openFile(ui.open, ui.view, false);
  if (PRESENTER) { drawNotes(); drawBand(); }
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
  document.title = (here ? `${here.name} · ` : "") + (PRESENTER ? "timewalk presenter" : "timewalk");
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
  $("slide-next").disabled = slide >= slides.length - 1;
  drawSlide($("slide"), slides[slide]);
  for (const entry of ui.terms.values()) if (!entry.el.hidden) requestAnimationFrame(() => entry.fit.fit());
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
  if (!list.length) {
    const none = document.createElement("span");
    none.className = "label";
    none.textContent = "no justfile here";
    holder.append(none);
    return;
  }
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
  requestAnimationFrame(() => { entry.fit.fit(); if (focus) entry.term.focus(); });
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
  // The projector sets the shell's size. The presenter page shows the same shell and leaves its size alone.
  const sendSize = () => !PRESENTER && ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
  ws.onopen = () => { fit.fit(); sendSize(); };
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
  const entry = { term, fit, ws, el };
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
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    // Alt+arrows work everywhere, a terminal included. Plain arrows work unless a terminal or a text field has the keys.
    if (!event.altKey) {
      const target = event.target instanceof Element ? event.target : document.body;
      if (target.closest(".term-pane") || target.closest("textarea, select, [contenteditable], input:not([type=checkbox]):not([type=radio])")) return;
    }
    const manySlides = (ui.state?.slides?.length || 0) > 1;
    if (event.key === "ArrowRight") { event.preventDefault(); $("next").click(); }
    if (event.key === "ArrowLeft") { event.preventDefault(); $("prev").click(); }
    // With one slide or a document, plain Up and Down are left to scroll.
    if (event.key === "ArrowDown" && (manySlides || event.altKey)) { event.preventDefault(); $("slide-next").click(); }
    if (event.key === "ArrowUp" && (manySlides || event.altKey)) { event.preventDefault(); $("slide-prev").click(); }
    if (event.defaultPrevented) event.stopPropagation();   // a key used here is not also typed into a terminal
  }, true);   // capture: seen before the terminal, which keeps the keys it handles to itself
  // drag the bar between the reader and the terminal
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

// ---------- the presenter page: notes and the clock ----------

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
  $("notes-title").textContent = here ? `${here.name} · ${mine?.title || here.subject}` : "between steps";
  if (mine?.text) $("notes").innerHTML = renderMarkdown(mine.text);
  else $("notes").innerHTML = `<p class="p-empty">${ui.notesPath
    ? `No notes for ${escapeHtml(here?.name || "this step")} in ${escapeHtml(ui.notesPath)}. Add a section headed "## ${escapeHtml(here?.name || "step-name")}".`
    : "No notes file. Start timewalk with --notes notes.md, with one \"## step-name\" section per step."}</p>`;
  const commands = $("commands");
  commands.replaceChildren();
  for (const command of mine?.commands || []) {
    const button = document.createElement("button");
    const text = document.createElement("span");
    text.textContent = command.text;
    const track = document.createElement("span");
    track.className = "track";
    const extra = /^runs([2-9])$/.exec(command.track);
    track.textContent = extra ? `Runs ${extra[1]}` : { main: "Main", runs: "Runs" }[command.track] || "at this step";
    button.append(text, track);
    button.title = "Types this into that terminal and runs it, on both pages";
    button.onclick = () => api("/api/type", { track: command.track, text: command.text }).catch((error) => showNotice(error.message, true));
    commands.append(button);
  }
  if (!commands.children.length) commands.innerHTML = `<span class="p-empty">None. In the notes, "$ " starts a command at this step, "runs$ " one for the Runs tab, "main$ " one in Main.</span>`;
}

function drawBand() {
  if (!ui.state) return;
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

if (PRESENTER) {
  document.body.classList.add("presenter");
  $("band").hidden = false;
  $("notes-pane").hidden = false;
  $("clock-start").onclick = () => api("/api/clock", { action: ui.state.clock ? "reset" : "start" });
  setInterval(drawBand, 1000);
}
applyAppearance();
guardKeys();
wireControls();
await init();
await refresh();
selectTab(ui.state.track || "replay");
if (ui.state.path) await openFile(ui.state.path, ui.state.view);
onEvents(async (event) => {
  if (event.type === "moved") { hideNotice(); await refresh(); }
  if (event.type === "edits") {
    await loadTree();
    if (ui.open) await openFile(ui.open, ui.view, false);
  }
  if (event.type === "slide") { ui.state = await api("/api/state"); drawSlides(); }
  if (event.type === "clock" && PRESENTER) { ui.state = await api("/api/state"); ui.skew = ui.state.now - Date.now() / 1000; drawBand(); }
  if (event.type === "show") {
    if (event.layout && event.layout !== ui.layout) applyLayout(event.layout);
    if (event.path) await openFile(event.path, event.view || "file");
    // The page that asked keeps the keys it had. A command sent from the presenter page gives the projector's terminal the keys.
    if (event.track) selectTab(event.track, event.from === PAGE || (event.focus === "audience" && !PRESENTER));
  }
});
