# Kūhaku Design Brief

The brief the design is made from. The [Codex](codex.html) turns it into laws, a construction
system and the icon library. When the two disagree, fix the Codex or change this brief on purpose.

## 1. Why Kūhaku exists
AI is powerful, but it is overwhelming. Kūhaku is an operating system for an agent-operated mindset: you tell the computer what to do, and the interface stays out of the way. Kūhaku is as much a design project as a technical one. Design is the thing that makes AI calm, seamless and bearable.

Kūhaku follows the idea of **calm technology** (Weiser & Brown, Xerox PARC): information lives at the edge of attention and moves to the centre only when it matters.

## 2. What Kūhaku means
空白 — kūhaku — blank, empty space. In Kūhaku, emptiness is not missing content; it is meaningful space that gives the few remaining elements their weight. White space is used deliberately, as in ikebana and ink painting.

**The red seal.** In sumi-e, the painting is black ink on empty paper, and the only red is the artist's seal. Kūhaku follows the same rule: a calm neutral field, and one red mark that carries meaning (the red macron in the logo; the one red element in an icon).

## 3. Influences — and what we take from each
- **Japanese aesthetics (sumi-e, ikebana, Kenya Hara / Muji):** emptiness as a value; the single decisive stroke; nothing added that isn't needed.
- **Scandinavian design (Arne Jacobsen, Daniel Rybakken):** objects that look effortless but hide enormous thinking; complexity spent on making things simple; light as a material.
- **Bauhaus and Swiss style:** geometry, grid, objectivity; typography doing the work.
- **Dieter Rams / Braun:** "less, but better." Colour is never decoration — it always has a reason.
- **Fukasawa & Morrison, "Super Normal":** the Japanese–European blend done right — quiet, ordinary, perfect.

Our references are mostly physical objects. On screen, "high-quality material" translates to: precise pixel alignment, one consistent stroke weight, optical (not just mathematical) sizing, careful type rendering, calm motion timing, and total consistency across the system.

## 4. The balance
Design must not become so quiet that it hurts function, and function must not override design.
**Tie-breaker:** function decides *whether* something exists; design decides *how* it exists.

## 5. Principles
1. **One red.** At most one red element per icon or screen. Red means focus, active, or attention.
2. **Colour signals, never decorates.** Colour appears only to communicate state. If a colour has no reason, remove it.
3. **Quiet by default, loud when needed.** Normal states stay neutral and peripheral; only exceptional states (e.g. critical battery) earn attention.
4. **Stable footprint.** An icon must not change its size or occupy more space when its state changes.
5. **Fewest strokes.** Every icon uses the minimum number of marks needed to be understood — like a single brushstroke.
6. **Geometry first, then beauty.** Built on a grid with simple forms, then refined optically until it feels effortless.
7. **Instantly readable.** State (open, active, charging) must be understood at a glance, without labels.
8. **Simple-looking, deeply thought.** If it looks obvious, the thinking was done well.

## 6. Case study: the battery icon
- Base: a simple bar — good.
- Added a charging bolt — not bad, not great: "meh."
- The bolt needed an extra column, so the icon grew wider → breaks principle 4.
- Added green for charging → a second accent colour that may not be needed → test against principles 1–3.
- Open question: should charging show at all, or only the critical state?

## 7. What we are building
1. **Design Manual (the Kūhaku Codex):** philosophy, principles, specifications.
2. **Specifications:** grid size, stroke weight, corners, colour tokens, typeface, spacing, motion timing.
3. **Icon Library:** every icon ever made, constantly updated, so the whole set can be reviewed side by side.
4. **Decision Log:** rejected versions are kept with the reason they were rejected. This log is how the system learns.

Long term, the Codex and icon library can become a public part of the Kūhaku website — the design direction we preach.

## 8. Icon notes at the time of the brief
- Strong: the first icons (– . ||) where only one element is red; the home icon.
- Weak: the work icon.
- Under review: battery, screen.
- Next: redesign Settings using this brief.
