// js/view/starfield.js — Deep-space backdrop (View layer).
//
// Replaces the old CSS repeating-gradient "star dust", which tiled on a fixed
// pitch and therefore read as dots on graph paper. Here every star is placed
// at a genuinely random position with a random depth, size, hue and twinkle
// phase, so the field never repeats.
//
// On top of the stars: slow parallax that follows the pointer, a drifting
// dust haze, occasional meteors, and a faint rotating "energy horizon" ring
// behind the orb. The whole field reacts to speech the same way the orb does
// — stars brighten, drift quicker and meteors fall more often.

let canvas, ctx;
let W = 0, H = 0, DPR = 1;
let stars = [];
let meteors = [];
let rafId = null;

let parallaxX = 0, parallaxY = 0;      // eased pointer parallax
let targetPX = 0, targetPY = 0;
let energy = 0;                        // 0..1 eased "speaking" amount
let energyTarget = 0;
let t = 0;

const STAR_DENSITY = 1 / 5200;         // stars per css pixel of viewport area
const MAX_STARS = 620;

function rand(a, b) { return a + Math.random() * (b - a); }

// Star colours: mostly white, with cyan/magenta/violet strays so the field
// belongs to the same neon palette as the orb.
const STAR_TINTS = [
  [255, 255, 255], [255, 255, 255], [255, 255, 255],
  [186, 240, 255], [160, 235, 255],
  [255, 170, 245], [200, 175, 255],
];

function makeStar() {
  // depth 0 = far (small, dim, barely moves), 1 = near (big, bright, parallaxes)
  const depth = Math.pow(Math.random(), 1.6);
  const tint = STAR_TINTS[(Math.random() * STAR_TINTS.length) | 0];
  return {
    x: Math.random() * W,
    y: Math.random() * H,
    depth,
    r: 0.35 + depth * 1.5 + (Math.random() < 0.04 ? 1.1 : 0), // a few bright ones
    base: 0.18 + depth * 0.62,
    tw: Math.random() * Math.PI * 2,        // twinkle phase
    twSpeed: rand(0.25, 1.15),
    drift: rand(-0.012, 0.012) * (0.3 + depth),
    tint,
  };
}

function seedStars() {
  const count = Math.min(MAX_STARS, Math.round(W * H * STAR_DENSITY));
  stars = new Array(count);
  for (let i = 0; i < count; i++) stars[i] = makeStar();
}

function spawnMeteor() {
  // Streaks fall diagonally across the upper two-thirds, from either side.
  const fromLeft = Math.random() < 0.65;
  const ang = fromLeft ? rand(0.22, 0.46) : Math.PI - rand(0.22, 0.46);
  const speed = rand(6.5, 12.5);
  meteors.push({
    x: fromLeft ? rand(-0.1, 0.6) * W : rand(0.4, 1.1) * W,
    y: rand(-0.1, 0.45) * H,
    vx: Math.cos(ang) * speed,
    vy: Math.sin(ang) * speed,
    life: 0,
    span: rand(52, 120),
    len: rand(90, 220),
    hue: Math.random() < 0.5 ? "0, 229, 255" : "255, 120, 240",
  });
}

function resize() {
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width = Math.round(W * DPR);
  canvas.height = Math.round(H * DPR);
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  seedStars();
}

