# Theia — Command routing & intent domains

`intent.route(text)` returns one of: `tab`, `system`, `media`, `query`, `convo`.
`intent.classify(text)` returns `stop` / `music` / `computer` / `conversation` (used for event labels and the legacy dispatch fallback).

## Domains (mutually exclusive)

| Domain | Examples | Executes via |
|---|---|---|
| `tab` | "close the youtube tab", "minimize the window", "new tab", "next tab", "reopen last tab", "show desktop", "task view" | `actions.tab_control` (pygetwindow close/minimize + hotkeys Ctrl+W/T/Shift+T/Tab, Win+D, Win+Tab) — **never** search/media |
| `system` | removed — lock/sleep/restart/shutdown are disabled project-wide and never dispatch | — |
| `media` | "play X", "listen to X", "next song", "volume up", "mute" | media search/play, music engine, `_media`, `_volume` |
| `query` | "search X", "look up X", "ask google X" | Google search (blocked if a system verb is present) |
| `convo` | anything else | LLM chat |

## Dispatch order in `Hub._process_text`

1. compound patterns (open-yt-and-play / search-and-play / play-next-song)
2. mic/listening-state commands (mute/unmute mic, Theia quiet, wake)
3. `route` → `tab` → allowlist gate (`_tab_action` + registry) → `tab_control`
4. `classify` → `music|computer` → `actions.handle` → `_audit_dispatch` → registry gate (`_dispatch_action` + allowlist) → per-kind execution
5. fallback: LLM chat (history-capped)

## Media execution path

- `build_media_query(q)` appends `official audio` (for music-type queries) and `-shorts` unless already present; exempt: video/podcast/trailer/movie/etc.
- `_fetch_yt_video_ids` parses YouTube search HTML: bracket-matched `videoRenderer` blocks **with `lengthText`** (full-length only — Shorts/reels excluded), falls back to generic `videoId` extraction.
- `media_open(query, confirm)` → thread opens `https://www.youtube.com/watch?v=<id>` (top result by default; ordinal index supported). It never stays on a results page.
- Result is recorded in `session.register_media(title)` so follow-up clarifiers resolve against it.

## Fast computer verbs (fire-and-forget)

`FastComputer` (on top of computer-use-mcp):
- `media("next"/"prev"/"toggle"/"mute"/"unmute"/"up"/"down")`
- `nav("back"/"refresh"/"close_tab"/"new_tab"/"restore_tab"/"home"/"page_down"/"page_up"/"address"/"find"/"bookmark"/"replay"/"desktop"/"task_view"/"lock")`
- `click_label` / `dblclick_label` / `type_text` / `press` / `scroll` — OCR-assisted (legacy client) with a 1.2s screenshot cache; verification screenshot removed for speed.

## Audit gate (`_audit_dispatch`)

- `tab` domain may not produce `web` (search) actions.
- `query` domain may not produce `system`/`app`/`done`.
- `media` domain may not produce `system`.
- `system` domain may not produce `web`.
- Mismatch → refuse with "That didn't line up — say it again, master."

## Negative gate (search)

`actions.handle` will not run the search branch if the text contains a system verb (`close`, `stop`, `exit`, `minimize`, `hide`, `delete`, `kill`).

## Media control phrases (never searched)

Regex `_CTRL_PHRASE_RE` matches `the next|previous|prev|first|second|third|this|that|some|another song|track|video|one` — these route to next/previous in the focused window (Shift+N / Shift+P / media keys), never to a search or a new tab.

## Multi-turn context (session.py)

- `register_media(title)` stores `{title, clarifiers, at}` and sets `awaiting_clarification`.
- `resolve_followup(text)` matches clarifier vocabulary (original, official, album, slowed, reverb, sped up, nightcore, remix, live, acoustic, cover, full length, extended, radio edit, instrumental, music video, lyrics, …). Chained clarifiers accumulate (max 4), TTL 120s. Non-clarifier utterances leave context intact.
- Resolution: `"<title> <clarifiers> official audio"` → play immediately.

## Local music engine (vs media)

- `music` kind with no query → `MusicEngine.play(None)` (local `~/Music`).
- `play <title>` goes to YouTube media path. `play the next song` goes to focus-window next.
