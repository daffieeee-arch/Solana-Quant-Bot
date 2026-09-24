/** Small synthetic Python-produced golden contract; no authentic source data. */
import golden from './mint-flow.json';
import { parseInspection } from '../../src/mint-inspector/contract.js';
import { parseMintFlow } from '../../src/mint-inspector/mint-flow.js';
export function fixtureMintFlow() {
  const copy = structuredClone(golden), inspection = parseInspection(copy.inspection);
  return { inspection, flow: parseMintFlow(copy.flow, inspection) };
}
