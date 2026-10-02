#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["gerbonara"]
# ///
"""Tiny PCB viewer. Usage: pcbview.py <board.kicad_pcb | gerber_dir> [port]

Renders layers to SVG, auto-reloads when the source changes, and lets you
click the board to leave notes. Notes are saved next to the source as
<source>.notes.json, in board coordinates (mm), for Claude to read.
"""
import json, math, re, subprocess, sys, tempfile, warnings, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SRC = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
NOTES = Path(str(SRC) + ".notes.json")
IS_KICAD = SRC.suffix == ".kicad_pcb"

KICAD_LAYERS = "B.Fab,B.Courtyard,B.Silkscreen,B.Mask,B.Cu,In1.Cu,In2.Cu,F.Cu,F.Mask,F.Silkscreen,F.Courtyard,F.Fab,User.Drawings,Edge.Cuts"
OFF_BY_DEFAULT = ("fab", "courtyard", "mask", "drawings")
# Approximations of JLCPCB's mask and surface finish options, for the realistic Gerber render.
MASKS = {"green": "#1b6b34", "blue": "#0b2f7a", "red": "#a51c1c", "black": "#151515", "white": "#f2f2f2",
         "yellow": "#d9b300", "purple": "#4b2a7a"}
FINISHES = {"hasl": "#cfcfcf", "enig": "#d9b25a", "bare": "#c99a82"}
GERBER_COLORS = {"copper": "#c83434", "silk": "#f2eda1", "mask": "#39a26a", "paste": "#999", "outline": "#d0d2cd", "drill": "#111"}


def mtime():
    files = [SRC] if SRC.is_file() else SRC.iterdir()
    return max(f.stat().st_mtime for f in files)


def kicad_layers():
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["kicad-cli", "pcb", "export", "svg", "--mode-multi", "-o", d, "-l", KICAD_LAYERS,
                        "--exclude-drawing-sheet", "--page-size-mode", "2", str(SRC)], check=True, capture_output=True)
        order = KICAD_LAYERS.split(",")
        found = {f.stem.split("-")[-1].replace("_", "."): f.read_text() for f in Path(d).glob("*.svg")}
        return [{"name": n, "svg": found[n]} for n in order if n in found]


def gerber_layers():
    from gerbonara import LayerStack
    warnings.simplefilter("ignore")
    stack = LayerStack.open(SRC)
    items = [(f"{side} {use}", use, layer) for (side, use), layer in stack.graphic_layers.items()]
    items += [("drill", "drill", d) for d in stack.drill_layers]
    rank = ["copper", "mask", "paste", "silk", "drill", "outline"]
    items.sort(key=lambda i: (i[1] in ("drill", "outline"), i[0].startswith("top"), rank.index(i[1]) if i[1] in rank else 9))  # bottom side first
    out = []
    for i, (name, use, layer) in enumerate(items):
        # Draw through a mask so clear-polarity shapes erase (e.g. silk clipped off pads by --subtract-soldermask)
        # instead of being painted white.
        svg = str(layer.to_svg(fg="white", bg="black"))
        # gerbonara crops each layer differently; drop that so every layer shares raw Gerber coords (Y up).
        svg = re.sub(r'<g transform="[^"]*">', '<g transform="scale(1 -1)">', svg, count=1)
        body = svg[svg.index(">", svg.index("<svg")) + 1:svg.rindex("</svg>")]
        color = "#4d7fc4" if name == "bottom copper" else GERBER_COLORS.get(use, "#888")
        out.append({"name": name, "svg": f'<svg xmlns="http://www.w3.org/2000/svg"><mask id="m{i}" maskUnits="userSpaceOnUse" '
                    f'x="-1e4" y="-1e4" width="2e4" height="2e4">{body}</mask><rect x="-1e4" y="-1e4" width="2e4" height="2e4" '
                    f'fill="{color}" mask="url(#m{i})"/></svg>'})
    return out, stack.board_bounds()


