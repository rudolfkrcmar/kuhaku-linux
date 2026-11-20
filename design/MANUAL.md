# Kūhaku · Design manual, in short

The standard is [codex.html](codex.html), "Kūhaku, the design of blank space": open it in a
browser. Where the software differs from it, the software is behind. This file is the same rules
in plain words, for anyone (or any AI) about to draw something for Kūhaku. The inventory of every
icon in the code today is [library.html](library.html), rebuilt by `python3 dev/design.py`.
[BRIEF.md](BRIEF.md) is the brief the codex was made from; [index.html](index.html) is the earlier,
hand-drawn manual, kept for its studies.

## Why

AI is loud. Kūhaku is the quiet around it (calm technology). Function decides whether a thing
exists; design decides how.

## Ten laws

1. One red per mark: a mark, a row or a panel carries at most one red element.
2. Red means here: yours, open, where you are, where you are needed. Nothing else is coloured.
3. State changes in place: recolour or move a stroke; never add a symbol, a colour or a column.
4. One pen: one weight, square ends, mitred corners. Sharp, or a whole circle.
5. Five strokes: a mark takes one to five strokes, most two or three.
6. Straight first: 0°, 45°, 90°, then the square. Curves only for round things (lens, clock, water).
7. Space is a material: remove before you add.
8. Quiet until needed: grey and at the edge at rest; attention asked once, in place, and given back.
9. Day and night are equals: drawn and checked in both.
10. It still works: quiet never costs function.

## Materials

- Paper `--bg` #f7f4f1 · #121212. Ink `--fg` #111111 · #ececec. Rest `--muted` #666666 · #9a9a9a.
  Hairline `--faint` ink at 8%. Seal `--accent` #e0301e · #f0705a.
- Five colours, no more: no green, no blue. Red is never used for running words. Every red comes
  from the token; no red is written by hand.
- Inter, light. 72/200 for time, 30/200 for answers, 15/300 body, 12.5/300 captions. Emphasis is a
  step darker or larger, never bold. Nothing light below 12.5.
- Surfaces are soft (sheet radius 18, hairline edge, one shadow); marks are hard (no radius).

## Construction

- Cell 24, margin 4, live area 16. Whole units; half units only to centre a part.
- The pen is the SVG default: no `stroke-linecap`, no `stroke-linejoin`, no `rx`. Fill none, except
  the dot.
- Four sizes, and the stroke follows: 12 → 2, 16 → 1.5, 18 → 1.33, 40 → 1 (one pixel on screen,
  1.67 at 40).
- Marks (a place, an app) have one `class="hot"` stroke: the part that acts. Grey at rest, red
  when open or pointed at. It stands free, crosses or lands on ink; never forms a corner with it.
- Glyphs (close, back, find…) are one ink and never red. One drawing per meaning, used everywhere.
- The drawings themselves are in codex.html (`BODIES`): copy them, don't redraw them.
- Other companies' marks (GitHub…) are drawn as their owners draw them, in one ink.

## Method

Symbol, simplification, function, further, simple and smart, too far? The step before the break
is the mark. Then choose the hot stroke, look at real size by day and night, and use it for a day.

## Controls

Switch: a 12 square, empty or filled with ink, never red. Choice: a word and its square per
option. List: value and chevron, opening a sheet. Level: a bar of 6 (8 when pointed at) with the
number beside it, no knob. Word: a button is a verb; the main one ink, others grey. Field: a
hairline, red caret. Tabs: ink, with a red line 14 by 1.5 under the current one. One red per panel.
Changes happen at once; no Apply.

## Signs

The menu line is red (the seal). The dot after it is ink: apps are open; the marks' hot strokes say
which. The composer is one upright red line, no dot. The battery is a bar of 18 by 6: red only when
low; plugged in, a one-pixel tick stands where charging will stop and the fill grows toward it.

## Practice

One thing per screen; everything else waits. One left edge; at most three type sizes and two
weights; space first, hairline second, never a box. About seven things in view at rest. A window
has one bar; places are words, where you are is ink. Words are short and plain, buttons are verbs,
no exclamation marks. Motion: rise and settle, `cubic-bezier(.2, .7, .2, 1)`, a quarter to a third
of a second; a slow pulse means working, nothing else; no spinners. Empty needs no excuse. Errors
are words in ink where it went wrong.
