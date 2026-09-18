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

/** Time-based dwell is independent of phone frame rate. STOP has no dwell. */
export function createTabletDirectionFilter(dwellMs = 180) {
  let candidate: TabletDirection = "stop", since = 0, lastFrame = 0;
  return {
    reset() { candidate = "stop"; since = lastFrame = 0; },
    update(x: number, y: number, confidence: number, now: number): TabletDirection {
      if (now - lastFrame > 250 || now < lastFrame) { candidate = "stop"; since = now; }
      lastFrame = now;
      let next = confidence >= .75 && [x,y,now].every(Number.isFinite) ? classifyTabletZone(x,y) : "stop";
      // Hold the existing cardinal zone near a diagonal boundary. The center
      // and outer safety ring always stop immediately, without hysteresis.
      if (next !== "stop" && candidate !== "stop" && Math.abs(Math.abs(x-.5)-Math.abs(y-.5)) < .035) next = candidate;
      if (next === "stop") { candidate = "stop"; since = now; return "stop"; }
      if (next !== candidate) { candidate = next; since = now; }
      return now - since >= dwellMs ? next : "stop";
    },
  };
}
