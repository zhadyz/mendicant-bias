"use client";

import { useEffect, useRef, useCallback, type RefObject } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  decay: number;
  size: number;
  hue: number;
}

interface WheelState {
  currentAngle: number;
  spinning: boolean;
  particles: Particle[];
  screenShake: { x: number; y: number };
  frameId: number | null;
  totalSpins: number;
}

interface UseMahoragaWheelResult {
  isSpinning: boolean;
  currentAngle: number;
}

// ---------------------------------------------------------------------------
// Animation timing (matches mahoraga.html exactly)
// ---------------------------------------------------------------------------

const TENSION = 1300;
const SNAP = 250;
const IMPACT = 120;
const SETTLE = 450;
const COOL = 900;

// ---------------------------------------------------------------------------
// Particle system
// ---------------------------------------------------------------------------

function spawnParticles(
  particles: Particle[],
  sz: number,
  count: number,
  intensity: number,
): void {
  const c = sz / 2;
  const r = sz * 0.38;
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2;
    const dist = r * (0.5 + Math.random() * 0.6);
    particles.push({
      x: c + dist * Math.cos(angle),
      y: c + dist * Math.sin(angle),
      vx: (Math.random() - 0.5) * intensity * 3.5,
      vy: (Math.random() - 0.5) * intensity * 3.5,
      life: 1,
      decay: 0.008 + Math.random() * 0.025,
      size: 1.5 + Math.random() * 3 * intensity,
      hue: 185 + Math.random() * 30, // Cyan-tinted (shifted from purple)
    });
  }
}

function updateParticles(particles: Particle[]): void {
  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i];
    p.x += p.vx;
    p.y += p.vy;
    p.vx *= 0.97;
    p.vy *= 0.97;
    p.life -= p.decay;
    if (p.life <= 0) particles.splice(i, 1);
  }
}

function drawParticles(ctx: CanvasRenderingContext2D, particles: Particle[]): void {
  for (const p of particles) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
    ctx.fillStyle = `hsla(${p.hue}, 70%, 75%, ${p.life * 0.8})`;
    ctx.fill();
  }
}

// ---------------------------------------------------------------------------
// 3D sphere rendering
// ---------------------------------------------------------------------------

