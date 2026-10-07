// Shared by the page and the print page: the token, the API, the event stream, and drawing slides.
import { marked } from "/static/vendor/marked/marked.esm.js";

export const token = new URLSearchParams(location.search).get("t") || "";

/** Call the server. Throws an Error carrying `status` and the response `body` when the call is refused. */
export async function api(path, body) {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token);
  const options = body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({ error: "the server sent something unreadable" }));
  if (!response.ok) throw Object.assign(new Error(data.error || response.statusText), { status: response.status, body: data });
  return data;
}

/** Open a WebSocket to the server, with the token attached. */
export function socket(path) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${scheme}://${location.host}${path}?t=${encodeURIComponent(token)}`);
}

/** Listen for server events (a move, a request to show something), reconnecting if the connection drops.
 *  After a reconnect the handler gets {type: "reconnected"}: what happened meanwhile was missed, so look again.
 *  A refusal (code 4403) means timewalk restarted with a new token: the handler gets {type: "refused"}. */
export function onEvents(handler) {
  let ws;
  let lost = false;
  const connect = () => {
    ws = socket("/ws/events");
    ws.onopen = () => { if (lost) { lost = false; handler({ type: "reconnected" }); } };
    ws.onmessage = (e) => handler(JSON.parse(e.data));
    ws.onclose = (event) => {
      lost = true;
      if (event.code === 4403) { handler({ type: "refused" }); return; }
      setTimeout(connect, 1500);
    };
  };
  connect();
  // send: tell the other windows something, such as a scroll, when the connection is open
  return { send: (message) => ws && ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(message)) };
}

export function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

