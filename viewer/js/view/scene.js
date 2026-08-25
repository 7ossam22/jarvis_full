// js/view/scene.js — the 3D brain scene (View layer): ForceGraph3D setup,
// node/link rendering, brain shell/wireframe/dust construction, containment
// force, camera flight, and the whole-brain "speaking" glow animation.
// Uses the global THREE / ForceGraph3D from the <script> tags in index.html
// (loaded before this module) — not npm imports, matching the project's
// zero-build-step design.
import { graphData, neighborsOf, linkKey } from "../model/graphData.js";
import { openPanel, closePanel } from "./panel.js";

const GROUP_PALETTE = ["#6be3ff", "#c39bff", "#ff8fd6", "#7affe0", "#8fa8ff", "#ff9b7a", "#b0ffe0", "#d6b3ff"];
const groupColorCache = {};
function groupColor(group) {
  if (!groupColorCache[group]) {
    const idx = Object.keys(groupColorCache).length % GROUP_PALETTE.length;
    groupColorCache[group] = GROUP_PALETTE[idx];
  }
  return groupColorCache[group];
}

let highlightNodes = new Set();
let highlightLinks = new Set();
let brainGlowActive = false; // true while JARVIS is speaking — read by the
                              // accessors below so the whole brain visibly
                              // "fires" harder while he talks.

function setHighlight(nodeIds) {
  highlightNodes = new Set(nodeIds);
  highlightLinks = new Set();
  graphData.links.forEach(l => {
    const a = typeof l.source === "object" ? l.source.id : l.source;
    const b = typeof l.target === "object" ? l.target.id : l.target;
    if (highlightNodes.has(a) && highlightNodes.has(b)) highlightLinks.add(linkKey(l));
  });
  refreshStyles();
}

export function clearHighlight() { setHighlight([]); }

function refreshStyles() {
  Graph.nodeColor(Graph.nodeColor());
  Graph.nodeVal(Graph.nodeVal());
  Graph.linkColor(Graph.linkColor());
  Graph.linkWidth(Graph.linkWidth());
  Graph.linkDirectionalParticles(Graph.linkDirectionalParticles());
}

// simple radial-gradient glow texture, reused across all node sprites
const glowTexture = (() => {
  const size = 128;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.55)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(c);
})();

let Graph;
let BRAIN_RADIUS = 150;
let BRAIN_SHELL_BASE_OPACITY = 0.09;
let BRAIN_WIRE_BASE_OPACITY = 0.16;
let brainShell, brainWire;

function brainDeform(v, baseR) {
  // Cheap deterministic "noise" (a few offset sine waves) standing in for
  // cortical folds — no extra dependency needed for a stylized look.
  const n = Math.sin(v.x * 0.045) * 7 + Math.sin(v.y * 0.06 + 1.7) * 6 + Math.sin(v.z * 0.05 + 3.1) * 7;
  v.normalize().multiplyScalar(baseR + n);
  v.y *= 0.76;  // flatten top/bottom
  v.z *= 1.18;  // elongate front-to-back
  return v;
}

function buildBrainMesh(radius, detail, opacity, color, wireframe) {
  const geometry = new THREE.IcosahedronGeometry(radius, detail);
  const pos = geometry.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    brainDeform(v, radius);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  geometry.computeVertexNormals();
  const material = new THREE.MeshBasicMaterial({
    color, transparent: true, opacity, wireframe,
    blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
  });
  return new THREE.Mesh(geometry, material);
}

// faint ambient drift of "synaptic dust" inside the skull, for depth
function addNeuralDust() {
  const COUNT = 260;
  const positions = new Float32Array(COUNT * 3);
  for (let i = 0; i < COUNT; i++) {
    const v = new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5);
    brainDeform(v, BRAIN_RADIUS * (0.3 + Math.random() * 0.55));
    positions[i * 3] = v.x; positions[i * 3 + 1] = v.y; positions[i * 3 + 2] = v.z;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const material = new THREE.PointsMaterial({ color: 0xb9d4ff, size: 1.1, transparent: true, opacity: 0.35 });
  Graph.scene().add(new THREE.Points(geometry, material));
}

function hexToInt(hex, fallback) {
  if (typeof hex !== "string") return fallback;
  const n = parseInt(hex.replace("#", "0x"), 16);
  return Number.isNaN(n) ? fallback : n;
}

