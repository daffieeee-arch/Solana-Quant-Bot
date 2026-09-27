"""Read only Rust's explicit collection and child inventories, never a glob.

Each child is verified independently and released before the next. UNION views
retain original records; collection ordinals are SQL context, not rewritten
canonical bytes. The collection is bounded metadata, not a combined JSON layer.
"""
import hashlib
import json
import pathlib
import re

from manifest_reader import (MAX_MANIFEST_BYTES, attach_dataset, inventory,
                             layer_names, load_manifest, pairs_unique,
                             regular_bytes, selection_inventory, sha)

MAX_BATCHES = 256
MAX_SELECTED_SLOTS = 256


def literal_path(root, relative):
    if not isinstance(relative, str) or not relative or any(c in relative for c in '*?[]{}'):
        raise ValueError('literal bounded manifest path required; no glob')
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or any(part in ['', '.', '..'] for part in path.parts):
        raise ValueError('relative manifest-listed child path required')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('manifest child path must not be a symlink')
    return current


def bounded_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}',value):
        raise ValueError('bounded canonical identity required')
    return value


def plan_inventory(plan):
    if plan.get('schema') != 'OF1_BATCH_COLLECTION_PLAN_1' or plan.get('research_ready') is not False:
        raise ValueError('unsupported collection plan / Research Ready promotion')
    bounded_id(plan['collection_id'])
    if set(plan)!={'schema','collection_id','workers','sources','logical_selection','batches','research_ready'}:
        raise ValueError('unexpected collection plan fields')
    if set(plan['workers'])!={'batch_decoder_sha256','projector_sha256'} or any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) for value in plan['workers'].values()):
        raise ValueError('exact worker executable hashes required')
    sources = {bounded_id(s['source_id']):s for s in plan['sources']}
    if not sources or len(sources)!=len(plan['sources']) or len(sources)>32:
        raise ValueError('bounded unique original source identities required')
    for source in sources.values():
        if set(source)!={'source_id','run_root','run_id','bindings','sample_identity'} or not pathlib.Path(source['run_root']).is_absolute():
            raise ValueError('exact original source identity fields required')
        bounded_id(source['run_id'])
    logical = plan['logical_selection']
    if not isinstance(logical,list) or not 0<len(logical)<=MAX_SELECTED_SLOTS:
        raise ValueError('bounded complete logical selection required')
    slots=[]
    for row in logical:
        if set(row)!={'slot','source_id','role'}:
            raise ValueError('unexpected logical selection fields')
        slot=row['slot']
        if type(slot) is not int or not 0<=slot<2**64 or row['source_id'] not in sources:
            raise ValueError('invalid logical slot/source identity')
        if row['role'] not in ['ORIGINAL_SELECTION','POSTHOC_DESCRIPTIVE_CONTEXT']:
            raise ValueError('unsupported selection role')
        if row['role']=='POSTHOC_DESCRIPTIVE_CONTEXT' and sources[row['source_id']].get('sample_identity') is not None:
            raise ValueError('posthoc context cannot acquire original research sample identity')
        slots.append(slot)
    if slots != sorted(set(slots)):
        raise ValueError('logical selection overlap or order conflict')
    if any(right!=left+1 for left,right in zip(slots,slots[1:])):
        raise ValueError('unplanned gap in contiguous logical selection')
    for source_id,source in sources.items():
        sample=source.get('sample_identity')
        if sample is not None:
            start,end=sample.get('start_slot'),sample.get('end_slot_exclusive')
            if type(start) is not int or type(end) is not int or not 0<=start<end<2**64 or end-start>MAX_SELECTED_SLOTS:
                raise ValueError('bounded original sample interval required')
            expected=list(range(sample['start_slot'],sample['end_slot_exclusive']))
            actual=[r['slot'] for r in logical if r['source_id']==source_id and r['role']=='ORIGINAL_SELECTION']
            if actual!=expected:
                raise ValueError('collection must retain full original native sample selection')
    batches=plan['batches']
    if not isinstance(batches,list) or not 0<len(batches)<=MAX_BATCHES:
        raise ValueError('bounded physical batch list required')
    assigned=[]; ids=set(); directories=set(); receipts=set()
    for batch in batches:
        if set(batch)!={'batch_id','source_id','slots','receipt_sequences','output_directory'}:
            raise ValueError('unexpected physical batch fields')
        identity=bounded_id(batch['batch_id'])
        if identity in ids or batch['source_id'] not in sources:
            raise ValueError('duplicate batch / unknown source')
        ids.add(identity)
        selected=batch['slots']
        if not 0<len(selected)<=3 or selected != sorted(set(selected)):
            raise ValueError('ordered bounded worker slot list required')
        if len(batch['receipt_sequences'])!=len(selected) or len(set(batch['receipt_sequences']))!=len(selected):
            raise ValueError('one exact unique receipt per slot required')
        sequences=batch['receipt_sequences']
        if any(type(n) is not int or not 4<=n<2**64 for n in sequences) or sequences!=sorted(sequences):
            raise ValueError('ordered original payload receipts required')
        for sequence in sequences:
            identity=(batch['source_id'],sequence)
            if identity in receipts:
                raise ValueError('overlapping original receipt assignment')
            receipts.add(identity)
        directory=batch['output_directory']
        bounded_id(directory)
        literal_path(pathlib.Path('/synthetic-inventory-root'),directory)
        if directory in directories or any(pathlib.PurePosixPath(directory).is_relative_to(d)
                                           or pathlib.PurePosixPath(d).is_relative_to(directory)
                                           for d in directories):
            raise ValueError('overlapping batch output directories')
        directories.add(directory)
        assigned += [(batch['source_id'],slot) for slot in selected]
    if assigned != [(row['source_id'],row['slot']) for row in logical]:
        raise ValueError('physical batches overlap, reorder, or leave an unplanned logical gap')
    return sources,logical,batches


