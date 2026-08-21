import { accessSync, constants, lstatSync, readdirSync, type Dirent, type PathLike, type Stats } from 'node:fs';
import { isAbsolute, join, normalize, parse } from 'node:path';

export type CockpitConfig = Readonly<{
  bindHost: string;
  port: number;
  outputDirectory?: string;
}>;

export type CockpitFilesystem = Readonly<{
  lstatSync(path: PathLike): Stats;
  accessSync(path: PathLike, mode?: number): void;
  readdirSync(path: PathLike, options: { withFileTypes: true }): Dirent[];
}>;

const filesystem: CockpitFilesystem = { lstatSync, accessSync, readdirSync };
const DECIMAL_PORT = /^(?:[1-9]\d{0,4})$/;

function parsePort(value: string | undefined): number {
  if (value === undefined || !DECIMAL_PORT.test(value)) throw new Error('INVALID_COCKPIT_PORT');
  const port = Number(value);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) throw new Error('INVALID_COCKPIT_PORT');
  return port;
}

function validIpv4(value: string): boolean {
  const parts = value.split('.');
  return parts.length === 4 && parts.every((part) => /^(?:0|[1-9]\d{0,2})$/.test(part)
    && Number(part) >= 0 && Number(part) <= 255);
}

function parseHost(value: string | undefined): string {
  const host = value === undefined ? '127.0.0.1' : value;
  if (host !== '::1' && !validIpv4(host)) throw new Error('INVALID_COCKPIT_BIND_HOST');
  return host;
}

function errorCode(error: unknown): string | undefined {
  return typeof error === 'object' && error !== null && 'code' in error
    ? String((error as { code?: unknown }).code)
    : undefined;
}

function inspectPath(fs: CockpitFilesystem, path: string): Stats {
  try { return fs.lstatSync(path); }
  catch (error) {
    if (errorCode(error) === 'ENOENT') throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
    throw new Error('OUTPUT_INSPECTION_FAILED');
  }
}

function assertNotWritable(fs: CockpitFilesystem, path: string, stats: Stats): void {
  if ((stats.mode & 0o222) !== 0) throw new Error('MUTABLE_PHASE8A_OUTPUT_DIR');
  try {
    fs.accessSync(path, constants.W_OK);
    throw new Error('MUTABLE_PHASE8A_OUTPUT_DIR');
  } catch (error) {
    if (error instanceof Error && error.message === 'MUTABLE_PHASE8A_OUTPUT_DIR') throw error;
    if (!['EACCES', 'EPERM', 'EROFS'].includes(errorCode(error) ?? '')) throw new Error('OUTPUT_INSPECTION_FAILED');
  }
}

function assertNoGitMarker(fs: CockpitFilesystem, directory: string): void {
  try {
    fs.lstatSync(join(directory, '.git'));
    throw new Error('WORKTREE_PHASE8A_OUTPUT_DIR');
  } catch (error) {
    if (error instanceof Error && error.message === 'WORKTREE_PHASE8A_OUTPUT_DIR') throw error;
    if (errorCode(error) !== 'ENOENT') throw new Error('OUTPUT_INSPECTION_FAILED');
  }
}

function assertImmutableOutputDirectory(path: string, fs: CockpitFilesystem): void {
  if (!isAbsolute(path) || normalize(path) !== path) throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
  const root = parse(path).root;
  const parts = path.slice(root.length).split('/').filter(Boolean);
  const rootStats = inspectPath(fs, root);
  if (rootStats.isSymbolicLink() || !rootStats.isDirectory()) throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
  if ((rootStats.mode & 0o002) !== 0 && (rootStats.mode & 0o1000) === 0) {
    throw new Error('WORLD_WRITABLE_PHASE8A_OUTPUT_ANCESTOR');
  }
  assertNoGitMarker(fs, root);
  let current = root;
  for (const part of parts) {
    current = join(current, part);
    const stats = inspectPath(fs, current);
    if (stats.isSymbolicLink()) throw new Error('SYMLINK_PHASE8A_OUTPUT_DIR');
    if (!stats.isDirectory()) throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
    const worldWritable = (stats.mode & 0o002) !== 0;
    const sticky = (stats.mode & 0o1000) !== 0;
    if (worldWritable && !sticky) throw new Error('WORLD_WRITABLE_PHASE8A_OUTPUT_ANCESTOR');
    assertNoGitMarker(fs, current);
  }
  const final = inspectPath(fs, path);
  if (!final.isDirectory()) throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
  assertNotWritable(fs, path, final);
  const pending = [path];
  while (pending.length) {
    const currentPath = pending.pop() as string;
    const currentStats = inspectPath(fs, currentPath);
    if (currentStats.isSymbolicLink()) throw new Error('SYMLINK_PHASE8A_OUTPUT_DIR');
    if (!currentStats.isDirectory() && !currentStats.isFile()) throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
    assertNotWritable(fs, currentPath, currentStats);
    if (currentStats.isDirectory()) {
      let entries: Dirent[];
      try { entries = fs.readdirSync(currentPath, { withFileTypes: true }); }
      catch { throw new Error('OUTPUT_INSPECTION_FAILED'); }
      for (const entry of entries) {
        if (entry.isSymbolicLink()) throw new Error('SYMLINK_PHASE8A_OUTPUT_DIR');
        pending.push(join(currentPath, entry.name));
      }
    }
  }
}

export function loadCockpitConfig(
  env: Readonly<Record<string, string | undefined>>,
  fs: CockpitFilesystem = filesystem,
): CockpitConfig {
  const port = parsePort(env.COCKPIT_PORT);
  const bindHost = parseHost(env.COCKPIT_BIND_HOST);
  const rawOutput = env.PHASE8A_RESEARCH_OUTPUT_DIR;
  if (rawOutput === undefined || rawOutput === '') return Object.freeze({ bindHost, port });
  if (rawOutput.trim() !== rawOutput) throw new Error('INVALID_PHASE8A_OUTPUT_DIR');
  assertImmutableOutputDirectory(rawOutput, fs);
  return Object.freeze({ bindHost, port, outputDirectory: rawOutput });
}
