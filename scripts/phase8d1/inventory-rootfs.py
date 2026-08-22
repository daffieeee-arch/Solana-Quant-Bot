#!/usr/bin/env python3
import argparse, base64, binascii, hashlib, json, math, re, tarfile

parser=argparse.ArgumentParser()
parser.add_argument('archive')
parser.add_argument('output')
parser.add_argument('--max-entries',type=int,default=200000)
parser.add_argument('--max-bytes',type=int,default=8*1024*1024*1024)
parser.add_argument('--allowlist',default='deployment/phase8d1/rootfs-public-key-test-vectors.json')
args=parser.parse_args()
with open(args.allowlist,encoding='utf-8') as handle: allowlist=json.load(handle)
if allowlist.get('schemaVersion')!='PHASE8D1_PUBLIC_KEY_TEST_VECTOR_ALLOWLIST_1' or not isinstance(allowlist.get('entries'),list): raise SystemExit('INVALID_PUBLIC_TEST_VECTOR_ALLOWLIST')
allowed_public_vectors={(entry.get('path'),entry.get('candidateSha256')) for entry in allowlist['entries']}
if any(not isinstance(path,str) or not re.fullmatch(r'[0-9a-f]{64}',digest or '') for path,digest in allowed_public_vectors): raise SystemExit('INVALID_PUBLIC_TEST_VECTOR_ALLOWLIST_ENTRY')
rows=[]
total=0
credential_findings=[]
public_test_vector_allowances=set()
credential_patterns=[
    ('private_key',re.compile(rb'-----BEGIN (?P<label>(?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----[ \t\r\n]*(?P<body>[A-Za-z0-9+/=\r\n]{64,131072})-----END (?P=label)-----'),None),
    ('github_token',re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),None),
    ('basic_auth_url',re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@'),None),
    ('credential_assignment',re.compile(rb'\b(TRITON_TOKEN|GITHUB_TOKEN|API_TOKEN|PASSWORD|SECRET|API_KEY)=([^\s\x00]{8,})',re.I),2),
]
def der_sequence_content(value):
    if len(value)<3 or value[0]!=0x30: return None
    first=value[1]
    if first<0x80: header,length=2,first
    else:
        width=first&0x7f
        if width<1 or width>4 or len(value)<2+width: return None
        length=int.from_bytes(value[2:2+width],'big');header=2+width
        if length<0x80: return None
    return value[header:] if header+length==len(value) else None
def is_structural_private_key(match):
    encoded=re.sub(rb'[\r\n]',b'',match.group('body'))
    try: decoded=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError): return False
    label=match.group('label')
    if label==b'OPENSSH PRIVATE KEY': return decoded.startswith(b'openssh-key-v1\x00')
    content=der_sequence_content(decoded)
    if content is None: return False
    return content.startswith(b'\x30') if label==b'ENCRYPTED PRIVATE KEY' else content.startswith(b'\x02')
def shape(value):
    counts={byte:value.count(byte) for byte in set(value)}
    length=len(value)
    entropy=0.0 if not length else -sum((count/length)*math.log2(count/length) for count in counts.values())
    return {'length':length,'entropy':round(entropy,3),'uniqueChars':len(counts),'maxCharRatio':0.0 if not length else round(max(counts.values())/length,3)}
with tarfile.open(args.archive,'r:*') as archive:
    for member in archive:
        if len(rows)>=args.max_entries: raise SystemExit('ROOTFS_ENTRY_LIMIT_EXCEEDED')
        total+=member.size
        if total>args.max_bytes: raise SystemExit('ROOTFS_UNCOMPRESSED_SIZE_LIMIT_EXCEEDED')
        kind='file' if member.isfile() else 'directory' if member.isdir() else 'symlink' if member.issym() else 'hardlink' if member.islnk() else 'device' if member.isdev() else 'other'
        if kind in ('device','other'): raise SystemExit(f'FORBIDDEN_ROOTFS_ENTRY_TYPE:{member.name}:{kind}')
        digest=None
        if member.isfile():
            stream=archive.extractfile(member)
            if stream is None: raise SystemExit(f'ROOTFS_FILE_UNREADABLE:{member.name}')
            h=hashlib.sha256(); overlap=b''; seen=set(); normalized_path=member.name.removeprefix('./')
            while chunk:=stream.read(1024*1024):
                h.update(chunk)
                sample=overlap+chunk
                for pattern_name,pattern,value_group in credential_patterns:
                    if pattern_name in seen: continue
                    if pattern_name=='private_key':
                        match=None
                        for candidate in pattern.finditer(sample):
                            if not is_structural_private_key(candidate): continue
                            candidate_sha=hashlib.sha256(candidate.group(0)).hexdigest()
                            if (normalized_path,candidate_sha) in allowed_public_vectors:
                                public_test_vector_allowances.add((normalized_path,candidate_sha));continue
                            match=candidate;break
                    else: match=pattern.search(sample)
                    if match:
                        value=match.group(value_group) if value_group else match.group(0)
                        finding={'path':member.name,'pattern':pattern_name,**shape(value)}
                        if pattern_name=='credential_assignment': finding['key']=match.group(1).decode('ascii').upper()
                        credential_findings.append(finding);seen.add(pattern_name)
                overlap=sample[-131584:]
            digest=h.hexdigest()
        rows.append({'path':member.name,'type':kind,'mode':format(member.mode,'04o'),'uid':member.uid,'gid':member.gid,'size':member.size,'sha256':digest,'linkTarget':member.linkname or None})
rows.sort(key=lambda row:row['path'])
if credential_findings:
    diagnostic={'error':'ROOTFS_CREDENTIAL_CONTENT_FINDINGS','count':len(credential_findings),'findings':credential_findings[:50],'truncated':len(credential_findings)>50,'publicTestVectorAllowances':len(public_test_vector_allowances)}
    raise SystemExit(json.dumps(diagnostic,separators=(',',':'),sort_keys=True))
with open(args.output,'w',encoding='utf-8') as handle:
    for row in rows: handle.write(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n')
print(json.dumps({'entries':len(rows),'uncompressedBytes':total,'credentialContentFindings':0,'publicTestVectorAllowances':len(public_test_vector_allowances),'output':args.output},sort_keys=True))
