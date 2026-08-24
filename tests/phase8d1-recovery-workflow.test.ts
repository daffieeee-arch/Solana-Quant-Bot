import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import YAML from 'yaml';

const path='.github/workflows/phase8d-images-recover.yml';

describe('Phase 8D1-R recovery workflow boundary',()=>{
  it('is workflow-dispatch-only with exact read-only permissions and required recovery inputs',()=>{
    expect(existsSync(path)).toBe(true);
    const raw=readFileSync(path,'utf8'),workflow:any=YAML.parse(raw),dispatch=workflow.on.workflow_dispatch;
    expect(Object.keys(workflow.on)).toEqual(['workflow_dispatch']);
    expect(workflow.permissions).toEqual({contents:'read',actions:'read',packages:'read'});
    expect(Object.keys(dispatch.inputs).sort()).toEqual(['cockpit_digest','cockpit_tag','confirmation','image_source_sha','original_publish_run_id','runner_digest','runner_tag'].sort());
    for(const input of Object.values(dispatch.inputs) as any[])expect(input.required).toBe(true);
    expect(dispatch.inputs.confirmation.description).toContain('RECOVER_EXISTING_PHASE8D_IMAGES');
    const jobs=Object.values(workflow.jobs) as any[];expect(jobs).toHaveLength(1);const job=jobs[0];expect(job['runs-on']).toBe('ubuntu-24.04');
    expect(job.if).toContain("github.ref == 'refs/heads/main'");
    const names=job.steps.map((s:any)=>s.name);
    for(const name of ['Check out exact recovery workflow source','Check out exact image source','Set up exact Node runtime','Install locked recovery dependencies','Enforce recovery policy and source ancestry','Set up isolated Buildx','Verify exact sandboxed BuildKit server','Run read-only existing-digest recovery','Upload bounded recovery evidence','Require recovery success'])expect(names).toContain(name);
    const checkouts=job.steps.filter((s:any)=>String(s.uses??'').startsWith('actions/checkout@'));expect(checkouts).toHaveLength(2);expect(checkouts.every((s:any)=>s.with['persist-credentials']===false)).toBe(true);expect(checkouts[0].with.ref).toBe('${{ github.sha }}');expect(checkouts[1].with.ref).toBe('${{ inputs.image_source_sha }}');expect(checkouts[1].with.path).toBe('phase8d1-image-source');
    const setup=job.steps.find((s:any)=>String(s.uses??'').startsWith('docker/setup-buildx-action@'));expect(setup.with.version).toBe('v0.12.1');expect(setup.with['driver-opts']).toContain('moby/buildkit@sha256:040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528');
    expect(job.steps.find((s:any)=>s.name==='Install locked recovery dependencies').run).toBe('npm ci');
    expect(job.steps.find((s:any)=>s.name==='Run read-only existing-digest recovery').run).toBe('scripts/phase8d1/recover-existing-digests.sh');
    const upload=job.steps.find((s:any)=>s.name==='Upload bounded recovery evidence');expect(String(upload.uses)).toMatch(/^actions\/upload-artifact@[0-9a-f]{40}$/);expect(upload.with.path).toBe('phase8d1-recovery-evidence/*.json\nphase8d1-recovery-evidence/*.txt\nphase8d1-recovery-evidence/*.sha256\n');
    expect(JSON.stringify(workflow)).not.toMatch(/packages.*write|contents.*write|id-token|attestations.*write|docker\/build-push-action|docker push|gh api.*DELETE|package.*delete|tag.*overwrite|package.*visibility.*PATCH|truenas|grafana|clickhouse/i);
  });
});
