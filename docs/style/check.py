"""Check Markdown prose against the measurable rules in GUIDE.md.

    python3 check.py page.md [page.md ...]      report each page; exit 1 if a hard rule is broken
    python3 check.py -v page.md                 also show the soft notes, for a person to judge

The checks read prose only: paragraphs, list items and headings. Code blocks, inline code, tables, link
targets, URLs, image lines, HTML and text in straight double quotes are not checked. Markdown comes first,
and quoted words are examples that the text talks about. A program cannot judge meaning, tone or a missing definition, so read the page too.
"""

import re
import sys
from pathlib import Path

SIMPLER = {
    "allow": "let", "allows": "lets", "enable": "let", "enables": "lets", "ensure": "make sure",
    "utilize": "use", "via": "through", "approximately": "about", "provide": "give", "provides": "gives",
    "perform": "do", "launch": "start", "prior": "before", "obtain": "get", "additional": "more",
    "however": "but", "therefore": "so", "whereas": "but", "although": "but", "unless": "if ... not",
    "simply": "(remove)", "just": "(remove)", "really": "(remove)", "actually": "(remove)",
    "basically": "(remove)", "quite": "(remove)", "crucial": "(remove)", "pivotal": "(remove)",
    "robust": "(a fact)", "seamless": "(a fact)", "seamlessly": "(a fact)", "powerful": "(a fact)",
    "delve": "look at", "leverage": "use", "landscape": "(a fact)", "tapestry": "(a fact)",
    "unlock": "(a fact)", "elevate": "(a fact)", "empower": "let", "matters": "(a fact)",
}
ING_OK = {"during", "nothing", "something", "anything", "everything", "thing", "things", "string", "strings",
          "bring", "ring", "spring", "morning", "evening", "heading", "headings", "setting", "settings",
          "warning", "warnings", "padding", "missing", "pending", "ending"}
PASSIVE = re.compile(r"\b(is|are|was|were|be|been|being)\s+(\w+ed|made|shown|kept|read|written|run|done|given|taken|"
                     r"seen|set|put|built|sent|found|left|thrown|known|held|drawn|told|hidden)\b", re.I)
COMMAND = re.compile(r"^(add|ask|change|check|choose|click|close|copy|delete|do|give|go|keep|look|make|move|open|"
                     r"press|put|read|remove|restart|run|see|select|send|set|show|start|stop|take|tell|try|type|use|"
                     r"write|edit|install|clone|follow|name|point|rehearse|drag|pass|leave)\b", re.I)
COUNT = re.compile(r"^(two|three|four|five|six|seven|several|a few)\s+\w+(\s+\w+)?[.:]", re.I)
VAGUE = re.compile(r"^(this|that|these|those)\s+(is|was|are|were|means|meant|makes|made|lets|shows|gives|keeps|"
                   r"says|does|did|can|will|would|should|matters|helps)\b", re.I)
CLAUSE = re.compile(r",\s+(and|but|so|which|because|while|when|where|if|or)\b|\b(because|which|while|whereas)\b", re.I)


def blocks(text: str) -> list[tuple[int, str, str]]:  # (line, text, kind) with kind "para", "item", "cell" or "head"
    "Read the prose of a page: paragraphs, list items and headings. Code and tables are left out."
    out, block, start, in_code, kind = [], [], 0, False, "para"

    def flush() -> None:
        nonlocal block
        if block:
            out.append((start, " ".join(block), kind))
        block = []

    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            flush()
            continue
        if in_code or not stripped or stripped.startswith("![") or stripped.startswith("<"):
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            out.append((number, stripped.lstrip("#").strip(), "head"))
            continue
        if stripped.startswith("|"):
            flush()
            continue  # tables are exempt: a cell can be a short phrase, a fragment or a value
        item = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.*)", line)
        if item:
            flush()
            start, kind, block = number, "item", [item.group(1)]
            continue
        if not block:
            start, kind = number, "para"
        block.append(stripped)
    flush()
    return out


