// Shared by the audience page and the presenter page: the token, the API, and the event stream.
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

/** Listen for server events (a move, a request to show something), reconnecting if the server restarts. */
export function onEvents(handler) {
  let ws;
  const connect = () => {
    ws = socket("/ws/events");
    ws.onmessage = (e) => handler(JSON.parse(e.data));
    ws.onclose = () => setTimeout(connect, 1500);
  };
  connect();
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

/** Draw one slide into a container. An entry is a path beside the manifest, with an optional #fragment:
 *  `deck.md#3` is the third slide of a Markdown file, `deck.pdf#page=3` a page of a PDF. Resolves when it is drawn. */
export async function drawSlide(container, entry) {
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
        box.innerHTML = renderMarkdown(text, url.slice(0, url.lastIndexOf("/") + 1));
        container.append(box);
        await Promise.all([...box.querySelectorAll("img")].map((img) => img.decode().catch(() => {})));
        return;
      }
      const slides = splitSlides(text);
      const number = fragment ? Number(fragment) : 1;
      if (!(number >= 1 && number <= slides.length)) throw new Error(`${path} has ${slides.length} slide${slides.length === 1 ? "" : "s"}; the manifest asks for number ${fragment}`);
      box.innerHTML = renderMarkdown(slides[number - 1], url.slice(0, url.lastIndexOf("/") + 1));
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
