# Product

## Register

product

## Users

Data engineers and developers who run custom Claude Code subagents. They review the report after a work session or weekly, looking for patterns: which agents performed well, which went off-track, and what to fix. They trust data, read fast, and want signal over noise.

## Product Purpose

agentlens turns raw session logs into scored assessments and actionable fix proposals for subagents. The HTML report is a first-class artifact: a self-contained, shareable single file that presents findings with the clarity of a well-edited technical article. It surfaces what matters (the fix, not just the score) and respects the reader's time.

## Brand Personality

Precise, opinionated, authoritative.

The voice of a senior engineer writing a post-incident review: direct, trusts the reader's intelligence, never over-explains, never decorates. Data earns its place; every element carries information.

## Anti-references

- **Generic SaaS dashboards**: cookie-cutter card grids, pastel gradients, "engagement metrics" aesthetic, decorative charts that don't inform.
- **Dense spreadsheet dumps**: raw tables with no hierarchy, no visual breathing room, no sense of priority.
- **Gamified/playful UI**: achievement badges, confetti, cartoon illustrations, streak counters, emoji-heavy scoring. The tool is serious about its findings.

## Design Principles

1. **The fix is the product, not the score.** A single number hides the why. Every view leads toward the actionable recommendation, not toward a leaderboard.
2. **Trust the reader.** Dense when it needs to be, sparse when it doesn't. No hand-holding copy, no redundant labels, no tooltips explaining obvious things.
3. **Hierarchy over decoration.** Typography, spacing, and weight do the work. No ornament that doesn't carry information. A well-set paragraph beats a card grid.
4. **Data has narrative.** Findings tell a story: what happened, why it matters, what to do. The layout follows that arc, not a generic grid.
5. **One report, self-contained.** The HTML file works offline, at `file://`, shared in Slack. No external dependencies, no loading states, no CDN.

## Accessibility & Inclusion

WCAG AA as baseline: 4.5:1 contrast for body text, 3:1 for large text. Reduced motion support via `prefers-reduced-motion`. Semantic HTML for screen readers. Color never as the sole indicator of state.
