import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(resolve(import.meta.dirname,'../../backend/static/reports.js'),'utf8');

class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.parent=null;this.listeners={};this.dataset={};this.attributes={};this.value='';this.checked=false;this.disabled=false;this.files=[];this.offsetParent={};this.isConnected=true;this._text='';this.className='';this.style={}}
  set textContent(v){this._text=String(v);this.children=[]}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join('')}
  get lastChild(){return this.children.at(-1)}
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n)}}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes)}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.isConnected=false}
  removeAttribute(name){delete this.attributes[name]}
  setAttribute(name,value){this.attributes[name]=value}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)}
  click(){for(const fn of this.listeners.click||[])fn({target:this})}
  focus(){globalThis._focused=this}
  querySelectorAll(selector){const found=[];const match=n=>selector.startsWith('#')?n.id===selector.slice(1):selector.startsWith('.')?n.className.split(' ').includes(selector.slice(1)):selector==='[data-ar-action]'?n.dataset.arAction!==undefined:selector==='button'?n.tagName==='button':false;const walk=n=>{for(const c of n.children){if(match(c))found.push(c);walk(c)}};walk(this);return found}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null}
}

function harness(role='resident'){
  const events={}, requests=[], asked=[];let auth={token:'private-token',user:{id:11,role}}, status='uploaded';
  const candidate={title:'检查报告',patient_name:'居民甲',specimen_collected_at:null,reported_at:null,warnings:['请核对原件'],pages:[{page:1,text:'原文'}],items:[{id:'i1',name:'血压',value:'132/84',unit:'mmHg',reference_range:'',flag:'',page:1,quote:'血压 132/84 mmHg'},{id:'i2',name:'其他项目',value:'10',unit:'U/L',reference_range:'',flag:'',page:1,quote:'其他项目 10 U/L'}]};
  const detail=()=>({id:4,resident_id:2,filename:'报告.pdf',status,page_count:2,revision:1,created_at:'2026-10-23T02:00:00',candidate,reviewed:status==='confirmed'?candidate:null,imported_vital_ids:[]});
  const route=async (url,options)=>{
    requests.push({url,options});
    if(url.startsWith('/api/residents/2/reports?'))return {items:[detail()],total:1};
    if(url==='/api/reports/4')return detail();
    if(url==='/api/reports/4/pages/1'||url==='/api/reports/4/pages/2')return new Blob(['png'],{type:'image/png'});
    if(url==='/api/reports/4/extract'){status='draft';return detail()}
    if(url==='/api/reports/4/confirm'){status='confirmed';return {...detail(),reviewed:JSON.parse(options.body),imported_vital_ids:[22]}}
    if(url.startsWith('/api/residents/2/reports?filename='))return detail();
    throw new Error('unexpected '+url);
  };
  const root=new Element('body');const document={body:root,activeElement:new Element('button'),createElement:tag=>new Element(tag),addEventListener:(name,fn)=>{(events[name]??=[]).push(fn)},removeEventListener:(name,fn)=>{events[name]=(events[name]||[]).filter(x=>x!==fn)}};
  const context=vm.createContext({document,console,Blob,Date,Promise,setTimeout,clearTimeout,AbortController,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail}},URL:{createObjectURL:()=> 'blob:page',revokeObjectURL:()=>{}},fetch:async(url,options={})=>{const result=await route(url,options);return {ok:true,status:200,json:async()=>result,blob:async()=>result}},addEventListener:(name,fn)=>{(events[name]??=[]).push(fn)},dispatchEvent:e=>{for(const fn of events[e.type]||[])fn(e)},anlinAssistantAuth:()=>auth});
  context.window=context;context.anlinAssistant={openReport:meta=>asked.push(meta)};vm.runInContext(source,context,{filename:'reports.js'});
  const findButton=label=>{const walk=n=>{if(n.tagName==='button'&&n.textContent.includes(label))return n;for(const c of n.children){const x=walk(c);if(x)return x}};return walk(root)};
  return {context,root,requests,events,asked,findButton,setAuth:a=>{auth=a},setStatus:s=>{status=s},getStatus:()=>status};
}

test('family and admin cannot open private report modal',async()=>{
  for(const role of ['family','admin']){const h=harness(role);await h.context.anlinReports.open(2,'居民甲');assert.equal(h.root.children.length,0);assert.equal(h.requests.length,0)}
});

