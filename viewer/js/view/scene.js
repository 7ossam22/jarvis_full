// js/view/scene.js — Cyberpunk-neon plexiform Orb scene (View layer).
// Renders an anatomical 3D Humanoid Brain (dual cerebral hemispheres,
// cortical gyri/sulci folds, cerebellum, brainstem, corpus callosum internal
// axon pathways) with glowing pearl neurons inside that surge with Zen energy
// when speaking.
import { graphData, neighborsOf, linkKey } from "../model/graphData.js";
import { openPanel, closePanel } from "./panel.js";

const GROUP_PALETTE = [
  "#2563eb", // Electric Blue
  "#ec4899", // Vibrant Magenta Pink
  "#3b82f6", // Azure
  "#f472b6", // Blossom Pink
  "#0ea5e9", // Sky Cyan-Blue
  "#db2777", // Deep Rose
  "#60a5fa", // Pale Blue
  "#a855f7"  // Blue-Pink Blend Violet
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

let Graph;
let BRAIN_RADIUS = 100;
let brainShell;          // the one and only orb
let orbHalo;             // soft additive hallow/glow disc behind the orb
let particleRing;        // orbiting glowing particles / digital noise
const ORB_BLUE = 0x00e5ff; // electric cyan  (#00E5FF)
const ORB_PINK = 0xff00e6; // neon magenta  (#FF00E6)
let orbUniforms = null;
let haloUniforms = null;
let orbGlow = 0;         // 0..1 eased "speaking" glow amount
let orbGlowTarget = 0;

// ---------------------------------------------------------------------
// Plexiform Neon Orb
// A living constellation shell: thousands of wave-displaced light points
// that breathe and ripple, plus an orbiting ring of glowing particles, all
// swept with a dual-tone cyan -> magenta neon gradient.
// ---------------------------------------------------------------------
const ORB_GRADIENT_CHUNK = `
  uniform vec3  uColorA;
  uniform vec3  uColorB;
  uniform float uPulse;
  uniform float uGlow;
  uniform float uTime;
`;

function buildPlexusOrb(radius, colorA, colorB) {
  const group = new THREE.Group();
  orbUniforms = {
    uColorA: { value: new THREE.Color(colorA) },
    uColorB: { value: new THREE.Color(colorB) },
    uPulse:  { value: 0.0 },
    uGlow:   { value: 0.0 },
    uTime:   { value: 0.0 }
  };

  // -- Dark occluding core: keeps far-side struts from muddling the front.
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(radius * 0.92, 64, 64),
    new THREE.MeshBasicMaterial({ color: 0x0b0d1a, transparent: true, opacity: 0.88 })
  );
  group.add(core);

  // -- Constellation shell: a dense fibonacci-distributed point cloud that
  //    breathes with multi-frequency wave displacement. The old rigid
  //    geodesic struts are gone: the surface is now living light, not scaffolding.
  const SHELL_COUNT = 2600;
  const shellPos = new Float32Array(SHELL_COUNT * 3);
  const shellSeed = new Float32Array(SHELL_COUNT);
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < SHELL_COUNT; i++) {
    const y = 1 - (i / (SHELL_COUNT - 1)) * 2;
    const rr = Math.sqrt(Math.max(0, 1 - y * y));
    const th = GOLDEN * i;
    shellPos[i * 3]     = Math.cos(th) * rr * radius;
    shellPos[i * 3 + 1] = y * radius;
    shellPos[i * 3 + 2] = Math.sin(th) * rr * radius;
    shellSeed[i] = Math.random();
  }
  const shellGeom = new THREE.BufferGeometry();
  shellGeom.setAttribute("position", new THREE.BufferAttribute(shellPos, 3));
  shellGeom.setAttribute("aSeed", new THREE.BufferAttribute(shellSeed, 1));

  const nodes = new THREE.Points(
    shellGeom,
    new THREE.ShaderMaterial({
      uniforms: orbUniforms,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: `
        attribute float aSeed;
        varying vec3  vPos;
        varying float vSeed;
        varying float vWave;
        uniform float uTime;
        uniform float uGlow;
        uniform float uPulse;
        void main() {
          vec3 dir = normalize(position);
          float R = length(position);
          vPos = dir;
          vSeed = aSeed;

          // Multi-frequency travelling waves: the "breath" of the orb.
          float w = sin(dir.x * 3.1 + uTime * 0.85)
                  + sin(dir.y * 4.7 - uTime * 1.25)
                  + sin(dir.z * 2.3 + uTime * 0.55)
                  + 0.65 * sin((dir.x + dir.y + dir.z) * 7.9 + uTime * 2.1);
          w *= 0.25;
          vWave = w * 0.5 + 0.5;

          float breathe = 1.0 + 0.035 * sin(uTime * 0.7);
          float amp = 0.055 + uGlow * 0.085 + uPulse * 0.035 * uGlow;
          vec3 p = dir * R * breathe * (1.0 + w * amp);

          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          float twinkle = 0.6 + 0.4 * sin(uTime * (1.4 + aSeed * 3.0) + aSeed * 9.0);
          gl_PointSize = (2.0 + aSeed * 2.2 + vWave * 2.4 + uGlow * 3.0 + uPulse * 1.4 * uGlow)
                         * twinkle * (300.0 / -mv.z);
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: ORB_GRADIENT_CHUNK + `
        varying vec3  vPos;
        varying float vSeed;
        varying float vWave;
        void main() {
          vec2 c = gl_PointCoord - 0.5;
          float d = length(c) * 2.0;
          if (d > 1.0) discard;
          float soft = pow(1.0 - d, 2.0);
          float t = clamp(vPos.x * 0.5 + 0.5, 0.0, 1.0);
          vec3 col = mix(uColorA, uColorB, smoothstep(0.0, 1.0, t));
          // crests of the wave run hotter than the troughs -> visible living ripple
          col = mix(col * 0.75, col + vec3(0.25), vWave);
          col = mix(col, vec3(1.0), pow(1.0 - d, 6.0) * (0.40 + uGlow * 0.4));
          float depthFade = 0.40 + 0.60 * clamp(vPos.z * 0.5 + 0.5, 0.0, 1.0);
          float a = soft * depthFade * (0.42 + vWave * 0.30 + uGlow * 0.55);
          gl_FragColor = vec4(col, a);
        }
      `
    })
  );
  group.add(nodes);

  return group;
}

// -- Orbiting ring of glowing particles / digital noise around the orb.
function buildParticleRing(radius, colorA, colorB) {
  const COUNT = 900;
  const pos = new Float32Array(COUNT * 3);
  const seed = new Float32Array(COUNT);
  for (let i = 0; i < COUNT; i++) {
    const ang = Math.random() * Math.PI * 2;
    const r = radius * (1.18 + Math.pow(Math.random(), 1.7) * 0.75);
    const lift = (Math.random() - 0.5) * radius * 0.42;
    pos[i * 3]     = Math.cos(ang) * r;
    pos[i * 3 + 1] = lift;
    pos[i * 3 + 2] = Math.sin(ang) * r;
    seed[i] = Math.random();
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geom.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));

  const mat = new THREE.ShaderMaterial({
    uniforms: orbUniforms,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexShader: `
      attribute float aSeed;
      varying float vSeed;
      varying vec3 vPos;
      uniform float uTime;
      uniform float uGlow;
      void main() {
        vSeed = aSeed;
        vPos = normalize(position);
        vec3 p = position;
        p.y += sin(uTime * (0.6 + aSeed) + aSeed * 6.28) * 3.5;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_PointSize = (1.4 + aSeed * 2.6 + uGlow * 2.0) * (300.0 / -mv.z);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: ORB_GRADIENT_CHUNK + `
      varying float vSeed;
      varying vec3 vPos;
      void main() {
        vec2 c = gl_PointCoord - 0.5;
        float d = length(c) * 2.0;
        if (d > 1.0) discard;
        float soft = pow(1.0 - d, 1.8);
        float t = clamp(vPos.x * 0.5 + 0.5, 0.0, 1.0);
        vec3 col = mix(uColorA, uColorB, t);
        float flicker = 0.55 + 0.45 * sin(uTime * (2.0 + vSeed * 5.0) + vSeed * 12.0);
        gl_FragColor = vec4(col, soft * flicker * (0.30 + uGlow * 0.45));
      }
    `
  });
  return new THREE.Points(geom, mat);
}

// ---------------------------------------------------------------------
// Hallow / halo: a camera-facing additive disc sitting BEHIND the orb that
// bleeds a soft blue-pink aura outwards. It swells and brightens on speech.
// ---------------------------------------------------------------------
function buildHaloMesh(radius, colorA, colorB) {
  const geometry = new THREE.PlaneGeometry(radius * 6.0, radius * 6.0, 1, 1);
  haloUniforms = {
    uColorA: { value: new THREE.Color(colorA) },
    uColorB: { value: new THREE.Color(colorB) },
    uGlow:   { value: 0.0 },
    uPulse:  { value: 0.0 }
  };
  const material = new THREE.ShaderMaterial({
    uniforms: haloUniforms,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: THREE.AdditiveBlending,
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3  uColorA;
      uniform vec3  uColorB;
      uniform float uGlow;
      uniform float uPulse;
      varying vec2 vUv;
      void main() {
        vec2 p = vUv - 0.5;
        float d = length(p) * 2.0;               // 0 at centre, 1 at edge
        float core = smoothstep(0.62, 0.16, d);  // bright inner bloom
        float wide = smoothstep(1.0, 0.10, d);   // wide feathered aura
        float a = core * 0.55 + wide * 0.35;
        a *= (0.42 + uGlow * 0.85 + uPulse * 0.12 * uGlow);
        vec3 col = mix(uColorA, uColorB, clamp(vUv.y * 0.85 + 0.08, 0.0, 1.0));
        gl_FragColor = vec4(col * (0.85 + uGlow * 0.8), a);
      }
    `
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.renderOrder = -10;   // always drawn behind the orb
  return mesh;
}

function hexToInt(hex, fallback) {
  if (typeof hex !== "string") return fallback;
  const n = parseInt(hex.replace("#", "0x"), 16);
  return Number.isNaN(n) ? fallback : n;
}

export function initScene(config) {
  BRAIN_RADIUS = config?.brain?.radius ?? 100;
  // Orb gradient is fixed: electric blue -> magenta pink. The old grey/white
  // brain.shell_color / brain.wire_color defaults are ignored on purpose; only
  // explicit orb_color_a / orb_color_b overrides are honoured.
  const shellColor = hexToInt(config?.brain?.orb_color_a, ORB_BLUE);
  const wireColor = hexToInt(config?.brain?.orb_color_b, ORB_PINK);

  Graph = ForceGraph3D()(document.getElementById("graph"))
    .graphData(graphData)
    .backgroundColor("rgba(0,0,0,0)")
    // Note balls, links and particles are fully suppressed: the orb is the whole visual.
    .nodeThreeObjectExtend(false)
    .nodeThreeObject(() => new THREE.Object3D())
    .nodeLabel(() => "")
    .nodeVal(0)
    .nodeOpacity(0)
    .linkWidth(0)
    .linkColor(() => "rgba(0,0,0,0)")
    .linkDirectionalParticles(0)
    .enableNodeDrag(false)
    .onBackgroundClick(() => { clearHighlight(); closePanel(); });

  // The orb is a fixed centrepiece: no auto-spin, no drag-rotate, no zoom, no pan.
  // 3d-force-graph may hand back TrackballControls (noZoom/noRotate/noPan) or
  // OrbitControls (enableZoom/enableRotate/enablePan), so lock both dialects.
  const controls = Graph.controls();
  controls.autoRotate = false;
  controls.enableRotate = false;
  controls.enableZoom = false;
  controls.enablePan = false;
  controls.enableDamping = false;
  controls.noRotate = true;
  controls.noZoom = true;
  controls.noPan = true;
  if (Graph.enableNavigationControls) Graph.enableNavigationControls(false);
  if (controls.update) controls.update();

  // Construct the solid gradient orb and its hallow
  orbHalo = buildHaloMesh(BRAIN_RADIUS, shellColor, wireColor);
  Graph.scene().add(orbHalo);
  brainShell = buildPlexusOrb(BRAIN_RADIUS, shellColor, wireColor);
  Graph.scene().add(brainShell);
  particleRing = buildParticleRing(BRAIN_RADIUS, shellColor, wireColor);
  Graph.scene().add(particleRing);

  // Keep the halo square-on to the camera and start the always-on render loop
  requestAnimationFrame(orbFrame);

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

  Graph.cameraPosition({ x: 0, y: 0, z: 700 });

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
      if (orbHalo) orbHalo.visible = true;
      if (particleRing) particleRing.visible = true;
      modal && modal.classList.add("hidden");
    });
  }

  if (btnNetwork) {
    btnNetwork.addEventListener("click", () => {
      setModeActive(btnNetwork);
      if (brainShell) brainShell.visible = false;
      if (orbHalo) orbHalo.visible = false;
      if (particleRing) particleRing.visible = false;
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

// Camera moves are disabled by design: triggering a note must never zoom or
// dolly the orb. These keep the highlight/panel behaviour and nothing else.
export function flyToNode(node, opts = {}) {
  if (!node) return;
  const ids = new Set([node.id, ...(neighborsOf[node.id] || [])]);
  setHighlight(ids);
  if (opts.openPanel) openPanel(node);
}

export function flyToCluster(ids) {
  setHighlight(new Set(ids));
}

export function refreshGraphData() {
  Graph.graphData(graphData);
  const countEl = document.getElementById("synapse-count");
  if (countEl) countEl.textContent = `Pathways: ${graphData.links.length}`;
}

// ---------------------------------------------------------------------
// Humanoid Brain Glow & Speech Pulsing Animation
// ---------------------------------------------------------------------
// A single always-on frame loop: keeps the halo facing the camera, eases the
// speaking glow in and out, and pulses the orb while JARVIS talks.
function orbFrame(ts) {
  const t = ts / 1000;

  // Halo is a billboard: copy the camera's orientation every frame.
  if (orbHalo && Graph) {
    const cam = Graph.camera();
    if (cam) orbHalo.quaternion.copy(cam.quaternion);
  }

  // Ease the glow towards its target so speech start/stop fades, never snaps.
  orbGlowTarget = brainGlowActive ? 1 : 0;
  orbGlow += (orbGlowTarget - orbGlow) * 0.09;
  if (Math.abs(orbGlowTarget - orbGlow) < 0.002) orbGlow = orbGlowTarget;

  const pulse = brainGlowActive ? (0.5 + 0.5 * Math.sin(t * 4.2)) : 0;

  if (orbUniforms) {
    orbUniforms.uPulse.value = pulse;
    orbUniforms.uGlow.value = orbGlow;
    orbUniforms.uTime.value = t;
  }
  // The constellation shell drifts slowly; the loose particles orbit faster.
  if (brainShell) {
    brainShell.rotation.y = t * 0.035;
    brainShell.rotation.x = Math.sin(t * 0.16) * 0.06;
  }
  if (particleRing) {
    particleRing.rotation.y = t * 0.12;
    particleRing.rotation.x = 0.28;
  }
  if (haloUniforms) {
    haloUniforms.uPulse.value = pulse;
    haloUniforms.uGlow.value = orbGlow;
  }
  if (brainShell) brainShell.scale.setScalar(1 + orbGlow * pulse * 0.035);
  if (orbHalo) orbHalo.scale.setScalar(1 + orbGlow * (0.10 + pulse * 0.06));

  requestAnimationFrame(orbFrame);
}

export function startBrainGlow() {
  if (brainGlowActive) return;
  brainGlowActive = true;

  const indicator = document.getElementById("speaking-indicator");
  if (indicator) indicator.classList.remove("hidden");
}

export function stopBrainGlow() {
  brainGlowActive = false;

  const indicator = document.getElementById("speaking-indicator");
  if (indicator) indicator.classList.add("hidden");
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
      Graph.cameraPosition({ x: 0, y: 0, z: 700 }, { x: 0, y: 0, z: 0 }, 1200);
    });
  }

  if (btnPulse) {
    btnPulse.addEventListener("click", () => {
      startBrainGlow();
      setTimeout(stopBrainGlow, 3500);
    });
  }

  if (btnWire) {
    // No wireframe layer any more - the button just hides/shows the orb.
    btnWire.addEventListener("click", () => {
      wireframeVisible = !wireframeVisible;
      if (brainShell) brainShell.visible = wireframeVisible;
      if (orbHalo) orbHalo.visible = wireframeVisible;
      if (particleRing) particleRing.visible = wireframeVisible;
      btnWire.classList.toggle("active", wireframeVisible);
    });
  }

  if (btnRotate) {
    // Spinning is disabled by design - the orb stays put. Hide the toggle so
    // it cannot re-enable rotation.
    btnRotate.classList.remove("active");
    btnRotate.style.display = "none";
  }

  if (btnReset) {
    btnReset.addEventListener("click", () => {
      clearHighlight();
      closePanel();
      Graph.cameraPosition({ x: 0, y: 0, z: 700 }, { x: 0, y: 0, z: 0 }, 1000);
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
      ctx.fillStyle = brainGlowActive ? "#ff00e6" : "#00e5ff";
      ctx.beginPath();
      ctx.roundRect(x, y, barWidth, h, 2);
      ctx.fill();
    }
    requestAnimationFrame(drawWave);
  }
  drawWave();
}

