import type { TabletDirection } from "./tablet-gesture.ts";

type Gateway = {
  connected: boolean;
  token: string;
  send: (command: Record<string, unknown>) => Promise<unknown>;
  sendDeviceState: (state: Record<string, unknown>) => void;
};
type State = { armed: boolean; busy: boolean };
const motion: Record<TabletDirection, [number, number]> = {
  forward: [.06, 0], backward: [-.045, 0], left: [0, .35], right: [0, -.35], stop: [0, 0],
};

/** A camera loop outlives React renders: always read the current link and authority. */
export function createTabletControl(
  gateway: () => Gateway,
  changed: (state: State) => void,
  message: (text: string) => void,
) {
  let armed = false, camera = false, visible = true, generation = 0;
  let session = gateway().token;
  let claimPending = false, drivePending = false, stopsPending = 0;
  const notify = () => changed({ armed, busy: claimPending || drivePending || stopsPending > 0 });
  const ready = () => camera && visible && gateway().connected && !!gateway().token;

  // A canceled request can reach the gateway after Stop. Release only its own
  // session, so a late response cannot take control away from the laptop.
  const release = async (link: Gateway) => {
    try { await link.send({ action: "release", source: "gesture" }); }
    catch { /* Gateway and motor watchdogs also expire a lost connection. */ }
  };

  const disarm = (action: "stop" | "release" | null) => {
    generation++;
    armed = false;
    notify();
    const link = gateway();
    if (action && link.connected && link.token) {
      stopsPending++;
      notify();
      return link.send({ action, source: "gesture" })
        .catch((error) => message(String(error)))
        .finally(() => { stopsPending--; notify(); });
    }
    return Promise.resolve();
  };

  const syncConnection = () => {
    const link = gateway();
    if (session !== link.token || !link.connected) {
      if (armed || claimPending || drivePending) disarm(null);
      session = link.token;
    }
  };

  return {
    stop: () => disarm("stop"),
    syncConnection,
    observeAuthority(active: boolean) {
      // Server STOP, watchdog and another operator's takeover also disarm the
      // retained camera loop. Never turn a heartbeat into an automatic claim.
      if (!active && armed) void disarm(null);
    },
    setCamera(active: boolean) {
      const wasActive = camera;
      camera = active;
      if (!active && (wasActive || armed || claimPending || drivePending)) disarm("release");
    },
    setVisible(active: boolean) {
      visible = active;
      if (!active && (armed || claimPending || drivePending)) disarm("release");
    },
    async claim() {
      syncConnection();
      if (!ready()) { message("Start the camera and connect the tablet before requesting control."); return; }
      if (claimPending || drivePending || stopsPending) {
        message("Waiting for the previous command to finish. Then request control again.");
        return;
      }
      const link = gateway(), attempt = ++generation;
      armed = false;
      claimPending = true;
      notify();
      try {
        await link.send({ action: "claim", source: "gesture" });
        if (attempt !== generation || !ready() || gateway().token !== link.token) {
          await release(link);
          return;
        }
        armed = true;
        message("Gesture control active. Keep your finger in the center to stop.");
      } catch (error) {
        if (attempt === generation) message(String(error));
      } finally {
        claimPending = false;
        notify();
      }
    },
    frame(direction: TabletDirection, state: Record<string, unknown>) {
      syncConnection();
      const link = gateway();
      if (link.connected && link.token) link.sendDeviceState(state);
      if (!armed || !ready() || drivePending) return;
      const attempt = generation, [linear, angular] = motion[direction];
      // Keep at most one movement request in flight. Never queue stale gestures.
      drivePending = true;
      void link.send({ action: "drive", source: "gesture", linear, angular })
        .then(async () => {
          if (attempt !== generation || gateway().token !== link.token) await release(link);
        })
        .catch(async (error) => {
          if (attempt === generation) {
            await disarm("release");
            message(String(error));
          }
        })
        .finally(() => { drivePending = false; notify(); });
    },
  };
}
