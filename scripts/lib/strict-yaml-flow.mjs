export class StrictYamlError extends Error {
  constructor(message, lineNumber) {
    super(lineNumber ? `line ${lineNumber}: ${message}` : message);
    this.name = 'StrictYamlError';
  }
}

export function own(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

export function mapObject() {
  return Object.create(null);
}

/** Remove a YAML comment while preserving # characters inside quoted scalars. */
export function stripYamlComment(line) {
  let quote = null;
  let escaped = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (quote === '"' && escaped) {
      escaped = false;
      continue;
    }
    if (quote === '"' && character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) {
        if (quote === "'" && line[index + 1] === "'") {
          index += 1;
          continue;
        }
        quote = null;
      }
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '#') return line.slice(0, index);
  }
  if (quote) throw new StrictYamlError('unterminated quoted scalar');
  return line;
}

export function scanTopLevel(text, target) {
  let quote = null;
  let escaped = false;
  let braces = 0;
  let brackets = 0;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quote === '"' && escaped) {
      escaped = false;
      continue;
    }
    if (quote === '"' && character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) {
        if (quote === "'" && text[index + 1] === "'") {
          index += 1;
          continue;
        }
        quote = null;
      }
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '{') braces += 1;
    else if (character === '}') braces -= 1;
    else if (character === '[') brackets += 1;
    else if (character === ']') brackets -= 1;
    if (braces < 0 || brackets < 0) throw new StrictYamlError('unbalanced flow collection');
    if (character === target && braces === 0 && brackets === 0) return index;
  }
  if (quote || braces !== 0 || brackets !== 0) throw new StrictYamlError('unterminated quoted or flow value');
  return -1;
}

function splitTopLevel(text, delimiter) {
  const parts = [];
  let start = 0;
  let quote = null;
  let escaped = false;
  let braces = 0;
  let brackets = 0;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quote === '"' && escaped) {
      escaped = false;
      continue;
    }
    if (quote === '"' && character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) {
        if (quote === "'" && text[index + 1] === "'") {
          index += 1;
          continue;
        }
        quote = null;
      }
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '{') braces += 1;
    else if (character === '}') braces -= 1;
    else if (character === '[') brackets += 1;
    else if (character === ']') brackets -= 1;
    if (braces < 0 || brackets < 0) throw new StrictYamlError('unbalanced flow collection');
    if (character === delimiter && braces === 0 && brackets === 0) {
      parts.push(text.slice(start, index));
      start = index + 1;
    }
  }
  if (quote || braces !== 0 || brackets !== 0) throw new StrictYamlError('unterminated quoted or flow value');
  parts.push(text.slice(start));
  return parts;
}

function parseQuotedScalar(raw, lineNumber) {
  if (raw.startsWith("'")) {
    if (!raw.endsWith("'") || raw.length < 2) throw new StrictYamlError('unterminated single-quoted scalar', lineNumber);
    return raw.slice(1, -1).replace(/''/g, "'");
  }
  if (raw.startsWith('"')) {
    if (!raw.endsWith('"') || raw.length < 2) throw new StrictYamlError('unterminated double-quoted scalar', lineNumber);
    try {
      return JSON.parse(raw);
    } catch {
      throw new StrictYamlError('invalid double-quoted scalar', lineNumber);
    }
  }
  return null;
}

export function parseKey(raw, lineNumber) {
  const trimmed = raw.trim();
  if (!trimmed) throw new StrictYamlError('empty mapping key', lineNumber);
  const quoted = parseQuotedScalar(trimmed, lineNumber);
  const key = quoted ?? trimmed;
  if (typeof key !== 'string' || !key) throw new StrictYamlError('mapping key must be a non-empty string', lineNumber);
  if (key === '<<') throw new StrictYamlError('YAML merge keys are not supported', lineNumber);
  if (/^[&*!?]/.test(key)) throw new StrictYamlError('anchors, aliases, tags, and complex keys are not supported', lineNumber);
  return key;
}

function parseFlowMap(raw, lineNumber) {
  if (!raw.endsWith('}')) throw new StrictYamlError('unterminated flow mapping', lineNumber);
  const inner = raw.slice(1, -1).trim();
  const result = mapObject();
  if (!inner) return result;
  for (const pair of splitTopLevel(inner, ',')) {
    if (!pair.trim()) throw new StrictYamlError('empty flow mapping entry', lineNumber);
    const colon = scanTopLevel(pair, ':');
    if (colon < 0) throw new StrictYamlError('flow mapping entry is missing a colon', lineNumber);
    const key = parseKey(pair.slice(0, colon), lineNumber);
    if (own(result, key)) throw new StrictYamlError(`duplicate mapping key ${JSON.stringify(key)}`, lineNumber);
    const valueRaw = pair.slice(colon + 1).trim();
    if (!valueRaw) throw new StrictYamlError(`flow mapping key ${JSON.stringify(key)} has no value`, lineNumber);
    result[key] = parseScalar(valueRaw, lineNumber);
  }
  return result;
}

function parseFlowSequence(raw, lineNumber) {
  if (!raw.endsWith(']')) throw new StrictYamlError('unterminated flow sequence', lineNumber);
  const inner = raw.slice(1, -1).trim();
  if (!inner) return [];
  return splitTopLevel(inner, ',').map((part) => {
    if (!part.trim()) throw new StrictYamlError('empty flow sequence entry', lineNumber);
    return parseScalar(part.trim(), lineNumber);
  });
}

export function parseScalar(raw, lineNumber) {
  const value = raw.trim();
  if (!value) return null;
  if (/^(?:\||>|\|-|>-|\|\+|>\+)$/.test(value)) {
    throw new StrictYamlError('block scalars are not supported by the canonical workflow subset', lineNumber);
  }
  if (/^[&*!]/.test(value)) throw new StrictYamlError('anchors, aliases, and tags are not supported', lineNumber);
  if (value.startsWith('{')) return parseFlowMap(value, lineNumber);
  if (value.startsWith('[')) return parseFlowSequence(value, lineNumber);
  const quoted = parseQuotedScalar(value, lineNumber);
  if (quoted !== null) return quoted;
  if (/^(?:true|false)$/i.test(value)) return value.toLowerCase() === 'true';
  if (/^(?:null|~)$/i.test(value)) return null;
  if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)) return Number(value);
  return value;
}
