import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(resolve(import.meta.dirname,'../../backend/static/assistant.js'),'utf8');
class Node {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.parent=null;this.attributes={};this.listeners={};this.className='';this._text='';this.hidden=false;this.checked=false;this.value='';this.disabled=false;this.scrollTop=0;this.scrollHeight=0}
  set textContent(value){this._text=String(value);this.children=[]}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n)}}
  appendChild(n){this.append(n)}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes)}
  setAttribute(k,v){this.attributes[k]=v}
  addEventListener(k,fn){(this.listeners[k]??=[]).push(fn)}
  focus(){}
  querySelectorAll(selector){const found=[];const walk=n=>{for(const c of n.children){if(selector.startsWith('.')&&c.className.split(' ').includes(selector.slice(1)))found.push(c);walk(c)}};walk(this);return found}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null}
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
function harness(){
  const root=new Node('body'),listeners={},requests=[],jumps=[];
  let auth={token:'token-1',user:{id:7,role:'resident'}},delayed=null,reportErrorStatus=null;
  const doc={body:root,createElement:tag=>new Node(tag)};
  const context=vm.createContext({document:doc,console,Promise,setTimeout,clearTimeout,AbortController,
    anlinAssistantAuth:()=>auth,
    anlinReports:{open:(...args)=>jumps.push(args)},
    addEventListener:(name,fn)=>{(listeners[name]??=[]).push(fn)},
    fetch:async(url,options={})=>{
      requests.push({url,options});
      if(url==='/api/assistant/status')return {ok:true,json:async()=>({configured:true,provider:'DeepSeek',model:'test'})};
      if(url==='/api/assistant/report-chat'){
        if(delayed)return await delayed.promise;
        if(reportErrorStatus)return {ok:false,status:reportErrorStatus,json:async()=>({detail:'报告不可用'})};
        return {ok:true,status:200,json:async()=>({answer:'请看报告原文。',evidence_status:'supported',actions:[],report:{id:4,resident_id:2,resident_name:'居民甲',filename:'报告.pdf',title:'检查报告',revision:2},sources:[
          {id:'p1-i1',report_id:4,revision:2,filename:'报告.pdf',title:'检查报告',page:1,quote:'原文 <img src=x>',confirmed_fields:{name:'血压',value:'132/84',unit:'mmHg',reference_range:'',flag:''},edited_fields:['value'],source_kind:'model_transcription'},
          {id:'meta:reported_at',report_id:4,revision:2,filename:'报告.pdf',title:'检查报告',page:1,quote:'报告日期 10 月 23 日',confirmed_fields:{field:'reported_at',label:'报告时间',value:'10 月 23 日'},edited_fields:['reported_at'],source_kind:'pdf_text'},
          {id:'p6-i1',report_id:4,revision:2,filename:'伪造.pdf',page:6,quote:'伪造页',confirmed_fields:{},edited_fields:[],source_kind:'pdf_text'},
          {id:'p1-i9',report_id:999,revision:2,filename:'外部.pdf',page:1,quote:'外部来源',confirmed_fields:{},edited_fields:[],source_kind:'pdf_text'},
          {id:'<script>',report_id:4,revision:2,filename:'伪造标识.pdf',page:1,quote:'伪造标识',confirmed_fields:{},edited_fields:[],source_kind:'pdf_text'},
        ]})};
      }
      if(url==='/api/assistant/chat')return {ok:true,status:200,json:async()=>({answer:'普通问答',sources:[],actions:[]})};
      throw new Error('unexpected '+url);
    }});
  context.window=context;vm.runInContext(source,context,{filename:'assistant.js'});
  const findButton=label=>{const walk=n=>{if(n.tagName==='button'&&n.textContent.includes(label))return n;for(const c of n.children){const found=walk(c);if(found)return found}};return walk(root)};
  const submit=async text=>{const input=root.querySelector('.aa-form').children[0];input.value=text;await root.querySelector('.aa-form').onsubmit({preventDefault(){}})};
  const consent=()=>root.querySelector('.aa-report-permission').children[0];
  return {root,context,requests,jumps,findButton,submit,consent,setAuth:a=>{auth=a},setReportError:status=>{reportErrorStatus=status},dispatch:name=>{for(const fn of listeners[name]||[])fn()},delay:()=>{let resolve;const promise=new Promise(r=>{resolve=r});delayed={promise,resolve}},resolveDelayed:response=>{delayed.resolve(response);delayed=null}};
}
const report={id:4,resident_id:2,resident_name:'居民甲',filename:'报告.pdf',title:'检查报告',revision:2};

