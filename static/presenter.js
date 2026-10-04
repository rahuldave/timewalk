// The presenter page: private notes, a clock, and controls that change what the audience page shows.
import { api, onEvents, escapeHtml, settings, renderMarkdown, drawSlide, isDoc } from "/static/common.js";

const $ = (id) => document.getElementById(id);
const ui = { state: null, notes: {}, notesPath: null, tree: null, skew: 0 };

document.documentElement.dataset.theme = settings.get("theme", "light");
document.documentElement.style.setProperty("--size", settings.get("size", 14) + "px");

function clockText(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

async function refresh() {
  const [state, notes, tree] = await Promise.all([api("/api/state"), api("/api/notes"), api("/api/tree")]);
  ui.state = state;
  ui.notes = notes.notes;
  ui.notesPath = notes.path;
  ui.tree = tree;
  ui.skew = state.now - Date.now() / 1000;
  draw();
}

function draw() {
  const { steps, current } = ui.state;
  const here = current === null ? null : steps[current];
  const next = current === null ? null : steps[current + 1];
  const mine = here ? ui.notes[here.name] : null;

  $("step-name").textContent = here ? here.name : "between steps";
  $("step-subject").textContent = here ? (mine?.title || here.subject) : "The working copy is not at one of the steps.";
  $("prev").disabled = current === null || current === 0;
  $("next").disabled = !next;
  document.title = here ? `${here.name} · presenter` : "timewalk presenter";

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
    track.textContent = extra ? `in the Runs ${extra[1]} tab` : { main: "in main", runs: "in the Runs tab" }[command.track] || "at this step";
    button.append(text, track);
    button.title = "Types this into the terminal the audience sees, and runs it";
    button.onclick = () => act(() => api("/api/type", { track: command.track, text: command.text }));
    commands.append(button);
  }
  if (!commands.children.length) commands.innerHTML = `<span class="p-empty">None. In the notes, a line starting with "$ " is a command at this step, "runs$ " one for the Runs tab, and "main$ " one in the main repository.</span>`;

  const files = $("files");
  files.replaceChildren();
  const edited = new Set(ui.tree.edits);
  for (const file of ui.tree.files.filter((f) => f.status || edited.has(f.path))) {
    const row = document.createElement("div");
    row.className = "row";
    const badge = document.createElement("span");
    badge.className = "badge " + (file.status || "E");
    badge.textContent = file.status === "A" ? "new" : file.status ? "changed" : "edited";
    const path = document.createElement("span");
    path.className = "path";
    path.textContent = file.path;
    path.title = file.path;
    const show = document.createElement("button");
    show.textContent = "Show";
    show.onclick = () => act(() => api("/api/show", { path: file.path, view: "file" }));
    row.append(badge, path, show);
    if (file.status === "M") {
      const changes = document.createElement("button");
      changes.textContent = "Changes";
      changes.onclick = () => act(() => api("/api/show", { path: file.path, view: "diff" }));
      row.append(changes);
    }
    if (edited.has(file.path)) {
      const edits = document.createElement("button");
      edits.textContent = "Edits";
      edits.title = "Shows what commands run at this step have changed in this file, not committed";
      edits.onclick = () => act(() => api("/api/show", { path: file.path, view: "edits" }));
      row.append(edits);
    }
    files.append(row);
  }
  if (!files.children.length) files.innerHTML = `<span class="p-empty">This step changed no files, and none are edited.</span>`;

  const { slides = [], slide = 0 } = ui.state;
  $("slides-card").hidden = !ui.state.has_slides;
  const doc = isDoc(slides[slide]) && slides.length === 1;
  $("slide-prev").hidden = $("slide-next").hidden = doc;
  $("slide-count").textContent = doc ? "a document, scrolled on the projector" : slides.length ? `${slide + 1} of ${slides.length}` : "none for this step";
  $("slide-prev").disabled = slide <= 0;
  $("slide-next").disabled = slide >= slides.length - 1;
  $("slide-list").replaceChildren(...slides.map((entry, i) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = `${i + 1}. ${entry}`;
    button.setAttribute("aria-current", String(i === slide));
    button.onclick = () => act(() => api("/api/slide", { to: i }));
    item.append(button);
    return item;
  }));
  $("slide-preview").hidden = !slides.length;
  drawSlide($("slide-preview"), slides[slide]);

  $("public").textContent = here?.note || "Nothing: this step's tag has no message.";
  const nextNotes = next ? ui.notes[next.name] : null;
  $("upcoming").innerHTML = next
    ? `<span class="step-name">${escapeHtml(next.name)}</span> ${escapeHtml(nextNotes?.title || next.subject)}` +
      (nextNotes?.time != null ? ` <span class="plan">planned at ${clockText(nextNotes.time)}</span>` : "")
    : "This is the last step.";
  drawClock();
}