export function initScene(config) {
  BRAIN_RADIUS = config?.brain?.radius ?? 150;
  const shellColor = hexToInt(config?.brain?.shell_color, 0x8a6bff);
  const wireColor = hexToInt(config?.brain?.wire_color, 0x6be3ff);

  Graph = ForceGraph3D()(document.getElementById("graph"))
    .graphData(graphData)
    .backgroundColor("#05060a")
    .nodeLabel(n => `${n.label}`)
    .nodeColor(n => (highlightNodes.size === 0 || highlightNodes.has(n.id)) ? groupColor(n.group) : "rgba(120,130,150,0.25)")
    .nodeVal(n => highlightNodes.has(n.id) ? 9 : (brainGlowActive ? 6.5 : 4.5))
    .nodeResolution(16)
    .nodeOpacity(0.95)
    .linkColor(l => highlightLinks.has(linkKey(l)) ? "rgba(255,255,255,0.9)" : (brainGlowActive ? "rgba(170,190,255,0.4)" : "rgba(120,140,200,0.15)"))
    .linkWidth(l => highlightLinks.has(linkKey(l)) ? 2 : 0.5)
    .linkDirectionalParticles(l => highlightLinks.has(linkKey(l)) ? (brainGlowActive ? 4 : 2) : (brainGlowActive ? 1 : 0))
    .linkDirectionalParticleWidth(2)
    .linkDirectionalParticleSpeed(0.006)
    .nodeThreeObjectExtend(true)
    .nodeThreeObject(n => {
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowTexture, color: groupColor(n.group), transparent: true,
        opacity: 0.85, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      sprite.scale.set(11, 11, 1);
      return sprite;
    })
    .onNodeClick(node => flyToNode(node, { openPanel: true }))
    .onBackgroundClick(() => { clearHighlight(); closePanel(); });

  Graph.controls().autoRotate = true;
  Graph.controls().autoRotateSpeed = 0.35;
  Graph.controls().addEventListener("start", () => { Graph.controls().autoRotate = false; });
  let idleTimer = null;
  Graph.controls().addEventListener("end", () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { Graph.controls().autoRotate = true; }, 4000);
  });

  brainShell = buildBrainMesh(BRAIN_RADIUS, 4, BRAIN_SHELL_BASE_OPACITY, shellColor, false);
  brainWire = buildBrainMesh(BRAIN_RADIUS + 4, 2, BRAIN_WIRE_BASE_OPACITY, wireColor, true);
  Graph.scene().add(brainShell);
  Graph.scene().add(brainWire);
  Graph.scene().fog = new THREE.FogExp2(0x05060a, 0.0016);

  addNeuralDust();

  // Keep nodes (neurons) contained inside the brain silhouette instead of
  // drifting off into open space under the default force-graph physics.
  Graph.d3Force("containment", (alpha) => {
    graphData.nodes.forEach((n) => {
      if (n.x == null) return;
      const uy = n.y / 0.76, uz = n.z / 1.18; // undo the shell's squash/elongation
      const dist = Math.hypot(n.x, uy, uz) || 1;
      const maxR = BRAIN_RADIUS * 0.72;
      if (dist > maxR) {
        const pull = (dist - maxR) * 0.02 * alpha;
        n.vx -= (n.x / dist) * pull;
        n.vy -= (uy / dist) * pull * 0.76;
        n.vz -= (uz / dist) * pull * 1.18;
      }
    });
  });
  const chargeForce = Graph.d3Force("charge");
  if (chargeForce) chargeForce.strength(-40);
  const linkForce = Graph.d3Force("link");
  if (linkForce) linkForce.distance(28);

  Graph.cameraPosition({ x: 0, y: 40, z: 340 });
}

// ---------------------------------------------------------------------
// Camera flight / fly-to-source
// ---------------------------------------------------------------------
export function flyToNode(node, opts = {}) {
  if (!node) return;
  const distance = 140;
  const distRatio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
  Graph.cameraPosition(
    { x: (node.x || 0) * distRatio, y: (node.y || 0) * distRatio, z: (node.z || 0) * distRatio },
    node,
    1400
  );
  const ids = new Set([node.id, ...(neighborsOf[node.id] || [])]);
  setHighlight(ids);
  if (opts.openPanel) openPanel(node);
}

export function flyToCluster(ids) {
  const idSet = new Set(ids);
  setHighlight(idSet);
  Graph.zoomToFit(1400, 120, n => idSet.has(n.id));
}

export function refreshGraphData() {
  Graph.graphData(graphData);
}

// ---------------------------------------------------------------------
// Brain glow — pulses the shell, wireframe, and neuron/link accessors
// while JARVIS is speaking, so the whole brain visibly lights up rather
// than just his voice playing with a static scene underneath.
// ---------------------------------------------------------------------
let brainGlowStartTs = 0;
let brainGlowStyleTick = 0;
function brainGlowFrame(ts) {
  if (!brainGlowActive) return;
  if (!brainGlowStartTs) brainGlowStartTs = ts;
  const t = (ts - brainGlowStartTs) / 1000;
  const pulse = 0.5 + 0.5 * Math.sin(t * 3.4);
  brainShell.material.opacity = BRAIN_SHELL_BASE_OPACITY + pulse * 0.24;
  brainWire.material.opacity = BRAIN_WIRE_BASE_OPACITY + pulse * 0.32;
  const scale = 1 + pulse * 0.02;
  brainShell.scale.setScalar(scale);
  brainWire.scale.setScalar(scale);
  // re-evaluate node/link accessor functions periodically (not every frame —
  // this reassigns internal force-graph state, so it's throttled) so the
  // brainGlowActive-aware styling above actually takes visible effect.
  if (ts - brainGlowStyleTick > 180) {
    brainGlowStyleTick = ts;
    refreshStyles();
  }
  requestAnimationFrame(brainGlowFrame);
}

export function startBrainGlow() {
  if (brainGlowActive) return;
  brainGlowActive = true;
  brainGlowStartTs = 0;
  refreshStyles();
  requestAnimationFrame(brainGlowFrame);
}

export function stopBrainGlow() {
  brainGlowActive = false;
  brainShell.material.opacity = BRAIN_SHELL_BASE_OPACITY;
  brainWire.material.opacity = BRAIN_WIRE_BASE_OPACITY;
  brainShell.scale.setScalar(1);
  brainWire.scale.setScalar(1);
  refreshStyles();
}
