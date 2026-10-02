# Antenna design language

Antenna should read as a tool Anti Fund built for itself. The identity is
measured from antifund.com: warm paper, near-black ink, a serif headline,
mono labels, hairline rules, one green. Nothing here is decorative. The
feeling to aim for is a well-set financial broadsheet that happens to be
alive.

## Tokens (in `web/src/app/globals.css`)

| Token | Value | Use |
| --- | --- | --- |
| `paper` | #faf8f1 | Page |
| `paper-2` | #f1ede2 | Hover, inset panels |
| `paper-3` | #e9e3d4 | Meter tracks, pressed |
| `ink` | #141414 | Text, lines, fills |
| `ink-2` | #373631 | Body text |
| `ink-3` | #59574f | Labels, secondary |
| `ink-4` | #6f6b5e | Tertiary text that still has to be read: ages, counts |
| `ink-5` | #8a8576 | Placeholders and keyboard hints only |
| `rule` | #cdc5b7 | Hairlines |
| `rule-2` | #9f988a | Emphasised hairlines |
| `signal` | #1f7a3d | The one accent: up, new, live |
| `heat` | #b4421c | Reserved for warnings only |

Tailwind classes exist for all of them: `bg-paper`, `text-ink-3`,
`border-rule`, `text-signal`.

## Type

Three faces, each with one job.

- **Source Serif 4** (`display` utility, or `font-serif`): headlines and
  company names. Weight 500, tracking -0.03em, tight leading.
- **IBM Plex Sans** (default): body and UI. 14px to 15px body, weight 400;
  500 for emphasis. Never bold 700.
- **IBM Plex Mono**: the `label` utility (11px, uppercase, tracking 0.08em,
  `ink-3`) for every caption and column head, and the `num` utility
  (tabular figures) for every number. Numbers are always mono.

Sizes: page headline 44 to 64px serif; section head 22 to 28px serif;
company name in a row 17px serif; body 14 to 15px sans; labels 11px mono.

## Layout

- Max width 1320px, gutters 20px mobile and 32px desktop
  (`mx-auto max-w-[1320px] px-5 sm:px-8`).
- Structure comes from hairlines (`border-rule`), not boxes. No cards, no
  drop shadows, no rounded corners beyond 2px, no gradients except the
  faint area under a chart line.
- A section opens with a mono label in a narrow left column and content in
  the wide right column, as on antifund.com (`grid grid-cols-12`, label in
  2 columns, content in 10) on desktop; stacked on mobile.
- Density is a feature. Rows are 56 to 64px. White space goes between
  sections (96px), not inside them.

## Colour discipline

Ink on paper does almost everything. Signal families are told apart by
position and label, never by hue. Green means exactly one thing: positive
movement or newness (a rising sparkline's last dot, a rank gain, the "New"
tag, a live indicator). If green appears more than a few times in a
viewport, remove some.

## Motion

Motion explains; it never performs. Use `motion/react`.

- Easing: `[0.16, 1, 0.3, 1]` for entrances and anything that settles.
- Durations: 150 to 250ms for hover and press, 400 to 600ms for entrances,
  up to 900ms for a chart drawing itself.
- Entrances: 8px rise plus fade, staggered 20 to 35ms per row, capped so the
  whole list is in within about 700ms. Animate once, on mount.
- Charts draw left to right. Numbers count up once (about 800ms) when they
  first appear.
- Filtering and sorting use layout animation so rows slide to their new
  place instead of blinking.
- Hover on a row: background to `paper-2` in 150ms, nothing moves.
- Underlines grow from the left on hover (`scale-x` on a 1px line).
- Always honour `prefers-reduced-motion` (`useReducedMotion`); the global
  stylesheet already neutralises CSS transitions.

## Components (in `web/src/components/ui`)

- `Shell` — header with wordmark and nav, footer with the run stamp.
- `Sparkline` — draws itself; last point green when rising.
- `FamilyGlyph` — eight cells, one per signal family, fixed order, ink
  opacity by strength. The signature mark of the product.
- `RankDelta` — movement against last week, or "New".
- `Meter` — a 3px track with an ink fill that grows in.

Reuse these. If something is missing, add it to `components/ui` in the same
spirit and keep it small.

## Voice

Plain and specific. Sentence case everywhere, including buttons and
headings. Say what a thing is: "Signals", not "Insights"; "Why now", not
"AI summary". Numbers carry their unit. No exclamation marks, no emoji, no
filler. Every claim on screen links to its evidence.

## Accessibility

Real tables for tabular data, real links for navigation, visible focus
(already global), labels on every chart (`role="img"` with `aria-label`),
and 4.5:1 contrast for text (`ink-3` and `ink-4` on paper pass; `ink-5` is for
placeholders and hints only).

## Data

Pages read from `@/lib/data` (`board`, `feed`, `meta`, `getDossier`,
`allSlugs`). Types are in `@/lib/types`. The site is a static export: no
server code, no fetches at runtime, every dynamic route needs
`generateStaticParams`. `entities.json` is large: import it only in server
components, and pass a client component just the fields it needs.
