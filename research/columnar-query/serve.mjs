#!/usr/bin/env node
// Optional read-only static report viewer; never opens a decoder or acquisition.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
const [directory,portText='7020']=process.argv.slice(2);
if(!directory)throw Error('usage: node serve.mjs REPORT_DIRECTORY [PORT]');
const root=fs.realpathSync(directory),port=Number(portText);
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('invalid local port');
const types={'index.html':'text/html; charset=utf-8','query-results.json':'application/json','query-execution.json':'application/json'};
for(const name of Object.keys(types)){const s=fs.lstatSync(path.join(root,name));if(!s.isFile()||s.size>2*1024*1024)throw Error('bounded regular report file required');}
const server=http.createServer((req,res)=>{
  const name=req.url==='/'?'index.html':(req.url??'').slice(1);
  if(req.method!=='GET'||!Object.hasOwn(types,name)){res.writeHead(404);res.end();return;}
  try{const bytes=fs.readFileSync(path.join(root,name));res.writeHead(200,{'Content-Type':types[name],'Content-Length':bytes.length,'Cache-Control':'no-store'});res.end(bytes);}
  catch{res.writeHead(500);res.end('Report unavailable');}
});
server.listen(port,'127.0.0.1',()=>console.log(`Read-only report: http://localhost:${port}/`));
server.on('error',error=>{console.error(`Local report listener: ${error.code}`);process.exitCode=1;});