def plain(text: str) -> str:  # Prose with inline code as one word, link text kept, markup gone
    "Reduce Markdown inline syntax to plain words."
    text = re.sub(r"`[^`]*`", "CODE", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "URL", text)
    text = re.sub(r'"[^"\n]*"', "QUOTE", text)  # quoted words are examples being talked about, not prose
    return re.sub(r"[*_]", "", text)


def sentences(text: str) -> list[str]:  # The sentences of a block
    "Split after a full stop, question mark or exclamation mark that ends a sentence."
    # A new sentence can start in lower case, as with a name like "timewalk" or "git".
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Za-z\"(])", text) if s.strip()]


def check(path: Path) -> tuple[list[str], list[str]]:  # Hard problems, soft notes
    "Check one page."
    hard, soft = [], []
    for line, raw, kind in blocks(path.read_text(encoding="utf-8")):
        text = plain(raw)
        where = f"{line}"
        for mark, name in (("—", "an em dash"), ("–", "an en dash"), ("·", "a middle dot"),
                           ("“", "a curly quote"), ("”", "a curly quote"), ("‘", "a curly quote"), ("’", "a curly quote")):
            if mark in text:
                hard.append(f"{where}: {name}")
        if kind == "head":
            if COUNT.match(text):
                hard.append(f"{where}: a heading that opens with a count: {text}")
            continue
        found = sentences(text)
        if kind == "para" and len(found) > 6:
            hard.append(f"{where}: a paragraph of {len(found)} sentences (6 at most)")
        if kind in ("para", "item") and found and COUNT.match(found[0]):
            hard.append(f"{where}: opens with a count: {found[0][:60]}")
        questions = 0
        for sentence in found:
            words = re.findall(r"[A-Za-z0-9][\w'\-./]*", sentence)
            limit = 20 if COMMAND.match(sentence) else 25
            if len(words) > limit:
                hard.append(f"{where}: {len(words)} words (at most {limit}): {sentence[:80]}")
            if VAGUE.match(sentence):
                hard.append(f"{where}: a vague demonstrative: {sentence[:60]}")
            colon = re.search(r"^(.*?):\s+\S", sentence)
            if colon and len(colon.group(1).split()) > 3 and kind != "cell":
                hard.append(f"{where}: a colon between clauses: {sentence[:80]}")
            if len(CLAUSE.findall(sentence)) >= 2:
                soft.append(f"{where}: maybe three clauses: {sentence[:80]}")
            questions = questions + 1 if sentence.endswith("?") else 0
            if questions == 2:
                hard.append(f"{where}: two questions in a row")
        for word in re.findall(r"\b[A-Za-z]+n't\b|\b[A-Za-z]+'(?:re|ll|ve|d|m)\b|\b(?:it|that|there|here|what|who)'s\b", text, re.I):
            hard.append(f"{where}: a contraction: {word}")
        for word in re.findall(r"\b(?:e\.g|i\.e|etc)\b\.?", text):
            hard.append(f"{where}: write 'for example' or say it in words, not {word}")
        if re.search(r"\bnot (just|only)\b", text, re.I):
            hard.append(f"{where}: 'not just' or 'not only': state what the thing is")
        for word in re.findall(r"\b[A-Za-z]+\b", text):
            key = word.lower()
            if key in SIMPLER:
                soft.append(f"{where}: '{word}': try {SIMPLER[key]}")
            if key.endswith("ing") and key not in ING_OK and len(key) > 4:
                soft.append(f"{where}: an -ing form: {word}")
        for match in PASSIVE.finditer(text):
            soft.append(f"{where}: maybe passive: {match.group(0)}")
        if ";" in text:
            soft.append(f"{where}: a semicolon")
    return hard, soft


def main() -> None:
    "Check every page named on the command line."
    verbose = "-v" in sys.argv
    failed = False
    for name in (a for a in sys.argv[1:] if a != "-v"):
        hard, soft = check(Path(name))
        print(f"{name}: {len(hard)} hard, {len(soft)} soft")
        for problem in hard:
            print("  HARD " + problem)
        if verbose:
            for note in soft:
                print("  soft " + note)
        failed = failed or bool(hard)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
