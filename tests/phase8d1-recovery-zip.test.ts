import { describe, expect, it } from 'vitest';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const script='scripts/phase8d1/inspect-artifact-zip.py';
function makeZip(path:string,kind:string){const code=`import zipfile,sys,stat\np=sys.argv[1];k=sys.argv[2]\nwith zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:\n if k=='safe': z.writestr('publish-state.json','{}')\n elif k=='large': z.writestr('huge.json','A'*1048577)\n elif k=='traversal': z.writestr('../escape.json','{}')\n elif k=='alias-empty': z.writestr('dir//a.json','{}')\n elif k=='alias-dot': z.writestr('dir/./a.json','{}')\n elif k=='alias-duplicate': z.writestr('dir/a.json','{}'); z.writestr('dir/./a.json','{}')\n elif k=='symlink':\n  i=zipfile.ZipInfo('link.json');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(i,'target')\n`;return spawnSync('python3',['-c',code,path,kind],{encoding:'utf8'});}

describe('Phase 8D1-R pre-extraction ZIP boundary',()=>{
  it('extracts only bounded regular safe files after complete central-directory validation',()=>{
    expect(existsSync(script)).toBe(true);const root=mkdtempSync(join(tmpdir(),'phase8d1-zip-'));
    try{
      const safe=join(root,'safe.zip'),safeOut=join(root,'safe-out');expect(makeZip(safe,'safe').status).toBe(0);let result=spawnSync('python3',[script,safe,safeOut],{encoding:'utf8'});expect(result.status,result.stderr).toBe(0);expect(readFileSync(join(safeOut,'publish-state.json'),'utf8')).toBe('{}');
      for(const kind of ['large','traversal','alias-empty','alias-dot','alias-duplicate','symlink']){const zip=join(root,`${kind}.zip`),out=join(root,`${kind}-out`);expect(makeZip(zip,kind).status).toBe(0);result=spawnSync('python3',[script,zip,out],{encoding:'utf8'});expect(result.status,kind).not.toBe(0);expect(existsSync(out),kind).toBe(false);}
    }finally{rmSync(root,{recursive:true,force:true});}
  });
});
