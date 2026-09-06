import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const evidence = JSON.parse(readFileSync('schemas/acquisition/of1/monitor-browser-evidence.json', 'utf8'));

describe('executed monitor browser evidence', () => {
  it('binds the original reviewed screenshots without treating them as new acquisition evidence', () => {
    const authentic = evidence.authentic_metadata;
    const simulation = evidence.local_simulation;
    for (const [path, hash] of [
      [authentic.screenshot, authentic.screenshot_sha256],
      [simulation.live_screenshot, simulation.live_screenshot_sha256],
      [simulation.after_restart.screenshot, simulation.after_restart.screenshot_sha256],
    ]) {
      const bytes = readFileSync(path);
      expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(hash);
    }
    expect(evidence.evidence).toBe('FIXTURE');
    expect(authentic.new_authentic_acquisition).toBe(false);
    expect(evidence.observed_page_network.non_loopback_page_requests).toBe(0);
  });

  it('retains distinct live, stop, restart and authentic recorded observations', () => {
    const simulation = evidence.local_simulation;
    const frames = simulation.live_frames;
    expect(frames).toHaveLength(3);
    expect(frames.every((frame: { published_display: string }) => frame.published_display === '0 B')).toBe(true);
    const progress = frames.map((frame: { operation_0_aria_progress_percent: string }) => Number(frame.operation_0_aria_progress_percent));
    expect(progress[0]).toBeGreaterThan(0);
    expect(progress[1]).toBeGreaterThan(progress[0]);
    expect(progress[2]).toBeGreaterThan(progress[1]);
    expect(simulation.stopped.reason_display).toBe('HTTP_ENTITY_TRUNCATED');
    expect(simulation.stopped.published_operations).toBe(2);
    expect(simulation.after_restart).toMatchObject({ published_operations: 4, attempts: 5, retries: 1, received_basis: 'DURABLE_LOWER_BOUND' });
    expect(evidence.authentic_metadata).toMatchObject({ mode: 'RECORDED', received_basis: 'RECEIPTS_ONLY', published_bytes: 5184161, attempts: 4, retries: 0 });
  });
});
