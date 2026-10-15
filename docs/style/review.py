"""Review the prose that changed against the writing guide, with a model, and fail on any finding.

check.py checks the form of the text: the length of a sentence, a vague word, a colon between clauses. This script
checks what check.py cannot: whether a reader of 12 can follow the text. It runs `claude -p`, headless, with the
guide, the glossary, the changed text, and the reviewer's prompt in docs/style/REVIEW.md, a copy of
walk-kit's style/REVIEW.md. It may read the code, to check what the text says of it, and nothing
else. A text that passed is kept in a cache, so a second run
of the same text costs nothing.

    python3 docs/style/review.py             # the files of `just style` that changed since the last commit
    python3 docs/style/review.py FILE ...    # the changes in these files
    python3 docs/style/review.py --all FILE  # these files whole
"""

import argparse
import glob
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STYLE = Path(__file__).resolve().parent
FILES = ["docs/*.md", "README.md", "CLAUDE.md", "demo/notes.md", "demo/walks/*/notes.md", "demo/slides/*.md"]   # as `just style`
CACHE = Path.home() / ".cache" / "timewalk" / "style-review"
MODEL = "opus"
ROUNDS = 3  # the review of a file fails at most this many times between two commits; then its findings go to a shadow file
FINDINGS = ROOT / "review-findings"  # one report for each reviewed file, ignored by git

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"},
            "quote": {"type": "string", "description": "The words at fault, as written"},
            "problem": {"type": "string", "description": "What a reader of 12 cannot follow, and why"},
            "rewrite": {"type": "string", "description": "The text as it should read"},
        }, "required": ["file", "quote", "problem", "rewrite"]}},
    },
    "required": ["verdict", "findings"],
}



def git(
    *args: str,  # Arguments to git
    cwd: Path = ROOT,  # Where to run it: the top of timewalk, unless a file of another repository is reviewed
) -> str:  # What git printed
    "Run git, at the top of timewalk unless told otherwise."
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def in_scope() -> list[str]:  # The files that `just style` checks, relative to the top
    "List the files that `just style` checks."
    return sorted({str(Path(found).relative_to(ROOT)) for pattern in FILES for found in glob.glob(str(ROOT / pattern))})


def changed(
    files: list[str],  # Files to look at, relative to the top
) -> list[str]:  # Those that differ from the last commit, or that git does not track
    "Find the files that changed since the last commit."
    tracked = set(git("diff", "--name-only", "-z", "HEAD", "--", *files).split("\0")) if files else set()
    untracked = set(git("ls-files", "--others", "--exclude-standard", "-z", "--", *files).split("\0")) if files else set()
    return [f for f in files if f in tracked | untracked]


def text_of(
    file: str,  # A file, relative to the top
    whole: bool,  # True for the whole file, False for its change since the last commit
) -> str | None:  # The text to review, with the file's name; None if the file did not change
    """Give a file's change since the last commit, or the whole file if git does not track it or if `whole` is set. A file
    may be in another repository, such as walk-kit, or in none."""
    path = (ROOT / file).resolve()
    try:
        tracked = not whole and git("ls-files", "--", path.name, cwd=path.parent).strip()
    except subprocess.CalledProcessError:   # not in a repository
        tracked = False
    if tracked:
        diff = git("diff", "-U4", "HEAD", "--", path.name, cwd=path.parent)
        return f"--- {file} (a diff: review the + lines) ---\n{diff}" if diff else None
    return f"--- {file} (the whole file) ---\n" + path.read_text(encoding="utf-8")


def review(
    text: str,  # The text to review, from text_of
) -> dict:  # The verdict and the findings
    "Ask the model for a review of the text, or take a pass from the cache."
    ask = "\n\n".join([(STYLE / "REVIEW.md").read_text(encoding="utf-8"),   # the reviewer's prompt, the same as walk-kit's
                       "=== WRITING GUIDE (docs/style/GUIDE.md) ===\n" + (STYLE / "GUIDE.md").read_text(encoding="utf-8"),
                       "=== GLOSSARY AND HEADINGS (docs/style/README.md) ===\n" + (STYLE / "README.md").read_text(encoding="utf-8"),
                       "=== TEXT TO REVIEW ===\n" + text])
    key = hashlib.sha256(f"{MODEL}\n{ask}".encode()).hexdigest()
    kept = CACHE / f"{key}.json"
    if kept.is_file():
        return json.loads(kept.read_text())
    done = subprocess.run(["claude", "-p", "--model", MODEL, "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
                           "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
                          input=ask, capture_output=True, text=True, cwd=ROOT)
    if done.returncode:
        raise SystemExit(f"style review: claude failed: {(done.stderr or done.stdout).strip()[-500:]}")
    found = json.loads(done.stdout).get("structured_output")
    if not isinstance(found, dict) or "verdict" not in found:
        raise SystemExit(f"style review: claude gave no verdict: {done.stdout[-500:]}")
    if found["verdict"] == "pass" and not found["findings"]:   # only a pass is kept: a failed text is reviewed again
        CACHE.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(found))
    return found


