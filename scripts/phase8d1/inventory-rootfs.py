#!/usr/bin/env python3
import argparse, hashlib, json, math, re, tarfile

parser=argparse.ArgumentParser()
parser.add_argument('archive')
parser.add_argument('output')
parser.add_argument('--max-entries',type=int,default=200000)
parser.add_argument('--max-bytes',type=int,default=8*1024*1024*1024)
args=parser.parse_args()
rows=[]
total=0
credential_findings=[]
credential_patterns=[
    ('private_key',re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),None),
    ('github_token',re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),None),
    ('basic_auth_url',re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@'),None),
    ('credential_assignment',re.compile(rb'\b(TRITON_TOKEN|GITHUB_TOKEN|API_TOKEN|PASSWORD|SECRET|API_KEY)=([^\s\x00]{8,})',re.I),2),
]
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
            h=hashlib.sha256(); overlap=b''; seen=set()
            while chunk:=stream.read(1024*1024):
                h.update(chunk)
                sample=overlap+chunk
                for pattern_name,pattern,value_group in credential_patterns:
                    if pattern_name in seen: continue
                    match=pattern.search(sample)
                    if match:
                        value=match.group(value_group) if value_group else match.group(0)
                        finding={'path':member.name,'pattern':pattern_name,**shape(value)}
                        if pattern_name=='credential_assignment': finding['key']=match.group(1).decode('ascii').upper()
                        credential_findings.append(finding);seen.add(pattern_name)
                overlap=sample[-512:]
            digest=h.hexdigest()
        rows.append({'path':member.name,'type':kind,'mode':format(member.mode,'04o'),'uid':member.uid,'gid':member.gid,'size':member.size,'sha256':digest,'linkTarget':member.linkname or None})
rows.sort(key=lambda row:row['path'])
if credential_findings:
    diagnostic={'error':'ROOTFS_CREDENTIAL_CONTENT_FINDINGS','count':len(credential_findings),'findings':credential_findings[:50],'truncated':len(credential_findings)>50}
    raise SystemExit(json.dumps(diagnostic,separators=(',',':'),sort_keys=True))
with open(args.output,'w',encoding='utf-8') as handle:
    for row in rows: handle.write(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n')
print(json.dumps({'entries':len(rows),'uncompressedBytes':total,'credentialContentFindings':0,'output':args.output},sort_keys=True))
