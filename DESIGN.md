<!-- SEED — pre-implementation; re-run /impeccable document in scan mode once code exists -->
---
name: agentlens
description: Subagent analysis reports with editorial authority
colors:
  primary: "#C84A32"
  accent: "#6B8FCC"
  bg: "#131313"
  surface: "#1E1D1C"
  ink: "#EBE9E7"
  muted: "#8A8684"
  border: "#3A3735"
  score-high: "#6BAA7A"
  score-mid: "#C89B4A"
  score-low: "#C84A32"
typography:
  display:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: "2rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.01em"
  mono:
    fontFamily: "ui-monospace, 'JetBrains Mono', 'Cascadia Code', monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "3px"
  md: "6px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
  xxl: "64px"
components:
  score-badge:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.bg}"
    rounded: "{rounded.sm}"
    padding: "4px 10px"
  fix-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "16px 20px"
---

# Design System: agentlens

## 1. Overview

**Creative North Star: "The Red Margin Note"**

A senior engineer's annotated report: the page is white, the type is set with care, and the red ink marks what matters. The report reads like a well-edited technical article, not a dashboard. Every element earns its place through information density, not decoration. The hierarchy is typographic: size, weight, and spacing do the work that card borders and color fills do in lesser tools.

This system explicitly rejects: card grids as default containers, pastel gradient backgrounds, gamified scoring (badges, streaks, confetti), and the generic SaaS dashboard aesthetic where everything is equally weighted behind identical rounded rectangles. It also rejects raw data dumps: tables without hierarchy, screens without a reading order.

**Key characteristics:**
- Dark ground (near-black, no hue tint) — the late-night terminal review
- Typographic hierarchy over decorative elements
- Crimson as a precise signal, not ambient color (restrained strategy, accent under 10%)
- Data presented as narrative: what happened, why it matters, what to do
- Self-contained single-file HTML (no CDN, works at `file://`)
- Generous whitespace between sections; density within sections

**Deferred to v2:**
- Per-session detail view (drilldown into one spawn's full verdict, tool sequence, and fixes)
- Per-project grouping (requires schema change to persist `project_dir` into `fact_session`)

## 2. Colors: The Red Margin Palette

A restrained palette on a near-black ground: the crimson glows against dark, the text is light. The accent blue lifts against the same ground without competing.

### Primary

`oklch(0.62 0.17 29)` — lifted crimson. Brighter than the light-mode variant so it reads against dark. Used for: primary scores, critical findings, error flags, the overall score numeral. Paired with dark text on primary fills only when `L > 0.78`; otherwise white.

### Accent

`oklch(0.62 0.12 255)` — lifted slate-blue. Clearly distinct from primary in hue. Used for: links, info badges, secondary emphasis.

### Neutrals

| Role | OKLCH | Usage |
|---|---|---|
| bg | `oklch(0.08 0.000 0)` | Page ground, pure near-black, no hue tint |
| surface | `oklch(0.13 0.005 29)` | Cards, panels, stats grids — subtle lift |
| ink | `oklch(0.92 0.005 29)` | Body text, headings — warm off-white |
| muted | `oklch(0.58 0.005 29)` | Secondary text, timestamps, metadata |
| border | `oklch(0.22 0.005 29)` | Subtle separators between sections |

### Semantic (scores and status)

Scores map to three levels, lifted for dark-ground legibility:

| Level | OKLCH | Meaning |
|---|---|---|
| high (4-5) | `oklch(0.62 0.12 155)` | Sage green — competence confirmed |
| mid (2-3) | `oklch(0.62 0.12 75)` | Warm amber — attention warranted |
| low (0-1) | `oklch(0.62 0.17 29)` | Primary crimson — this is the finding |

## 3. Typography

Editorial authority through a two-family system: a serif for display hierarchy, a sans for body and UI.

### Type stack

| Role | Family | Notes |
|---|---|---|
| Display / headings | Newsreader (variable) | Embedded in HTML as base64. Optical sizes, editorial weight. |
| Body / UI | Inter (variable) | Embedded or system fallback. Clean, data-friendly. |
| Code / data | System mono stack | `ui-monospace, 'JetBrains Mono', 'Cascadia Code', monospace` |

### Scale (fixed rem, 1.2 ratio)

| Step | Size | Weight | Usage |
|---|---|---|---|
| display | 2rem | 600 | Report title, session header |
| h1 | 1.5rem | 600 | Section headings (Findings, Fix Proposals) |
| h2 | 1.25rem | 600 | Subsection headings (per-agent, per-dimension) |
| h3 | 1rem | 600 | Card headings, inline labels |
| body | 0.9375rem | 400 | Prose, descriptions, evidence text |
| label | 0.8125rem | 500 | Metadata, timestamps, column headers |
| small | 0.75rem | 400 | Footnotes, provenance markers |

Headings in Newsreader; body and below in Inter. Monospace for: score values, token counts, file paths, tool names.

### Measure

Body prose capped at 68ch. Data tables can run wider. The report uses a single-column layout with a max-width of ~780px for the content well (960px outer container).

## 4. Elevation

Flat. No box-shadows. Hierarchy is communicated through background tint (surface vs. bg), typographic weight, and spacing. The report is a printed page, not a layered interface.

The only elevation: a subtle `1px` border in `oklch(0.90 0.005 29)` on surface-level containers where the background alone doesn't provide enough separation (e.g., fix-proposal cards adjacent to one another). Used sparingly.

## 5. Components

### Score badge

A small inline pill showing a dimension score (0-5). Background color maps to the semantic score tier. Text is white. Mono font, tight padding. Reads as marginalia, not a button.

### Verdict summary

The report header: agent name, window dates, overall score as a large mono numeral, dimension scores as a compact horizontal row of score badges. No card border; separated by spacing alone.

### Fix-proposal card

A surface-colored block with: target (file/agent/rubric), dimension tag, recommendation text (marked as untrusted model output per ADR 0011), and rationale. Left-aligned, no decorative border. The dimension tag uses the score-tier color.

### Tool-sequence timeline

A compact vertical list: each row is one tool event (tool name in mono, error/denial flagged with primary color). Head and tail shown with an ellipsis for long sequences. No connecting lines or dots; indentation and spacing carry the sequence.

### Trend indicator

A small inline element showing prior-window delta: an arrow (up/down/flat) and a percentage. Rendered in score-tier color. Suppressed below `min_sessions_for_trend` with a muted "insufficient data" label.

### Parent-lens row

A compact row per parent session showing spawn fan-out: agent types and counts, any spawns with errors flagged. Reads as a summary table, not a card grid.

## 6. Do's and Don'ts

**Do:**
- Let typography carry the hierarchy. A well-set heading is more authoritative than a card border.
- Use primary (red oxide) only for findings that demand attention. The report should feel calm overall; red marks the exceptions.
- Present findings as narrative: task, evidence, recommendation. Not as disconnected data points.
- Mark untrusted model output (evidence, recommendations) distinctly from locally-derived facts (scores, tool counts). A subtle background tint or a provenance label.
- Use monospace for anything the user might grep for: tool names, file paths, session IDs, token counts.

**Don't:**
- Don't wrap every element in a card. Most content sits directly on the page ground.
- Don't use color as the sole indicator of score. Always pair with the numeric value.
- Don't animate on page load. The report opens and is immediately readable.
- Don't add interactive JavaScript beyond a dark-mode toggle (future) or section collapse. The report is a document, not an app.
- Don't use gradient fills, decorative borders, or ornamental icons. If it doesn't carry information, remove it.
- Don't put untrusted model output (judge-authored recommendations) in a visually authoritative position (large type, primary color). It's advisory; style it as such.