def load_collection(path):
    """Accept a final collection directory or an immutable progress snapshot."""
    manifest_path = path/'collection.json' if path.is_dir() else path
    root = manifest_path.parent
    raw=regular_bytes(manifest_path,MAX_MANIFEST_BYTES)
    if regular_bytes(pathlib.Path(str(manifest_path)+'.sha256'),65).decode().strip()!=sha(raw):
        raise ValueError('collection publication/hash mismatch')
    manifest=json.loads(raw,object_pairs_hook=pairs_unique)
    if manifest.get('schema')!='OF1_BATCH_COLLECTION_1' or manifest.get('research_ready') is not False:
        raise ValueError('unsupported collection schema / Research Ready promotion')
    _,logical,planned=plan_inventory(manifest['plan'])
    # Exact source plan bytes are separately bound by Rust. Its hash must also
    # be the binding in every child; do not invent Python canonical JSON bytes.
    if not re.fullmatch('[0-9a-f]{64}',manifest['plan_sha256']):
        raise ValueError('invalid original plan hash')
    retained_plan=regular_bytes(root/'plan.json',MAX_MANIFEST_BYTES)
    if sha(retained_plan)!=manifest['plan_sha256'] or json.loads(retained_plan,object_pairs_hook=pairs_unique)!=manifest['plan']:
        raise ValueError('collection differs from retained original plan bytes')
    batches=manifest['batches']
    if len(batches)!=len(planned):
        raise ValueError('missing batch outcome inventory')
    paths=set(); all_verified=True
    for expected,actual in zip(planned,batches,strict=True):
        if (actual['batch_id'],actual['source_id'],actual['selected_slots']) != (expected['batch_id'],expected['source_id'],expected['slots']):
            raise ValueError('collection batch differs from exact plan')
        if actual['state'] not in ['PENDING','VERIFIED']:
            raise ValueError('unsupported batch outcome')
        all_verified=all_verified and actual['state']=='VERIFIED'
        if actual['state']=='VERIFIED':
            relative=actual['parquet_manifest_path']
            path=literal_path(root,relative)
            if relative in paths or path != literal_path(root,expected['output_directory'])/'parquet'/'manifest.json':
                raise ValueError('duplicate or substituted child manifest reference')
            paths.add(relative)
            if sha(regular_bytes(path,MAX_MANIFEST_BYTES))!=actual['parquet_manifest_sha256']:
                raise ValueError('child manifest hash mismatch')
    outcomes=manifest['slot_outcomes']
    if [(r['source_id'],r['slot'],r['role']) for r in outcomes] != [(r['source_id'],r['slot'],r['role']) for r in logical]:
        raise ValueError('missing, reordered or substituted selected-slot outcomes')
    accounted=all_verified and all(row['state']=='ACCOUNTED' for row in outcomes)
    if manifest['state'] not in ['COMPLETE','INCOMPLETE'] or (manifest['state']=='COMPLETE')!=accounted:
        raise ValueError('false complete collection claim')
    if manifest['completeness']['all_selected_slots_accounted'] is not accounted:
        raise ValueError('collection accounting completeness mismatch')
    return manifest,sha(raw)