function frame() {
  rafId = requestAnimationFrame(frame);
  t += 0.016;

  energy += (energyTarget - energy) * 0.045;
  parallaxX += (targetPX - parallaxX) * 0.035;
  parallaxY += (targetPY - parallaxY) * 0.035;

  ctx.clearRect(0, 0, W, H);

  // ---- Energy horizon: a faint elliptical ring sitting behind the orb ----
  const cx = W / 2, cy = H * 0.5;
  const ringR = Math.min(W, H) * (0.30 + 0.012 * Math.sin(t * 0.55) + energy * 0.02);
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(Math.sin(t * 0.08) * 0.22);
  ctx.scale(1, 0.30);
  const ring = ctx.createRadialGradient(0, 0, ringR * 0.72, 0, 0, ringR);
  ring.addColorStop(0, "rgba(0, 229, 255, 0)");
  ring.addColorStop(0.72, `rgba(0, 229, 255, ${0.055 + energy * 0.07})`);
  ring.addColorStop(0.88, `rgba(255, 0, 230, ${0.045 + energy * 0.06})`);
  ring.addColorStop(1, "rgba(255, 0, 230, 0)");
  ctx.fillStyle = ring;
  ctx.beginPath();
  ctx.arc(0, 0, ringR, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  // ---- Stars ----
  const speed = 1 + energy * 2.2;
  for (let i = 0; i < stars.length; i++) {
    const s = stars[i];

    // gentle sideways drift; wrap around the edges so the field never empties
    s.x += s.drift * speed;
    if (s.x < -4) s.x = W + 4; else if (s.x > W + 4) s.x = -4;

    s.tw += 0.016 * s.twSpeed * speed;
    const twinkle = 0.62 + 0.38 * Math.sin(s.tw);
    const a = Math.min(1, s.base * twinkle * (1 + energy * 0.55));

    const px = s.x + parallaxX * (0.25 + s.depth * 1.75);
    const py = s.y + parallaxY * (0.25 + s.depth * 1.75);
    const r = s.r * (1 + energy * 0.22);
    const [cr, cg, cb] = s.tint;

    // halo for the nearer stars, so bright ones actually bloom
    if (s.depth > 0.62) {
      const g = ctx.createRadialGradient(px, py, 0, px, py, r * 5.5);
      g.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${a * 0.34})`);
      g.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(px, py, r * 5.5, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${a})`;
    ctx.beginPath();
    ctx.arc(px, py, r, 0, Math.PI * 2);
    ctx.fill();
  }

  // ---- Meteors ----
  if (Math.random() < 0.0022 + energy * 0.006) spawnMeteor();
  for (let i = meteors.length - 1; i >= 0; i--) {
    const m = meteors[i];
    m.life++;
    m.x += m.vx;
    m.y += m.vy;
    if (m.life > m.span) { meteors.splice(i, 1); continue; }

    // fade in over the first fifth of the life, out over the rest
    const p = m.life / m.span;
    const alpha = (p < 0.2 ? p / 0.2 : 1 - (p - 0.2) / 0.8) * 0.8;
    const nx = m.vx / Math.hypot(m.vx, m.vy);
    const ny = m.vy / Math.hypot(m.vx, m.vy);
    const tailX = m.x - nx * m.len, tailY = m.y - ny * m.len;

    const grad = ctx.createLinearGradient(m.x, m.y, tailX, tailY);
    grad.addColorStop(0, `rgba(255, 255, 255, ${alpha})`);
    grad.addColorStop(0.25, `rgba(${m.hue}, ${alpha * 0.6})`);
    grad.addColorStop(1, `rgba(${m.hue}, 0)`);
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.6;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(tailX, tailY);
    ctx.lineTo(m.x, m.y);
    ctx.stroke();
  }
}

export function initStarfield() {
  if (canvas) return;
  canvas = document.createElement("canvas");
  canvas.id = "starfield";
  document.body.appendChild(canvas);
  ctx = canvas.getContext("2d");
  resize();

  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", (e) => {
    // Parallax is deliberately tiny — a hint of depth, not a fairground ride.
    targetPX = (e.clientX / window.innerWidth - 0.5) * -26;
    targetPY = (e.clientY / window.innerHeight - 0.5) * -18;
  }, { passive: true });

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    frame();                 // draw a single static field
    cancelAnimationFrame(rafId);
    rafId = null;
    return;
  }
  frame();
}

// Called from scene.js alongside the orb's speaking glow.
export function setStarfieldEnergy(on) { energyTarget = on ? 1 : 0; }