def kicad_glb():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "b.glb"
        subprocess.run(["kicad-cli", "pcb", "export", "glb", "-o", str(out), "--subst-models", "--include-tracks",
                        "--include-pads", "--include-zones", "--include-silkscreen", "--include-soldermask", str(SRC)],
                       check=True, capture_output=True)
        return out.read_bytes()


def outline_loops(stack):
    """Board outline as closed point lists (mm, Gerber coords), arcs sampled into 16 segments."""
    loops = []
    for loop in stack.outline_polygons():
        if len(loop) > 1 and loop[-1] == loop[0]:  # gerbonara repeats the first segment to close; it breaks triangulation
            loop = loop[:-1]
        pts = []
        for p in loop:
            if hasattr(p, "cx"):
                a0, a1 = math.atan2(p.y1 - p.cy, p.x1 - p.cx), math.atan2(p.y2 - p.cy, p.x2 - p.cx)
                if p.clockwise and a1 >= a0:
                    a1 -= 2 * math.pi
                elif not p.clockwise and a1 <= a0:
                    a1 += 2 * math.pi
                r = math.hypot(p.x1 - p.cx, p.y1 - p.cy)
                pts += [(p.cx + r * math.cos(a0 + (a1 - a0) * i / 16), p.cy + r * math.sin(a0 + (a1 - a0) * i / 16)) for i in range(16)]
            else:
                pts.append((p.x1, p.y1))
        loops.append(pts)
    return loops


def gerber_pretty(mask="green", finish="hasl"):
    from gerbonara import LayerStack
    warnings.simplefilter("ignore")
    stack = LayerStack.open(SRC)
    colors = {"copper": FINISHES.get(finish, "#cfcfcf"), "mask": MASKS.get(mask, MASKS["green"]) + "d9",
              "silk": "#111111" if mask == "white" else "#f4f4f4", "paste": "#999999", "drill": "#111111", "outline": "#00000000"}
    # Drop the white page background so everything outside the board comes out transparent.
    out = {side: str(stack.to_pretty_svg(side=side, colors=colors)).replace("background-color:white", "")
           for side in ("top", "bottom")}
    return json.dumps(out | {"bounds": stack.board_bounds(), "loops": outline_loops(stack), "mask": colors["mask"][:7]})


