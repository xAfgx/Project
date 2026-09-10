"use strict";

const path = require("path");
const { spawnSync } = require("child_process");

const helper = path.join(__dirname, "cursor_path_helper.cjs");

function run(preferred) {
  const child = spawnSync(process.execPath, [helper], {
    input: JSON.stringify({
      start: { x: 20, y: 30 },
      end: { x: 420, y: 210 },
      preferred,
      steps: 36,
    }),
    encoding: "utf8",
  });
  if (child.status !== 0) throw new Error(child.stderr || child.stdout || `helper exited ${child.status}`);
  const result = JSON.parse(String(child.stdout || "{}"));
  if (!Array.isArray(result.points) || result.points.length < 2) throw new Error(`No path from ${preferred}: ${child.stdout}`);
  return result;
}

const ghost = run("ghost-cursor");
if (ghost.provider !== "ghost-cursor") throw new Error(`Expected ghost-cursor, got ${ghost.provider}`);

const bezier = run("bezier");
if (bezier.provider !== "bezier-mouse-js") throw new Error(`Expected bezier-mouse-js, got ${bezier.provider}`);

console.log(`Cursor providers passed. ghost=${ghost.points.length} bezier=${bezier.points.length}`);