function drawClock() {
  if (!ui.state) return;
  const started = ui.state.clock;
  const { steps, current } = ui.state;
  const here = current === null ? null : steps[current];
  const next = current === null ? null : steps[current + 1];
  const elapsed = started ? Date.now() / 1000 + ui.skew - started : 0;
  $("elapsed").textContent = clockText(elapsed);
  $("clock-start").textContent = started ? "Reset" : "Start the clock";
  const planned = here ? ui.notes[here.name]?.time : null;
  const nextPlanned = next ? ui.notes[next.name]?.time : null;
  const plan = $("plan");
  if (!started || planned == null) { plan.className = "plan"; plan.textContent = planned != null ? `this step is planned at ${clockText(planned)}` : ""; return; }
  if (nextPlanned != null && elapsed > nextPlanned) { plan.className = "plan late"; plan.textContent = `${clockText(elapsed - nextPlanned)} over: the next step was due at ${clockText(nextPlanned)}`; }
  else if (nextPlanned != null) { plan.className = "plan"; plan.textContent = `${clockText(nextPlanned - elapsed)} left in this step`; }
  else { plan.className = "plan"; plan.textContent = `started at ${clockText(planned)}`; }
}

async function act(call) {
  $("notice").hidden = true;
  try { await call(); }
  catch (error) {
    const notice = $("notice");
    notice.className = "notice error";
    notice.replaceChildren();
    if (error.body?.edits?.length && error.message === "uncommitted edits") {
      const text = document.createElement("span");
      text.textContent = `The working copy has uncommitted edits in ${error.body.edits.slice(0, 4).join(", ")}.`;
      const go = document.createElement("button");
      go.textContent = "Set the edits aside and move";
      go.onclick = () => act(() => api("/api/move", { to: ui.pendingMove, set_aside: true }));
      notice.append(text, go);
    } else notice.textContent = error.message;
    notice.hidden = false;
  }
}

function move(to) {
  ui.pendingMove = to;
  return act(() => api("/api/move", { to }));
}

$("prev").onclick = () => move(ui.state.current - 1);
$("next").onclick = () => move(ui.state.current + 1);
$("slide-prev").onclick = () => act(() => api("/api/slide", { to: ui.state.slide - 1 }));
$("slide-next").onclick = () => act(() => api("/api/slide", { to: ui.state.slide + 1 }));
for (const button of document.querySelectorAll("[data-layout]")) button.onclick = () => act(() => api("/api/show", { layout: button.dataset.layout }));
$("clock-start").onclick = () => act(async () => { await api("/api/clock", { action: ui.state.clock ? "reset" : "start" }); });
document.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.target.closest("input, textarea")) return;
  // The arrows advance through the slides of a step and then on to the next step. With Shift they move a whole step.
  const moreSlides = !event.shiftKey && !$("slide-next").disabled, earlierSlides = !event.shiftKey && !$("slide-prev").disabled;
  if (event.key === "ArrowRight") { event.preventDefault(); if (moreSlides) $("slide-next").click(); else if (!$("next").disabled) $("next").click(); }
  if (event.key === "ArrowLeft") { event.preventDefault(); if (earlierSlides) $("slide-prev").click(); else if (!$("prev").disabled) $("prev").click(); }
});

await refresh();
setInterval(drawClock, 1000);
onEvents((event) => { if (["moved", "clock", "slide", "edits"].includes(event.type)) refresh(); });
