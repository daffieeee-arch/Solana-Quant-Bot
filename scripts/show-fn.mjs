import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../src/providers/triton-geyser.ts', import.meta.url), 'utf8');
// print de eerste guard van parseGenericSwap
const start = src.indexOf('export function parseGenericSwap');
console.log(src.slice(start, start + 900));