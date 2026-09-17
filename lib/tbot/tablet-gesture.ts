export type TabletDirection = "forward" | "backward" | "left" | "right" | "stop";

export function classifyTabletZone(x: number, y: number): TabletDirection {
  const dx = x - .5, dy = y - .5, distance = Math.hypot(dx, dy);
  if (distance < .12 || distance > .43) return "stop";
  if (Math.abs(dx) > Math.abs(dy)) return dx < 0 ? "left" : "right";
  return dy < 0 ? "forward" : "backward";
}

export function stableTabletDirection(previous: TabletDirection, frames: number, next: TabletDirection, required = 4) {
  const count = next === previous ? frames + 1 : 1;
  return {candidate: next, frames: count, output: count >= required ? next : "stop" as TabletDirection};
}
