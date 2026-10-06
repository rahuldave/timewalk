// Lays out every slide of the deck, one per page, for printing to PDF. Pages of a PDF deck are not drawn here:
// the exporter copies those pages straight from their file. A document (an entry ending in #doc) is not drawn
// here either: the exporter prints each one separately, as /print?doc=entry, so that it can run over several pages.
// The notes of a step are printed the same way, as /print?notes=step.
// A brand (deck.brand, from timewalk-pdf --brand) adds its font, colours, footer and logo, a cover page and a
// page of one colour before each step. Without one, the pages are drawn as they always were.
import { drawSlide, isDoc, renderMarkdown } from "/static/common.js";

const deck = await (await fetch("/api/deck")).json();
document.title = deck.title || "Slides";
const brand = deck.brand || {};
const look = brand.brand || {};
if (deck.brand) await applyBrand();
const only = new URLSearchParams(location.search).get("doc");
const notesOf = new URLSearchParams(location.search).get("notes");
if (notesOf) {
  // One step's notes, flowing from page to page: the prose, the cues in their own shade, the commands as code.
  const step = deck.steps.find((s) => s.name === notesOf) || { name: notesOf, title: "", notes: "" };
  const margins = document.createElement("style");
  margins.textContent = "@page { margin: 40px 0 36px; }";
  document.head.append(margins);
  const head = document.createElement("p");
  head.className = "doc-head";
  head.textContent = [deck.title, step.name, step.title, "notes"].filter(Boolean).join(" | ");
  const box = document.createElement("div");
  box.className = "doc-print notes-print";
  const text = document.createElement("div");
  text.className = "slide-md slide-doc";
  text.innerHTML = renderMarkdown(step.notes || "");
  box.append(text);
  document.body.append(head, box);
} else if (only) {
  // One document, flowing from page to page, headed by the step it belongs to.
  const step = deck.steps.find((s) => s.slides.includes(only)) || { name: "", title: "" };
  const margins = document.createElement("style");
  margins.textContent = "@page { margin: 40px 0 36px; }";
  document.head.append(margins);
  const head = document.createElement("p");
  head.className = "doc-head";
  head.textContent = [deck.title, step.name, step.title].filter(Boolean).join(" · ");
  const box = document.createElement("div");
  box.className = "doc-print";
  document.body.append(head, box);
  await drawSlide(box, only);
} else {
  if (brand.cover) drawCover();
  let number = 0;
  for (const step of deck.steps) {
    if (step.slides.length && brand.divider?.color) drawDivider(step);
    for (const [i, entry] of step.slides.entries()) {
      number += 1;
      if (entry.split("#")[0].toLowerCase().endsWith(".pdf") || isDoc(entry)) continue;
      const page = document.createElement("section");
      page.className = "page";
      const slide = document.createElement("div");
      slide.className = "slide";
      const footer = document.createElement("footer");
      for (const text of [look.footer ?? (deck.title || ""), [step.name, step.title].filter(Boolean).join(" · ") + (step.slides.length > 1 ? `  (${i + 1} of ${step.slides.length})` : ""), String(number)]) {
        const part = document.createElement("span");
        part.textContent = text;
        footer.append(part);
      }
      page.append(slide, footer);
      if (look.logo && look.logo_on === "every") page.append(logo("brand-logo-small"));
      document.body.append(page);
      await drawSlide(slide, entry);
      // A slide with too much on it is shrunk until it fits its page, instead of being cut off.
      const text = slide.querySelector(".slide-md");
      const spills = () => text.scrollHeight > text.clientHeight + 1 || [...text.querySelectorAll("pre, table")].some((el) => el.scrollWidth > el.clientWidth + 1 || el.offsetWidth > text.clientWidth);
      for (let size = 27; text && spills() && size > 13; size -= 1) text.style.fontSize = size - 1 + "px";
    }
  }
}
await document.fonts.ready;
document.body.dataset.ready = "1";

