import { useState } from 'react';
import { tr, useLocale } from '../../i18n';
import type { EffectSpec, EffectParamSpec } from '../../lib/effects';
import { paramControls } from '../../lib/effectControls';
import { ParamControls } from './ParamControls';

type Values = Record<string,unknown>;
const scalar=(schema:EffectParamSpec)=>['number','integer','boolean','string'].includes(schema.type || '');
function geometry(schema:EffectParamSpec,depth=0):boolean {
  if(depth>5)return false;
  if(scalar(schema))return true;
  if(schema.type==='object')return !!schema.properties && Object.values(schema.properties).every(child=>geometry(child,depth+1));
  return schema.type==='array' && typeof schema.maxItems==='number' && schema.maxItems<=8192 && !!schema.items && geometry(schema.items,depth+1);
}
function valid(schema:EffectParamSpec,value:unknown):boolean {
  if(schema.type==='object')return !!value && typeof value==='object' && !Array.isArray(value) &&
    ((schema.required || []) as string[]).every(key=>(value as Values)[key]!==undefined) &&
    Object.entries(value as Values).every(([key,child])=>!!schema.properties?.[key] && valid(schema.properties[key],child));
  if(schema.type==='array')return Array.isArray(value) && value.length>=Number(schema.minItems || 0) && value.length<=Number(schema.maxItems || 256) &&
    value.every(item=>valid(schema.items!,item)) && value.every((item,index)=>!index || !item || typeof item!=='object' ||
      !('time' in item || 'at' in item) || Number(item.time ?? item.at)>Number(value[index-1].time ?? value[index-1].at));
  if(schema.type==='number' || schema.type==='integer')return typeof value==='number' && Number.isFinite(value) &&
    (schema.type!=='integer' || Number.isInteger(value)) && value>=Number(schema.minimum ?? -Infinity) && value<=Number(schema.maximum ?? Infinity);
  if(schema.type==='boolean')return typeof value==='boolean';
  return typeof value==='string' && (!schema.enum || schema.enum.includes(value));
}
function defaults(schema:EffectParamSpec,index=0,count=1,context:Values={}):unknown {
  if(schema.default!==undefined)return structuredClone(schema.default);
  if(schema.type==='array')return Array.from({length:Number(schema.minItems || 0)},(_,i)=>defaults(schema.items!,i,Number(schema.minItems || 0),context));
  if(schema.type==='object')return Object.fromEntries(Object.entries(schema.properties || {}).flatMap(([key,field])=>{
    const required=((schema.required || []) as string[]).includes(key);
    if(!scalar(field) && !required)return [];
    const value=field.default ?? context[key] ?? (key==='time'?index:key==='at'?index/Math.max(1,count-1):key==='value'?1-index/Math.max(1,count-1):
      key==='x'?[.2,.8,.5][index%3]:key==='y'?[.2,.2,.8][index%3]:defaults(field,index,count,context));
    return [[key,structuredClone(value)]];
  }));
  if(schema.type==='boolean')return false;
  if(schema.type==='string')return schema.enum?.[0] ?? '';
  return Math.max(Number(schema.minimum ?? 0),Math.min(Number(schema.maximum ?? Infinity),0));
}
export function validStructuredParameters(spec:EffectSpec,params:Values):boolean {
  return Object.entries(spec.parameters.properties || {}).every(([name,schema])=>params[name]===undefined ||
    scalar(schema) || !geometry(schema) || valid(schema,params[name]));
}

/** Recursively edit bounded geometry, paging large tracking-frame/vertex arrays. */
export function StructuredEffectControls({spec,params,onChange,onCommit}: {
  spec:EffectSpec;params:Values;onChange:(next:Values)=>void;onCommit:(next:Values)=>void;
}) {
  const locale=useLocale();
  const fields=Object.entries(spec.parameters.properties || {}).filter(([,schema])=>!scalar(schema) && geometry(schema));
  const text=(value:unknown,fallback:string):string=>typeof value==='string'?value:value && typeof value==='object'?
    String((value as Values)[locale] || (value as Values).en || (value as Values)['zh-CN'] || fallback):fallback;
  return <>{fields.map(([name,schema])=>{
    const label=text(schema['x-cutvoke-label'],text(schema.description,name)).split(/[；。]/)[0];
    const update=(value:unknown,commit=false)=>{
      const next={...params};if(value===undefined)delete next[name];else next[name]=value;
      onChange(next);if(commit && validStructuredParameters(spec,next))onCommit(next);
    };
    return <details className="inspector__adv structured-effect" key={name} data-parameter={name}>
      <summary>{label}{Array.isArray(params[name])?` · ${(params[name] as unknown[]).length}`:''}</summary>
      <label className="inspector__subrow"><input type="checkbox" aria-label={tr('启用 {0}',label)} checked={params[name]!==undefined} onChange={event=>{
        const count=Math.max(1,Number(schema.minItems || 0));
        update(event.target.checked?(schema.type==='array'?Array.from({length:count},(_,i)=>defaults(schema.items!,i,count,params)):defaults(schema,0,1,params)):undefined,true);
      }}/>{tr('启用')}</label>
      {params[name]!==undefined?<GeometryField spec={spec} schema={schema} value={params[name]} context={params} update={update} commit={()=>{if(validStructuredParameters(spec,params))onCommit(params);}}/>:null}
      {params[name]!==undefined && !valid(schema,params[name])?<p className="cv-hint" role="alert">{tr('请检查参数范围或关键点顺序')}</p>:null}
    </details>;
  })}</>;
}

