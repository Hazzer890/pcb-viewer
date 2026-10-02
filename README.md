# pcb-viewer

A small PCB viewer for designing boards with Claude. Point it at a KiCad board or a Gerber folder and it opens in the browser, reloads whenever the file changes, and lets you click on the board to leave notes that Claude can read and act on.

## Usage

```
uv run pcbview.py board.kicad_pcb        # or a folder of Gerbers
uv run pcbview.py gerbers/ 8766          # optional port, default 8765
```

You need [uv](https://docs.astral.sh/uv/), and `kicad-cli` (tested with KiCad 10) for `.kicad_pcb` files. Gerbers are read with [gerbonara](https://gitlab.com/gerbolyze/gerbonara), which uv installs on first run.

- **Layers**: each layer as an SVG with on/off toggles, scroll to zoom, drag to pan, cursor position in mm in the corner.
- **Top / Bottom**: realistic renders, roughly what JLCPCB's preview shows. For Gerbers you can pick the mask colour and finish (HASL, ENIG, bare copper).
- **3D**: orbit the board. KiCad boards include component models (from `kicad-cli pcb export glb`), Gerbers get the outline extruded with the renders on each side.

## Notes

Click the board in the Layers view, type a note, and it is saved next to the source as `<source>.notes.json` (for example `board.kicad_pcb.notes.json`):

```json
[{"x": 30.72, "y": 33.54, "text": "Move logo 2mm up"}]
```

Coordinates are in mm, KiCad coordinates for `.kicad_pcb` and Gerber coordinates (Y up) for Gerbers. Claude can edit or clear that file and the viewer updates within a second.

## Claude Code skill

`SKILL.md` makes this a Claude Code skill. It launches the viewer when you start on a board, works through your notes when you say "check notes", renders the board to check its own edits, and regenerates Gerbers after changes. To install it, symlink the repo into your skills folder:

```
ln -s "$PWD" ~/.claude/skills/pcb-viewer
```

## Known limits

- Drilled holes are drawn but not cut through the 3D Gerber board, only the outline and its cutouts are.
- Copper under mask on the Gerber renders comes out a bit greyer than JLC's preview.
- The mask and finish picker only applies to Gerbers, KiCad boards use the colours set in the board stackup.
- three.js loads from a CDN the first time you open a 3D view.
