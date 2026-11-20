# Theia — Design System (FigureAI)

## Direction contract

- **THESIS:** A product dossier for a voice companion: expansive lab-white fields, exact black type, and a technical device drawing carry the visual weight. The interface recedes so the voice is the product.
- **OWN-WORLD:** Monochrome robotics-catalog restraint. Lab White canvas, Figure Black ink, Absolute Black media chapters, Machine Gray metadata, 1px Calibration Gray rules. No gradients, no saturated accents, no heavy shadows.
- **STORY:** A wake screen that reads like a spec sheet — hero statement, measured attributes, a dark field-recording chapter, and a console where the live controls live.
- **FIRST VIEWPORT:** Transparent header (brand, nav, status pill); split hero with the device drawing on the left and a 122px display title on the right; below, the spec rail.
- **FORM:** The device drawing is the live state indicator — its dashed ring and core react to listening/thinking/speaking via transform-only animation. The Console is the primary surface: voice lab, control grid, live activity feed.
- **FINISH:** Verified with Lighthouse 100/100/100/100 on both views, no console errors, no horizontal overflow at 390px, and reduced-motion fallback.

## Tokens

| Token | Value | Role |
|---|---|---|
| `--color-lab-white` | `#ffffff` | canvas |
| `--color-figure-black` | `#0c0c0c` | editorial text, filled controls |
| `--color-absolute-black` | `#000000` | field chapter |
| `--color-machine-gray` | `#6d6d6d` | values, labels, metadata |
| `--color-calibration-gray` | `#cecece` | 1px rules |
| `--font-pp-neue-machina-plain` | Space Grotesk | display, nav, stats (400) |
| `--font-neue-haas-grot-text` | Inter | body, labels, controls (400/500) |

Type scale: display 122px/0.92 -1.22px · media-heading 28px · editorial 22px · body-large 17px/1.6 -0.17px · body 16px/1.5 · nav/button 14px.

Radii: buttons 24px · utility controls 29px · pills 9999px · inputs/images 0px.
Spacing: element 20px · card padding 32px · section gap 40px+.

## Components

- **Header** — fixed 98px, transparent; `.scrolled` adds blur(16px) + hairline.
- **Device drawing** — concentric rings + core; ring spins (36s idle, 2.4s listening, 1.5s thinking), core pulses (1.2s listening, 0.5s speaking). Animation only on transform/opacity.
- **Spec rail** — right-aligned rows, Machine Gray 52px values, 1px rules, uppercase 12px labels; live-fed by server `config` payload.
- **Field chapter** — Absolute Black, SVG monitor grid + crosshair, REC + UTC timecode, white copy lower-left.
- **Console** — full-page view (`/console` or `#console`): voice lab (selectable rows + per-row Test), control grid (mic/agent/wake toggles at 29px — all default off; mic asks for browser permission on first enable), live mini-spec readout, and an activity feed fed by server `user`/`assistant`/`ignored` events with reply-latency chips. Test voice plays with a long fade and is never interruptible.
- **Footer** — nav matrix, no dead links, static metadata columns.

## State machine

body class `state-{idle,listening,thinking,speaking}` drives ring/core + status pill dot. Server dedupes identical state broadcasts, emits `user`/`assistant`/`ignored` events for the feed (full assistant text appears once her sentence is spoken), and logs to `server.log` with a resilient asyncio exception handler + auto-restart. Speech is serialized, chunked by sentence (no per-chunk fades mid-reply, no truncation on long replies), and spoken while the mic keeps listening — the playback worker drains each chunk fully (no timeout caps) before tearing down, so `speaking` stays true until the audio physically finishes; during speech, non-"stop" input is buffered and answered only after she finishes; the only interrupt is the literal word "stop" (or Escape in the console), which clears the queue; the test voice can never be interrupted.

## Responsive

- ≤1020px: single-column hero/specs/console; rail left-aligned.
- ≤720px: nav hidden, header 76px, control rail stacks, annotations culled.
- `prefers-reduced-motion`: all animation disabled.

## Accessibility

Labelled toggles with `aria-pressed`, `role=status` pill, focus-visible 2px outlines, matching visible/accessible names, contrast AA.