function GeometryField({spec,schema,value,context,update,commit}: {
  spec:EffectSpec;schema:EffectParamSpec;value:unknown;context:Values;update:(value:unknown,commit?:boolean)=>void;commit:()=>void;
}) {
  const [selected,setSelected]=useState(0);
  const locale=useLocale();
  const fieldLabel=(field:EffectParamSpec,fallback:string)=>{
    const label=field['x-cutvoke-label'];return typeof label==='string'?label:label && typeof label==='object'?
      String((label as Values)[locale] || (label as Values).en || (label as Values)['zh-CN'] || fallback):fallback;
  };
  if(schema.type==='object') {
    const row=value as Values;
    const set=(name:string,child:unknown,apply=false)=>{const next={...row};if(child===undefined)delete next[name];else next[name]=child;update(next,apply);};
    return <><ParamControls controls={paramControls({...spec,parameters:{type:'object',properties:schema.properties}},row)}
      onChange={(key,child)=>{if(typeof child!=='number' || Number.isFinite(child))set(key,child);}} onCommit={change=>{if(change)set(change.name,change.value,true);else commit();}}/>
      {Object.entries(schema.properties || {}).filter(([,field])=>!scalar(field)).map(([name,field])=><details key={name} data-parameter={name} className="inspector__adv">
        <summary>{fieldLabel(field,name)}{Array.isArray(row[name])?` · ${(row[name] as unknown[]).length}`:''}</summary>
        {row[name]===undefined?<button type="button" className="cv-chip" onClick={()=>set(name,context[name] || defaults(field,0,1,context),true)}>{tr('添加')}</button>:
          <GeometryField spec={spec} schema={field} value={row[name]} context={context} update={(next,apply)=>set(name,next,apply)} commit={commit}/>}</details>)}
    </>;
  }
  if(schema.type!=='array')return null;
  const rows=value as unknown[],index=Math.min(selected,Math.max(0,rows.length-1)),paged=rows.length>8;
  const minimum=Number(schema.minItems || 0),maximum=Number(schema.maxItems || 256);
  return <>{paged?<select className="cv-input" aria-label={tr('编辑项目')} value={index} onChange={event=>setSelected(Number(event.target.value))}>
    {rows.map((row,i)=><option value={i} key={i}>{i+1}{row && typeof row==='object' && 'at' in row?` · ${(row as Values).at} s`:''}</option>)}</select>:null}
    {rows.map((row,i)=>!paged || i===index?<div key={i} className="structured-effect__row">
      <span className="cv-hint">{i+1}</span><GeometryField spec={spec} schema={schema.items!} value={row} context={context} update={(next,apply)=>update(rows.map((old,j)=>j===i?next:old),apply)} commit={commit}/>
      <button type="button" className="cv-chip" aria-label={tr('删除第 {0} 项',i+1)} disabled={rows.length<=minimum} onClick={()=>update(rows.filter((_,j)=>i!==j),true)}>{tr('删除')}</button>
    </div>:null)}
    <button type="button" className="cv-chip" disabled={rows.length>=maximum} onClick={()=>{
      let next=defaults(schema.items!,rows.length,Math.max(2,rows.length+1),context) as Values;
      const previous=rows.at(-1) as Values | undefined;
      if(previous && schema.items?.type==='object')next={...structuredClone(previous)};
      if(schema.items?.properties?.id?.type==='string')next.id=`item_${crypto.randomUUID().slice(0,8)}`;
      if('time' in next)next.time=Number(previous?.time ?? -1)+1;
      if('at' in next) {
        const max=Number(schema.items?.properties?.at.maximum ?? 86400);
        if(max<=1 && rows.length) {next.at=(Number(previous?.at ?? 1)+Number((rows.at(-2) as Values)?.at ?? 0))/2;update([...rows.slice(0,-1),next,...rows.slice(-1)],true);return;}
        next.at=Number(previous?.at ?? -1)+1;
      }
      update([...rows,next],true);setSelected(rows.length);
    }}>{tr('添加')}</button>
  </>;
}
