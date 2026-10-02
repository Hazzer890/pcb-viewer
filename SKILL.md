---
name: pcb-viewer
description: Live PCB viewer and review loop for KiCad (.kicad_pcb) and Gerber boards. Use whenever creating, editing, or reviewing a PCB with Claude, when a .kicad_pcb or Gerber folder is being generated or changed, or when the user says "check notes", "look at the board", "open the viewer", or refers to notes they left on the board.
---

# PCB viewer

`pcbview.py` sits next to this file. It serves a browser view of a board that auto-reloads on save, with a Layers view (per-layer SVG, click to leave notes), Top/Bottom realistic renders, and an orbitable 3D view. Notes the user leaves are written to `<source>.notes.json` next to the board.

## 1. Launch the viewer

Once per session per board, as soon as you start working on it, run in the background:

```
uv run <this skill dir>/pcbview.py <board.kicad_pcb | gerber_dir> [port]
```

- Default port 8765. If `ss -ltn | grep :8765` shows it taken (another board, or already running), pick the next port. Do not start a second viewer on a board that already has one.
- It opens the browser itself. Tell the user the URL once.
- Prefer the `.kicad_pcb` over Gerbers when both exist: it shows components in 3D and renders exactly what KiCad will export.
- Needs `kicad-cli` for KiCad boards; `uv` pulls in `gerbonara` for Gerbers.

## 2. Read and resolve notes

When the user says "check notes" (or similar), or at the start of a turn on a board whose `<source>.notes.json` is non-empty:

1. Read `<source>.notes.json`: a list of `{"x", "y", "text"}`, coordinates in mm.
   - `.kicad_pcb`: KiCad board coordinates (Y down), same as in the file.
   - Gerber folder: Gerber coordinates (Y up). For Gerbers exported from KiCad, KiCad Y = -Gerber Y.
2. Find what is at each point (footprint, track, zone, graphic) and make the edit the note asks for. If a note is ambiguous, ask rather than guess.
3. Remove each note you addressed from the file and leave the rest. Rewrite the file as a JSON list (`[]` when all are done). The viewer picks up the change within a second.
4. Tell the user in one line per note what you changed.

## 3. Self-check renders

After a meaningful edit, look at the result before reporting back:

- KiCad: `kicad-cli pcb render --side top --quality basic --width 800 --height 1000 -o <scratch>/top.png <board>` (about 2 s), then Read the PNG. Use `--side bottom` when the change is on the back.
- Gerbers only: screenshot the viewer's Top view with the browser tools.

Check the edit landed where intended and nothing obvious broke (silk on pads, text off the board, overlapping parts). Fix before reporting.

## 4. Keep Gerbers in sync

If the project has a Gerber folder or a generator script that exports Gerbers, regenerate them after editing the `.kicad_pcb`, using the project's existing export command or flags (for example `--subtract-soldermask`) so the fab output matches the board. Mention it if you could not.
