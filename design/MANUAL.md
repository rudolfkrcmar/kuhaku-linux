# Kūhaku · Design manual

空白, kūhaku: blank space. The space in a painting that is left, not forgotten.

This is the one place to return to before drawing an icon, a page, a panel or a word in Kūhaku.
It says what Kūhaku should feel like, where that comes from, the rules that follow, and how to work.
Its companion is the [icon library](library.html): every icon Kūhaku draws, rebuilt from the code
each time (`python3 dev/design.py`), so the manual and the real thing can be laid side by side.

---

## 1 · Why the design matters

AI is loud. It is fast, it can do almost anything, and it says so. Left alone it overwhelms.
Kūhaku is as much a design project as an AI one: its job is to take that power and make it calm,
quiet and seamless, so that a person meets a clear surface and not a machine.

The whole difficulty is balance:

- Do not make it so quiet that function suffers. Calm that hides what you need is not calm, it is fog.
- Do not let function shout over design. A feature that adds noise must earn it or wait.

Every decision is weighed on that scale. When in doubt, the question is never "is it simpler?"
nor "is it more useful?" but "is it the most useful thing that is still quiet?"

## 2 · Where it comes from

Kūhaku is a blend, not a copy. Each source gives one thing.

- **Bauhaus**: geometry first. Line, square, circle. Form from function.
- **Swiss typography**: type does the lifting. A good typeface, set well, can elevate a whole
  surface without anything else. Kūhaku's is Inter, light weights, generous size.
- **Sumi-e**: one confident stroke instead of many careful ones. An icon is a gesture, not a drawing.
- **Ikebana**: the space around a thing is part of the thing. Emptiness is arranged, not left over.
- **Danish design** (Arne Jacobsen, Daniel Rybakken, the Stelton bottle opener, Danish houses):
  it looks simple, and only on a second look do you see how much thinking it took to look that
  simple. Large calm planes of good material. Shapes that read as square but are not quite: a
  softened corner, a proportion tuned by eye.
- **Dieter Rams**: colour with a reason. Grey for the body, one colour for the thing you act on.
  Less, but better.

Colour may be vivid. Kūhaku is not grey and not timid. But colour never shouts: it is placed,
once, where it means something.

## 3 · Principles

1. **One thing speaks.** In every mark, every row, every screen, at most one thing is red.
   The rest is ink and space. If two things want to speak, one of them waits.
2. **Colour has a reason.** Red means: this is yours, this is on, this needs you, act here.
   Any colour that cannot finish the sentence "this is coloured because…" goes.
3. **State changes in place.** A thing shows its state by changing itself (a stroke turns red,
   a line grows, a bar fills), not by adding something beside it. The footprint stays the same.
4. **Space is a material.** White space is designed as carefully as the marks. Do not fill a gap
   because it is there. Remove before you add.
5. **Geometry, then a human touch.** Start on the grid with lines, squares and circles. Then tune
   by eye: a rounded end, a corner of 2, a proportion nudged until it rests.
6. **Type carries the hierarchy.** Size and weight (200, 300, 400), never boxes, borders or
   badges. A title is big and light; the thing that matters is a step darker, not bolder.
7. **Quiet until needed.** Labels, hints and controls appear when you point at them or need them,
   and leave when you don't. Nothing explains itself twice.
8. **Day and night are equals.** Every mark, colour and screen is designed in both and checked in
   both. Password and lock screens are always dark.
9. **Calm motion.** Things rise and settle (`cubic-bezier(.2,.7,.2,1)`, a quarter to a third of a
   second). Nothing bounces, nothing spins for show. A pulse means "working", and only that.
10. **Seen thinking.** The finished thing looks effortless. That look is the result of the most
    effort, not the least. If it looks simple but feels arbitrary, it is not finished.

## 4 · The materials

### Colour

| Token | Day | Night | Means |
|---|---|---|---|
| `--bg` | `#f7f4f1` | `#121212` | Paper. Warm by day, near black by night. |
| `--fg` | `#111111` | `#ececec` | Ink. What you read, what is on. |
| `--muted` | `#666666` | `#9a9a9a` | Ink at rest. Icons, captions, things not in use. |
| `--faint` | ink at 8% | ink at 8% | Hairlines, grids, the edge of a sheet. |
| `--accent` | `#e0301e` | `#f0705a` | The one voice. On, yours, needs you, act here. |
| `--charge` | `#2f9d62` | `#5cc98a` | Battery charging. The only other colour; under review (see 7). |

No other colours in Kūhaku's own interface. Colours in the code are tokens, never written out
(`var(--accent)`, not `#e5484d`).

### Type

Inter (variable). Light is the default voice.

- Answers, the composer and page titles: 26 to 30, weight 200, tracking slightly tight (-.01 to -.02em).
- Big numbers (time, volume, battery): 72, weight 200, tabular figures.
- Body and panels: 14 to 15, weight 300.
- The top row and captions: 12 to 13, weight 300, tracking slightly open (+.02em); the time a step
  darker at 400.
- Never bold for emphasis. Never all capitals as decoration.

### Space and form

