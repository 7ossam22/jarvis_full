// js/view/scene.js — Vanilla Latte 3D Humanoid Brain Scene (View layer).
// Renders an anatomical 3D Humanoid Brain (dual cerebral hemispheres,
// cortical gyri/sulci folds, cerebellum, brainstem, corpus callosum internal
// axon pathways) with glowing neurons inside that surge with warm energy
// when speaking.
import { graphData, neighborsOf, linkKey } from "../model/graphData.js";
import { openPanel, closePanel } from "./panel.js";

const GROUP_PALETTE = [
  "#f4a261", "#e6ccb2", "#d4a373", "#e9c46a",
  "#faedcd", "#ccd5ae", "#ddb892", "#b08968"
];
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
let brainGlowActive = false; // true while JARVIS is speaking
let wireframeVisible = true;

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
  if (!Graph) return;
  Graph.nodeColor(Graph.nodeColor());
  Graph.nodeVal(Graph.nodeVal());
  Graph.linkColor(Graph.linkColor());
  Graph.linkWidth(Graph.linkWidth());
  Graph.linkDirectionalParticles(Graph.linkDirectionalParticles());
}

// Warm golden radial-gradient glow texture for neuron soma points
const glowTexture = (() => {
  const size = 128;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(254, 250, 224, 1)");
  g.addColorStop(0.35, "rgba(244, 162, 97, 0.6)");
  g.addColorStop(1, "rgba(212, 163, 115, 0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(c);
})();

let Graph;
let BRAIN_RADIUS = 140;
let BRAIN_SHELL_BASE_OPACITY = 0.12;
let BRAIN_WIRE_BASE_OPACITY = 0.22;
let brainShell, brainWire, brainStemMesh;

// ---------------------------------------------------------------------
// Anatomical Humanoid Brain Mesh Deformation Algorithm
// Computes dual cerebral hemispheres, longitudinal fissure, frontal/
// parietal/occipital/temporal lobes, cerebellum, and brain stem.
// ---------------------------------------------------------------------
function humanoidBrainDeform(v, baseR) {
  const u = v.clone().normalize();
  const isLeft = u.x < 0;
  const side = isLeft ? -1 : 1;

  // 1. Brain Stem (Inferior Central Base)
  const isStem = Math.abs(u.x) < 0.24 && u.y < -0.32 && u.z > -0.35 && u.z < 0.35;
  if (isStem) {
    v.x = u.x * baseR * 0.35;
    v.z = (u.z * baseR * 0.4) - baseR * 0.2;
    v.y = u.y * baseR * 1.15;
    return v;
  }

  // 2. Cerebellum (Infero-Posterior Base)
  const isCerebellum = u.y < -0.28 && u.z < -0.32 && Math.abs(u.x) < 0.72;
  if (isCerebellum) {
    const cerX = (u.x - side * 0.2) * 1.35;
    const cerY = (u.y + 0.52) * 2.0;
    const cerZ = (u.z + 0.62) * 1.9;
    const cerDist = Math.sqrt(cerX * cerX + cerY * cerY + cerZ * cerZ);
    // Folia (fine horizontal striation grooves)
    const folia = Math.sin(u.y * 42.0) * 2.2;
    const cerR = baseR * 0.42 + folia;
    v.x = (u.x * 0.8) + (side * baseR * 0.14);
    v.y = -baseR * 0.46 + (u.y * cerR * 0.52);
    v.z = -baseR * 0.50 + (u.z * cerR * 0.52);
    return v;
  }

  // 3. Cerebral Hemispheres (Left & Right Cortex)
  // Longitudinal fissure (sagittal gap dividing left and right hemispheres)
  const distFromGap = Math.abs(u.x);
  const fissureIndent = Math.exp(-distFromGap * 8.5) * (baseR * 0.26);

  // Anatomical Lobe Proportion Scaling
  let lobeX = 0.96;
  let lobeY = 0.82; // slightly flattened top-to-bottom
  let lobeZ = 1.22; // elongated front-to-back

  // Frontal lobe prefrontal bulge
  if (u.z > 0.25) lobeZ += Math.sin((u.z - 0.25) * Math.PI) * 0.16;
  // Temporal lobe lateral expansion
  if (u.y < 0.12 && u.y > -0.48 && Math.abs(u.x) > 0.38) lobeX += 0.20;

  // Cortical Gyri and Sulci Folds (Multi-frequency trigonometric harmonics)
  const f1 = Math.sin(u.x * 11.5) * Math.cos(u.y * 9.5) * Math.sin(u.z * 10.5);
  const f2 = Math.sin(u.x * 20.0 + 1.2) * Math.sin(u.y * 18.0 + 0.6) * Math.cos(u.z * 16.0);
  const f3 = Math.cos(u.x * 5.5) * Math.sin(u.z * 6.5 + u.y * 4.5);
  const gyriSulci = (f1 * 5.2 + f2 * 2.8 + f3 * 3.8);

  const finalR = baseR * 0.86 + gyriSulci - fissureIndent;

  v.x = u.x * finalR * lobeX;
  v.y = u.y * finalR * lobeY;
  v.z = u.z * finalR * lobeZ;

  // Center gap separation
  if (Math.abs(v.x) < 7 && v.y > -baseR * 0.28) {
    v.x += side * 3.5;
  }

  return v;
}

function buildHumanoidBrainMesh(radius, detail, opacity, color, wireframe) {
  const geometry = new THREE.IcosahedronGeometry(radius, detail);
  const pos = geometry.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    humanoidBrainDeform(v, radius);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  geometry.computeVertexNormals();
  const material = new THREE.MeshBasicMaterial({
    color, transparent: true, opacity, wireframe,
    blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
  });
  return new THREE.Mesh(geometry, material);
}

// Ambient neural dust floating inside the brain cavity
function addNeuralDust() {
  const COUNT = 320;
  const positions = new Float32Array(COUNT * 3);
  for (let i = 0; i < COUNT; i++) {
    const v = new THREE.Vector3((Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2);
    humanoidBrainDeform(v, BRAIN_RADIUS * (0.25 + Math.random() * 0.55));
    positions[i * 3] = v.x;
    positions[i * 3 + 1] = v.y;
    positions[i * 3 + 2] = v.z;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const material = new THREE.PointsMaterial({
    color: 0xf4a261, size: 1.4, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending
  });
  Graph.scene().add(new THREE.Points(geometry, material));
}

function hexToInt(hex, fallback) {
  if (typeof hex !== "string") return fallback;
  const n = parseInt(hex.replace("#", "0x"), 16);
  return Number.isNaN(n) ? fallback : n;
}

export function initScene(config) {
  BRAIN_RADIUS = config?.brain?.radius ?? 140;
  const shellColor = hexToInt(config?.brain?.shell_color, 0xd4a373);
  const wireColor = hexToInt(config?.brain?.wire_color, 0xf4a261);

  Graph = ForceGraph3D()(document.getElementById("graph"))
    .graphData(graphData)
    .backgroundColor("#15110f")
    .nodeLabel(n => `<div style="background: rgba(28,23,20,0.9); padding: 4px 10px; border-radius: 8px; border: 1px solid #d4a373; font-family: Outfit, sans-serif; color: #faedcd;">${n.label}</div>`)
    .nodeColor(n => (highlightNodes.size === 0 || highlightNodes.has(n.id)) ? groupColor(n.group) : "rgba(168,152,136,0.2)")
    .nodeVal(n => highlightNodes.has(n.id) ? 9.5 : (brainGlowActive ? 7.0 : 4.8))
    .nodeResolution(16)
    .nodeOpacity(0.95)
    .linkColor(l => highlightLinks.has(linkKey(l)) ? "rgba(254,250,224,0.95)" : (brainGlowActive ? "rgba(244,162,97,0.55)" : "rgba(212,163,115,0.2)"))
    .linkWidth(l => highlightLinks.has(linkKey(l)) ? 2.4 : (brainGlowActive ? 1.0 : 0.6))
    .linkDirectionalParticles(l => highlightLinks.has(linkKey(l)) ? (brainGlowActive ? 5 : 3) : (brainGlowActive ? 2 : 0))
    .linkDirectionalParticleWidth(2.5)
    .linkDirectionalParticleSpeed(0.008)
    .nodeThreeObjectExtend(true)
    .nodeThreeObject(n => {
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowTexture, color: groupColor(n.group), transparent: true,
        opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      sprite.scale.set(12, 12, 1);
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

  // Construct Humanoid Brain Shell & Wireframe Cortex
  brainShell = buildHumanoidBrainMesh(BRAIN_RADIUS, 4, BRAIN_SHELL_BASE_OPACITY, shellColor, false);
  brainWire = buildHumanoidBrainMesh(BRAIN_RADIUS + 3, 2, BRAIN_WIRE_BASE_OPACITY, wireColor, true);
  Graph.scene().add(brainShell);
  Graph.scene().add(brainWire);
  Graph.scene().fog = new THREE.FogExp2(0x15110f, 0.0015);

  addNeuralDust();

  // Containment force: Constrains neuron nodes inside the anatomical humanoid brain shape
  Graph.d3Force("containment", (alpha) => {
    graphData.nodes.forEach((n) => {
      if (n.x == null) return;
      const uy = n.y / 0.82, uz = n.z / 1.22;
      const dist = Math.hypot(n.x, uy, uz) || 1;
      const maxR = BRAIN_RADIUS * 0.74;
      if (dist > maxR) {
        const pull = (dist - maxR) * 0.022 * alpha;
        n.vx -= (n.x / dist) * pull;
        n.vy -= (uy / dist) * pull * 0.82;
        n.vz -= (uz / dist) * pull * 1.22;
      }
    });
  });
  const chargeForce = Graph.d3Force("charge");
  if (chargeForce) chargeForce.strength(-42);
  const linkForce = Graph.d3Force("link");
  if (linkForce) linkForce.distance(30);

  Graph.cameraPosition({ x: 0, y: 35, z: 320 });

  initToolbarControls();
  initVoiceWaveformCanvas();
  initInteractiveUI();
}

// ---------------------------------------------------------------------
// Search Filter, View Mode Switcher, and Analytics Dashboard Modal
// ---------------------------------------------------------------------
function initInteractiveUI() {
  const searchInput = document.getElementById("graph-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase().trim();
      if (!q) {
        clearHighlight();
        return;
      }
      const matches = graphData.nodes.filter(n =>
        n.label.toLowerCase().includes(q) || (n.group && n.group.toLowerCase().includes(q))
      ).map(n => n.id);
      if (matches.length) setHighlight(matches);
    });
  }

  const btnCortex = document.getElementById("mode-cortex");
  const btnNetwork = document.getElementById("mode-network");
  const btnAnalytics = document.getElementById("mode-analytics");
  const modal = document.getElementById("analytics-modal");
  const modalClose = document.getElementById("analytics-close");

  function setModeActive(btn) {
    [btnCortex, btnNetwork, btnAnalytics].forEach(b => b && b.classList.remove("active"));
    if (btn) btn.classList.add("active");
  }

  if (btnCortex) {
    btnCortex.addEventListener("click", () => {
      setModeActive(btnCortex);
      if (brainShell) brainShell.visible = true;
      if (brainWire) brainWire.visible = wireframeVisible;
      modal && modal.classList.add("hidden");
    });
  }

  if (btnNetwork) {
    btnNetwork.addEventListener("click", () => {
      setModeActive(btnNetwork);
      if (brainShell) brainShell.visible = false;
      if (brainWire) brainWire.visible = false;
      modal && modal.classList.add("hidden");
    });
  }

  if (btnAnalytics) {
    btnAnalytics.addEventListener("click", () => {
      setModeActive(btnAnalytics);
      openAnalyticsModal();
    });
  }

  if (modalClose) {
    modalClose.addEventListener("click", () => {
      modal && modal.classList.add("hidden");
      setModeActive(btnCortex);
    });
  }
}

function openAnalyticsModal() {
  const modal = document.getElementById("analytics-modal");
  if (!modal) return;

  const totalNotesEl = document.getElementById("stat-total-notes");
  const totalLinksEl = document.getElementById("stat-total-links");
  const clusterListEl = document.getElementById("cluster-list");

  if (totalNotesEl) totalNotesEl.textContent = graphData.nodes.length;
  if (totalLinksEl) totalLinksEl.textContent = graphData.links.length;

  if (clusterListEl) {
    const groups = {};
    graphData.nodes.forEach(n => {
      groups[n.group] = (groups[n.group] || 0) + 1;
    });
    clusterListEl.innerHTML = Object.entries(groups)
      .map(([grp, count]) => `<div class="cluster-item"><strong>${grp}</strong> (${count} notes)</div>`)
      .join("");
  }

  modal.classList.remove("hidden");
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
  const countEl = document.getElementById("synapse-count");
  if (countEl) countEl.textContent = `Pathways: ${graphData.links.length}`;
}

// ---------------------------------------------------------------------
// Humanoid Brain Glow & Speech Pulsing Animation
// ---------------------------------------------------------------------
let brainGlowStartTs = 0;
let brainGlowStyleTick = 0;
function brainGlowFrame(ts) {
  if (!brainGlowActive) return;
  if (!brainGlowStartTs) brainGlowStartTs = ts;
  const t = (ts - brainGlowStartTs) / 1000;
  const pulse = 0.5 + 0.5 * Math.sin(t * 4.2);
  
  brainShell.material.opacity = BRAIN_SHELL_BASE_OPACITY + pulse * 0.28;
  brainWire.material.opacity = BRAIN_WIRE_BASE_OPACITY + pulse * 0.38;
  
  const scale = 1 + pulse * 0.025;
  brainShell.scale.setScalar(scale);
  brainWire.scale.setScalar(scale);

  if (ts - brainGlowStyleTick > 150) {
    brainGlowStyleTick = ts;
    refreshStyles();
  }
  requestAnimationFrame(brainGlowFrame);
}

export function startBrainGlow() {
  if (brainGlowActive) return;
  brainGlowActive = true;
  brainGlowStartTs = 0;
  
  const indicator = document.getElementById("speaking-indicator");
  if (indicator) indicator.classList.remove("hidden");

  refreshStyles();
  requestAnimationFrame(brainGlowFrame);
}

export function stopBrainGlow() {
  brainGlowActive = false;
  
  const indicator = document.getElementById("speaking-indicator");
  if (indicator) indicator.classList.add("hidden");

  brainShell.material.opacity = BRAIN_SHELL_BASE_OPACITY;
  brainWire.material.opacity = BRAIN_WIRE_BASE_OPACITY;
  brainShell.scale.setScalar(1);
  brainWire.scale.setScalar(1);
  refreshStyles();
}

// ---------------------------------------------------------------------
// 3D Brain Toolbar & Quick Action Buttons
// ---------------------------------------------------------------------
function initToolbarControls() {
  const btnFocus = document.getElementById("tb-focus-brain");
  const btnPulse = document.getElementById("tb-pulse-synapses");
  const btnWire = document.getElementById("tb-toggle-wire");
  const btnRotate = document.getElementById("tb-toggle-rotate");
  const btnReset = document.getElementById("tb-reset-cam");

  if (btnFocus) {
    btnFocus.addEventListener("click", () => {
      clearHighlight();
      Graph.cameraPosition({ x: 0, y: 35, z: 320 }, { x: 0, y: 0, z: 0 }, 1200);
    });
  }

  if (btnPulse) {
    btnPulse.addEventListener("click", () => {
      startBrainGlow();
      setTimeout(stopBrainGlow, 3500);
    });
  }

  if (btnWire) {
    btnWire.addEventListener("click", () => {
      wireframeVisible = !wireframeVisible;
      brainWire.visible = wireframeVisible;
      btnWire.classList.toggle("active", wireframeVisible);
    });
  }

  if (btnRotate) {
    btnRotate.addEventListener("click", () => {
      const isRotating = !Graph.controls().autoRotate;
      Graph.controls().autoRotate = isRotating;
      btnRotate.classList.toggle("active", isRotating);
    });
  }

  if (btnReset) {
    btnReset.addEventListener("click", () => {
      clearHighlight();
      closePanel();
      Graph.cameraPosition({ x: 0, y: 35, z: 320 }, { x: 0, y: 0, z: 0 }, 1000);
    });
  }

  // Quick Action Chips Wiring
  document.querySelectorAll("#prompt-chips .chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const promptText = chip.getAttribute("data-prompt");
      const inputEl = document.getElementById("chat-input");
      if (inputEl && promptText) {
        inputEl.value = promptText;
        inputEl.focus();
      }
    });
  });
}

// ---------------------------------------------------------------------
// Audio Spectrum Waveform Visualizer Canvas
// ---------------------------------------------------------------------
function initVoiceWaveformCanvas() {
  const canvas = document.getElementById("voice-waveform");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  function drawWave() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const bars = 14;
    const barWidth = 4;
    const gap = 4;
    const now = Date.now() / 200;

    for (let i = 0; i < bars; i++) {
      const x = i * (barWidth + gap) + 6;
      let h = 3;
      if (brainGlowActive) {
        h = 4 + Math.abs(Math.sin(now + i * 0.6)) * 14;
      } else {
        h = 3 + Math.abs(Math.sin(now * 0.4 + i * 0.4)) * 3;
      }
      const y = (canvas.height - h) / 2;
      ctx.fillStyle = brainGlowActive ? "#f4a261" : "#d4a373";
      ctx.beginPath();
      ctx.roundRect(x, y, barWidth, h, 2);
      ctx.fill();
    }
    requestAnimationFrame(drawWave);
  }
  drawWave();
}

