import {
  isAlias,
  isPair,
  isScalar,
  parseAllDocuments,
  visit,
} from 'yaml';

export class StrictYamlError extends Error {
  constructor(message) {
    super(message);
    this.name = 'StrictYamlError';
  }
}

/**
 * Parse GitHub Actions YAML with a standards-compliant YAML 1.2 parser.
 * The canonical CI workflow intentionally rejects advanced YAML constructs so
 * policy validation always sees one explicit, duplicate-free structure.
 */
export function parseWorkflowYaml(yaml) {
  if (typeof yaml !== 'string') throw new StrictYamlError('workflow must be text');

  const documents = parseAllDocuments(yaml, {
    keepSourceTokens: true,
    logLevel: 'silent',
    merge: false,
    prettyErrors: true,
    schema: 'core',
    strict: true,
    stringKeys: true,
    uniqueKeys: true,
    version: '1.2',
  });
  if (documents.length !== 1) {
    throw new StrictYamlError(`workflow must contain exactly one YAML document; found ${documents.length}`);
  }

  const document = documents[0];
  const parserIssues = [...document.errors, ...document.warnings];
  if (parserIssues.length > 0) {
    const messages = parserIssues.map((issue) => issue.message.replace(
      /^Map keys must be unique/i,
      'duplicate mapping key',
    ));
    throw new StrictYamlError(messages.join('; '));
  }
  if (
    document.directives.docStart === true
    || document.directives.docEnd
    || document.directives.yaml.explicit
    || Object.keys(document.directives.tags).some((key) => key !== '!!')
  ) {
    throw new StrictYamlError('YAML directives and document markers are not supported');
  }

  let unsupported = null;
  visit(document, (_key, node) => {
    if (unsupported) return visit.BREAK;
    if (isAlias(node) || node?.anchor) {
      unsupported = 'anchors and aliases are not supported';
      return visit.BREAK;
    }
    if (node?.tag) {
      unsupported = 'explicit YAML tags are not supported';
      return visit.BREAK;
    }
    if (isPair(node) && isScalar(node.key) && node.key.value === '<<') {
      unsupported = 'YAML merge keys are not supported';
      return visit.BREAK;
    }
    if (isScalar(node) && (node.type === 'BLOCK_LITERAL' || node.type === 'BLOCK_FOLDED')) {
      unsupported = 'block scalars are not supported by the canonical workflow';
      return visit.BREAK;
    }
    return undefined;
  });
  if (unsupported) throw new StrictYamlError(unsupported);

  const root = document.toJS({ maxAliasCount: 0 });
  if (!root || Array.isArray(root) || typeof root !== 'object') {
    throw new StrictYamlError('workflow root must be a mapping');
  }
  return root;
}
