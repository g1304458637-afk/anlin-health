(function(){
'use strict';
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem('token')||'', me=null, resident=null, communities=[], famShared=[], vtType='blood_pressure', vitalOffset=0;
let sessionVersion=0, trendDays=30, trendRequestSeq=0, vitalRequestSeq=0, pendingVital=null;
window.anlinAssistantAuth=()=>({token,user:me});
function sessionChanged(){sessionVersion++;trendRequestSeq++;vitalRequestSeq++;pendingVital=null;window.AnlinPlans?.reset();window.dispatchEvent(new CustomEvent('anlin-session-change'))}

const toast=window.AnlinCore.makeToast(()=>$('toast'),2600);
const api=window.AnlinCore.createApi({getToken:()=>token,getVersion:()=>sessionVersion,onAuthExpired:logout,
  describeError:d=>typeof d==='string'?d:Array.isArray(d)?d.map(x=>x.msg).join('；'):'操作失败'});
const {esc,fmtTime,fmtD,localDay,localDate,localInputDate}=window.AnlinCore;

function icon(name){const paths={home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z"/><path d="M9 21v-8h6v8"/>',heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/><path d="M3 12h4l2-4 4 8 2-4h6"/>',calendar:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M16 3v4M8 3v4M3 11h18m-13 5 3 2 5-4"/>',file:'<path d="M14 2H5a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9Z"/><path d="M14 2v7h7M7 13h10M7 17h7"/>',user:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',users:'<circle cx="9" cy="8" r="4"/><path d="M2 21v-2a7 7 0 0 1 14 0v2M17 4a4 4 0 0 1 0 8m3 9v-2a7 7 0 0 0-3-6"/>'};return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.heart}</svg>`}
async function loadHomeLatest(){
  const version=sessionVersion;if(!resident)return;
  try{const lists=await Promise.all(Object.keys(VT).map(metric=>api(`/api/residents/${resident.id}/vitals?limit=1&metric=${metric}`)));if(version!==sessionVersion)return;
    $('homeLatest').innerHTML=Object.keys(VT).map((metric,i)=>{const p=lists[i][0],value=p?(metric==='blood_pressure'?`${p.systolic} / ${p.diastolic}`:metric==='blood_glucose'?p.glucose_mmol_l:p.weight_kg):'—';return `<div class="kv"><span>${VT[metric].label}</span><b>${esc(value)} <small style="font-size:11px;font-weight:400;color:var(--muted)">${VT[metric].unit}</small></b></div>`}).join('');
  }catch(e){if(version===sessionVersion)$('homeLatest').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
}

/* ---------- 登录 ---------- */
async function doLogin(){
  const u=$('luser').value.trim(),p=$('lpwd').value;
  if(!u||!p)return toast('请输入账号和密码');
  const btn=$('loginBtn');if(btn.disabled)return;btn.disabled=true;
  try{await submitLogin('/api/auth/login',{username:u,password:p})}catch(e){toast(e.message)}finally{btn.disabled=false}
}
async function submitLogin(path,payload){
  const res=await fetch(path,{method:'POST',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(res.status===401&&res.headers.get('X-MFA-Required')==='totp'){
    const code=await window.AnlinCore.promptDialog('此账号已开启两步验证，请输入认证器中的6位验证码：');
    if(!code)return;
    return submitLogin('/api/auth/login/totp',{...payload,totp_code:code.trim()});
  }
  const body=await res.json().catch(()=>({}));
  if(!res.ok)throw new Error(typeof body.detail==='string'?body.detail:'登录失败');
  token=body.access_token;sessionStorage.setItem('token',token);me=body.user;sessionChanged();
  await enterApp();
}
async function registerFamily(){
  const b={username:$('ruser').value.trim(),display_name:$('rname').value.trim(),password:$('rpwd').value};
  if(!b.username||!b.display_name||!b.password)return toast('请填写完整');
  const btn=$('regBtn');if(btn.disabled)return;btn.disabled=true;
  try{await submitLogin('/api/auth/register-family',b);toast('注册成功，欢迎使用')}catch(e){toast(e.message)}finally{btn.disabled=false}
}
function showLogin(){
  $('loginView').classList.remove('hidden');$('appView').classList.add('hidden');
  me=null;resident=null;communities=[];famShared=[];vtType='blood_pressure';vitalOffset=0;trendDays=30;pendingVital=null;
  for(const id of ['whoami','tabbar','obComm','homeArchive','homeReminders','homeLatest','healthPlans','vitalSaveState','vtTabs','vtFields','vtTrend','vtList','vtPaging','remList','meProfile','meShare','famList','famDetail','claimStatus','consentState','consentHistory'])$(id).replaceChildren();
  for(const id of ['luser','lpwd','ruser','rname','rpwd','pwCur','pwNew','gCode','claimId','claimName','claimPhone','claimNote','obName','obYear','remTitle','remDate','vtMeasuredAt']){const el=$(id);if(el)el.value=''}
  $('obConsent').checked=false;$('regBox').classList.add('hidden');$('claimCard').classList.add('hidden');$('consentCard').classList.add('hidden');
  $('famDetail').classList.add('hidden');$('famList').classList.remove('hidden');
  $('toast').textContent='';$('toast').classList.add('hidden');
  document.querySelectorAll('main .view').forEach(x=>x.classList.remove('on'));
}
function logout(){sessionStorage.removeItem('token');token='';showLogin();sessionChanged()}

/* ---------- 启动 ---------- */
async function enterApp(){
  if(!me||!['resident','family'].includes(me.role)){logout();$('demoHint').innerHTML='工作人员或管理员请使用 <a href="/console">社区工作台</a> 登录。';return}
  const version=sessionVersion;
  $('loginView').classList.add('hidden');$('appView').classList.remove('hidden');
  $('whoami').textContent=(me.display_name||me.username)+' · '+(me.role==='family'?'家属':'居民');
  try{
    if(me.role==='family'){buildTabs([['v-family','家人','👥'],['v-me','我的','🙂']]);renderFamilyList()}
    else{buildTabs([['v-home','首页','🏠'],['v-vitals','健康','❤️'],['v-appoint','预约','📅'],['v-remind','提醒','⏰'],['v-me','我的','🙂']])}
    if(me.role!=='family'){
      const loadedCommunities=await api('/api/communities');if(version!==sessionVersion)return;communities=loadedCommunities;
      $('obComm').innerHTML=communities.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
      try{const loadedResident=await api('/api/residents/me');if(version!==sessionVersion)return;resident=loadedResident}catch(e){if(e.message==='会话已切换')return;resident=null}
      renderHome();renderVitals();
    }
    tab(me.role==='family'?'v-family':'v-home');
    maybeShowRiskPopup();
  }catch(e){toast(e.message)}
}
function buildTabs(tabs){
  $('tabbar').innerHTML=tabs.map(t=>`<button data-v="${t[0]}" data-act="tab"><span class="ico">${icon({'v-home':'home','v-vitals':'heart','v-appoint':'calendar','v-remind':'calendar','v-me':'user','v-family':'users'}[t[0]])}</span>${t[0]==='v-remind'?'计划':t[1]}</button>`).join('');
}
function tab(v){
  if(!me)return;
  document.querySelectorAll('main .view').forEach(x=>x.classList.remove('on'));
  const el=$(v);if(el)el.classList.add('on');
  document.querySelectorAll('#tabbar button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  if(v==='v-remind'){renderReminders();if(resident&&me.role==='resident')window.AnlinPlans?.mount($('healthPlans'),{residentId:resident.id,request:api,notify:toast})}
  if(v==='v-me')renderMe();
  if(v==='v-appoint')renderAppointments();
  if(v==='v-home')renderHome();
  if(v==='v-vitals')renderVitals();
  if(v==='v-vitals'&&me?.role==='resident')loadRiskCard();
}

/* ---------- 首页 ---------- */
async function renderHome(){
  if(me.role==='family')return;
  const version=sessionVersion;
  const has=!!resident;
  $('onboardBox').classList.toggle('hidden',has);
  $('homeBox').classList.toggle('hidden',!has);
  if(!has)return;
  const comm=(communities.find(c=>c.id===resident.community_id)||{}).name||('社区 #'+resident.community_id);
  $('homeDate').textContent=new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});
  $('homeArchive').innerHTML=`<div><div class="page-kicker">一份属于您的健康档案</div><h2>${esc(resident.name)}，您好</h2><p>${esc(comm)} · ${esc(resident.birth_year)} 年出生<br>今天，从关心自己的一件小事开始。</p><span class="tag">${resident.consent_active?'社区关怀已连接':'社区服务授权未生效'}</span></div><img src="/static/care-garden.svg" alt="">`;
  loadHomeLatest();
  try{
    const [rems,planTasks]=await Promise.all([api(`/api/residents/${resident.id}/reminders?pending_only=true&limit=100`),window.AnlinPlans?api(`/api/residents/${resident.id}/plan-occurrences?days=1&status=pending&limit=100`).catch(()=>null):Promise.resolve(null)]);
    if(version!==sessionVersion)return;
    const today=localDate();
    const planned=(planTasks?.items||[]).map(t=>({id:t.id,title:t.title,due_date:localDay(new Date(t.due_at)),plan:true}));
    const due=[...rems,...planned].filter(r=>!r.completed_at&&fmtD(r.due_date)<=today).sort((a,b)=>a.due_date.localeCompare(b.due_date)).slice(0,4);
    $('homeReminders').innerHTML=due.length?due.map(r=>`<div class="item"><span class="tag-grey tag">${fmtD(r.due_date)<today?'逾期 '+fmtD(r.due_date).slice(5):'今天'}</span><div class="grow"><b>${esc(r.title)}</b></div></div>`).join(''):'<div class="empty">今天的安排很从容<br><small>可以去计划页安排下一次记录。</small></div>';
  }catch(e){if(!me)return;$('homeReminders').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
}

/* ---------- 健康 ---------- */
const VT={blood_pressure:{label:'血压',unit:'mmHg'},blood_glucose:{label:'血糖',unit:'mmol/L'},weight:{label:'体重',unit:'kg'}};
function renderVitals(){
  if(me?.role==='family'||!resident)return;
  $('vtFormTitle').textContent='记录'+VT[vtType].label;
  $('vtTabs').innerHTML=Object.keys(VT).map(k=>`<button class="pill ${k===vtType?'on':''}" data-act="set-vt" data-metric="${k}">${VT[k].label} <span class="helper" style="margin:0;font-size:11px;color:inherit">${VT[k].unit}</span></button>`).join('');
  const f=$('vtFields');
  if(vtType==='blood_pressure')f.innerHTML=`<div class="field"><label for="vSys">收缩压（高压）</label><input id="vSys" type="number" inputmode="numeric" min="40" max="300" placeholder="例如 120"></div><div class="field"><label for="vDia">舒张压（低压）</label><input id="vDia" type="number" inputmode="numeric" min="20" max="200" placeholder="例如 80"></div>`;
  else if(vtType==='blood_glucose')f.innerHTML=`<div class="field"><label for="vGlu">血糖值（mmol/L）</label><input id="vGlu" type="number" step="0.1" min="0.5" max="50" placeholder="例如 5.6"></div><div class="field"><label for="vContext">进餐相关说明</label><select id="vContext"><option value="unknown">不确定 / 未记录</option><option value="fasting">空腹</option><option value="before_meal">餐前</option><option value="after_meal">餐后</option></select></div>`;
  else f.innerHTML=`<div class="field"><label for="vWt">体重（kg）</label><input id="vWt" type="number" step="0.1" min="5" max="400" placeholder="例如 62.5"></div>`;
  $('vtMeasuredAt').value=localInputDate(new Date());
  $('vitalSaveState').textContent='';pendingVital=null;
  loadVitals();loadTrend();
}
async function setVT(k){vtType=k;vitalOffset=0;renderVitals()}
function pageVitals(step){vitalOffset=Math.max(0,vitalOffset+step*20);loadVitals()}
async function loadVitals(){
  const version=sessionVersion,metric=vtType,seq=++vitalRequestSeq;
  try{
    const list=await api(`/api/residents/${resident.id}/vitals?limit=20&offset=${vitalOffset}&metric=${metric}`);
    if(version!==sessionVersion||metric!==vtType||seq!==vitalRequestSeq)return;
    const contexts={fasting:'空腹',before_meal:'餐前',after_meal:'餐后',unknown:'进餐情况未注明'};
    $('vtList').innerHTML=list.length?list.map(v=>{
      const val=metric==='blood_pressure'?`${v.systolic} / ${v.diastolic} ${VT[metric].unit}`:`${metric==='blood_glucose'?v.glucose_mmol_l:v.weight_kg} ${VT[metric].unit}`;
      return `<div class="item"><div class="grow"><b class="history-value">${esc(val)}</b>${contexts[v.measurement_context]?`<span class="context-label">${contexts[v.measurement_context]}</span>`:''}<div class="d">${esc(fmtTime(v.measured_at))}</div></div><button class="btn btn-line btn-danger" style="height:38px;padding:0 14px;font-size:12px" data-act="del-vital" data-id="${v.id}">删除</button></div>`;
    }).join(''):'<div class="empty">还没有记录，从今天的第一次测量开始。</div>';
    $('vtPaging').innerHTML=`<button class="btn btn-line" ${vitalOffset?'':'disabled'} data-act="page-vitals" data-step="-1">上一页</button><span class="helper" style="margin:0">第 ${Math.floor(vitalOffset/20)+1} 页</span><button class="btn btn-line" ${list.length===20?'':'disabled'} data-act="page-vitals" data-step="1">下一页</button>`;
  }catch(e){if(version!==sessionVersion||seq!==vitalRequestSeq)return;$('vtList').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
}
function setTrendRange(days){trendDays=days;document.querySelectorAll('#trendRanges button').forEach(b=>b.classList.toggle('on',Number(b.dataset.days)===days));loadTrend()}
async function loadTrend(){
  if(!resident||!window.AnlinTrend)return;
  const seq=++trendRequestSeq,version=sessionVersion,metric=vtType;
  const end=new Date(),start=new Date();start.setDate(end.getDate()-trendDays+1);
  const tz=Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Shanghai';
  $('vtTrend').innerHTML='<div class="chart-empty">正在整理这段时间的记录…</div>';
  try{
    const data=await api(`/api/residents/${resident.id}/vitals/series?metric=${metric}&start_date=${localDay(start)}&end_date=${localDay(end)}&tz=${encodeURIComponent(tz)}`);
    if(seq!==trendRequestSeq||version!==sessionVersion||metric!==vtType)return;
    window.AnlinTrend.render($('vtTrend'),data);
  }catch(e){if(seq===trendRequestSeq&&version===sessionVersion)$('vtTrend').innerHTML='<div class="chart-empty">'+esc(e.message)+'</div>'}
}
async function saveVital(){
  let payload={type:vtType};
  if(vtType==='blood_pressure'){
    const s=+($('vSys').value),d=+($('vDia').value);
    if(!s||!d)return toast('请填写收缩压和舒张压');payload.systolic=s;payload.diastolic=d;
  }else if(vtType==='blood_glucose'){
    const g=+($('vGlu').value);if(!g)return toast('请填写血糖值');payload.glucose_mmol_l=g;payload.measurement_context=$('vContext').value||'unknown';
  }else{const w=+($('vWt').value);if(!w)return toast('请填写体重');payload.weight_kg=w}
  const when=new Date($('vtMeasuredAt').value);
  if(Number.isNaN(when.getTime()))return toast('请填写实际测量时间');payload.measured_at=when.toISOString();
  const fingerprint=JSON.stringify(payload);
  if(!pendingVital||pendingVital.fingerprint!==fingerprint)pendingVital={fingerprint,id:window.crypto?.randomUUID?.()||('vital_'+Date.now()+'_'+Math.random().toString(36).slice(2))};
  payload.client_request_id=pendingVital.id;
  const version=sessionVersion,btn=$('vtSave');if(btn.disabled)return;btn.disabled=true;$('vitalSaveState').textContent='正在保存…';
  try{await api(`/api/residents/${resident.id}/vitals`,'POST',payload);if(version!==sessionVersion)return;pendingVital=null;vitalOffset=0;toast('这次测量已保存');renderVitals();$('vitalSaveState').textContent='已保存，可在趋势与历史记录中查看。'}catch(e){if(version!==sessionVersion)return;$('vitalSaveState').textContent='保存未确认，内容已保留。可以再次点击保存，重复重试不会新增同一条记录。';toast(e.message)}finally{btn.disabled=false}
}
async function delVital(id){
  if(!(await window.AnlinCore.confirmDialog('删除这条记录？删除后无法找回。')))return;
  try{await api(`/api/residents/${resident.id}/vitals/${id}`,'DELETE');toast('已删除');loadVitals();loadTrend()}catch(e){toast(e.message)}
}

/* ---------- 提醒 ---------- */
async function renderReminders(){
  if(me.role==='family'||!resident)return;
  const version=sessionVersion;
  try{
    const list=await api(`/api/residents/${resident.id}/reminders`);
    if(version!==sessionVersion)return;
    $('remList').innerHTML=list.length?list.map(r=>`<div class="item"><button class="circle ${r.completed_at?'done':''}" data-act="done-remind" data-id="${r.id}" aria-label="标记完成">${r.completed_at?'✓':''}</button><div class="grow"><b style="${r.completed_at?'color:#9AA1A7;text-decoration:line-through':''}">${esc(r.title)}</b><div class="d">${fmtD(r.due_date)}${r.completed_at?' · 已完成':''}</div></div><span class="tag-grey tag">${esc({measure:'测量',questionnaire:'问卷',followup:'随访',appointment:'预约'}[r.type]||r.type)}</span></div>`).join(''):'<div class="empty">还没有提醒事项</div>';
    if(!$('remDate').value)$('remDate').value=localDate();
  }catch(e){if(!me)return;$('remList').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
}
async function doneRemind(id){
  try{await api(`/api/residents/${resident.id}/reminders/${id}/complete`,'PATCH');renderReminders()}catch(e){toast(e.message)}
}

/* ---------- 风险提示与预约 ---------- */
const RISK_METRICS={blood_pressure:'血压',blood_pressure_trend:'血压趋势',blood_glucose:'血糖',weight:'体重'};
async function loadRiskCard(){
  if(me?.role!=='resident'||!resident)return;
  const version=sessionVersion;
  try{
    const out=await api(`/api/residents/${resident.id}/risk-alerts?days=14`);
    if(version!==sessionVersion)return;
    const card=$('riskCard');
    if(!out.items.length){card.classList.add('hidden');return}
    card.classList.remove('hidden');
    $('riskList').innerHTML=out.items.map(a=>{
      const handled=a.acknowledged_at?`<div class="d">社区已处理 · ${esc(a.outcome_label||'')}${a.outcome_note?'：'+esc(a.outcome_note):''}（${esc(fmtTime(a.acknowledged_at))}）</div>`:'';
      return `<div class="item"><span class="${a.level==='critical'?'tag-coral':'tag'} tag">${a.level==='critical'?'需尽快关注':'关注'}</span><div class="grow"><b>${esc(RISK_METRICS[a.metric]||a.metric)} · ${esc(a.value_text)}</b><div class="d">${esc(a.message)}</div><div class="d">${esc(fmtTime(a.created_at))}</div>${handled}</div></div>`}).join('');
  }catch(e){}
}
async function renderAppointments(){
  if(me?.role!=='resident'||!resident)return;
  const version=sessionVersion;
  if(!$('apDate').value)$('apDate').value=localDate();
  try{
    const list=await api(`/api/residents/${resident.id}/appointments`);
    if(version!==sessionVersion)return;
    const labels={pending:'待确认',confirmed:'已确认',completed:'已完成',cancelled:'已取消'};
    $('apList').innerHTML=list.length?list.map(a=>`<div class="card"><div class="row"><div><b>${esc(a.kind_label)}</b><div class="d">${esc(a.preferred_date)} ${esc(a.time_slot_label)}${a.note?' · '+esc(a.note):''}</div>${a.response_note?`<div class="d">社区回复：${esc(a.response_note)}${a.handled_by?'（'+esc(a.handled_by)+'）':''}</div>`:''}</div><span class="${a.status==='confirmed'?'tag':a.status==='pending'?'tag-coral':'tag-grey'} tag">${labels[a.status]||a.status}</span></div>${a.status==='pending'||a.status==='confirmed'?`<div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn btn-line btn-danger" style="height:38px;padding:0 14px;font-size:13px" data-act="cancel-appointment" data-id="${a.id}">取消预约</button></div>`:''}</div>`).join(''):'<div class="empty">还没有预约记录，从上面的表单提交第一次预约。</div>';
  }catch(e){if(!me)return;$('apList').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
}
async function submitAppointment(){
  if(me?.role!=='resident'||!resident)return;
  const body={kind:$('apKind').value,preferred_date:$('apDate').value,time_slot:$('apSlot').value,note:$('apNote').value.trim()||null};
  if(!body.preferred_date)return toast('请选择希望日期');
  const btn=$('apSubmit');if(btn.disabled)return;btn.disabled=true;
  try{await api(`/api/residents/${resident.id}/appointments`,'POST',body);$('apNote').value='';toast('预约已提交，等待社区确认');renderAppointments()}catch(e){toast(e.message)}finally{btn.disabled=false}
}
async function cancelAppointment(id){
  if(!(await window.AnlinCore.confirmDialog('取消这个预约？')))return;
  try{await api(`/api/residents/${resident.id}/appointments/${id}/cancel`,'POST');toast('已取消');renderAppointments()}catch(e){toast(e.message)}
}
/* 登录后检查近 72 小时的风险提示，弹一次窗口（居民本人与获授权家属）。 */
async function maybeShowRiskPopup(){
  try{
    if(sessionStorage.getItem('anlinRiskPopup'))return;
    let lines=[];
    if(me.role==='resident'&&resident){
      const out=await api(`/api/residents/${resident.id}/risk-alerts?days=3`);
      lines=out.items.map(a=>`${RISK_METRICS[a.metric]||a.metric} ${a.value_text}（${a.level==='critical'?'需尽快关注':'关注'}）`);
    }else if(me.role==='family'){
      const shared=await api('/api/family/shared-residents');
      for(const r of shared.slice(0,5)){
        if(!r.can_vitals)continue;
        try{const out=await api(`/api/residents/${r.resident_id}/risk-alerts?days=3`);
          for(const a of out.items)lines.push(`${r.name}：${RISK_METRICS[a.metric]||a.metric} ${a.value_text}（${a.level==='critical'?'需尽快关注':'关注'}）${a.acknowledged_at?'（社区已处理）':''}`);
        }catch(e){}
      }
    }
    if(lines.length){
      sessionStorage.setItem('anlinRiskPopup','1');
      await window.AnlinCore.confirmDialog('近期的健康风险提示：\n· '+lines.slice(0,4).join('\n· ')+(lines.length>4?`\n…共 ${lines.length} 条，详情见健康页`:'')+'\n\n以上来自常见参考范围，不构成医疗诊断；如感不适应及时就医。',{okLabel:'知道了',danger:lines.some(l=>l.includes('需尽快关注'))});
    }
  }catch(e){}
}

/* ---------- 本周健康小结 ---------- */
function renderWeeklySummary(data){
  const m=data.measurements||{};
  const fmtStats=(label,s,unit)=>s?`<div class="kv"><span>${label}</span><b>${s.count} 次 · 均 ${s.avg}${unit}（${s.min}~${s.max}）</b></div>`:`<div class="kv"><span>${label}</span><b>本周没有记录</b></div>`;
  const bp=m.blood_pressure;
  const bpLine=bp?`<div class="kv"><span>血压</span><b>${bp.count} 次 · 均 ${bp.avg}/${bp.avg_dia??'-'} mmHg</b></div>`:'<div class="kv"><span>血压</span><b>本周没有记录</b></div>';
  const alertLine=data.alerts.total?`<div class="kv"><span>风险提示</span><b>${data.alerts.total} 条（${data.alerts.handled} 条已处理）</b></div>`:'<div class="kv"><span>风险提示</span><b>本周没有</b></div>';
  const apptLine=data.appointments.upcoming?`<div class="kv"><span>即将到来</span><b>${data.appointments.upcoming.kind==='doctor'?'医生门诊陪同':'工作人员上门'} · ${data.appointments.upcoming.date}</b></div>`:'';
  return bpLine+fmtStats('血糖',m.blood_glucose,' mmol/L')+fmtStats('体重',m.weight_kg,' kg')+alertLine+apptLine;
}
async function loadMySummary(){
  if(me?.role!=='resident'||!resident)return;
  const version=sessionVersion;
  try{
    const data=await api(`/api/residents/${resident.id}/weekly-report`);
    if(version!==sessionVersion)return;
    const body=$('summaryBody');
    if(body)body.innerHTML=renderWeeklySummary(data);
  }catch(e){const body=$('summaryBody');if(body)body.innerHTML='<div class="empty">'+esc(e.message)+'</div>'}
}
async function loadFamilySummary(rid,box){
  const version=sessionVersion;
  try{
    const data=await api(`/api/residents/${rid}/weekly-report`);
    if(version!==sessionVersion)return;
    box.insertAdjacentHTML('beforeend','<div class="card"><h3>TA 的一周小结</h3>'+renderWeeklySummary(data)+'</div>');
  }catch(e){}
}

/* ---------- 我的 / 家人共享 ---------- */
async function renderMe(){
  if(me.role==='family'){
    $('meProfile').innerHTML=`<h3>我的账号</h3><div class="kv"><span>称呼</span><b>${esc(me.display_name)}</b></div><div class="kv"><span>账号</span><b>${esc(me.username)}</b></div><div class="kv"><span>身份</span><b>家庭成员</b></div>`;
    $('meShare').innerHTML=`<div class="card"><h3>我的家属邀请码</h3><div class="helper">把此邀请码告诉居民。居民登录自己的账号，在“我的”页面输入您的邀请码，选择可见范围和期限后授予查看权。</div><div class="mono" style="margin:12px 0">${esc(me.invite_code)}</div><div class="row" style="gap:10px"><button class="btn btn-line" style="flex:1" data-act="copy-invite">复制邀请码</button><button class="btn btn-line" style="flex:1" data-act="rotate-invite">更换邀请码</button></div></div>`;
    $('claimCard').classList.add('hidden');$('consentCard').classList.add('hidden');
    return;
  }
  if(resident){
    const comm=(communities.find(c=>c.id===resident.community_id)||{}).name||('社区 #'+resident.community_id);
    $('meProfile').innerHTML=`<h3>我的档案</h3><div class="kv"><span>姓名</span><b>${esc(resident.name)}</b></div><div class="kv"><span>出生年份</span><b>${resident.birth_year}</b></div><div class="kv"><span>所属社区</span><b>${esc(comm)}</b></div>
      <div class="row" style="margin-top:12px"><button class="btn btn-line" data-act="prompt-edit">修改资料</button></div>`;
    $('claimCard').classList.add('hidden');$('consentCard').classList.remove('hidden');
    renderShare();renderConsent();loadMySummary();
  }else{
    $('meProfile').innerHTML='<h3>还没有档案</h3><div class="empty">如社区已有您的纸质档案，请先申请认领；否则回首页建立新档案。</div>';
    $('meShare').innerHTML='';
    $('claimCard').classList.remove('hidden');$('consentCard').classList.add('hidden');$('claimStatus').textContent='提交后请携证件到社区现场核验；核验完成后重新登录查看档案。';
  }
}
async function submitClaim(){
  const resident_id=Number($('claimId').value),full_name=$('claimName').value.trim(),phone=$('claimPhone').value.trim(),note=$('claimNote').value.trim();
  if(!Number.isInteger(resident_id)||resident_id<1||full_name.length<2||!phone)return toast('请填写档案编号、登记姓名和电话');
  const btn=$('claimBtn');btn.disabled=true;
  try{const out=await api('/api/residents/claim-requests','POST',{resident_id,full_name,phone,note:note||null});toast(out.message||'申请已提交');$('claimStatus').textContent='申请已提交。如档案信息匹配，社区工作人员会进行现场核验；请携有效证件前往社区。';$('claimId').value='';$('claimName').value='';$('claimPhone').value='';$('claimNote').value=''}
  catch(e){toast(e.message)}finally{btn.disabled=false}
}
async function renderConsent(){
  if(!resident)return;
  const id=resident.id,version=sessionVersion;
  try{
    const latest=await api(`/api/residents/${id}`);if(version!==sessionVersion)return;resident=latest;
    const history=await api(`/api/residents/${id}/consents`);
    if(version!==sessionVersion)return;
    $('consentState').innerHTML=`<div class="helper">当前状态：<b>${resident.consent_active?'已授权社区健康服务':'未授权社区健康服务'}</b>。撤回后社区工作人员将不能查看您的档案和测量记录。</div>`;
    $('consentHistory').innerHTML=history.length?history.slice(0,8).map(c=>`<div class="item"><div class="grow">${c.granted?'授予':'撤回'}社区服务授权<div class="d">${esc(fmtTime(c.created_at))} · ${esc(({approved:'已生效',pending:'待复核',rejected:'已驳回'})[c.review_status]||c.review_status)}</div></div></div>`).join(''):'<div class="empty">暂无授权记录</div>';
    $('consentBtn').textContent=resident.consent_active?'撤回社区服务授权':'重新授予社区服务授权';
    renderHome();
  }catch(e){if(!me)return;$('consentState').textContent=e.message}
}
async function toggleConsent(){
  if(!resident)return;
  const granted=!resident.consent_active;
  if(!(await window.AnlinCore.confirmDialog(granted?'确认重新授权社区工作人员查看您的档案和测量数据？':'确认撤回社区服务授权？')))return;
  const btn=$('consentBtn');btn.disabled=true;
  try{await api(`/api/residents/${resident.id}/consents`,'POST',{granted});toast(granted?'已重新授权':'已撤回授权');await renderConsent()}
  catch(e){toast(e.message)}finally{btn.disabled=false}
}
async function promptEdit(){
  const name=await window.AnlinCore.promptDialog('姓名：',resident.name);if(name===null)return;
  const phone=await window.AnlinCore.promptDialog('联系电话（可留空）：',resident.phone||'');
  try{
    resident=await api(`/api/residents/${resident.id}`,'PATCH',{name,phone:phone||null});
    toast('已更新');renderMe();
  }catch(e){toast(e.message)}
}
async function renderShare(){
  const version=sessionVersion;
  const box=$('meShare');
  try{
    const grants=await api(`/api/residents/${resident.id}/family-grants`);
    if(version!==sessionVersion||!me)return;
    box.innerHTML=`<div class="card"><h3>授权家人查看</h3><div class="helper">请家属登录自己的账号，在“我的”页面找到家属邀请码并告诉您。您在此输入后，授权才会生效。</div>
        <div class="field"><label for="gCode">家人的邀请码</label><input id="gCode" placeholder="家人在“我的”页面可以看到"></div>
        <label class="check"><input type="checkbox" id="gProfile" checked><span>允许查看基本档案（姓名、年龄）</span></label>
        <label class="check"><input type="checkbox" id="gVitals" checked><span>允许查看测量数据（血压/血糖/体重）</span></label>
        <div class="field"><label for="gDays">有效天数</label><input id="gDays" type="number" value="30" min="1" max="365"></div>
        <button class="btn btn-primary" id="gBtn" type="button">授予查看权</button>
      </div>
      <div class="card"><h3>已授权的家人</h3>${grants.length?grants.map(g=>`<div class="item"><div class="grow"><b>${esc(g.grantee_name)}</b><div class="d">${g.can_profile?'可看档案 ':''}${g.can_vitals?'可看测量 ':''}· 到期 ${fmtD(g.expires_at)}${g.revoked_at?' · 已撤回':''}</div></div>${g.revoked_at?'':'<button class="btn btn-line btn-danger" style="height:38px;padding:0 14px;font-size:13.5px" data-act="revoke-grant" data-id="'+g.id+'">撤回</button>'}</div>`).join(''):'<div class="empty">还没有授权过家人</div>'}</div>`;
    $('gBtn').onclick=async()=>{
      const canP=$('gProfile').checked,canV=$('gVitals').checked,days=+($('gDays').value)||30;
      if(!canP&&!canV)return toast('至少选择一项可见范围');
      const btn=$('gBtn');if(btn.disabled)return;btn.disabled=true;
      try{await api(`/api/residents/${resident.id}/family-grants`,'POST',{invite_code:$('gCode').value.trim(),can_profile:canP,can_vitals:canV,expires_in_days:days});toast('已授权');renderShare()}catch(e){toast(e.message)}finally{btn.disabled=false}
    };
  }catch(e){if(!me)return;box.innerHTML='<div class="card"><div class="empty">'+esc(e.message)+'</div></div>'}
}
function copyInvite(){
  const code=me.invite_code;
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(code).then(()=>toast('邀请码已复制'),()=>toast('邀请码：'+code));
  else toast('邀请码：'+code);
}
async function rotateInvite(){
  if(!(await window.AnlinCore.confirmDialog('更换后旧的邀请码立即失效，确定？')))return;
  try{const r=await api('/api/me/invite-code/rotate','POST');me.invite_code=r.invite_code;toast('已更换');renderMe()}catch(e){toast(e.message)}
}
async function revokeGrant(id){
  if(!(await window.AnlinCore.confirmDialog('撤回后家人立即失去查看权限，确定？')))return;
  try{await api(`/api/residents/${resident.id}/family-grants/${id}`,'DELETE');toast('已撤回');renderShare()}catch(e){toast(e.message)}
}

/* ---------- 家属视图 ---------- */
async function renderFamilyList(){
  const version=sessionVersion;
  try{
    const list=await api('/api/family/shared-residents');
    if(version!==sessionVersion||!me)return;
    famShared=list;
    $('famList').innerHTML=list.length?list.map(r=>`<div class="card"><div class="row"><div><b style="font-size:18px">${esc(r.name)}</b><div class="d" style="color:var(--muted);font-size:13px;margin-top:4px">${r.can_profile?'基本档案 ':''}${r.can_vitals?'测量数据 ':''}可见</div></div><button class="btn btn-line" data-act="open-fam" data-rid="${r.resident_id}">查看</button>${r.grant_id?`<button class="btn btn-line btn-danger" data-act="decline-grant" data-rid="${r.resident_id}" data-gid="${r.grant_id}">拒绝授权</button>`:''}</div></div>`).join(''):'<div class="card"><div class="empty">还没有可以查看的家人。请将“我的”页面中的家属邀请码告诉居民，由居民登录自己的账号授予查看权。</div></div>';
    $('famDetail').classList.add('hidden');$('famList').classList.remove('hidden');
  }catch(e){if(!me)return;$('famList').innerHTML='<div class="card"><div class="empty">'+esc(e.message)+'</div></div>'}
}
async function declineGrant(residentId,grantId){
  if(!(await window.AnlinCore.confirmDialog('拒绝这项授权后将不能再查看该居民的数据，确定？')))return;
  try{await api(`/api/residents/${residentId}/family-grants/${grantId}/decline`,'POST');toast('已拒绝授权');renderFamilyList()}catch(e){toast(e.message)}
}
async function openFam(rid){
  const version=sessionVersion;
  try{
    const profile=await api(`/api/residents/${rid}`).catch(()=>null);
    const [bp,gl,wt]=await Promise.all(['blood_pressure','blood_glucose','weight'].map(metric=>api(`/api/residents/${rid}/vitals?limit=8&metric=${metric}`).catch(()=>null)));
    const vitals=bp||gl||wt;
    if(version!==sessionVersion)return;
    let html=`<button class="btn btn-line" data-act="family-back" style="margin-bottom:14px">← 返回列表</button>`;
    if(profile){
      html+=`<div class="card"><h3>基本档案</h3><div class="kv"><span>姓名</span><b>${esc(profile.name)}</b></div><div class="kv"><span>出生年份</span><b>${profile.birth_year}</b></div></div>`;
    }
    if(vitals){

      const sec=(title,rows,fmt)=>`<div class="card"><h3>${title}</h3>${rows?.length?rows.slice(0,8).map(v=>`<div class="item"><div class="grow"><b>${esc(fmt(v))}</b><div class="d">${esc(fmtTime(v.measured_at))}</div></div></div>`).join(''):'<div class="empty">暂无记录</div>'}</div>`;
      html+=sec('血压 · 最近 8 条',bp,v=>`${v.systolic}/${v.diastolic} mmHg`)+sec('血糖 · 最近 8 条',gl,v=>`${v.glucose_mmol_l} mmol/L`)+sec('体重 · 最近 8 条',wt,v=>`${v.weight_kg} kg`);
    }
    const shared=famShared.find(r=>r.resident_id===rid);
    if(shared&&shared.can_write_vitals){
      html+=`<div class="card"><h3>帮 TA 记录一次测量</h3><div class="row" style="gap:10px">
        <div class="field" style="flex:1;margin:0"><label for="fpType">类型</label><select id="fpType"><option value="blood_pressure">血压</option><option value="blood_glucose">血糖</option><option value="weight">体重</option></select></div>
        <div class="field" style="flex:1;margin:0"><label for="fpVal">数值</label><input id="fpVal" inputmode="decimal" placeholder="血压填 收缩压/舒张压，如 130/85；血糖如 5.6；体重如 62.5"></div>
        <div class="field" style="flex:1;margin:0"><label for="fpDia">舒张压（仅血压）</label><input id="fpDia" type="number" step="1" placeholder="如 85"></div>
        </div><button class="btn btn-primary" id="fpSave" type="button" style="margin-top:12px">保存代录</button><div class="helper">代录会记录“${esc(me.display_name||'家属')} 代录”，家人和社区都能看到。</div></div>`;
    }
    $('famDetail').innerHTML=html;
    $('famList').classList.add('hidden');$('famDetail').classList.remove('hidden');
    loadFamilySummary(rid,$('famDetail'));
    if(shared&&shared.can_write_vitals){
      $('fpSave').onclick=async()=>{
        const type=$('fpType').value,val=$('fpVal').value,dia=$('fpDia').value;
        if(!val)return toast('请填写数值');
        let payload;
        if(type==='blood_pressure'){
          const parts=String(val).split(/[\/／]/);
          const s=Number(parts[0]),d=Number(parts.length>1?parts[1]:dia);
          if(!s||!d)return toast('血压请填写 收缩压/舒张压');
          payload={type,systolic:s,diastolic:d};
        }else if(type==='blood_glucose'){payload={type,glucose_mmol_l:Number(val)}}
        else{payload={type,weight_kg:Number(val)}}
        const when=new Date();payload.measured_at=when.toISOString();
        payload.client_request_id='fam_'+rid+'_'+Date.now();
        try{await api(`/api/residents/${rid}/vitals`,'POST',payload);toast('已代录');openFam(rid)}catch(e){toast(e.message)}
      };
    }
    window.scrollTo({top:0});
  }catch(e){toast(e.message)}
}

/* ---------- 设置 ---------- */
async function changePassword(){
  const cur=$('pwCur').value,ne=$('pwNew').value;
  if(!cur||!ne)return toast('请填写完整');
  try{
    const out=await api('/api/auth/change-password','POST',{current_password:cur,new_password:ne});
    token=out.access_token;sessionStorage.setItem('token',token);sessionChanged();
    $('pwCur').value='';$('pwNew').value='';toast('密码已更新，其他设备已退出');
  }catch(e){toast(e.message)}
}

/* ---------- 绑定 ---------- */
$('loginBtn').onclick=doLogin;
$('lpwd').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin()});
$('regToggle').onclick=()=>$('regBox').classList.toggle('hidden');
$('regBtn').onclick=registerFamily;
$('logoutBtn').onclick=logout;
$('obBtn').onclick=async()=>{
  const body={name:$('obName').value.trim(),birth_year:+($('obYear').value),sex:$('obSex').value,community_id:+($('obComm').value),consent_to_community_care:$('obConsent').checked};
  if(!body.name||!body.birth_year)return toast('请填写姓名和出生年份');
  if(!body.consent_to_community_care)return toast('需要勾选同意后才能建档');
  try{resident=await api('/api/residents/me','POST',body);toast('档案建立成功');renderHome();renderVitals()}catch(e){toast(e.message)}
};
$('vtSave').onclick=saveVital;
$('remAdd').onclick=async()=>{
  const title=$('remTitle').value.trim();
  if(!title)return toast('请填写提醒内容');
  try{await api(`/api/residents/${resident.id}/reminders`,'POST',{type:$('remType').value,title,due_date:$('remDate').value||localDate()});$('remTitle').value='';toast('已添加');renderReminders();renderHome()}catch(e){toast(e.message)}
};
$('pwBtn').onclick=changePassword;
$('claimBtn').onclick=submitClaim;
$('consentBtn').onclick=toggleConsent;
$('apSubmit').onclick=submitAppointment;
$('logoutAll').onclick=async()=>{
  if(!(await window.AnlinCore.confirmDialog('退出所有设备？')))return;
  try{await api('/api/auth/logout-all','POST')}catch(e){}
  logout();
};

/* ---------- 启动 ---------- */
fetch('/api/auth/demo-status').then(r=>r.json()).then(s=>{if(s.enabled)$('demoHint').textContent='演示提示：本页可用工作人员开通的账号登录（开发环境示例见管理台）。'}).catch(()=>{});
if(token){
  api('/api/me').then(async u=>{me=u;sessionChanged();await enterApp();maybeShowRiskPopup()}).catch(()=>{});
}else{
  showLogin();
}
if(localStorage.getItem('anlinLarge')==='1')document.documentElement.classList.add('large-mode');
$('fontToggle').onclick=()=>{const on=document.documentElement.classList.toggle('large-mode');localStorage.setItem('anlinLarge',on?'1':'0');toast(on?'已切换为大字模式':'已恢复标准字号')};
$('exportBtn').onclick=async()=>{
  if(!resident)return;
  try{
    const res=await fetch(`/api/residents/${resident.id}/export.csv`,{headers:{Authorization:'Bearer '+token}});
    if(!res.ok)throw new Error('导出失败');
    const blob=await res.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download='anlin-health-data.csv';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),2000);toast('已导出');
  }catch(e){toast(e.message)}
};

$('openReportBtn').addEventListener('click',()=>{if(me?.role==='resident'&&resident)window.anlinReports?.open(resident.id,resident.name)});
window.addEventListener('anlin-reports-confirmed',e=>{if(me?.role==='resident'&&resident?.id===e.detail?.residentId)renderVitals()});
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
$('homeReportBtn').addEventListener('click',()=>{if(me?.role==='resident'&&resident)window.anlinReports?.open(resident.id,resident.name)});
// expose the shared runtime for tests and modules that reach it via window
Object.assign(window, {api, toast});
window.setTrendRange=setTrendRange;
window.tab=tab;window.setVT=setVT;window.pageVitals=pageVitals;window.saveVital=saveVital;window.delVital=delVital;
window.doneRemind=doneRemind;window.promptEdit=promptEdit;
window.copyInvite=copyInvite;window.rotateInvite=rotateInvite;window.revokeGrant=revokeGrant;
window.renderFamilyList=renderFamilyList;window.openFam=openFam;window.declineGrant=declineGrant;

/* ---------- data-act 事件注册（替代内联 onclick，配合 CSP script-src 'self'） ---------- */
window.AnlinCore.bindActions({
  'tab': ds => tab(ds.v),
  'trend-range': ds => setTrendRange(Number(ds.days)),
  'set-vt': ds => setVT(ds.metric),
  'del-vital': ds => delVital(Number(ds.id)),
  'page-vitals': ds => pageVitals(Number(ds.step)),
  'done-remind': ds => doneRemind(Number(ds.id)),
  'cancel-appointment': ds => cancelAppointment(Number(ds.id)),
  'copy-invite': () => copyInvite(),
  'rotate-invite': () => rotateInvite(),
  'prompt-edit': () => promptEdit(),
  'revoke-grant': ds => revokeGrant(Number(ds.id)),
  'open-fam': ds => openFam(Number(ds.rid)),
  'decline-grant': ds => declineGrant(Number(ds.rid), Number(ds.gid)),
  'family-back': () => renderFamilyList(),
});
})();
// expose for tests and sibling modules that reach the shared runtime via window
Object.assign(window, {api, toast});