/** Read and write small settings, tolerating a browser that refuses storage. */
export const settings = {
  get(key, fallback) {
    try { const v = localStorage.getItem("timewalk." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("timewalk." + key, JSON.stringify(value)); } catch { /* not remembered, still works */ }
  },
};

/** Two digits for a step chip: its position in the sequence. */
export function stepLabel(step) {
  return String(step.index).padStart(2, "0");
}

/** Render Markdown for notes and slides. Relative image paths are resolved against `base`. */
export function renderMarkdown(text, base = "") {
  const box = document.createElement("div");
  box.innerHTML = marked.parse(text, { gfm: true });
  for (const img of box.querySelectorAll("img")) {
    const src = img.getAttribute("src") || "";
    if (!/^(https?:|data:|\/)/.test(src)) img.setAttribute("src", base + src);
  }
  for (const link of box.querySelectorAll("a[href]")) { link.target = "_blank"; link.rel = "noopener"; }
  if (window.hljs) for (const code of box.querySelectorAll("pre code")) {
    const language = (code.className.match(/language-([\w-]+)/) || [])[1];
    if (language && window.hljs.getLanguage(language)) code.innerHTML = window.hljs.highlight(code.textContent, { language }).value;
  }
  return box.innerHTML;
}

/** Split a Markdown deck into slides: a line that is exactly --- starts a new slide, except inside fenced code. */
export function splitSlides(text) {
  const slides = [[]];
  let fence = null;
  for (const line of text.split("\n")) {
    const mark = line.match(/^\s*(```+|~~~+)/);
    if (mark) fence = fence === null ? mark[1][0] : (mark[1][0] === fence ? null : fence);
    if (fence === null && line.trim() === "---") slides.push([]);
    else slides[slides.length - 1].push(line);
  }
  const kept = slides.map((lines) => lines.join("\n").trim()).filter(Boolean);
  return kept.length ? kept : [""];
}

/** Whether a manifest entry is a whole Markdown document, scrolled, rather than one slide. */
export function isDoc(entry) {
  return /\.(md|markdown)#doc$/i.test(String(entry || ""));
}

const COMMAND = /^\s*(main|runs[2-9]?)?\$\s+(.+)$/;

/** Split Markdown into prose and commands, in order. A line such as `$ ls`, `runs$ ...` or `main$ ...` is a
 *  command, except inside a fenced code block, where it is code to read. The same rule as the notes. */
export function commandParts(text) {
  const parts = [];
  let fence = null;
  for (const line of text.split("\n")) {
    const mark = line.match(/^\s*(```+|~~~+)/);
    const command = fence === null && !mark ? line.match(COMMAND) : null;
    if (mark) fence = fence === null ? mark[1][0] : (mark[1][0] === fence ? null : fence);
    if (command) parts.push({ kind: "command", track: command[1] || "replay", text: command[2].trim() });
    else if (parts.length && parts.at(-1).kind === "text") parts.at(-1).text += "\n" + line;
    else parts.push({ kind: "text", text: line });
  }
  return parts.filter((part) => part.kind === "command" || part.text.trim());
}

/** Render Markdown into a box. With a `command` function, each command becomes what that function makes, a
 *  button on the page, in its place. Without one, as in print, each run of commands becomes a code block. */
function renderWithCommands(box, text, base, command) {
  const parts = commandParts(text);
  if (!command) {
    const markdown = [];
    for (const [i, part] of parts.entries()) {
      if (part.kind === "text") { markdown.push(part.text); continue; }
      const prefix = part.track === "replay" ? "$ " : `${part.track}$ `;
      if (parts[i - 1]?.kind !== "command") markdown.push("```");
      markdown.push(prefix + part.text);
      if (parts[i + 1]?.kind !== "command") markdown.push("```");
    }
    box.innerHTML = renderMarkdown(markdown.join("\n"), base);
    return;
  }
  box.replaceChildren();
  let group = null;
  for (const part of parts) {
    if (part.kind === "text") {
      const prose = document.createElement("div");
      prose.innerHTML = renderMarkdown(part.text, base);
      box.append(...prose.childNodes);
      group = null;
      continue;
    }
    if (!group) {
      group = document.createElement("div");
      group.className = "p-commands slide-commands";
      box.append(group);
    }
    group.append(command(part));
  }
}

/** Draw one slide into a container. An entry is a path beside the manifest, with an optional #fragment:
 *  `deck.md#3` is the third slide of a Markdown file, `deck.pdf#page=3` a page of a PDF. Resolves when it is drawn. */
export async function drawSlide(container, entry, { command = null } = {}) {
  container.replaceChildren();
  if (!entry) return;
  const [path, fragment] = String(entry).split("#");
  const url = "/slides/" + path.split("/").map(encodeURIComponent).join("/");
  const ext = path.split(".").pop().toLowerCase();
  if (ext === "md" || ext === "markdown") {
    const box = document.createElement("div");
    box.className = fragment === "doc" ? "slide-md slide-doc" : "slide-md";
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`${path} was not found beside the slides manifest`);
      const text = await response.text();
      if (fragment === "doc") {
        // A document is shown whole, as ordinary Markdown: a --- line is a rule, not a new slide.
        renderWithCommands(box, text, url.slice(0, url.lastIndexOf("/") + 1), command);
        container.append(box);
        await Promise.all([...box.querySelectorAll("img")].map((img) => img.decode().catch(() => {})));
        return;
      }
      const slides = splitSlides(text);
      const number = fragment ? Number(fragment) : 1;
      if (!(number >= 1 && number <= slides.length)) throw new Error(`${path} has ${slides.length} slide${slides.length === 1 ? "" : "s"}; the manifest asks for number ${fragment}`);
      renderWithCommands(box, slides[number - 1], url.slice(0, url.lastIndexOf("/") + 1), command);
    } catch (error) { box.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`; }
    container.append(box);
    await Promise.all([...box.querySelectorAll("img")].map((img) => img.decode().catch(() => {})));
  } else if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) {
    const img = document.createElement("img");
    img.src = url;
    img.alt = path;
    container.append(img);
    await img.decode().catch(() => {});
  } else {
    const frame = document.createElement("iframe");
    const pdf = ext === "pdf";
    frame.src = url + (fragment ? "#" + fragment + (pdf ? "&toolbar=0&navpanes=0&view=Fit" : "") : pdf ? "#toolbar=0&navpanes=0&view=Fit" : "");
    frame.title = path;
    const loaded = new Promise((resolve) => { frame.onload = resolve; setTimeout(resolve, 4000); });
    container.append(frame);
    await loaded;
  }
}