def shadow(
    file: str,  # A reviewed file, relative to the top of timewalk, or a path outside it
) -> Path:  # Its shadow file in review-findings, at the same path with .md added
    "Name the shadow file of a reviewed file."
    path = (ROOT / file).resolve()
    name = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path).lstrip("/")
    return FINDINGS / f"{name}.md"


def round_of(
    file: str,  # A reviewed file
) -> int:  # The number of this review of the file since the last commit, from 1
    "Count the reviews of a file that had findings since the last commit, from the report's header. A pass does not count."
    head = git("rev-parse", "HEAD").strip()
    kept = shadow(file)
    lines = kept.read_text(encoding="utf-8").splitlines()[:3] if kept.is_file() else []
    if f"commit: {head}" not in lines:
        return 1
    return next((int(line.split()[1]) for line in lines if line.startswith("round: ")), 0) + 1


NOTE = "The author writes `- Fixed: ...` under each finding that the author fixed. A finding with no Fixed line is for Rahul."


def keep(
    file: str,  # A reviewed file
    number: int,  # The number of this review
    findings: list[dict],  # Its findings
) -> None:
    """Add the findings of a review to the file's report, one file for each reviewed file. A report counts from the last
    commit: after a new commit, the first review starts a new report."""
    kept = shadow(file)
    head = git("rev-parse", "HEAD").strip()
    old = kept.read_text(encoding="utf-8") if kept.is_file() else ""
    rounds = old.split("\n\n", 1)[1] if f"commit: {head}" in old.splitlines()[:3] and "\n\n" in old else ""
    body = "\n".join(f"## \"{f['quote']}\"\n\n- Problem: {f['problem']}\n- Rewrite: {f['rewrite']}\n" for f in findings)
    section = f"# Round {number}\n\n{body or 'No findings.'}\n"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(f"round: {number}\ncommit: {head}\nfile: {file}\n\n{NOTE}\n\n{rounds.removeprefix(NOTE + chr(10) + chr(10))}{section}", encoding="utf-8")


def main() -> None:
    "Review the changed prose, print each finding, and exit 1 if there is one."
    parser = argparse.ArgumentParser(description="Review the prose that changed against the writing guide, with a model")
    parser.add_argument("files", nargs="*", help="files to review (default: the files of `just style` that changed since the last commit)")
    parser.add_argument("--all", action="store_true", help="review the files whole, not only their change")
    args = parser.parse_args()
    files = args.files or changed(in_scope())
    if files and not shutil.which("claude"):
        print("style review: skipped, because claude (Claude Code) is not installed")
        return
    if not files:
        print("style review: no prose changed")
        return
    failed = 0
    for file in files:
        number = round_of(file)
        if number > ROUNDS:   # its findings wait in its shadow file for a person, until the next commit
            print(f"{file}: round {ROUNDS} is done; its findings are in {shadow(file).relative_to(ROOT)}")
            continue
        text = text_of(file, args.all)
        if text is None:   # no change since the last commit
            continue
        found = review(text)
        if not found["findings"]:   # a pass is not a round: the next change is reviewed again
            print(f"{file}: pass")
            continue
        keep(file, number, found["findings"])
        if number == ROUNDS:   # the last round: the findings go to a person, and the check passes
            print(f"{file}: round {ROUNDS}, {len(found['findings'])} findings left in {shadow(file).relative_to(ROOT)}")
            continue
        for finding in found["findings"]:
            failed += 1
            print(f"{finding['file']}: \"{finding['quote']}\"\n  problem: {finding['problem']}\n  rewrite: {finding['rewrite']}\n")
        print(f"{file}: FAIL (round {number} of {ROUNDS})")
    if failed:
        raise SystemExit(f"style review: {failed} findings")


if __name__ == "__main__":
    main()
