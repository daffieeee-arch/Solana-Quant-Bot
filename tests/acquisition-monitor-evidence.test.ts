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

describe('executed paced payload monitor evidence', () => {
  const payload = JSON.parse(readFileSync('schemas/acquisition/of1/monitor-payload-browser-evidence.json', 'utf8'));

  it('binds real browser images and the executed simulator source without promoting Fixture evidence', () => {
    for (const { path, sha256 } of payload.screenshots) {
      const bytes = readFileSync(path);
      expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(sha256);
    }
    expect(createHash('sha256').update(readFileSync('rust/of1-range-recorder/src/bin/of1-monitor-simulation.rs')).digest('hex')).toBe(payload.simulator_source_sha256);
    expect(payload.evidence).toBe('Fixture');
    expect(payload.new_authentic_acquisition).toBe(false);
    expect(payload.page_network).toEqual({ non_loopback: 0, denied: 0, warnings: 0 });
    expect(payload.payload).toMatchObject({ bytes: 468, verified_nodes: 5, verified_links: 4, root_to_slot_membership: 'UNAVAILABLE', domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4', dataframe_payload_and_checksum: 'NOT_EVALUATED' });
  });

  it('observes increasing in-request payload bytes before publication, bounded ETA and durable restart accounting', () => {
    const [first, second, third, complete] = payload.frames;
    expect([first.stage, second.stage, third.stage]).toEqual(['DOWNLOADING', 'DOWNLOADING', 'DOWNLOADING']);
    expect(first.payload_received).toBeGreaterThan(0);
    expect(second.payload_received).toBeGreaterThan(first.payload_received);
    expect(third.payload_received).toBeGreaterThan(second.payload_received);
    expect(third.payload_received).toBeLessThan(payload.payload.bytes);
    expect([first.payload_published, second.payload_published, third.payload_published]).toEqual([0, 0, 0]);
    const domProgress = [first, second, third].map(frame => Number(frame.progress.find((bar: { label: string }) => bar.label === 'Operatie 4 ontvangen bytes').value));
    expect(domProgress[1]).toBeGreaterThan(domProgress[0]);
    expect(domProgress[2]).toBeGreaterThan(domProgress[1]);
    expect(first.download_eta_ms).toBeNull();
    expect(second.download_eta_ms).toBeGreaterThan(third.download_eta_ms);
    expect(third.download_eta_ms).toBeGreaterThan(0);
    expect(complete).toMatchObject({ stage: 'COMPLETE', payload_received: 468, payload_published: 468, download_eta_ms: null });
    expect(payload.final).toEqual({ publications: 5, attempts: 6, retries: 1, published_bytes: 5184608, reserved_entity_bytes: 5196756, received_basis: 'DURABLE_LOWER_BOUND' });
  });
});