- Sheets and panels: radius 18, a 1px faint edge and one soft shadow, no borders inside.
- Hit areas are generous (34 in the top row, 96 in the menu) while the marks stay small.
- Separate with space first, a faint hairline second, never with a box.

## 5 · The icon grammar

Kūhaku's icons are the clearest expression of all of this. The quick-start row is the model:

- **Miru** `=`: two shelves. **Tsukuru** `||`: two pillars. **Mado**: a window and its title bar.
  **Tegami**: an envelope reduced to the flap and the fold. **Claude**: the asterisk.
- Each is drawn in grey, and **exactly one stroke turns red when the app is open**. Open or not is
  told by one line changing colour, with no badge, dot or underline added.

That is the grammar. Two kinds of icon follow from it.

**Marks** stand for a place: an app, a menu entry, a setting, an account. A mark has one *hot*
part (`class="hot"`) that is grey at rest and red when on, open or pointed at. The hot part is the
idea of the thing: the arrow in Log out, the cursor line in Terminal, the beat in Kansei, the knob
you last moved in Settings.

**Glyphs** are tools: close, back, forward, reply, search. One ink, no hot part. They do, they
don't mean.

### Rules

- **Grid**: 24 by 24. Draw inside 3.5 to 20.5; the edge is breathing room.
- **Line**: the line you see is about 1px. So the stroke follows the size: 1.4 at 16 to 18,
  1 at 40, a little more on the smallest glyphs (13 and under). The library flags lines that land
  far from 1px.
- **Ends**: round caps and joins for marks in the menu and glyphs; square (butt) ends where the
  mark is pure geometry (Wi-Fi, the quick-start row).
- **No fills**, except a dot. A dot is the smallest complete sign there is; use it sparingly.
- **Few strokes.** Two or three is usual, five is a lot. If a mark needs more, the idea is not
  yet found.
- **One accent.** One hot part per mark. An arrow drawn in two paths is still one part.
- **Same footprint.** States change strokes, not size. A new state never adds a column.
- **Both themes.** Check at real size, by day and by night, at rest and on.

### Signs of the top row

Some marks are not icons but signs, drawn in CSS:

- **The menu**: one red line. Longer when pointed at, heavier when open.
- **Open apps**: a red dot right after the line, there only while apps are open.
- **The line and dot**: the composer, where you speak to Claude.
- **Battery**: a bar, 18 by 6, the track at 18% ink. It fills with ink; it turns red when low.

## 6 · How to make something

The method, for an icon, a panel or a page.

1. **Name the one idea.** In a few words: what is this, and what does it change? If you need a
   sentence with "and", it is two things.
2. **Find the gesture.** What is the least that still says it? Strip the metaphor until one more
   cut would lose it (an envelope becomes a V and a line).
3. **Draw on the grid.** Lines, squares, circles first. Then tune by eye.
4. **Choose the hot part.** Which stroke is the idea? That one turns red. If none of them is, it
   is a glyph.
5. **Put it among its neighbours.** Build the library and look at it beside the icons it will
   sit with, at real size, day and night. Weight, size and rhythm must match the row.
6. **Subtract.** Take away each part in turn. If it still reads, leave it out.
7. **Test the footprint.** Show every state. Does any state make it wider, add a symbol or add a
   colour? Then find a way for it to change in place.
8. **Use it for a day.** Calm is felt over time, not in a screenshot.

## 7 · Reviewing

Before anything new is kept, it answers yes to each of these:

- Does one thing speak, and is it the right thing?
- Can every colour finish "this is coloured because…"?
- Does every state change in place, without growing?
- Does it read at real size, by day and by night?
- Does it sit in its row as if it had always been there?
- Is there anything left to take away?
- Is it still useful? (Quiet must not cost function.)

### Case: the battery

The battery is a good lesson, so it stays here.

- A bar alone: beautiful. Pure geometry, one glance, no words.
- A bolt added when charging: not wrong, just *meh*. It said something the bar could say itself,
  and it added a column, so the top row moved. Removed: it broke principle 3.
- Green when charging: possible, and it does change in place. But it is a second colour in a
  one-colour system, and charging does not need you. Under principle 2, colour is for what is yours
  or needs you. A proposal to decide: charging stays ink and the fill breathes slowly (the pulse
  that already means "working"); red stays for low; green leaves. Until decided, green stays and is
  marked as under review.

### Open questions

The library's red notes, and these, are the current list of things to look at:

- **Account · work**: the case does not yet have the clarity of the house. Look for a mark as
  simple as the house's roof.
- **Open apps dot** uses a written-out red (`#e5484d`) instead of `--accent`.
- **Small glyphs** in Tegami, Tsukuru and Miru drift in weight (1.5 to 2.2); bring them to one
  line weight per size.
- **Battery** green (above).

## 8 · Keeping it alive

- Every new or changed icon: run `python3 dev/design.py` and look at the library before committing.
  `dev/check` fails if the library is out of date, so it cannot drift from the code.
- Every decision that changes a rule is written here, with its reason, in the same change.
- Settle an open question by changing the code, then moving it from "Open questions" into a case
  above if the lesson is worth keeping.

One day this manual and the library can live on Kūhaku's website, as what Kūhaku stands for.
Write it so that a stranger could read it.
