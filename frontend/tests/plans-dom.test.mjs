import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(resolve(import.meta.dirname,'../../backend/static/plans.js'),'utf8');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.attributes={};this.value='';this.checked=false;this.disabled=false;this.className='';this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  appendChild(child){this.children.push(child);if(this.tagName==='select'&&this.children.length===1)this.value=child.value;return child;}
  replaceChildren(){this.children=[];this._text='';}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  setAttribute(name,value){this.attributes[name]=value;}
  querySelectorAll(selector){const out=[];const walk=n=>{for(const child of n.children){if(child.tagName===selector)out.push(child);walk(child);}};walk(this);return out;}
  async fire(name){for(const fn of this.listeners[name]||[])await fn({preventDefault(){}});}
}
function harness(request){
  const events={},root=new Element('div');
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},Date,Intl,Promise,console,confirm:()=>true,addEventListener:(name,fn)=>{events[name]=fn;}});
  context.window=context;vm.runInContext(source,context);
  return {root,context,events,mount:()=>context.AnlinPlans.mount(root,{residentId:3,request,notify(){}}),button:label=>root.querySelectorAll('button').find(b=>b.textContent===label)};
}
const plan={id:1,title:'虚构测量',status:'active',frequency:'daily',weekdays:[],local_time:'08:00',timezone:'Asia/Shanghai',start_date:'2026-10-23',end_date:null};
const task={id:2,plan_id:1,title:'虚构测量',status:'pending',due_at:'2026-10-24T00:00:00Z',scheduled_at:'2026-10-24T00:00:00Z',timezone:'Asia/Shanghai'};
const tick=()=>new Promise(r=>setTimeout(r,0));

test('weekly form requires a weekday and sends an explicit local schedule',async()=>{
  const calls=[];const h=harness(async(path,method,body)=>{calls.push({path,method,body});if(method==='POST')return plan;return path.endsWith('/plans')?[]:{items:[],total:0};});
  await h.mount();const selects=h.root.querySelectorAll('select');selects[1].value='weekly';await selects[1].fire('change');
  const inputs=h.root.querySelectorAll('input');inputs.find(x=>x.type==='text').value='每周安排';
  const form=h.root.querySelectorAll('form')[0];await form.fire('submit');assert.match(h.root.textContent,/至少选择一个星期/);assert.equal(calls.filter(x=>x.method==='POST').length,0);
  inputs.find(x=>x.type==='checkbox').checked=true;await form.fire('submit');const posted=calls.find(x=>x.method==='POST').body;
  assert.deepEqual(Array.from(posted.weekdays),[0]);assert.equal(posted.timezone,'Asia/Shanghai');assert.equal(posted.frequency,'weekly');assert.equal(posted.local_time,'08:00');
});

test('task actions use the occurrence endpoint and preserve the parent plan',async()=>{
  const calls=[];let complete=false;const h=harness(async(path,method,body)=>{calls.push({path,method,body});if(method==='POST'){complete=true;return {...task,status:'completed'};}return path.endsWith('/plans')?[plan]:{items:complete?[]:[task],total:complete?0:1};});
  await h.mount();await h.button('完成').fire('click');await tick();
  assert.ok(calls.some(x=>x.path==='/api/residents/3/plan-occurrences/2/complete'&&x.method==='POST'));
  assert.equal(calls.some(x=>x.method==='POST'&&x.path.includes('/plans/')),false);assert.match(h.root.textContent,/已记录完成/);
});

test('session reset discards an in-flight load and clears all plan content',async()=>{
  let resolvePlans,resolveTasks;const h=harness(path=>new Promise(resolve=>{if(path.endsWith('/plans'))resolvePlans=resolve;else resolveTasks=resolve;}));
  const pending=h.mount();h.events['anlin-session-change']();assert.equal(h.root.children.length,0);
  resolvePlans([plan]);resolveTasks({items:[task],total:1});await pending;
  assert.equal(h.root.children.length,0);assert.equal(h.root.textContent,'');
});