test('draft requires per-report cloud consent, then confirms with explicit measurement time and immutable evidence',async()=>{
  const h=harness();await h.context.anlinReports.open(2,'居民甲');assert.match(h.root.textContent,/选择报告文件/);h.findButton('报告.pdf').click();await new Promise(r=>setTimeout(r,0));assert.ok(h.root.textContent.includes(new Date('2026-10-23T02:00:00Z').toLocaleString('zh-CN')));
  assert.match(h.root.textContent,/放大原件/);h.findButton('放大原件').click();assert.match(h.root.querySelector('.ar-zoomed').className,/ar-zoomed/);assert.equal(h.findButton('适应宽度').attributes['aria-pressed'],'true');h.findButton('适应宽度').click();assert.equal(h.root.querySelector('.ar-zoomed'),null);
  h.findButton('开始提取').click();await new Promise(r=>setTimeout(r,0));assert.equal(h.requests.some(x=>x.url.endsWith('/extract')),false);
  const consent=h.root.querySelector('#ar-cloud-consent');consent.checked=true;h.findButton('开始提取').click();await new Promise(r=>setTimeout(r,0));assert.equal(h.getStatus(),'draft');
  assert.equal(h.findButton('询问这份报告'),undefined);
  assert.ok(h.findButton('放大原件'));h.findButton('放大原件').click();assert.ok(h.root.querySelector('.ar-zoomed'));
  assert.match(h.root.textContent,/请核对原件/);assert.match(h.root.textContent,/血压 132\/84 mmHg/);
  const identity=h.root.querySelector('#ar-identity');identity.checked=true;
  const importArea=h.root.querySelectorAll('.ar-import')[0];const selected=importArea.children[0].children[0];selected.checked=true;selected.listeners.change[0]();const time=importArea.querySelectorAll('.ar-field')[0].children[1];time.value='2026-10-22T08:30';time.listeners.input[0]();
  h.findButton('确认报告').click();await new Promise(r=>setTimeout(r,0));const call=h.requests.find(x=>x.url.endsWith('/confirm'));assert.ok(call);const payload=JSON.parse(call.options.body);assert.equal(payload.identity_confirmed,true);assert.equal(payload.items[0].id,'i1');assert.equal(payload.items[0].page,1);assert.equal(payload.items[0].quote,'血压 132/84 mmHg');assert.equal(payload.imports.length,1);assert.equal(payload.imports[0].systolic,132);assert.equal(payload.imports[0].diastolic,84);assert.ok(payload.imports[0].measured_at.endsWith('Z'));assert.equal(h.getStatus(),'confirmed');
  h.findButton('询问这份报告').click();assert.equal(h.asked.length,1);assert.equal(h.asked[0].id,4);assert.equal(h.asked[0].revision,1);assert.equal(h.root.children.length,0);
  h.context.dispatchEvent(new h.context.CustomEvent('anlin-session-change'));assert.equal(h.root.children.length,0);
});

test('a reviewed report can be saved without creating measurements',async()=>{
  const h=harness();await h.context.anlinReports.open(2,'居民甲');h.findButton('报告.pdf').click();await new Promise(r=>setTimeout(r,0));
  h.root.querySelector('#ar-cloud-consent').checked=true;h.findButton('开始提取').click();await new Promise(r=>setTimeout(r,0));
  h.root.querySelector('#ar-identity').checked=true;h.findButton('确认报告').click();await new Promise(r=>setTimeout(r,0));
  const payload=JSON.parse(h.requests.find(x=>x.url.endsWith('/confirm')).options.body);
  assert.equal(payload.imports.length,0);assert.equal(payload.items.length,2);assert.equal(payload.items[1].name,'其他项目');
});

test('citation jump opens only a matching confirmed report revision and actual page',async()=>{
  const h=harness();h.setStatus('confirmed');await h.context.anlinReports.open(2,'居民甲',{reportId:4,page:2,expectedRevision:1});
  assert.ok(h.findButton('询问这份报告'));assert.ok(h.requests.some(x=>x.url==='/api/reports/4/pages/2'));
  h.context.anlinReports.close();
  await h.context.anlinReports.open(2,'居民甲',{reportId:4,page:1,expectedRevision:2});
  assert.equal(h.findButton('询问这份报告'),undefined);assert.match(h.root.textContent,/版本已变化/);
});
