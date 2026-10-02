"""Build a small repository with four tagged steps, to try timewalk on.

    python demo/make_demo.py            writes demo/sample, replacing any earlier one

Then:

    uv run timewalk.py demo/sample --notes demo/notes.md --slides demo/slides/slides.toml
"""

import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE / "sample"

STEPS = [
    ("step-00", "An empty project", "A README and nothing else. This is where every project starts.", {
        "README.md": "# sample\n\nA tiny project that grows one step at a time.\n",
        ".gitignore": ".venv/\n__pycache__/\nruns/\n*.db\n",
    }),
    ("step-01", "A file arrives, with no tests and no types", "greet.py does the work. Nothing checks it yet.\nThe justfile is born with one recipe.", {
        "src/greet.py": 'def greet(name, excited=False):\n    return "Hello, " + name + ("!" if excited else ".")\n',
        "justfile": "# Run the program once\nrun:\n    python3 -c \"import sys; sys.path.insert(0, 'src'); from greet import greet; print(greet('class'))\"\n",
    }),
    ("step-02", "Its tests", "Three tests for greet. The justfile gains: test.", {
        "tests/test_greet.py": "import sys\n\nsys.path.insert(0, \"src\")\nfrom greet import greet\n\n\ndef test_plain():\n    assert greet(\"Ada\") == \"Hello, Ada.\"\n\n\ndef test_excited():\n    assert greet(\"Ada\", excited=True) == \"Hello, Ada!\"\n\n\ndef test_empty_name():\n    assert greet(\"\") == \"Hello, .\"\n",
        "justfile": "# Run the program once\nrun:\n    python3 -c \"import sys; sys.path.insert(0, 'src'); from greet import greet; print(greet('class'))\"\n\n# Run the tests. Extra arguments go to the test runner: just test -k plain\ntest *args:\n    uvx pytest -q tests {{args}}\n",
    }),
    ("step-03", "Its types and docs", "The same function, now typed and documented. The behaviour did not change; what a reader can learn from the signature did.", {
        "src/greet.py": 'def greet(\n    name: str,  # Who to greet\n    excited: bool = False,  # End with an exclamation mark\n) -> str:  # The greeting\n    "Build a greeting for one person."\n    return "Hello, " + name + ("!" if excited else ".")\n',
        "README.md": "# sample\n\nA tiny project that grows one step at a time.\n\n## Use\n\n```\njust run\njust test\n```\n",
    }),
]


def git(*args: str) -> None:
    "Run git in the sample repository."
    subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True)


def main() -> None:
    "Write the sample repository and tag each step."
    if REPO.exists():
        subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)
        shutil.rmtree(REPO)
        shutil.rmtree(HERE / "sample-replay", ignore_errors=True)
    REPO.mkdir(parents=True)
    git("init", "--quiet", "--initial-branch", "main")
    git("config", "user.name", "timewalk demo")
    git("config", "user.email", "demo@example.invalid")
    for tag, subject, note, files in STEPS:
        for path, text in files.items():
            target = REPO / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git("add", "--all")
        git("commit", "--quiet", "--message", subject)
        git("tag", "--annotate", tag, "--message", note)
    print(f"wrote {REPO} with {len(STEPS)} steps")


if __name__ == "__main__":
    main()