// The brand's style, and its font: a file in the brand folder is loaded here, a name must be on this machine.
async function applyBrand() {
  const rules = [];
  if (look.font) {
    let family = look.font;
    if (/\.(ttf|otf|woff2?)$/i.test(look.font)) {
      family = "brand-font";
      try {
        const face = new FontFace(family, `url(${look.font})`, { weight: "100 900" });
        document.fonts.add(await face.load());
      } catch {
        document.body.dataset.problem = `the brand's font file ${decodeURIComponent(look.font.replace("/brand/", ""))} is not a font that the browser can read`;
      }
    } else if (!installed(family)) {
      document.body.dataset.problem = `the brand's font "${family}" is not on this machine. Install it, or put its font file in the brand folder and name the file in font`;
    }
    rules.push(`.page, .page .slide-md, .doc-print .slide-md, .doc-head { font-family: "${family}", sans-serif; }`);
  }
  if (look.text_color) rules.push(`.page .slide-md, .doc-print .slide-md { color: ${look.text_color}; }`);
  if (look.title_color) {
    rules.push(`.page .slide-md h1, .page .slide-md h2, .page .slide-md h3, .doc-print .slide-md h1, .doc-print .slide-md h2 { color: ${look.title_color}; font-weight: 400; }`);
    // The title at the top of a slide gets a thin rule under it, across the page, as in a classic deck.
    rules.push(".page .slide-md > :is(h1, h2):first-child { margin: -26px -64px 0.6em; padding: 0 48px 10px; border-bottom: 1.5px solid #404040; }");
  }
  rules.push(
    ".brand-cover { justify-content: center; align-items: center; text-align: center; gap: 0; }",
    ".brand-cover h1 { font-size: 44px; font-weight: 400; line-height: 1.25; margin: 0 120px 48px; text-wrap: balance; color: #000; }",
    `.brand-cover .box { background: ${look.accent || "#333"}; color: #fff; font-size: 32px; padding: 10px 26px; margin-bottom: 26px; }`,
    ".brand-cover .line { font-size: 27px; color: #222; } .brand-cover .line ~ .line { font-size: 17px; }",
    ".brand-logo { position: absolute; left: 30px; bottom: 30px; width: 80px; }",
    ".brand-logo-small { position: absolute; left: 20px; bottom: 12px; height: 28px; }",
    ".page:has(.brand-logo-small) footer { padding-left: 64px; }",
    `.brand-divider { background: ${brand.divider?.color || "#fff"}; color: #fff; justify-content: center; padding: 0 96px; }`,
    ".brand-divider .name { font-size: 22px; opacity: 0.75; } .brand-divider h1 { font-size: 52px; font-weight: 400; line-height: 1.2; margin: 8px 0 0; text-wrap: balance; }",
  );
  if (look.footer) rules.push(".page footer span:first-child { font-variant: small-caps; letter-spacing: 0.04em; }");
  const style = document.createElement("style");
  style.textContent = rules.join("\n");
  document.head.append(style);
}

// Whether the browser has a font of this name: text in it is as wide as in a fallback only when it is missing.
function installed(family) {
  const probe = document.createElement("canvas").getContext("2d");
  const width = (font) => { probe.font = `40px ${font}`; return probe.measureText("mmmwwwlliI0O@#").width; };
  return ["monospace", "serif"].some((fallback) => width(`"${family}", ${fallback}`) !== width(fallback));
}

function logo(className) {
  const img = document.createElement("img");
  img.className = className;
  img.src = look.logo;
  img.alt = "";
  return img;
}

// The first page: the title, a line in a box of the accent colour, lines under it, and the logo.
function drawCover() {
  const page = document.createElement("section");
  page.className = "page brand-cover";
  const title = document.createElement("h1");
  title.textContent = brand.cover.title ?? deck.title ?? "";
  page.append(title);
  if (brand.cover.box) {
    const box = document.createElement("div");
    box.className = "box";
    box.textContent = brand.cover.box;
    page.append(box);
  }
  for (const text of brand.cover.lines || []) {
    const line = document.createElement("div");
    line.className = "line";
    line.textContent = text;
    page.append(line);
  }
  if (look.logo) page.append(logo("brand-logo"));
  document.body.append(page);
}

// A page of one colour before a step, to tell the steps apart, with the step's title on it unless the brand says not.
function drawDivider(step) {
  const page = document.createElement("section");
  page.className = "page brand-divider";
  if (brand.divider.title !== false) {
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = step.name;
    const title = document.createElement("h1");
    title.textContent = step.title || "";
    page.append(name, title);
  }
  document.body.append(page);
}
