#!/usr/bin/env node
// Optional read-only static report viewer; never opens a decoder or acquisition.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
// The real 5,001-row fixture report is 3.3 MB. Keep a small explicit viewer
// bound without truncating query rows or changing any dataset/shard limit.
export const MAX_REPORT_BYTES=8*1024*1024;
const types={'index.html':'text/html; charset=utf-8','query-results.json':'application/json','query-execution.json':'application/json'};
export function validateReportDirectory(directory){
 const root=fs.realpathSync(directory);
 for(const name of Object.keys(types)){const s=fs.lstatSync(path.join(root,name));if(!s.isFile()||s.size>MAX_REPORT_BYTES)throw Error('bounded regular report file required');}
 return root;
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
const [directory,portText='7020']=process.argv.slice(2);
if(!directory)throw Error('usage: node serve.mjs REPORT_DIRECTORY [PORT]');
const root=validateReportDirectory(directory),port=Number(portText);
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('invalid local port');
const server=http.createServer((req,res)=>{
  const name=req.url==='/'?'index.html':(req.url??'').slice(1);
  if(req.method!=='GET'||!Object.hasOwn(types,name)){res.writeHead(404);res.end();return;}
  try{const bytes=fs.readFileSync(path.join(root,name));res.writeHead(200,{'Content-Type':types[name],'Content-Length':bytes.length,'Cache-Control':'no-store'});res.end(bytes);}
  catch{res.writeHead(500);res.end('Report unavailable');}
});
server.listen(port,'127.0.0.1',()=>console.log(`Read-only report: http://localhost:${port}/`));
server.on('error',error=>{console.error(`Local report listener: ${error.code}`);process.exitCode=1;});
}
