import {
  StrictYamlError,
  mapObject,
  own,
  parseKey,
  parseScalar,
  scanTopLevel,
  stripYamlComment,
} from './strict-yaml-flow.mjs';

/**
 * Parse the deliberately small, canonical YAML subset used by this workflow.
 * Unsupported YAML constructs fail closed instead of being approximated.
 */
export function parseWorkflowYaml(yaml) {
  if (typeof yaml !== 'string') throw new StrictYamlError('workflow must be text');
  const lines = [];
  const sourceLines = yaml.replace(/^\uFEFF/, '').split(/\r?\n/);
  for (let offset = 0; offset < sourceLines.length; offset += 1) {
    const raw = sourceLines[offset];
    if (raw.includes('\t')) throw new StrictYamlError('tabs are not allowed', offset + 1);
    const withoutComment = stripYamlComment(raw).replace(/\s+$/, '');
    if (!withoutComment.trim()) continue;
    const indent = withoutComment.length - withoutComment.trimStart().length;
    if (indent % 2 !== 0) throw new StrictYamlError('indentation must use two-space increments', offset + 1);
    const content = withoutComment.slice(indent);
    if (['---', '...'].includes(content) || content.startsWith('%')) {
      throw new StrictYamlError('YAML directives and document markers are not supported', offset + 1);
    }
    lines.push({ indent, content, lineNumber: offset + 1 });
  }
  if (lines.length === 0) throw new StrictYamlError('workflow is empty');
  if (lines[0].indent !== 0) throw new StrictYamlError('root mapping must start at indentation zero', lines[0].lineNumber);

  let index = 0;
  const isSequenceLine = (content) => content === '-' || content.startsWith('- ');

  const assignPair = (target, pairText, pairIndent, lineNumber) => {
    const colon = scanTopLevel(pairText, ':');
    if (colon < 0) throw new StrictYamlError('mapping entry is missing a colon', lineNumber);
    const key = parseKey(pairText.slice(0, colon), lineNumber);
    if (own(target, key)) throw new StrictYamlError(`duplicate mapping key ${JSON.stringify(key)}`, lineNumber);
    const valueRaw = pairText.slice(colon + 1).trim();
    if (valueRaw) {
      target[key] = parseScalar(valueRaw, lineNumber);
      if (index < lines.length && lines[index].indent > pairIndent) {
        throw new StrictYamlError(`scalar key ${JSON.stringify(key)} cannot have a nested block`, lines[index].lineNumber);
      }
      return;
    }
    if (index < lines.length && lines[index].indent > pairIndent) {
      if (lines[index].indent !== pairIndent + 2) {
        throw new StrictYamlError('nested blocks must indent by exactly two spaces', lines[index].lineNumber);
      }
      target[key] = parseNode(pairIndent + 2);
    } else {
      target[key] = null;
    }
  };

  const parseMapping = (indent) => {
    const result = mapObject();
    while (index < lines.length && lines[index].indent === indent && !isSequenceLine(lines[index].content)) {
      const line = lines[index];
      index += 1;
      assignPair(result, line.content, indent, line.lineNumber);
    }
    if (index < lines.length && lines[index].indent > indent) {
      throw new StrictYamlError('unexpected indentation', lines[index].lineNumber);
    }
    return result;
  };

  const parseSequence = (indent) => {
    const result = [];
    while (index < lines.length && lines[index].indent === indent && isSequenceLine(lines[index].content)) {
      const line = lines[index];
      const rest = line.content === '-' ? '' : line.content.slice(2).trim();
      index += 1;
      if (!rest) {
        if (index < lines.length && lines[index].indent > indent) {
          if (lines[index].indent !== indent + 2) {
            throw new StrictYamlError('sequence children must indent by exactly two spaces', lines[index].lineNumber);
          }
          result.push(parseNode(indent + 2));
        } else {
          result.push(null);
        }
        continue;
      }
      const colon = scanTopLevel(rest, ':');
      if (colon >= 0) {
        const item = mapObject();
        assignPair(item, rest, indent + 2, line.lineNumber);
        while (index < lines.length && lines[index].indent === indent + 2 && !isSequenceLine(lines[index].content)) {
          const continuation = lines[index];
          index += 1;
          assignPair(item, continuation.content, indent + 2, continuation.lineNumber);
        }
        if (index < lines.length && lines[index].indent > indent) {
          throw new StrictYamlError('unexpected indentation in sequence mapping', lines[index].lineNumber);
        }
        result.push(item);
      } else {
        result.push(parseScalar(rest, line.lineNumber));
        if (index < lines.length && lines[index].indent > indent) {
          throw new StrictYamlError('scalar sequence item cannot have a nested block', lines[index].lineNumber);
        }
      }
    }
    return result;
  };

  function parseNode(indent) {
    if (index >= lines.length || lines[index].indent !== indent) {
      throw new StrictYamlError('unexpected indentation', lines[index]?.lineNumber);
    }
    return isSequenceLine(lines[index].content) ? parseSequence(indent) : parseMapping(indent);
  }

  const root = parseNode(0);
  if (index !== lines.length) throw new StrictYamlError('could not consume complete workflow', lines[index]?.lineNumber);
  if (!root || Array.isArray(root) || typeof root !== 'object') {
    throw new StrictYamlError('workflow root must be a mapping');
  }
  return root;
}
