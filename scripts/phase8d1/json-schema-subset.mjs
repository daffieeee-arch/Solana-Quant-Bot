#!/usr/bin/env node

function typeMatches(value, type) {
  if (type === 'null') return value === null;
  if (type === 'array') return Array.isArray(value);
  if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value);
  if (type === 'integer') return Number.isSafeInteger(value);
  if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
  return typeof value === type;
}
function resolvePointer(root, ref) {
  if (!ref.startsWith('#/')) throw new Error(`unsupported_schema_ref:${ref}`);
  return ref.slice(2).split('/').reduce((value, part) => value?.[part.replaceAll('~1','/').replaceAll('~0','~')], root);
}
function dateTime(value) {
  if (typeof value !== 'string') return false;
  const match=value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/);
  if (!match) return false;
  const [,yearText,monthText,dayText,hourText,minuteText,secondText,,offsetHourText='0',offsetMinuteText='0']=match;
  const year=Number(yearText),month=Number(monthText),day=Number(dayText),hour=Number(hourText),minute=Number(minuteText),second=Number(secondText),offsetHour=Number(offsetHourText),offsetMinute=Number(offsetMinuteText);
  const daysInMonth=new Date(Date.UTC(year,month,0)).getUTCDate();
  return month>=1&&month<=12&&day>=1&&day<=daysInMonth&&hour<=23&&minute<=59&&second<=59&&offsetHour<=23&&offsetMinute<=59&&Number.isFinite(Date.parse(value));
}
function walk(schema, value, root, path, errors) {
  if (schema.$ref) return walk(resolvePointer(root, schema.$ref), value, root, path, errors);
  if (schema.const !== undefined && !Object.is(value, schema.const)) errors.push(`${path}:const`);
  if (schema.enum && !schema.enum.some(entry => Object.is(entry, value))) errors.push(`${path}:enum`);
  if (schema.type) {
    const types=Array.isArray(schema.type)?schema.type:[schema.type];
    if (!types.some(type=>typeMatches(value,type))) { errors.push(`${path}:type`); return; }
  }
  if (schema.not) { const nested=[]; walk(schema.not,value,root,path,nested); if (!nested.length) errors.push(`${path}:not`); }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) errors.push(`${path}:minLength`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${path}:maxLength`);
    if (schema.pattern && !new RegExp(schema.pattern,'u').test(value)) errors.push(`${path}:pattern`);
    if (schema.format === 'date-time' && !dateTime(value)) errors.push(`${path}:format-date-time`);
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${path}:minimum`);
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${path}:maximum`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errors.push(`${path}:minItems`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${path}:maxItems`);
    if (schema.uniqueItems && new Set(value.map(entry=>JSON.stringify(entry))).size !== value.length) errors.push(`${path}:uniqueItems`);
    if (schema.items) value.forEach((entry,index)=>walk(schema.items,entry,root,`${path}[${index}]`,errors));
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    for (const key of schema.required ?? []) if (!Object.prototype.hasOwnProperty.call(value,key)) errors.push(`${path}.${key}:required`);
    if (schema.additionalProperties === false) for (const key of Object.keys(value)) if (!Object.prototype.hasOwnProperty.call(schema.properties ?? {},key)) errors.push(`${path}.${key}:additionalProperties`);
    for (const [key,child] of Object.entries(schema.properties ?? {})) if (Object.prototype.hasOwnProperty.call(value,key)) walk(child,value[key],root,`${path}.${key}`,errors);
  }
}
export function validateJsonSchema(schema,value) { const errors=[]; walk(schema,value,schema,'$',errors); return [...new Set(errors)].sort(); }