function drawSphere(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  glowAmt: number,
): void {
  const gB = glowAmt * 80;

  // Forerunner cyan-gold palette (shifted from pure golden)
  const baseR = 120 + gB * 0.3;
  const baseG = 160 + gB * 0.5;
  const baseB = 100 + gB * 0.7;

  // Shadow
  ctx.save();
  ctx.beginPath();
  ctx.arc(x + radius * 0.08, y + radius * 0.1, radius * 1.05, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(0,0,0,0.3)";
  ctx.fill();
  ctx.restore();

  // Main sphere gradient
  const grad = ctx.createRadialGradient(
    x - radius * 0.3, y - radius * 0.35, radius * 0.05,
    x, y, radius,
  );
  grad.addColorStop(0, `rgb(${Math.min(255, baseR + 80)},${Math.min(255, baseG + 70)},${Math.min(255, baseB + 50)})`);
  grad.addColorStop(0.4, `rgb(${baseR},${baseG},${baseB})`);
  grad.addColorStop(0.75, `rgb(${baseR * 0.65},${baseG * 0.6},${baseB * 0.55})`);
  grad.addColorStop(1, `rgb(${baseR * 0.35},${baseG * 0.3},${baseB * 0.25})`);

  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = grad;
  ctx.fill();

  // Highlight spot
  ctx.beginPath();
  ctx.arc(x - radius * 0.25, y - radius * 0.28, radius * 0.22, 0, Math.PI * 2);
  const hlGrad = ctx.createRadialGradient(
    x - radius * 0.25, y - radius * 0.28, 0,
    x - radius * 0.25, y - radius * 0.28, radius * 0.22,
  );
  hlGrad.addColorStop(0, "rgba(255,255,240,0.7)");
  hlGrad.addColorStop(1, "rgba(255,255,240,0)");
  ctx.fillStyle = hlGrad;
  ctx.fill();

  // Glow aura when energized
  if (glowAmt > 0.1) {
    ctx.save();
    ctx.shadowColor = `rgba(0, 200, 220, ${glowAmt * 0.6})`;
    ctx.shadowBlur = radius * glowAmt * 1.5;
    ctx.beginPath();
    ctx.arc(x, y, radius * 0.95, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(0, 200, 220, ${glowAmt * 0.3})`;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Wheel rendering
// ---------------------------------------------------------------------------

function drawWheel(
  ctx: CanvasRenderingContext2D,
  sz: number,
  angle: number,
  glowAmt: number,
  screenShake: { x: number; y: number },
  particles: Particle[],
): void {
  const c = sz / 2;
  const outerR = sz * 0.32;
  const spokeLen = sz * 0.42;
  const hubR = sz * 0.065;
  const rimW = sz * 0.022;
  const spokeW = sz * 0.028;
  const ballR = sz * 0.045;
  const centerBallR = sz * 0.055;

  const gB = Math.floor(glowAmt * 80);

  ctx.clearRect(0, 0, sz, sz);
  ctx.save();
  ctx.translate(c + screenShake.x, c + screenShake.y);
  ctx.rotate(angle);
  ctx.translate(-c, -c);

  // Outer glow aura
  if (glowAmt > 0.05) {
    ctx.save();
    ctx.shadowColor = `rgba(0, 180, 200, ${glowAmt * 0.9})`;
    ctx.shadowBlur = 25 + glowAmt * 55;
    ctx.beginPath();
    ctx.arc(c, c, spokeLen + ballR, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(0, 180, 200, ${glowAmt * 0.2})`;
    ctx.lineWidth = rimW * 2;
    ctx.stroke();
    ctx.restore();
  }

  // Outer rim
  ctx.beginPath();
  ctx.arc(c, c, outerR, 0, Math.PI * 2);
  const rimBase = 150 + gB;
  ctx.strokeStyle = `rgb(${rimBase},${rimBase - 15},${rimBase - 50})`;
  ctx.lineWidth = rimW;
  ctx.stroke();

  // 8 thick spokes
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4 - Math.PI / 2;
    const cosA = Math.cos(a);
    const sinA = Math.sin(a);
    const perpX = -sinA;
    const perpY = cosA;
    const halfW = spokeW / 2;

    const x1 = c + hubR * cosA + perpX * halfW;
    const y1 = c + hubR * sinA + perpY * halfW;
    const x2 = c + hubR * cosA - perpX * halfW;
    const y2 = c + hubR * sinA - perpY * halfW;
    const x3 = c + spokeLen * cosA - perpX * halfW * 0.8;
    const y3 = c + spokeLen * sinA - perpY * halfW * 0.8;
    const x4 = c + spokeLen * cosA + perpX * halfW * 0.8;
    const y4 = c + spokeLen * sinA + perpY * halfW * 0.8;

    const spokeBase = 130 + gB * 0.7;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.lineTo(x3, y3);
    ctx.lineTo(x4, y4);
    ctx.closePath();
    ctx.fillStyle = `rgb(${spokeBase},${spokeBase - 12},${spokeBase - 45})`;
    ctx.fill();

    ctx.strokeStyle = `rgb(${spokeBase + 25},${spokeBase + 10},${spokeBase - 20})`;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  // Balls at spoke tips
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4 - Math.PI / 2;
    const bx = c + spokeLen * Math.cos(a);
    const by = c + spokeLen * Math.sin(a);
    drawSphere(ctx, bx, by, ballR, glowAmt);
  }

  // Center ball
  drawSphere(ctx, c, c, centerBallR, glowAmt);

  ctx.restore();
  drawParticles(ctx, particles);
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useMahoragaWheel(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  spinTrigger: number,
): UseMahoragaWheelResult {
  const stateRef = useRef<WheelState>({
    currentAngle: 0,
    spinning: false,
    particles: [],
    screenShake: { x: 0, y: 0 },
    frameId: null,
    totalSpins: 0,
  });

  const isSpinningRef = useRef(false);
  const triggerRef = useRef(0);

  // Draw a single static frame
  const drawStatic = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const sz = Math.min(rect.width, rect.height);

    canvas.width = sz * dpr;
    canvas.height = sz * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    drawWheel(ctx, sz, stateRef.current.currentAngle, 0, { x: 0, y: 0 }, []);
  }, [canvasRef]);

  // Spin animation
  const spinOnce = useCallback(() => {
    const s = stateRef.current;
    if (s.spinning) return;
    s.spinning = true;
    isSpinningRef.current = true;

    const canvas = canvasRef.current;
    if (!canvas) {
      s.spinning = false;
      isSpinningRef.current = false;
      return;
    }
    const maybeCtx = canvas.getContext("2d");
    if (!maybeCtx) {
      s.spinning = false;
      isSpinningRef.current = false;
      return;
    }
    const ctx: CanvasRenderingContext2D = maybeCtx;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const sz = Math.min(rect.width, rect.height);
    canvas.width = sz * dpr;
    canvas.height = sz * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const startAngle = s.currentAngle;
    const turnAmount = (Math.PI * 2) / 8;
    const startTime = performance.now();
    const TOTAL = TENSION + SNAP + IMPACT + SETTLE + COOL;

    function animate(now: number) {
      const elapsed = now - startTime;
      let angle = startAngle;
      let glowAmt = 0;

      if (elapsed < TENSION) {
        const tp = elapsed / TENSION;
        const freq = 6 + tp * 45;
        const amp = tp * tp * tp * 0.045;
        angle = startAngle + Math.sin(elapsed * freq * 0.01) * amp;
        glowAmt = tp * tp * tp * 0.85;
        const shake = tp * tp * 2.2;
        s.screenShake.x = (Math.random() - 0.5) * shake;
        s.screenShake.y = (Math.random() - 0.5) * shake;
        if (tp > 0.25 && Math.random() < tp * 0.5) spawnParticles(s.particles, sz, 1, tp * 1.2);

      } else if (elapsed < TENSION + SNAP) {
        const sp = (elapsed - TENSION) / SNAP;
        const overshoot = 1.07;
        const eased = 1 - Math.pow(1 - sp, 5);
        const overEased = eased * overshoot - (overshoot - 1) * Math.pow(sp, 4);
        angle = startAngle + turnAmount * Math.min(overEased, 1.05);
        glowAmt = 1.0;
        const shake = (1 - sp) * 5;
        s.screenShake.x = (Math.random() - 0.5) * shake;
        s.screenShake.y = (Math.random() - 0.5) * shake;
        if (sp < 0.2) spawnParticles(s.particles, sz, 6, 2.5);

      } else if (elapsed < TENSION + SNAP + IMPACT) {
        const ip = (elapsed - TENSION - SNAP) / IMPACT;
        angle = startAngle + turnAmount * (1 + 0.05 * (1 - ip));
        glowAmt = 1.0 - ip * 0.15;
        const shake = (1 - ip) * 3.5;
        s.screenShake.x = (Math.random() - 0.5) * shake;
        s.screenShake.y = (Math.random() - 0.5) * shake;
        spawnParticles(s.particles, sz, 2, 1.8 * (1 - ip));

      } else if (elapsed < TENSION + SNAP + IMPACT + SETTLE) {
        const setp = (elapsed - TENSION - SNAP - IMPACT) / SETTLE;
        const bounce = Math.sin(setp * Math.PI * 2.5) * 0.006 * (1 - setp);
        angle = startAngle + turnAmount + bounce;
        glowAmt = 0.85 * (1 - setp * 0.5);
        s.screenShake.x = 0;
        s.screenShake.y = 0;

      } else {
        const cp = (elapsed - TENSION - SNAP - IMPACT - SETTLE) / COOL;
        angle = startAngle + turnAmount;
        glowAmt = 0.42 * (1 - cp);
        s.screenShake.x = 0;
        s.screenShake.y = 0;
      }

      updateParticles(s.particles);
      drawWheel(ctx, sz, angle, glowAmt, s.screenShake, s.particles);

      if (elapsed < TOTAL) {
        s.frameId = requestAnimationFrame(animate);
      } else {
        s.currentAngle = startAngle + turnAmount;
        s.screenShake = { x: 0, y: 0 };
        s.particles = [];
        s.spinning = false;
        s.totalSpins++;
        isSpinningRef.current = false;
        drawWheel(ctx, sz, s.currentAngle, 0, { x: 0, y: 0 }, []);
      }
    }

    s.frameId = requestAnimationFrame(animate);
  }, [canvasRef]);

  // Watch spinTrigger changes
  useEffect(() => {
    if (spinTrigger > triggerRef.current) {
      triggerRef.current = spinTrigger;
      spinOnce();
    }
  }, [spinTrigger, spinOnce]);

  // Initial draw + resize
  useEffect(() => {
    drawStatic();

    const handleResize = () => {
      if (!stateRef.current.spinning) drawStatic();
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      if (stateRef.current.frameId) {
        cancelAnimationFrame(stateRef.current.frameId);
      }
    };
  }, [drawStatic]);

  return {
    isSpinning: isSpinningRef.current,
    currentAngle: stateRef.current.currentAngle,
  };
}
