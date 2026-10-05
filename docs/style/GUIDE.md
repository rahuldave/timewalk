# Not so shitty AI first drafts

A writing guide for documentation, READMEs and other prose that an AI model writes and a person reads. It
joins two sources. The first source is ASD-STE100, Simplified Technical English, which aerospace manuals
use so that readers who are not native English speakers understand them. The second source is the
plain-writing skill by Shreya Shankar, which keeps prose plain and free of the habits of AI writing.

Give this file to the model with the task, together with a glossary of the project's terms. Then run
`check.py` on the result, and read the result yourself.

## Markdown comes first

The rules apply to the prose of a page, which is its paragraphs, list items, headings and image captions.
Markdown syntax always wins over a rule. The rules do not apply to these constructs:

- **Fenced code blocks and inline code.** Commands, code, file names and program output stay exactly as
  they are, with their own punctuation.
- **Tables.** A table cell can be a short phrase, a fragment or a value. Write the prose around the table
  by the rules.
- **Link targets, URLs and image paths.** The text of a link follows the rules, but its target does not.
- **Quoted examples.** Words in straight double quotes are words that the text talks about. They keep their
  own form, as in the list of words to avoid.
- **Front matter, HTML and comments.** Metadata at the top of a file, and HTML tags, keep their own syntax.

A rule about colons, dashes or quotes never applies to a character inside one of these constructs.

## Words

1. **Use simple, everyday words.** Choose the plain word when a longer word means the same thing:

   | Write | Not |
   |---|---|
   | use | utilize |
   | let | allow, enable |
   | make sure | ensure |
   | give | provide |
   | start | launch |
   | about | approximately |
   | through | via |
   | before | prior to |
2. **Use one term for one thing.** Keep a glossary of the project's terms, and use each term the same way
   every time. Do not change to a synonym for variety. A word keeps one meaning in the whole document.
3. **Define jargon the first time it appears on a page.** Use a technical term only when it is the most
   precise word, and say in plain words what it means. Do not invent jargon, and do not invent a
   hyphenated adjective that no dictionary has.
4. **Write "for example".** Do not write "e.g.", "i.e." or "etc.". If a list is not complete, say so in
   words.
5. **Do not add empty emphasis.** Remove a word that adds force but no fact. Examples are "really",
   "simply", "just", "actually", "crucial", "pivotal", "robust", "seamless", "powerful" and "matters". Also
   avoid the words that AI writing overuses, for example "delve", "leverage", "landscape", "tapestry",
   "unlock", "elevate" and "empower".
6. **Do not use contractions in documentation.** Write "do not" and "it is". A reader who is not a native
   speaker reads the full form more easily.

## Sentences

7. **Write complete sentences.** Each sentence has a subject and a verb. Do not write fragments, except in
   a table cell, a short list item or a label.
8. **Keep a sentence to 25 words or fewer, and to one or two clauses.** If a sentence needs three clauses,
   make two sentences, or use a list. But do not cut one thought into a stack of short sentences. Join two
   related clauses with "and", "but", "because" or "so".
9. **Prefer the active voice and simple verb forms.** Write "the server reads the file" and not "the file
   is read by the server". Write "runs" and not "is running". A passive or an -ing form is not wrong, but
   use it only when the simple form is unclear.
10. **Put not more than three nouns in a row.** Break a longer group with "of", "for" or "in". Write "the
    size of the terminal font" and not "the terminal font size setting".
11. **Say exactly what happens.** Name who or what does the action, and by what means. Remove side clauses
    that add no fact, for example "as we move forward" or "for the time being".

## Instructions

12. **Write an instruction as a command.** Write "Open the file" and not "You should open the file" or "The
    file can be opened".
13. **Give one instruction in one sentence, in the order the reader does it.** Use a numbered list, or
    "First", "Second" and "Third", for a sequence. Two actions that happen at the same time can share a
    sentence.
14. **Put a condition first.** Write "If the test fails, read the log" and not "Read the log if the test
    fails". An instruction has 20 words or fewer.
15. **Start a warning with a short command, then give the reason.** Write "Do not put the address on a
    slide. Anyone with the address can run commands as you."

## Paragraphs

16. **Start each paragraph with a topic sentence.** Then give support, with a fact or an example. Start
    more support with a plain word, for example "For example", "Also" or "But".
17. **Keep a paragraph to six sentences or fewer.** Do not give three or more examples in a row for the
    same point.

## Tone

18. **Keep the writing boring, literal and descriptive.** Do not write a catchy heading, a slogan or a
    clever label. A heading says what the section is about, for example "Moving with edits" and not "The
    point of no return".
19. **Do not use analogies or metaphors.** Describe the real thing in literal words.
20. **Do not give objects fake agency.** A tool can return, write or read. But do not write as if a system
    decides or intends something, when a person or a process is the real actor.
21. **State what a thing is.** Do not write "not just X, but Y" or "not only X, it is Y".
22. **Do not ask rhetorical questions.** State the problem.
23. **Do not start a sentence with a vague "This", "That", "These" or "Those".** Name the thing: write "The
    flag lets a move discard edits" and not "This lets a move discard edits".
24. **Do not open with a count.** Do not write "Three things to know." State the first thing. If there are
    many things, use a list.

## Punctuation and formatting

25. **Do not use dashes or middle dots.** Do not use an em dash or an en dash, and do not use the middle
    dot as a separator. Write a range with "to", as in "10 to 20 seconds".
26. **Do not join two clauses with a colon or a semicolon in prose.** Write two sentences. A colon is
    correct before a list, and after a short label such as "Summary:" or "Note:".
27. **Use straight quotes**, " and ', not curly quotes.
28. **Use sentence case in headings.** Write "How to install the tool" and not "How To Install The Tool".
29. **Use bold to set one thing off from the next.** Bold the first words of a list item or a paragraph, so
    the reader can scan the items. Bold the names of things the reader must find on a screen, for example
    a button. Do not use bold for emphasis in the middle of a sentence.
30. **Use lists and tables where they help, and not more.** In prose, keep a list to about four items, and
    nest the items if there are more. On a reference page, a table is the clearest form for options,
    commands and keys.

## Check the result

`check.py` measures the rules that a program can measure:

```
python3 check.py page.md [page.md ...]       # hard problems fail the check
python3 check.py -v page.md                  # also show the soft notes, for a person to judge
```

The hard checks find these problems:

- a sentence or a paragraph that is too long
- a contraction, a dash, a middle dot or a curly quote
- a colon between two clauses
- an abbreviation such as "e.g.", or a "not just" construction
- a count at the start of a paragraph, a vague "This is", or two questions in a row

The soft notes are for a person to judge. They show -ing forms, possible passives, semicolons, sentences
that may have three clauses, and words with a simpler alternative.

The program cannot judge meaning, tone or a missing definition. Read every page after it passes.

## Sources

- **ASD-STE100, Simplified Technical English**, from the AeroSpace and Defence Industries Association of
  Europe (ASD). Aircraft makers write their maintenance manuals in it, so that readers who are not native
  English speakers understand them. Andrej Karpathy suggested on X that the same rules suit text that AI
  models write. The rules for words, sentences, instructions and paragraphs come from it. Its dictionary
  of approved words is not part of this guide.
- **The plain-writing skill by Shreya Shankar**, https://github.com/docwriter-org/plain-writing-skill,
  under the MIT licence, copyright (c) 2026 Shreya Shankar. The rules for tone, punctuation and the
  patterns of AI writing come from it. This guide states the rules in its own words. It also changes two
  of them. It forbids contractions in documentation, and it allows bold at the start of a list item.
