// Lays out every slide of the deck, one per page, for printing to PDF. Pages of a PDF deck are not drawn here:
// the exporter copies those pages straight from their file. A document (an entry ending in #doc) is not drawn
// here either: the exporter prints each one separately, as /print?doc=entry, so that it can run over several pages.
// The notes of a step are printed the same way, as /print?notes=step.
import { drawSlide, isDoc, renderMarkdown } from "/static/common.js";

const deck = await (await fetch("/api/deck")).json();
document.title = deck.title || "Slides";
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
  let number = 0;
  for (const step of deck.steps) {
    for (const [i, entry] of step.slides.entries()) {
      number += 1;
      if (entry.split("#")[0].toLowerCase().endsWith(".pdf") || isDoc(entry)) continue;
      const page = document.createElement("section");
      page.className = "page";
      const slide = document.createElement("div");
      slide.className = "slide";
      const footer = document.createElement("footer");
      for (const text of [deck.title || "", [step.name, step.title].filter(Boolean).join(" · ") + (step.slides.length > 1 ? `  (${i + 1} of ${step.slides.length})` : ""), String(number)]) {
        const part = document.createElement("span");
        part.textContent = text;
        footer.append(part);
      }
      page.append(slide, footer);
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
