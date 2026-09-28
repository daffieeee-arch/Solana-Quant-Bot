import { useEffect, useState } from 'react';
import { MAX_COHORT_BYTES, type DevelopmentCohort } from '../../../src/mint-inspector/development-cohort';
import { INSTRUCTION_CATEGORIES, parseInstructionCoverage, type InstructionCoverage, type InventoryInstruction, type InventoryPackage } from '../../../src/mint-inspector/instruction-coverage';
import { registeredResponse, SnapshotMismatch } from './read-response';
import { object } from '../../../src/mint-inspector/contract';
const names: Record<string,string> = { ADMITTED_TRADE:'Handelsinstructie met Silver', SUPPORTING_EVENT_CPI:'Ondersteunende event-CPI', OTHER_INSTRUCTION:'Ander aangetoond instructietype', REJECTED_TRADE:'Afgewezen handelsinstructie', UNEXPLAINED:'Onvoldoende verklaard' };
const shown = (v: unknown) => v === null || v === undefined ? 'UNAVAILABLE' : String(v);
function Evidence({title,value}:{title:string;value:unknown}) { return <details className="evidence"><summary>{title}</summary><pre className="raw-record">{JSON.stringify(value,null,2)}</pre></details>; }
export function InstructionCoverageView({report,ordinal}:{report:InstructionCoverage;ordinal:number}) {
  const [reason,setReason]=useState('ALL'), [category,setCategory]=useState('ALL'), [page,setPage]=useState(0), [unknownPage,setUnknownPage]=useState(0), [selected,setSelected]=useState<{p:InventoryPackage;ix:InventoryInstruction}|null>(null);
  const w=report.windows[ordinal], inv=w.instruction_inventory, summary=w.summary;
  const rows=inv.packages.flatMap(p=>p.instructions.map(ix=>({p,ix}))).filter(({ix})=>(reason==='ALL'||ix.reasons.includes(reason))&&(category==='ALL'||ix.category===category));
  function reset(){setPage(0);setSelected(null);}
  return <section className="instruction-coverage" aria-label="Instructiedekking">
    <h2>Unieke Pump-instructies · venster {w.ordinal}</h2>
    <p>Een instructiepositie is één waarneming. Een probe is een afzonderlijke controle; een event-CPI is geen tweede handelsinstructie. Aanwezigheid bewijst geen uitvoering, ook niet bij een geslaagde transactie.</p>
    <div className="table-scroll"><table><caption>Afzonderlijke noemers · volledig geselecteerd venster</caption><thead><tr><th>Alle packages</th><th>Packages met Pump-instructies</th><th>Unieke instructies</th><th>Probe-uitkomsten</th><th>Feiten</th><th>Packages met dekkingsonzekerheid</th><th>Onzekerheidsmeldingen</th></tr></thead><tbody><tr>
      <td>{shown(object(w.counts).packages)}</td><td>{shown(summary.packages_with_identified_pump_instructions)}</td><td>{shown(summary.unique_pump_instructions)}</td><td>{shown(summary.probe_references)}</td><td>{shown(summary.silver_facts)}</td><td>{shown(summary.uncertain_packages)}</td><td>{shown(summary.uncertainty_items)}</td>
    </tr></tbody></table></div>
    <div className="table-scroll"><table><caption>Exclusieve categorieën van geïnventariseerde instructies</caption><thead><tr><th>Categorie</th><th>Instructies</th></tr></thead><tbody>{INSTRUCTION_CATEGORIES.map(c=><tr key={c}><td>{names[c]}</td><td>{shown(object(summary.category_counts)[c])}</td></tr>)}</tbody></table></div>
    <p>{shown(summary.failed_parent_instructions)} instructies hebben een mislukte oudertransactie. Geen succesvolle state transition of Silver-feit volgt daaruit. {shown(summary.duplicate_instruction_references)} dubbele instructieverwijzingen zijn samengenomen; verschillende posities blijven afzonderlijk.</p>
    <div className="boundary"><strong>Negatieve conclusie: {shown(summary.negative_conclusion)}</strong><span>{shown(summary.potentially_hiding_packages)} packages hebben mogelijk verhullende instructies of ontbrekende context buiten bewezen failures. Geen paar in de toegelaten feiten betekent geen bewezen afwezigheid. De bestaande positieve paarbewijzen blijven behouden.</span></div>
    <div className="cohort-selectors">
      <label>Instructiecategorie<select value={category} onChange={e=>{setCategory(e.target.value);reset();}}><option value="ALL">Alle categorieën</option>{INSTRUCTION_CATEGORIES.map(c=><option key={c} value={c}>{names[c]}</option>)}</select></label>
      <label>Instructiereden<select value={reason} onChange={e=>{setReason(e.target.value);reset();}}><option value="ALL">Alle redenen</option>{Object.keys(object(summary.reason_instruction_counts)).map(r=><option key={r} value={r}>{r}</option>)}</select></label>
    </div>
    <p role="status">{rows.length} instructies binnen de filters; pagina {page+1} van {Math.max(1,Math.ceil(rows.length/40))}. De noemers hierboven gelden voor het volledige venster.</p>
    <div className="table-scroll"><table><caption>Instructies in chainvolgorde · maximaal 40 per pagina</caption><thead><tr><th>Chainpositie</th><th>Ouderstatus</th><th>Indeling / reden</th><th>Bron</th></tr></thead><tbody>{rows.slice(page*40,(page+1)*40).map(({p,ix})=><tr key={ix.instruction_id} className={ix.instruction_id===selected?.ix.instruction_id?'current-trade':''}>
      <td>{shown(p.effective_at.slot)} / tx {shown(p.effective_at.transaction_index_in_slot)}<small>outer {shown(ix.location.outer_index)} · {ix.location.kind==='DECLARED_TOP_LEVEL'?'top-level':`CPI ${shown(ix.location.inner_order)}`}</small></td><td>{shown(p.transaction_status)}</td><td>{names[ix.category]}<small>{ix.reasons.join(' · ')}</small></td>
      <td><button aria-controls="instruction-source" onClick={()=>setSelected({p,ix})}>Inspecteer instructie {shown(ix.instruction_sha256).slice(0,8)} · {shown(ix.location.outer_index)}/{shown(ix.location.inner_order)}</button></td>
    </tr>)}</tbody></table></div>
    <div className="tags"><button disabled={page===0} onClick={()=>setPage(p=>p-1)}>Vorige instructiepagina</button><button disabled={(page+1)*40>=rows.length} onClick={()=>setPage(p=>p+1)}>Volgende instructiepagina</button></div>
    <article id="instruction-source" aria-label="Geselecteerde instructiebron">{selected?<><h3>Instructie en volledig atomair bewijsverband</h3><p className="cohort-mint">{selected.ix.instruction_id}</p>
      <Evidence title="Oorspronkelijke instructie, probes en diagnoses" value={selected.ix}/>
      <Evidence title="Alle Pump-instructies en feitkoppelingen van deze ouder" value={selected.p}/>
      <Evidence title="Collectie, bronreceipt, Bronze-parent en oorspronkelijke producent" value={{collection_sha256:w.collection_sha256,source:selected.p.source,bronze_record_sha256:selected.p.bronze_record_sha256,part:w.parts[Number(selected.p.part_id)],sample_identity:w.sample_identity}}/>
    </>:<p>Selecteer een instructie. De broninspectie bewaart alle bijbehorende Pump-instructies en feiten van dezelfde ouder; dit is geen volledige herdecode of volledige Bronze-transactieweergave.</p>}</article>
    <h3>Ontbrekende context blijft afzonderlijk zichtbaar</h3>
    <p>Programma-, locatie- en CPI-onzekerheid telt niet als een verzonnen unieke instructie. Onbekende daadwerkelijke CPI-rechten en historische activering blijven buiten deze tellingen onbewezen.</p>
    <Evidence title="Onzekerheidsredenen en afzonderlijke probe-uitkomsten" value={{unknown:summary.uncertainty_reason_counts,probes:summary.probe_outcomes,probe_references:summary.probe_references,distinct_instruction_probe_values:summary.distinct_instruction_probe_values,diagnosis_references:summary.diagnosis_references}}/>
    {inv.uncertainty.length>0?<><p>Onzekere packages {unknownPage*20+1}–{Math.min((unknownPage+1)*20,inv.uncertainty.length)} van {inv.uncertainty.length}.</p>{inv.uncertainty.slice(unknownPage*20,(unknownPage+1)*20).map(u=><Evidence key={String(u.package_id)} title={`Onzeker package ${shown(object(u.effective_at).slot)} / ${shown(object(u.effective_at).transaction_index_in_slot)}`} value={{...u,part:w.parts[Number(u.part_id)]}}/>)}<div className="tags"><button disabled={unknownPage===0} onClick={()=>setUnknownPage(p=>p-1)}>Vorige onzekerheden</button><button disabled={(unknownPage+1)*20>=inv.uncertainty.length} onClick={()=>setUnknownPage(p=>p+1)}>Volgende onzekerheden</button></div></>:<p>Geen ontbrekende programma-/locatie-/CPI-context geregistreerd in deze inventaris. Onvoldoende verklaarde instructies hierboven blijven afzonderlijke onzekerheid.</p>}
    <Evidence title="Versiegebonden dekkingsregels en producentbinding" value={{rules:report.rules,producer:report.producer,cohort_report_sha256:report.cohort_report_sha256,native_admission_sha256:report.native_admission_sha256}}/>
    <a href="/evidence/development-instructions.json" download>Download reproduceerbare instructiedekking-JSON</a>
    <p>Research Ready=false · Alleen DEVELOPMENT. Geen B8-labels, definitieve frequentie of sufficiëntieoordeel. Onbekende quote-eenheden, accountrollen, lifecyclefasen en uitvoerbaarheid blijven onbekend.</p>
  </section>;
}
export function InstructionCoveragePanel({cohort,ordinal}:{cohort:DevelopmentCohort;ordinal:number}) {
  const [result,setResult]=useState<InstructionCoverage|null>(null),[failure,setFailure]=useState<string|null>(null);
  useEffect(()=>{
    const controller=new AbortController();let active=true;
    const cohortSha=document.querySelector<HTMLMetaElement>('meta[name="inspector-snapshot-development-cohort"]')?.content;
    const registered=document.querySelector<HTMLMetaElement>('meta[name="inspector-snapshot-development-instructions"]')?.content;
    if (!cohortSha || !registered || registered==='UNAVAILABLE') {setFailure('UNAVAILABLE');return ()=>controller.abort();}
    const timer=setTimeout(()=>controller.abort(),10000);
    void fetch('/api/development-instructions',{signal:controller.signal,cache:'no-store',credentials:'omit'})
      .then(registeredResponse('development-instructions',MAX_COHORT_BYTES)).then(v=>parseInstructionCoverage(v,cohort,cohortSha))
      .then(v=>{if(active)setResult(v);}).catch(e=>{if(active)setFailure(e instanceof SnapshotMismatch?'STALE':'UNAVAILABLE');}).finally(()=>clearTimeout(timer));
    return ()=>{active=false;controller.abort();clearTimeout(timer);};
  },[cohort]);
  return result?<InstructionCoverageView key={ordinal} report={result} ordinal={ordinal}/>:<section aria-label="Instructiedekking"><h2>Instructiedekking</h2><p role="status">{failure??'CONTROLEREN'} · {failure?'Geen geldige geregistreerde inventaris; geen aangevulde tellingen.':'De gebonden inventaris wordt geladen.'}</p></section>;
}
