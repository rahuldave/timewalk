// Lays out every slide of the deck, one per page, for printing to PDF. Pages of a PDF deck are not drawn here:
// the exporter copies those pages straight from their file.
import { drawSlide } from "/static/common.js";

const deck = await (await fetch("/api/deck")).json();
document.title = deck.title || "Slides";
let number = 0;
for (const step of deck.steps) {
  for (const [i, entry] of step.slides.entries()) {
    number += 1;
    if (entry.split("#")[0].toLowerCase().endsWith(".pdf")) continue;
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
await document.fonts.ready;
document.body.dataset.ready = "1";