test('report mode requires its own opt-in and renders safe citation evidence with page jump',async()=>{
  const h=harness();await tick();assert.equal(h.context.anlinAssistant.openReport(report),true);
  assert.match(h.root.textContent,/报告.pdf · 版本 2/);
  await h.submit('这份报告写了什么？');assert.equal(h.requests.filter(x=>x.url==='/api/assistant/report-chat').length,0);
  h.consent().checked=true;h.consent().onchange();await h.submit('这份报告写了什么？');
  const call=h.requests.find(x=>x.url==='/api/assistant/report-chat');assert.ok(call);
  assert.deepEqual(JSON.parse(call.options.body),{report_id:4,message:'这份报告写了什么？',history:[],allow_report_data:true});
  assert.match(h.root.textContent,/原文转录（请对照原件）/);
  assert.match(h.root.textContent,/原文 <img src=x>/);
  assert.match(h.root.textContent,/已确认字段：血压 132\/84 mmHg/);
  assert.match(h.root.textContent,/人工核对后修订：结果/);
  assert.match(h.root.textContent,/已确认字段：报告时间：10 月 23 日/);
  assert.match(h.root.textContent,/人工核对后修订：报告时间/);
  assert.equal(h.root.querySelectorAll('.aa-report-source').length,2);
  assert.doesNotMatch(h.root.textContent,/伪造页|外部来源|伪造标识/);
  assert.equal(h.root.querySelectorAll('.aa-source-jump').length,2);
  h.findButton('查看原件页').onclick();assert.deepEqual(JSON.parse(JSON.stringify(h.jumps[0])),[2,'居民甲',{reportId:4,page:1,expectedRevision:2}]);
  h.consent().checked=false;h.consent().onchange();assert.equal(h.root.querySelectorAll('.aa-report-source').length,0);
  await h.submit('再次提问');assert.equal(h.requests.filter(x=>x.url==='/api/assistant/report-chat').length,1);
});

test('report authorization and version errors clear report context and citations',async()=>{
  for(const status of [403,404,409]){
    const h=harness();await tick();h.context.anlinAssistant.openReport(report);h.consent().checked=true;h.consent().onchange();
    await h.submit('首次提问');assert.equal(h.root.querySelectorAll('.aa-report-source').length,2);
    h.setReportError(status);await h.submit('再次提问');
    assert.equal(h.root.querySelector('.aa-report-context').hidden,true);
    assert.equal(h.root.querySelectorAll('.aa-report-source').length,0);
    assert.doesNotMatch(h.root.textContent,/报告.pdf · 版本 2/);
    assert.match(h.root.textContent,/报告状态或授权已变化/);
  }
});

test('switching report or session discards an older in-flight answer and consent',async()=>{
  const h=harness();await tick();h.context.anlinAssistant.openReport(report);h.consent().checked=true;h.consent().onchange();
  h.delay();const pending=h.submit('旧报告问题');await tick();
  h.context.anlinAssistant.openReport({...report,id:5,filename:'新报告.pdf',revision:1});
  assert.equal(h.consent().checked,false);assert.doesNotMatch(h.root.textContent,/旧报告问题/);
  h.resolveDelayed({ok:true,status:200,json:async()=>({answer:'旧报告敏感回答',report,sources:[],actions:[]})});await pending;
  assert.doesNotMatch(h.root.textContent,/旧报告敏感回答/);
  h.setAuth({token:'',user:null});h.dispatch('anlin-session-change');await tick();
  assert.equal(h.root.children[0].hidden,true);assert.doesNotMatch(h.root.textContent,/新报告.pdf/);
});