def sql_literal(value):
    return "'"+str(value).replace("'","''")+"'"


def attach_collection(db, root, manifest):
    sources,logical,planned=plan_inventory(manifest['plan'])
    if any((source.get('sample_identity') or {}).get('b7') is not None for source in sources.values()):
        raise ValueError('B7_ANALYTICAL_EXPORT_NOT_AUTHORIZED: pending and complete campaigns use guarded native reports only')
    role_map={r['slot']:r['role'] for r in logical}
    layer_parts={layer:[] for layer in ['bronze','silver']}
    ordinals={layer:0 for layer in layer_parts}
    digests={layer:b'OF1_ORDERED_RECORD_CHAIN_1' for layer in layer_parts}
    seen_records={layer:0 for layer in layer_parts}
    summary_batches=[]; summary_slots=[]; complete_slots={}
    for expected,batch in zip(planned,manifest['batches'],strict=True):
        display={'batch_id':batch['batch_id'],'source_id':batch['source_id'],
                 'slots':batch['selected_slots'],'state':batch['state']}
        if batch['state']!='VERIFIED':
            summary_batches.append(display)
            continue
        child_path=literal_path(root,batch['parquet_manifest_path'])
        child,child_hash=load_manifest(child_path.parent)
        if child_hash!=batch['parquet_manifest_sha256']:
            raise ValueError('child manifest identity changed')
        selected=selection_inventory(child)
        if selected['selected_slots']!=expected['slots']:
            raise ValueError('child selected slots differ from physical plan')
        # A readable incomplete child is evidence, never a VERIFIED batch.
        # selection_inventory also checks each accounted slot's exact counts.
        if selected['status']!='ACCOUNTED' or selected['all_expected_packages_accounted'] is not True:
            raise ValueError('child package accounting incomplete')
        if child.get('sample_identity')!=sources[batch['source_id']].get('sample_identity'):
            raise ValueError('child reclassified original source sample')
        binding=child.get('batch_binding')
        if not isinstance(binding,dict) or binding.get('plan_sha256')!=manifest['plan_sha256'] or binding.get('batch_id')!=batch['batch_id'] or binding.get('source_id')!=batch['source_id']:
            raise ValueError('child lacks exact original plan/batch/source binding')
        if json.loads(binding['plan_json'],object_pairs_hook=pairs_unique)!=manifest['plan']:
            raise ValueError('collection plan differs from child original plan bytes')
        if child['input']['execution']['executable_sha256']!=manifest['plan']['workers']['batch_decoder_sha256']:
            raise ValueError('child decoder executable differs from plan')
        if child['writer']['executable_sha256']!=manifest['plan']['workers']['projector_sha256']:
            raise ValueError('child projector executable differs from plan')
        attach_dataset(db,child_path.parent,child)
        for layer in layer_parts:
            rows=child['layers'][layer]['rows']
            if batch[layer+'_ordinal_start']!=ordinals[layer] or batch[layer+'_ordinal_end_exclusive']!=ordinals[layer]+rows:
                raise ValueError('collection global ordinal overlap or gap')
            cursor=db.execute(f'SELECT record_bytes FROM {layer} ORDER BY record_ordinal')
            while row:=cursor.fetchone():
                record=row[0][:-1]
                digests[layer]=hashlib.sha256(digests[layer]+len(record).to_bytes(8,'little')+record).digest()
                seen_records[layer]+=1
            names=layer_names(child,layer)
            paths=[str(child_path.parent/name) for name in names]
            role='CASE slot '+''.join('WHEN '+str(slot)+' THEN '+sql_literal(role_map[slot])+' ' for slot in expected['slots'])+'ELSE NULL END'
            part=('SELECT * EXCLUDE(record_ordinal), record_ordinal AS batch_record_ordinal, '
                  f'(record_ordinal+{ordinals[layer]})::UBIGINT AS record_ordinal, '
                  f'{sql_literal(batch["batch_id"])} AS collection_batch_id, '
                  f'{sql_literal(batch["source_id"])} AS collection_source_id, {role} AS collection_role '
                  'FROM read_parquet(['+','.join(sql_literal(p) for p in paths)+'])')
            layer_parts[layer].append(part)
            ordinals[layer]+=rows
            db.execute('DROP VIEW '+layer)
        inv=inventory(child)
        display.update(bronze_rows=inv['bronze']['rows'],silver_rows=inv['silver']['rows'],
                       parquet_files=sum(v['file_count'] for v in inv.values()),
                       parquet_bytes=sum(v['bytes'] for v in inv.values()))
        for row in selected['slots']:
            complete_slots[row['slot']]=row
        summary_batches.append(display)
    for layer,parts in layer_parts.items():
        expected_digest=digests[layer].hex() if seen_records[layer] else sha(digests[layer])
        if manifest['layers'][layer]['rows']!=ordinals[layer] or manifest['layers'][layer]['ordered_logical_sha256']!=expected_digest:
            raise ValueError('global logical record identity differs from batch chain')
        if parts:
            db.execute('CREATE VIEW '+layer+' AS '+' UNION ALL '.join(parts))
    if layer_parts['silver']:
        missing=db.execute('SELECT COUNT(*) FROM silver s WHERE NOT EXISTS (SELECT 1 FROM bronze b WHERE '
                           's.bronze_record_sha256=b.record_sha256 AND s.slot=b.slot AND '
                           's.transaction_index=b.transaction_index AND s.raw_sha256=b.raw_sha256 AND '
                           's.collection_source_id=b.collection_source_id)').fetchone()[0]
        if missing:
            raise ValueError('Silver global source/parent binding failed')
    for declared in manifest['slot_outcomes']:
        slot=declared['slot']; actual=complete_slots.get(slot)
        if actual is not None:
            nonzero_declared={k:v for k,v in declared['dispositions'].items() if v}
            nonzero_actual={k:v for k,v in actual['outcomes'].items() if v}
            if declared['state']!='ACCOUNTED' or declared['transaction_envelopes']!=actual['expected_packages'] or nonzero_declared!=nonzero_actual:
                raise ValueError('collection slot accounting differs from original child')
        elif declared['state']!='PENDING':
            raise ValueError('unpublished slot cannot be accounted')
        summary_slots.append({'slot':slot,'source_id':declared['source_id'],'role':declared['role'],
                              'state':declared['state'],'expected_packages':actual['expected_packages'] if actual else None,
                              'present_packages':actual['present_packages'] if actual else None,
                              'decoded_packages':actual['decoded_packages'] if actual else None,
                              'outcomes':actual['outcomes'] if actual else None,'accounted':actual['accounted'] if actual else False})
    accounted=len(complete_slots)==len(logical) and all(row['accounted'] is True for row in complete_slots.values())
    status='COMPLETE' if accounted else 'INCOMPLETE'
    if manifest['state']!=status or manifest['completeness']['all_selected_slots_accounted'] is not accounted:
        raise ValueError('collection completeness differs from verified child accounting')
    return {'collection_id':manifest['plan']['collection_id'],'status':status,
            'verified_batches':sum(b['state']=='VERIFIED' for b in summary_batches),
            'batches':summary_batches,'selected_slots':summary_slots,'layers':manifest['layers'],
            'sources':manifest['plan']['sources'],'research_ready':False,
            'completeness':manifest['completeness'],'plan_sha256':manifest['plan_sha256']}
