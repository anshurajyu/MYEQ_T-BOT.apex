export type CommandProof = { epoch: string; generation: number; permit: string; expires_in_ms: number };

/** Server time decides freshness; local sequence and generation prevent replay. */
export function createCommandProof() {
  let proof: CommandProof | null = null, sequence = 0, minimumGeneration = 0;
  return {
    version() { return proof ? `${proof.epoch}:${proof.generation}` : null; },
    reset() { proof = null; sequence = 0; minimumGeneration = 0; },
    accept(next?: CommandProof) {
      if (!next || typeof next.epoch !== 'string' || !Number.isInteger(next.generation) || next.generation < minimumGeneration) return false;
      if (proof && (next.epoch !== proof.epoch || next.generation < proof.generation)) return false;
      proof = next;
      return true;
    },
    stamp(command: Record<string, unknown>) {
      if (command.action === 'stop' || command.action === 'cancel') {
        minimumGeneration = Math.max(minimumGeneration, (proof?.generation ?? 0) + 1);
        return command;
      }
      if (proof && proof.generation < minimumGeneration) throw Error('Waiting for STOP acknowledgement before re-arming.');
      if (!proof) throw Error('Waiting for current gateway authority. Select control again.');
      return { ...command, epoch: proof.epoch, generation: proof.generation, permit: proof.permit, seq: ++sequence };
    },
  };
}