class H(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json"):
        body = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if self.path == "/":
            self.send(PAGE.replace("$TITLE", SRC.name), "text/html")
        elif self.path == "/mtime":
            self.send(str(mtime()))
        elif self.path == "/board":
            try:
                layers, bounds = (kicad_layers(), None) if IS_KICAD else gerber_layers()
                self.send(json.dumps({"yUp": not IS_KICAD, "off": OFF_BY_DEFAULT, "layers": layers, "bounds": bounds}))
            except Exception as e:  # half-written file mid-save: report, the next poll retries
                self.send(json.dumps({"error": str(getattr(e, "stderr", b"") or e)}))
        elif url.path == "/model":  # 3D source: KiCad GLB with parts, or realistic Gerber renders for a slab
            try:
                self.send(kicad_glb(), "model/gltf-binary") if IS_KICAD else self.send(gerber_pretty(**q))
            except Exception as e:
                self.send_error(500, str(getattr(e, "stderr", b"") or e)[:200])
        elif self.path == "/notes":
            self.send(NOTES.read_text() if NOTES.exists() else "[]")
        else:
            self.send_error(404)

    def do_POST(self):
        notes = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        NOTES.write_text(json.dumps(notes, indent=2) + "\n")
        self.send("ok")

    def log_message(self, *a):
        pass


PAGE = r"""<!doctype html><meta charset=utf-8><title>$TITLE</title>
<style>
body{margin:0;display:flex;height:100vh;background:#111;color:#ddd;font:13px system-ui,sans-serif}
#side{width:220px;padding:10px;overflow:auto;border-right:1px solid #333}
#side label{display:block}
#v{flex:1;cursor:crosshair}
#pos{position:fixed;bottom:6px;right:10px;color:#888}
#err{color:#f66;white-space:pre-wrap}
#three{flex:1;min-width:0;overflow:hidden} #three canvas{display:block} #views button.on{background:#ff0}
ol{padding-left:18px} li{margin:4px 0} li button{margin-left:4px}
.note circle{fill:#ff0;stroke:#000;stroke-width:.15} .note text{font-size:1.4px;text-anchor:middle;fill:#000}
</style>
<div id=side><b>$TITLE</b><div id=err></div>
<p id=views><button data-v=layers class=on>Layers</button> <button data-v=top>Top</button> <button data-v=bottom>Bottom</button> <button data-v=3d>3D</button></p>
<p id=look hidden>Mask <select id=mask><option>green<option>blue<option>red<option>black<option>white<option>yellow<option>purple</select>
Finish <select id=finish><option value=hasl>HASL<option value=enig>ENIG<option value=bare>Copper</select></p>
<h4>Layers</h4><div id=layers></div>
<h4>Notes <small>(click board)</small></h4><ol id=notes></ol></div>
<svg id=v xmlns="http://www.w3.org/2000/svg"><g id=cam></g><g id=pins></g></svg><div id=three hidden></div>
<div id=pos></div>
<script>
const v = document.getElementById('v'), cam = document.getElementById('cam'), pins = document.getElementById('pins');
let board, notes = [], vb, last = 0, hidden = null;

const view = () => v.setAttribute('viewBox', vb.join(' '));
const toSvg = e => { const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(v.getScreenCTM().inverse()); return [p.x, p.y]; };
// Board coords: KiCad is Y-down already; Gerber layers are drawn flipped, so flip back to report real Gerber Y.
const toBoard = ([x, y]) => [+x.toFixed(2), +(board.yUp ? -y : y).toFixed(2)];
const fromBoard = (x, y) => [x, board.yUp ? -y : y];

async function load() {
  const b = await (await fetch('/board')).json();
  document.getElementById('err').textContent = b.error || '';
  if (b.error) return;
  board = b;
  hidden ??= new Set(b.layers.map(l => l.name).filter(n => b.off.some(o => n.toLowerCase().includes(o))));
  cam.innerHTML = '';
  for (const l of b.layers) {
    const svg = new DOMParser().parseFromString(l.svg, 'image/svg+xml').documentElement;
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.dataset.name = l.name;
    g.style.opacity = /mask/i.test(l.name) ? .4 : .85;
    for (const c of [...svg.children]) if (!['title', 'desc'].includes(c.tagName)) g.append(c);
    cam.append(g);
  }
  document.getElementById('layers').innerHTML = b.layers.map(l =>
    `<label><input type=checkbox data-n="${l.name}" ${hidden.has(l.name) ? '' : 'checked'}> ${l.name}</label>`).join('');
  applyHidden();
  if (!vb) {
    // Gerber layers are giant masked rects, so fit to the board bounds (flipped to screen Y) instead of measuring.
    const [[x0, y0], [x1, y1]] = b.bounds || [[0, 0], [0, 0]];
    const r = b.bounds ? {x: x0, y: -y1, width: x1 - x0, height: y1 - y0} : cam.getBBox(), m = Math.max(r.width, r.height) * .05; vb = [r.x - m, r.y - m, r.width + 2 * m, r.height + 2 * m]; view();
  }
  drawNotes();
}
function applyHidden() { for (const g of cam.children) g.style.display = hidden.has(g.dataset.name) ? 'none' : ''; }
document.getElementById('layers').onchange = e => { const n = e.target.dataset.n; e.target.checked ? hidden.delete(n) : hidden.add(n); applyHidden(); };

function drawNotes() {
  if (!vb) return;  // first render still in flight
  const r = vb[2] / 80;
  pins.innerHTML = notes.map((n, i) => { const [x, y] = fromBoard(n.x, n.y);
    return `<g class=note transform="translate(${x} ${y}) scale(${r})"><circle r=1.2></circle><text y=.5>${i + 1}</text></g>`; }).join('');
  const ol = document.getElementById('notes'); ol.innerHTML = '';
  notes.forEach((n, i) => { const li = document.createElement('li'); li.textContent = `(${n.x}, ${n.y}) ${n.text} `;
    const del = document.createElement('button'); del.textContent = '×'; del.onclick = () => { notes.splice(i, 1); save(); };
    li.append(del); ol.append(li); });
}
async function save() { await fetch('/notes', {method: 'POST', body: JSON.stringify(notes)}); drawNotes(); }

// pan / zoom / click-to-note
let drag = null;
v.onpointerdown = e => { drag = {x: e.clientX, y: e.clientY, vb: [...vb], moved: false}; v.setPointerCapture(e.pointerId); };
v.onpointermove = e => {
  if (board) document.getElementById('pos').textContent = toBoard(toSvg(e)).join(', ') + ' mm';
  if (!drag) return;
  const k = vb[2] / v.clientWidth, dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  vb = [drag.vb[0] - dx * k, drag.vb[1] - dy * k, vb[2], vb[3]]; view();
};
v.onpointerup = e => {
  const d = drag; drag = null;
  if (!d || d.moved || !board) return;  // pointerup can arrive without a pointerdown on this element
  const text = prompt('Note at ' + toBoard(toSvg(e)).join(', '));
  if (text) { const [x, y] = toBoard(toSvg(e)); notes.push({x, y, text}); save(); }
};
v.onwheel = e => {
  e.preventDefault();
  const [px, py] = toSvg(e), f = e.deltaY > 0 ? 1.2 : 1 / 1.2;
  vb = [px - (px - vb[0]) * f, py - (py - vb[1]) * f, vb[2] * f, vb[3] * f]; view(); drawNotes();
};

// Auto-reload when the board or the notes file changes on disk (Claude edits either).
setInterval(async () => {
  const m = +await (await fetch('/mtime')).text();
  if (m !== last) { last = m; dispatchEvent(new Event('boardchange')); await load(); }
  // Only redraw on change: rebuilding the list every tick swaps the × buttons out mid-click.
  const n = await (await fetch('/notes')).json();
  if (JSON.stringify(n) !== JSON.stringify(notes)) { notes = n; drawNotes(); }
}, 1000);
</script>
<script type=importmap>{"imports": {"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>
<script type=module>
// Realistic render: Top/Bottom are the JLC-style 2D views, 3D orbits. KiCad -> GLB with parts; Gerbers -> textured slab.
const box = document.getElementById('three'), [look, mask, finish] = ['look', 'mask', 'finish'].map(id => document.getElementById(id));
let T, renderer, scene, camera, controls, model, size, stale = true, mode = 'layers';

async function init() {
  T = await import('three');
  const {OrbitControls} = await import('three/addons/controls/OrbitControls.js');
  const {RoomEnvironment} = await import('three/addons/environments/RoomEnvironment.js');
  renderer = new T.WebGLRenderer({antialias: true});
  box.append(renderer.domElement);
  scene = new T.Scene();
  scene.background = new T.Color(0x111111);
  scene.environment = new T.PMREMGenerator(renderer).fromScene(new RoomEnvironment()).texture;
  camera = new T.OrthographicCamera();
  controls = new OrbitControls(camera, renderer.domElement);
  new ResizeObserver(fit).observe(box);
  renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
}

async function tex(svg) {  // rasterise at 2048px so zooming stays crisp
  const img = new Image();
  img.src = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
  await img.decode();
  const [, , w, h] = svg.match(/viewBox="([^"]+)"/)[1].split(' ').map(Number), k = 2048 / Math.max(w, h);
  const c = Object.assign(document.createElement('canvas'), {width: w * k, height: h * k});
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  const t = new T.CanvasTexture(c);
  t.colorSpace = T.SRGBColorSpace;
  t.anisotropy = 8;
  return [t, w, h];
}

// Board body is the real outline extruded; the largest loop is the edge, the rest are cutouts.
// ponytail: drilled holes aren't cut, they're only drawn in the texture; add them from the drill layer if it matters.
async function slab({top, bottom, bounds: [[x0, y0], [x1, y1]], loops, mask}) {
  const [tt] = await tex(top), [bt] = await tex(bottom), w = x1 - x0, h = y1 - y0, th = 1.6, g = new T.Group();
  const v2 = l => l.map(([x, y]) => new T.Vector2(x, y));
  loops.sort((a, b) => Math.abs(T.ShapeUtils.area(v2(b))) - Math.abs(T.ShapeUtils.area(v2(a))));
  const shape = new T.Shape(v2(loops[0]));
  shape.holes = loops.slice(1).map(l => new T.Path(v2(l)));
  // Shape UVs are raw mm; map them onto the render's board bounds. Bottom render is pre-mirrored: u = (x1 - x) / w.
  tt.repeat.set(1 / w, 1 / h); tt.offset.set(-x0 / w, -y0 / h);
  bt.repeat.set(-1 / w, 1 / h); bt.offset.set(x1 / w, -y0 / h);
  // Caps in mask colour so any gap in the render (gerbonara misses the odd corner) reads as mask; sides are FR4.
  const body = new T.Mesh(new T.ExtrudeGeometry(shape, {depth: th, bevelEnabled: false}),
    [new T.MeshBasicMaterial({color: mask}), new T.MeshStandardMaterial({color: 0xc8b88a})]);
  body.position.z = -th / 2;
  // Unlit faces so the mask/finish colours come out as picked, not washed out by the environment light.
  const face = (map, z, side) => { const m = new T.Mesh(new T.ShapeGeometry(shape), new T.MeshBasicMaterial({map, side, alphaTest: .5}));
    m.position.z = z; return m; };
  g.add(body, face(tt, th / 2 + .01, T.FrontSide), face(bt, -th / 2 - .01, T.BackSide));
  g.rotation.x = -Math.PI / 2;  // Gerber +Y -> -Z (screen up in top view), board normal -> +Y
  return g;
}

async function loadModel() {
  stale = false;
  const r = await fetch('/model?' + new URLSearchParams({mask: mask.value, finish: finish.value}));
  if (!r.ok) return document.getElementById('err').textContent = r.statusText;
  let obj;
  if (r.headers.get('content-type').includes('gltf')) {
    const {GLTFLoader} = await import('three/addons/loaders/GLTFLoader.js');
    obj = (await new GLTFLoader().parseAsync(await r.arrayBuffer(), '')).scene;
  } else { look.hidden = false; obj = await slab(await r.json()); }
  const b = new T.Box3().setFromObject(obj);
  obj.position.sub(b.getCenter(new T.Vector3()));
  size = b.getSize(new T.Vector3());
  if (model) scene.remove(model);
  scene.add(model = obj);
}

function fit() {
  if (!size) return;  // observer fires before the first model arrives
  const w = box.clientWidth, h = box.clientHeight, a = w / h, S = Math.max(size.x, size.z);
  renderer.setSize(w, h);
  const half = Math.max(size.z, size.x / a) * .55;
  Object.assign(camera, {left: -half * a, right: half * a, top: half, bottom: -half, near: -S * 10, far: S * 10});
  camera.updateProjectionMatrix();
}

function preset(v) {
  const d = Math.max(size.x, size.z) * 2, e = d * 1e-3;  // tiny Z offset fixes screen-up so board isn't spun
  camera.position.set(...{top: [0, d, e], bottom: [0, -d, -e], '3d': [d * .4, d * .6, d * .7]}[v]);
  camera.zoom = v === '3d' ? .75 : 1;  // tilted view needs more room than the flat fit
  controls.target.set(0, 0, 0);
  fit();
}

document.getElementById('views').onclick = async e => {
  const v = e.target.dataset.v;
  if (!v) return;
  mode = v;
  for (const b of document.querySelectorAll('#views button')) b.classList.toggle('on', b === e.target);
  document.getElementById('v').style.display = v === 'layers' ? '' : 'none';  // SVG has no .hidden
  box.hidden = v === 'layers';
  if (v === 'layers') return;
  if (!renderer) await init();
  if (stale) await loadModel();
  preset(v);
};
look.onchange = loadModel;
addEventListener('boardchange', () => { stale = true; if (mode !== 'layers' && renderer) loadModel(); });
</script>"""

if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    print(f"Viewing {SRC} at {url}\nNotes: {NOTES}")
    webbrowser.open(url)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
