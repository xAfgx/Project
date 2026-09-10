"use strict";

const fs = require("fs");

function cleanPoint(value) {
  const x = Number(value && value.x);
  const y = Number(value && value.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("Invalid cursor point");
  return { x, y };
}

function normalize(points, start, end) {
  const clean = [];
  for (const point of points || []) {
    const x = Number(point && point.x);
    const y = Number(point && point.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (!clean.length || Math.abs(clean[clean.length - 1].x - x) > 0.01 || Math.abs(clean[clean.length - 1].y - y) > 0.01) {
      clean.push({ x, y });
    }
  }
  if (!clean.length || Math.hypot(clean[0].x - start.x, clean[0].y - start.y) > 1) clean.unshift(start);
  if (Math.hypot(clean[clean.length - 1].x - end.x, clean[clean.length - 1].y - end.y) > 1) clean.push(end);
  return clean;
}

function internalBezier(start, end, steps) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const normalX = -dy / distance;
  const normalY = dx / distance;
  const bend = Math.min(42, Math.max(8, distance * 0.08));
  const c1 = { x: start.x + dx * 0.34 + normalX * bend, y: start.y + dy * 0.34 + normalY * bend };
  const c2 = { x: start.x + dx * 0.72 + normalX * bend * 0.45, y: start.y + dy * 0.72 + normalY * bend * 0.45 };
  const points = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const u = 1 - t;
    points.push({
      x: u*u*u*start.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*end.x,
      y: u*u*u*start.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*end.y,
    });
  }
  return points;
}

function plan(input) {
  const start = cleanPoint(input.start);
  const end = cleanPoint(input.end);
  const preferred = String(input.preferred || "ghost-cursor");
  const steps = Math.max(12, Math.min(120, Number(input.steps) || 42));

  if (preferred !== "bezier") {
    try {
      const ghost = require("ghost-cursor");
      if (typeof ghost.path === "function") {
        const points = ghost.path(start, end, { useTimestamps: false, moveSpeed: 85 });
        return { provider: "ghost-cursor", points: normalize(points, start, end) };
      }
    } catch (_) {}
  }

  try {
    const { BezierMouse } = require("bezier-mouse-js");
    const mouse = new BezierMouse();
    const points = mouse.bezierCurveTo(start, end, { steps, deviation: Math.min(28, Math.max(10, Math.hypot(end.x-start.x, end.y-start.y) * 0.06)) });
    return { provider: "bezier-mouse-js", points: normalize(points, start, end) };
  } catch (_) {}

  return { provider: "internal-bezier", points: normalize(internalBezier(start, end, steps), start, end) };
}

try {
  const raw = fs.readFileSync(0, "utf8");
  const result = plan(JSON.parse(raw || "{}"));
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stdout.write(JSON.stringify({ provider: "none", points: [], error: error instanceof Error ? error.message : String(error) }));
  process.exitCode = 1;
}
