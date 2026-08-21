#!/usr/bin/env python3
import argparse, hashlib, json, re, tarfile

parser=argparse.ArgumentParser()
parser.add_argument('archive')
parser.add_argument('output')
parser.add_argument('--max-entries',type=int,default=200000)
parser.add_argument('--max-bytes',type=int,default=8*1024*1024*1024)
args=parser.parse_args()
rows=[]
total=0
credentialContentFindings=0
credential_patterns=[
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@'),
    re.compile(rb'\b(?:TRITON_TOKEN|GITHUB_TOKEN|API_TOKEN|PASSWORD|SECRET|API_KEY)=[^\s\x00]{8,}',re.I),
]
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
            h=hashlib.sha256(); overlap=b''
            while chunk:=stream.read(1024*1024):
                h.update(chunk)
                sample=overlap+chunk
                if any(pattern.search(sample) for pattern in credential_patterns): credentialContentFindings+=1
                overlap=sample[-512:]
            digest=h.hexdigest()
        rows.append({'path':member.name,'type':kind,'mode':format(member.mode,'04o'),'uid':member.uid,'gid':member.gid,'size':member.size,'sha256':digest,'linkTarget':member.linkname or None})
rows.sort(key=lambda row:row['path'])
if credentialContentFindings: raise SystemExit(f'ROOTFS_CREDENTIAL_CONTENT_FINDINGS:{credentialContentFindings}')
with open(args.output,'w',encoding='utf-8') as handle:
    for row in rows: handle.write(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n')
print(json.dumps({'entries':len(rows),'uncompressedBytes':total,'credentialContentFindings':credentialContentFindings,'output':args.output},sort_keys=True))
