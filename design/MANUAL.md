# Kūhaku · Design manual, in short

The manual itself is [index.html](index.html): open it in a browser. It is drawn by hand, with
construction drawings, the colour study, reductions and redraws. This file is the same rules in
plain words, for anyone (or any AI) about to draw something for Kūhaku. The inventory of every
icon in the code today is [library.html](library.html), rebuilt by `python3 dev/design.py`.

## Why

AI is loud. Kūhaku sets it in calm. The difficulty is balance: quiet must never cost function,
and function must never shout over the surface. Ask of everything: is this the most useful thing
that is still quiet?

## Lineage, one lesson each

Bauhaus: geometry first. Swiss type: type does the lifting. Sumi-e: one stroke, with confidence.
Ikebana: space is the material. Jacobsen, Rybakken, Stelton: looks simple, is not. Rams: colour
has a reason.

## Ten principles

1. One thing speaks: at most one red thing in a mark, a row, a screen.
2. Colour has a reason: red means yours, on, or needs you. Nothing else.
3. State changes in place: never add a symbol or a column for a state.
4. Space is a material: remove before you add.
5. Cut, not moulded: square ends, sharp corners; curves only for things truly round.
6. Type carries the order: size and weight (200, 300, 400); no bold, boxes or badges.
7. Quiet until needed.
8. Day and night are equals; password and lock screens are always night.
9. Calm motion: rise and settle, about .25s; a slow pulse means "working", only that.
10. The effort is invisible: simple that feels arbitrary is not finished.

## Colour: paper, ink, seal

- Paper `--bg` #f7f4f1 (night #121212): warm, never pure white. Most of every screen.
- Ink `--fg` #111111 (night #ececec), at 60% `--muted` for things at rest, at 8% `--faint` for hairlines.
- Seal `--accent` #e0301e (night #f0705a): the vermilion of the hanko stamp. Once per mark.
- Proportion: about 90 paper, 9 ink, 1 seal.
- No fourth colour. Written-out colours in code are a fault (`var(--accent)`, never `#e5484d`).
- Battery charging: green is rejected (a fourth colour for a state that does not need you); the
  proposal is an ink fill that breathes slowly. `--charge` stays only until that is adopted.

## The line: how an icon is made

- Grid 24. Live area 4 to 20.
- Rhythm 6: parallel lines at 9 and 15.
- Weight: the line seen on screen is about 1px. Stroke 1.4 at 16 to 18px, 1 at 40px.
- Ends `butt`, joins `miter`. No `rx`, no round caps. True circles and half-circles may stay round.
- Optical size: squares smaller (9.6), circles slightly larger, than a 16-unit line.
- Two kinds:
  - **Mark** (an app, a place, a menu entry, an account): grey at rest, exactly one part
    (`class="hot"`) takes the seal when on, open or pointed at. That part is the idea of the thing.
  - **Glyph** (close, back, reply, attach, picture): one ink, no seal.
- The canon is the quick-start row: Miru `=`, Tsukuru `||`, Mado (window and title bar), Tegami
  (flap and fold), Claude (four strokes on a circle of 8).

## Method

1. Name the one idea. 2. Find the gesture: draw the object, reduce until one more cut would lose
it. 3. Build it on the grid. 4. Give one stroke the seal, or make it a glyph. 5. Set it among its
neighbours (run `dev/design.py`, look at the inventory). 6. Subtract. 7. Show every state: none may
grow. 8. Live with it for a day, then write the decision into the manual.

## Open

The manual's chapter 08 holds redraws of icons that drifted (soft corners, borrowed shapes), the
work account mark among them. Adopt them one by one; the inventory's red notes show what is left.
