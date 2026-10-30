let token='',me=null,residents=[],visits=[],residentOffset=0,residentHasMore=false,residentQuery='',residentSearchTimer=null,statsOffset=0,statsQuery='',statsSearchTimer=null,sessionVersion=0,residentRequestSeq=0,statsRequestSeq=0,loginBusy=false;
let visitItems=[],visitOffset=0,visitFilter='planned',visitTotal=0,visitRequestSeq=0,visitStatsRequestSeq=0,visitLoading=false,visitTotals={total:0,planned:0,done:0,overdue:0};
window.anlinAssistantAuth=()=>({token,user:me});
function sessionChanged(){sessionVersion++;window.dispatchEvent(new CustomEvent('anlin-session-change'))}
const $=x=>document.getElementById(x); const clean=window.AnlinCore.esc;
function fmtTime(s){if(!s)return '';const raw=String(s),utc=/[zZ]|[+-]\d\d:\d\d$/.test(raw)?raw:raw+'Z';return new Date(utc).toLocaleString('zh-CN')}
const notify=window.AnlinCore.makeToast(()=>$('toast'),4000);
const api=window.AnlinCore.createApi({getToken:()=>token,getVersion:()=>sessionVersion,onAuthExpired:logout,
  describeError:d=>typeof d==='string'?d:Array.isArray(d)?d.map(x=>x.msg).join('；'):'操作失败'});
// expose the shared runtime for tests and modules that reach it via window
Object.assign(window, {api, notify});

async function login(totpCode){if(loginBusy&&!totpCode)return;if(!totpCode)loginBusy=true;try{const path=totpCode?'/api/auth/login/totp':'/api/auth/login';const payload=totpCode?{username:$('username').value.trim(),password:$('password').value,totp_code:totpCode}:{username:$('username').value.trim(),password:$('password').value};const out=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const body=await out.json().catch(()=>({}));if(out.status===401&&out.headers.get('X-MFA-Required')==='totp'){const code=await window.AnlinCore.promptDialog('此账号已开启两步验证，请输入认证器中的6位验证码：');if(code)await login(code.trim());return}if(!out.ok)throw new Error(typeof body.detail==='string'?body.detail:'登录失败');if(!['staff','admin'].includes(body.user?.role))throw new Error('此入口仅供社区工作人员或管理员使用，居民和家属请使用居民服务入口');logout();token=body.access_token;me=body.user;sessionChanged();$('loginShade').classList.add('hidden');$('greet').textContent=me.display_name+'，欢迎回到工作台。';$('avatar').textContent=(me.display_name||'安').slice(-1);await refresh();maybeShowRiskPopup()}catch(e){notify('登录失败：'+e.message)}finally{if(!totpCode)loginBusy=false}}
(async()=>{try{const s=await(await fetch('/api/auth/demo-status')).json();if(s.enabled){$('demoHint').classList.remove('hidden');$('username').value='staff'}}catch{}})()
function logout(){token='';me=null;residents=[];visits=[];residentOffset=0;residentHasMore=false;residentQuery='';statsOffset=0;statsQuery='';residentRequestSeq++;statsRequestSeq++;visitRequestSeq++;visitStatsRequestSeq++;visitItems=[];visitOffset=0;visitFilter='planned';visitTotal=0;visitLoading=false;visitTotals={total:0,planned:0,done:0,overdue:0};$('visitStatus').value='planned';$('visitStatus').disabled=false;$('visitSummary').textContent='';$('visitPageLabel').textContent='第 1 页';$('visitPrev').disabled=true;$('visitNext').disabled=true;clearTimeout(residentSearchTimer);clearTimeout(statsSearchTimer);sessionChanged();clearAdminAccounts();closeModal();$('modalFields').replaceChildren();$('modalTitle').textContent='操作';$('modalDesc').textContent='';$('toast').textContent='';$('toast').classList.add('hidden');$('loginShade').classList.remove('hidden');$('username').value='';$('password').value='';$('pwCur').value='';$('pwNew').value='';$('mfaPassword').value='';$('mfaExistingCode').value='';$('mfaSecret').replaceChildren();$('mfaSecret').classList.add('hidden');$('search').value='';$('search2').value='';$('statsSearch').value='';$('newResident').disabled=false;$('provisionResidentView').disabled=false;$('createResidentView').disabled=false;for(const id of ['residentCount','visitCount','greet','avatar','claimCount','consentCount','acctInfo'])$(id).textContent='—';for(const id of ['residentRows','residentTable2','visitRows','visitList','claimQueue','consentQueue','residentPaging','statsPaging','statChips','statChart','latestBody'])$(id).replaceChildren();currentView='overview';document.querySelectorAll('#sideNav > div').forEach(el=>el.classList.toggle('active',el.dataset.view==='overview'));document.querySelectorAll('.view').forEach(el=>el.classList.toggle('hidden',el.id!=='view-overview'))}
async function refresh(){
  const requestSeq=++residentRequestSeq,version=sessionVersion;
  try{
    if(!me)return;
    if(me.role==='admin'){
      residents=[];visits=[];
      const s=await api('/api/admin/summary');
      if(version!==sessionVersion||requestSeq!==residentRequestSeq)return;
      $('residentCount').textContent=s.communities.reduce((a,c)=>a+c.active_resident_count,0);
      $('visitCount').textContent='—';
      $('residentRows').innerHTML='<tr><td colspan="5">管理员仅能查看聚合指标；居民个人信息不在此处显示。</td></tr>';
      $('visitRows').innerHTML='<div class="empty">管理员不处理具体随访事项</div>';
      $('newResident').disabled=true;$('provisionResidentView').disabled=true;$('createResidentView').disabled=true;return;
    }
    $('newResident').disabled=false;$('provisionResidentView').disabled=false;$('createResidentView').disabled=false;
    const visitStatsSeq=++visitStatsRequestSeq;
    const [rs,vs,stats,visitStats]=await Promise.all([
      api('/api/community/residents?limit=50&offset='+residentOffset+'&q='+encodeURIComponent(residentQuery)),
      api('/api/community/visits?status=planned&limit=8&offset=0'),
      api('/api/community/stats'),api('/api/community/visits/stats')
    ]);
    if(version!==sessionVersion||requestSeq!==residentRequestSeq)return;
    residents=rs;residentHasMore=rs.length===50;visits=vs;
    $('residentCount').textContent=stats.resident_count;
    if(visitStatsSeq===visitStatsRequestSeq)applyVisitStats(visitStats);
    renderResidents();renderResidentsTable();renderVisits();
    if(currentView==='stats')loadStats();
    if(currentView==='visits')await loadVisitList();
  }catch(e){if(version===sessionVersion&&requestSeq===residentRequestSeq&&e.message!=='会话已切换')notify(e.message)}
}
$('today').textContent=new Date().toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'});
function openStaffReport(id){if(me?.role!=='staff')return;const r=residents.find(x=>x.id===id&&x.consent_active);if(!r)return notify('请先选择已授权的居民');window.anlinReports?.open(r.id,r.name)}
function renderResidents(){if(!me||me.role!=='staff')return;$('residentRows').innerHTML=residents.map(r=>`<tr><td><span class="name-cell"><span class="avatar">${clean(r.name.slice(0,1))}</span>${clean(r.name)}</span></td><td>${r.birth_year}</td><td>${r.owner_user_id?'自主建档':'协助登记'}</td><td><span class="tag">${r.consent_active?'已授权':'未生效'}</span></td><td><button class="btn btn-sm" data-act="show-vital" data-id="${r.id}">＋ 记录指标</button> <button class="btn btn-sm" data-act="open-staff-report" data-id="${r.id}">报告导入</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">没有符合条件的居民</td></tr>'}
function renderVisits(){$('visitRows').innerHTML=visits.slice(0,8).map(v=>{const r=residents.find(x=>x.id===v.resident_id);return `<div class="visit"><div class="visit-date">${clean(v.due_date.slice(5))}</div><div class="visit-meta"><div class="visit-name">${clean(r?.name||'居民档案 #'+v.resident_id)}</div><div class="visit-desc">${v.status==='done'?'走访已完成':'计划走访 · 待完成'}</div></div>${v.status==='done'?'<span class="tag">已完成</span>':`<button class="btn btn-sm" data-act="finish-visit" data-id="${v.id}"">完成</button>`}</div>`}).join('')||'<div class="empty">暂无随访任务</div>'}
function modal(title,desc,fields,onSubmit){$('modalTitle').textContent=title;$('modalDesc').textContent=desc;$('modalFields').innerHTML=fields;$('overlay').style.display='flex';$('modalSubmit').disabled=false;$('modalForm').onsubmit=async e=>{e.preventDefault();if($('modalSubmit').disabled)return;$('modalSubmit').disabled=true;try{await onSubmit(new FormData(e.target));closeModal();await refresh();if(currentView==='residents')await loadResidentsView();notify('保存成功')}catch(err){notify(err.message)}finally{$('modalSubmit').disabled=false}}}
function closeModal(){$('overlay').style.display='none';$('modalForm').onsubmit=null}
let fieldSeq=0;function field(label,name,type='text',extra=''){const id='f_'+(++fieldSeq);return `<div class="field"><label for="${id}">${label}</label><input id="${id}" name="${name}" type="${type}" required ${extra}></div>`}
function showCreate(){if(me?.role!=='staff')return;modal('登记线下居民档案','只有在取得居民明确知情同意、并有可追溯线下证据时，才能协助建档。纸质同意需由另一名工作人员复核后才能访问档案。',field('居民姓名','name')+field('出生年份','birth_year','number','min="1900" max="2100"')+field('联系电话','phone','tel','required')+field('线下知情同意记录编号','evidence_ref','text','minlength="5" placeholder="例如 PAPER-2026-001"')+'<label class="checkbox"><input name="consent" type="checkbox" required>我确认已取得居民的明确知情同意，并已经按机构制度留存证明。</label>',async d=>{await api('/api/community/residents','POST',{name:d.get('name').trim(),birth_year:Number(d.get('birth_year')),phone:d.get('phone').trim(),community_id:me.community_id,sex:'undisclosed',consent_confirmed:d.has('consent'),evidence_ref:d.get('evidence_ref').trim()})})}
function showProvision(){if(me?.role!=='staff')return;modal('开通居民登录账号','仅创建登录账号。若社区已有线下档案，居民登录后可申请现场核验认领；不要重复登记档案。',field('登录账号','username','text','minlength="4" maxlength="40" autocomplete="off"')+field('居民称呼','display_name')+field('初始密码','initial_password','password','minlength="10" autocomplete="new-password"'),async d=>{await api('/api/community/accounts','POST',{username:d.get('username').trim(),display_name:d.get('display_name').trim(),initial_password:d.get('initial_password')})})}
function localDateTimeValue(date=new Date()){
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}
function showVital(id){
  if(me?.role!=='staff')return;
  const r=residents.find(x=>x.id===id),localNow=localDateTimeValue();
  modal('记录健康指标','为 '+(r?.name||'居民')+' 手动录入数据。请填写实际测量时间（当前设备时区），此记录不属于医疗诊断。',
    '<div class="field"><label for="metricType">指标类型</label><select name="type" id="metricType"><option value="blood_pressure">血压（mmHg）</option><option value="blood_glucose">血糖（mmol/L）</option><option value="weight">体重（kg）</option></select></div><div id="metricFields"></div>'+field('实际测量时间','measured_at','datetime-local','value="'+localNow+'" max="'+localNow+'"'),async d=>{
      const measured=new Date(d.get('measured_at'));
      if(!d.get('measured_at')||Number.isNaN(measured.getTime()))throw new Error('请填写有效的实际测量时间');
      if(measured.getTime()>Date.now()+60000)throw new Error('实际测量时间不能晚于现在');
      const type=d.get('type'),payload={type,measured_at:measured.toISOString()};
      if(type==='blood_pressure'){payload.systolic=Number(d.get('systolic'));payload.diastolic=Number(d.get('diastolic'))}
      if(type==='blood_glucose')payload.glucose_mmol_l=Number(d.get('glucose'));
      if(type==='weight')payload.weight_kg=Number(d.get('weight'));
      await api(`/api/residents/${id}/vitals`,'POST',payload);
    });
  $('metricType').addEventListener('change',renderMetricFields);renderMetricFields();
}
function renderMetricFields(){const t=$('metricType')?.value;if(!$('metricFields'))return;$('metricFields').innerHTML=t==='blood_pressure'?field('收缩压','systolic','number','min="40" max="300"')+field('舒张压','diastolic','number','min="20" max="200"'):t==='blood_glucose'?field('血糖 mmol/L','glucose','number','step="0.1" min="0.5" max="50"'):field('体重 kg','weight','number','step="0.1" min="5" max="400"')}
function showVisit(){if(me?.role!=='staff')return notify('管理员不处理随访');if(!residents.length)return notify('请先登记居民');let opts=residents.map(r=>`<option value="${r.id}">${clean(r.name)}</option>`).join('');modal('新增线下随访','为居民安排一次社区走访。','<div class="field"><label>居民</label><select name="resident_id">'+opts+'</select></div>'+field('走访日期','due_date','date')+'<div class="field"><label>工作备注（选填）</label><textarea name="note" maxlength="500" rows="3"></textarea></div>',async d=>{await api('/api/community/visits','POST',{resident_id:Number(d.get('resident_id')),due_date:d.get('due_date'),note:d.get('note')||null})})}
async function finishVisit(id){if(!(await window.AnlinCore.confirmDialog('确认已完成本次走访？')))return;try{await api('/api/community/visits/'+id+'/finish','PATCH',{note:'线下走访已完成'});await refresh();notify('已标记完成')}catch(e){notify(e.message)}}



/* ------- 多视图工作台 ------- */
function switchView(v){
  if(!me){logout();return}
  if(currentView==='settings'&&v!=='settings'){
    clearAdminAccounts();$('acctInfo').textContent='—';
    for(const id of ['pwCur','pwNew','mfaPassword','mfaExistingCode'])$(id).value='';
    $('mfaSecret').replaceChildren();$('mfaSecret').classList.add('hidden');
  }
  if(currentView==='visits'&&v!=='visits'){visitRequestSeq++;visitLoading=false}
  currentView=v;
  document.querySelectorAll('#sideNav > div').forEach(function(el){el.classList.toggle('active',el.dataset.view===v)});
  document.querySelectorAll('.view').forEach(function(el){el.classList.toggle('hidden',el.id!=='view-'+v)});
  if(v==='residents')loadResidentsView();
  if(v==='visits')loadVisitList();
  if(v==='stats')loadStats();
  if(v==='settings')renderSettings();
  if(v==='risk')loadRiskView();
  if(v==='appointments')loadAppointmentsView();
}
let currentView='overview';
let adminUsers=[],adminCommunities=[],adminRequestSeq=0,adminActionBusy=new Set();
function clearAdminAccounts(){
  adminRequestSeq++;adminUsers=[];adminCommunities=[];adminActionBusy.clear();
  for(const id of ['adminUsername','adminDisplayName','adminInitialPassword'])$(id).value='';
  $('adminCommunity').replaceChildren();$('adminAccountRows').replaceChildren();
  $('adminCreateBtn').disabled=false;$('adminAccountsPanel').classList.add('hidden');
}
window.addEventListener('anlin-reports-confirmed',e=>{if(me?.role==='staff'&&residents.some(r=>r.id===e.detail?.residentId))refresh()});
function renderResidentsTable(){
  if(me.role!=='staff')return;
  const box=document.getElementById('residentTable2');
  if(!box){setTimeout(renderResidentsTable,300);return}
  box.innerHTML=residents.map(r=>'<tr><td><span class="name-cell"><span class="avatar">'+clean(r.name.slice(0,1))+'</span>'+clean(r.name)+'</span></td><td>'+r.birth_year+'</td><td>'+(r.owner_user_id?'自主建档':'协助登记')+'</td><td><span class="tag">'+(r.consent_active?'已授权':'未生效')+'</span></td><td><button class="btn btn-sm" data-act="show-vital" data-id="'+r.id+'">＋ 记录指标</button> <button class="btn btn-sm" data-act="open-staff-report" data-id="'+r.id+'">报告导入</button> <button class="btn btn-sm btn-danger" data-act="withdraw-consent" data-id="'+r.id+'">撤回授权</button></td></tr>').join('')||'<tr><td colspan="5" class="empty">没有符合条件的居民</td></tr>';
  $('residentPaging').innerHTML='<button class="btn btn-sm" '+(residentOffset?'':'disabled')+' data-act="page-residents" data-step="-1">上一页</button><span class="sub">第 '+(Math.floor(residentOffset/50)+1)+' 页</span><button class="btn btn-sm" '+(residentHasMore?'':'disabled')+' data-act="page-residents" data-step="1">下一页</button>';
}
function searchResidents(q){residentQuery=q.trim();residentOffset=0;$('search').value=q;$('search2').value=q;clearTimeout(residentSearchTimer);residentSearchTimer=setTimeout(refresh,250)}
function pageResidents(step){if(step>0&&!residentHasMore)return;residentOffset=Math.max(0,residentOffset+step*50);refresh()}
async function loadResidentsView(){
  if(!me){return}
  if(me.role!=='staff'){
    document.getElementById('residentTable2').innerHTML='<tr><td colspan="5" class="empty">管理员不读取居民个人明细</td></tr>';
    document.getElementById('claimQueue').innerHTML='<div class="empty">工作人员专属队列</div>';
    document.getElementById('consentQueue').innerHTML='<div class="empty">工作人员专属队列</div>';
    return;
  }
  await refresh();loadClaims();loadConsentReviews();
}
async function withdrawConsent(id){
  const ref=await window.AnlinCore.promptDialog('撤回该居民的社区护理授权：请输入机构留存的线下撤回证据编号：');
  if(!ref)return;
  try{await api('/api/residents/'+id+'/consents','POST',{granted:false,evidence_ref:ref});notify('线下撤回证据已提交，待另一名工作人员复核；社区访问现已暂停');await loadResidentsView()}catch(e){notify(e.message)}
}
async function loadClaims(){
  const version=sessionVersion;
  try{
    const list=await api('/api/community/claim-requests?status=pending');
    if(version!==sessionVersion||!me)return;
    document.getElementById('claimCount').textContent=list.length;
    document.getElementById('claimQueue').innerHTML=list.length?list.map(c=>'<div class="queue-item"><b>'+clean(c.requester_name)+'</b><span>申请认领档案 #'+c.resident_id+'</span><span class="mono">姓名 '+(c.name_matched?'✓':'✗')+' · 电话 '+(c.phone_matched?'✓':'✗')+'</span><span class="sub">'+clean(c.stated_name)+' / '+clean(c.stated_phone||'未填')+'</span><span class="grow"></span><button class="btn btn-sm btn-primary" data-act="review-claim" data-id="'+c.id+'" data-approve="true">批准</button><button class="btn btn-sm btn-danger" data-act="review-claim" data-id="'+c.id+'" data-approve="false">拒绝</button></div>').join(''):'<div class="empty">暂无待核验申请</div>';
  }catch(e){if(!me)return;document.getElementById('claimQueue').innerHTML='<div class="empty">'+clean(e.message)+'</div>'}
}
async function reviewClaim(id,approve){
  try{await api('/api/community/claim-requests/'+id+'/review','POST',{approve:approve,note:null});notify(approve?'已批准认领，居民即获档案归属':'已拒绝认领');await refresh();await loadClaims()}catch(e){notify(e.message)}
}
async function loadConsentReviews(){
  const version=sessionVersion;
  try{
    const list=await api('/api/community/consent-reviews');
    if(version!==sessionVersion||!me)return;
    document.getElementById('consentCount').textContent=list.length;
    document.getElementById('consentQueue').innerHTML=list.length?list.map(ev=>'<div class="queue-item"><span class="mono">'+clean(ev.evidence_ref||'—')+'</span><span>档案 #'+ev.resident_id+' · '+(ev.granted?'授予同意':'撤回同意')+' · '+fmtTime(ev.created_at)+'</span><span class="grow"></span>'+(ev.actor_id===me.id?'<span class="sub">需其他工作人员复核</span>':'<button class="btn btn-sm btn-primary" data-act="review-consent" data-id="'+ev.id+'" data-approve="true">通过</button><button class="btn btn-sm btn-danger" data-act="review-consent" data-id="'+ev.id+'" data-approve="false">驳回</button>')+'</div>').join(''):'<div class="empty">暂无待复核证据</div>';
  }catch(e){if(!me)return;document.getElementById('consentQueue').innerHTML='<div class="empty">'+clean(e.message)+'</div>'}
}
async function reviewConsentEvent(id,approve){
  let note=null;
  if(!approve){const inp=await window.AnlinCore.promptDialog('驳回原因（可留空）：');if(inp===null)return;if(inp)note=inp}
  try{await api('/api/community/consent-events/'+id+'/review','POST',{approve:approve,note:note});notify(approve?'证据复核通过':'已驳回');await refresh();await loadConsentReviews()}catch(e){notify(e.message)}
}
function applyVisitStats(stats){
  visitTotals=stats;$('visitCount').textContent=stats.planned;
  $('visitSummary').textContent='待完成 '+stats.planned+' · 已完成 '+stats.done+' · 逾期 '+stats.overdue;
}
function renderVisitList(){
  $('visitList').innerHTML=visitItems.map(v=>{
    const r=residents.find(x=>x.id===v.resident_id);
    return '<div class="visit"><div class="visit-date">'+clean(v.due_date.slice(5))+'</div><div class="visit-meta"><div class="visit-name">'+clean(r?.name||'居民档案 #'+v.resident_id)+'</div><div class="visit-desc">'+(v.status==='done'?'走访已完成':'计划走访 · 待完成')+'</div></div>'+(v.status==='done'?'<span class="tag">已完成</span>':'<button class="btn btn-sm btn-primary" data-act="finish-visit" data-id="'+v.id+'">完成</button>')+'</div>';
  }).join('')||'<div class="empty">当前状态下暂无随访任务</div>';
  $('visitPageLabel').textContent='第 '+(Math.floor(visitOffset/25)+1)+' / '+Math.max(1,Math.ceil(visitTotal/25))+' 页 · 共 '+visitTotal+' 项';
  $('visitPrev').disabled=visitLoading||visitOffset===0;
  $('visitNext').disabled=visitLoading||visitOffset+25>=visitTotal;
}
async function loadVisitList(){
  if(!me)return;
  if(me.role!=='staff'){
    $('visitList').innerHTML='<div class="empty">管理员不处理具体随访事项</div>';
    $('visitStatus').disabled=true;$('visitSummary').textContent='';$('visitPageLabel').textContent='';
    $('visitPrev').disabled=true;$('visitNext').disabled=true;return;
  }
  const requestSeq=++visitRequestSeq,statsSeq=++visitStatsRequestSeq,version=sessionVersion,filter=visitFilter,offset=visitOffset;
  visitLoading=true;$('visitStatus').disabled=false;$('visitPrev').disabled=true;$('visitNext').disabled=true;
  $('visitList').innerHTML='<div class="empty">正在读取...</div>';
  try{
    const [items,stats]=await Promise.all([
      api('/api/community/visits?limit=25&offset='+offset+(filter==='all'?'':'&status='+filter)),
      api('/api/community/visits/stats')
    ]);
    if(version!==sessionVersion||requestSeq!==visitRequestSeq||currentView!=='visits')return;
    visitItems=items;visitTotal=filter==='all'?stats.total:stats[filter];
    if(statsSeq===visitStatsRequestSeq)applyVisitStats(stats);
    if(offset&&offset>=visitTotal){visitOffset=Math.max(0,Math.floor((visitTotal-1)/25)*25);return await loadVisitList()}
    visitLoading=false;renderVisitList();
  }catch(e){
    if(version!==sessionVersion||requestSeq!==visitRequestSeq||currentView!=='visits')return;
    visitLoading=false;visitItems=[];$('visitList').innerHTML='<div class="empty">'+clean(e.message)+'</div>';
    $('visitPrev').disabled=visitOffset===0;$('visitNext').disabled=true;
  }
}
function filterVisits(status){
  if(!['planned','done','all'].includes(status))return;
  visitFilter=status;visitOffset=0;$('visitStatus').value=status;return loadVisitList();
}
function pageVisits(step){
  if(visitLoading||(step>0&&visitOffset+25>=visitTotal)||(step<0&&!visitOffset))return;
  visitOffset=Math.max(0,visitOffset+step*25);return loadVisitList();
}
$('visitStatus').addEventListener('change',e=>filterVisits(e.target.value));
$('visitPrev').addEventListener('click',()=>pageVisits(-1));
$('visitNext').addEventListener('click',()=>pageVisits(1));
async function loadStats(){
  const requestSeq=++statsRequestSeq;
  const version=sessionVersion;
  const chips=document.getElementById('statChips'),chart=document.getElementById('statChart'),body=document.getElementById('latestBody');
  if(me.role==='admin'){
    try{
      const sm=await api('/api/admin/summary');
      if(version!==sessionVersion||requestSeq!==statsRequestSeq)return;
      chips.innerHTML=sm.communities.map(c=>'<section class="stat"><div class="stat-label">'+clean(c.name)+' <span class="chip">聚合</span></div><div class="value">'+c.active_resident_count+'</div><div class="foot">有效授权居民数</div></section>').join('');
      chart.innerHTML='<div class="empty">管理员视图仅提供社区级聚合计数</div>';
      body.innerHTML='<tr><td colspan="5" class="empty">管理员不读取个人明细</td></tr>';
    }catch(e){notify(e.message)}
    return;
  }
  try{
    const stats=await api('/api/community/health-stats?limit=25&offset='+statsOffset+'&q='+encodeURIComponent(statsQuery));
    if(requestSeq!==statsRequestSeq)return;
    const totals=stats.totals,byDay=Object.fromEntries(stats.daily_counts.map(d=>[d.date,d.count]));
    const rows=stats.residents.map(r=>({name:r.name,bp:r.blood_pressure?r.blood_pressure.systolic+'/'+r.blood_pressure.diastolic+' mmHg':'—',glu:r.blood_glucose?r.blood_glucose.glucose_mmol_l+' mmol/L':'—',wt:r.weight?r.weight.weight_kg+' kg':'—',n:r.record_count}));
    chips.innerHTML=[['血压记录',totals.blood_pressure],['血糖记录',totals.blood_glucose],['体重记录',totals.weight]].map(kv=>'<section class="stat"><div class="stat-label">'+kv[0]+' <span class="chip">本社区</span></div><div class="value">'+kv[1]+'</div><div class="foot">全部授权居民的记录总数</div></section>').join('');
    const days=[];for(let i=13;i>=0;i--){const d=new Date(Date.now()-i*86400000).toISOString().slice(0,10);days.push([d.slice(5),byDay[d]||0])}
    const max=Math.max(1,...days.map(d=>d[1]));const bw=420/14;
    chart.innerHTML='<svg class="statbar-svg" viewBox="0 0 420 156" role="img" aria-label="近14天测量次数柱状图">'+days.map((d,i)=>{const h=(118)*d[1]/max;const x=i*bw+8;return '<rect x="'+x+'" y="'+(140-Math.max(h,2))+'" width="'+(bw-14)+'" height="'+Math.max(h,2)+'" rx="4" fill="'+(i===13?'#F8533F':'#7A5CF0')+'" opacity="'+(i===13?1:.72)+'"></rect><text x="'+(x+(bw-14)/2)+'" y="153" font-size="9" fill="#8B929A" text-anchor="middle">'+d[0]+'</text>'+(d[1]?'<text x="'+(x+(bw-14)/2)+'" y="'+(132-h)+'" font-size="10" fill="#3A3C40" text-anchor="middle">'+d[1]+'</text>':'')}).join('')+'</svg>';
    body.innerHTML=rows.map(r=>'<tr><td><span class="name-cell"><span class="avatar">'+clean(r.name.slice(0,1))+'</span>'+clean(r.name)+'</span></td><td>'+clean(r.bp)+'</td><td>'+clean(r.glu)+'</td><td>'+clean(r.wt)+'</td><td>'+r.n+'</td></tr>').join('')||'<tr><td colspan="5" class="empty">暂无数据</td></tr>';
    $('statsPaging').innerHTML='<button class="btn btn-sm" '+(statsOffset?'':'disabled')+' data-act="page-stats" data-step="-1">上一页</button><span class="sub">第 '+(Math.floor(statsOffset/25)+1)+' 页 · 共 '+stats.total_residents+' 人</span><button class="btn btn-sm" '+(statsOffset+25<stats.total_residents?'':'disabled')+' data-act="page-stats" data-step="1">下一页</button>';
  }catch(e){notify(e.message)}
}
function searchStats(q){statsQuery=q.trim();statsOffset=0;clearTimeout(statsSearchTimer);statsSearchTimer=setTimeout(loadStats,250)}
function pageStats(step){statsOffset=Math.max(0,statsOffset+step*25);loadStats()}
function renderSettings(){
  const c=(me.community_id!=null)?('社区 #'+me.community_id):'—';
  const role=clean({staff:'社区工作人员',admin:'系统管理员',resident:'居民',family:'家庭成员'}[me.role]||me.role);
  document.getElementById('acctInfo').innerHTML='<div><b>'+clean(me.display_name)+'</b>（@'+clean(me.username)+'）</div><div>角色：'+role+'</div><div>所属：'+c+'</div>';
  if(me.role==='admin'){$('adminAccountsPanel').classList.remove('hidden');loadAdminAccounts()}
  else clearAdminAccounts();
}
async function loadAdminAccounts(){
  if(me?.role!=='admin'||currentView!=='settings')return;
  const seq=++adminRequestSeq,version=sessionVersion;
  $('adminAccountRows').innerHTML='<tr><td colspan="6">正在读取...</td></tr>';
  try{
    const [users,communities]=await Promise.all([api('/api/admin/users'),api('/api/communities')]);
    if(seq!==adminRequestSeq||version!==sessionVersion||currentView!=='settings')return;
    adminUsers=users;adminCommunities=communities;
    $('adminCommunity').innerHTML=communities.map(c=>`<option value="${c.id}">${clean(c.name)}</option>`).join('');
    renderAdminAccounts();
  }catch(e){if(seq===adminRequestSeq&&me?.role==='admin'&&currentView==='settings')$('adminAccountRows').innerHTML='<tr><td colspan="6">'+clean(e.message)+'</td></tr>'}
}
function renderAdminAccounts(){
  if(me?.role!=='admin'||currentView!=='settings')return;
  const names=new Map(adminCommunities.map(c=>[c.id,c.name]));
  const roleName={admin:'管理员',staff:'工作人员',resident:'居民',family:'家属'};
  $('adminAccountRows').innerHTML=adminUsers.map(u=>{
    const action=u.id===me.id||u.role==='admin'?'—':`<button class="btn btn-sm ${u.is_active?'btn-danger':''}" data-act="toggle-admin-account" data-id="${u.id}" data-activate="${!u.is_active}">${u.is_active?'停用':'启用'}</button>`;
    return `<tr><td>${clean(u.display_name)}<div class="sub">@${clean(u.username)}</div></td><td>${clean(roleName[u.role]||u.role)}</td><td>${clean(names.get(u.community_id)||'—')}</td><td>${u.is_active?'正常':'已停用'}</td><td>${u.mfa_enabled?'已开启':'未开启'}</td><td>${action}</td></tr>`;
  }).join('')||'<tr><td colspan="6">暂无账号</td></tr>';
}
async function createStaffAccount(){
  if(me?.role!=='admin'||currentView!=='settings')return;
  const btn=$('adminCreateBtn');if(btn.disabled)return;
  const body={username:$('adminUsername').value.trim(),display_name:$('adminDisplayName').value.trim(),community_id:Number($('adminCommunity').value),initial_password:$('adminInitialPassword').value};
  if(!body.username||!body.display_name||!Number.isInteger(body.community_id)||!body.community_id||!body.initial_password)return notify('请填写账号、称呼、社区和初始密码');
  btn.disabled=true;
  try{await api('/api/admin/users','POST',body);$('adminUsername').value='';$('adminDisplayName').value='';notify('工作人员账号已开通');await loadAdminAccounts()}
  catch(e){notify(e.message)}finally{$('adminInitialPassword').value='';btn.disabled=false}
}
async function toggleAdminAccount(id,activate){
  if(me?.role!=='admin'||currentView!=='settings'||adminActionBusy.has(id))return;
  const target=adminUsers.find(u=>u.id===id);
  if(!target||target.id===me.id||target.role==='admin'||target.is_active===activate)return;
  if(!activate&&!(await window.AnlinCore.confirmDialog(`停用 ${target.display_name}（@${target.username}）？其当前会话会立即失效。`)))return;
  adminActionBusy.add(id);
  try{await api(`/api/admin/users/${id}/${activate?'activate':'deactivate'}`,'POST');notify(activate?'账号已启用':'账号已停用');await loadAdminAccounts()}
  catch(e){notify(e.message)}finally{adminActionBusy.delete(id)}
}
$('adminCreateForm').addEventListener('submit',e=>{e.preventDefault();createStaffAccount()});
async function changePassword(){
  const cur=document.getElementById('pwCur').value,ne=document.getElementById('pwNew').value;
  if(!cur||!ne){notify('请填写当前密码与新密码');return}
  try{const out=await api('/api/auth/change-password','POST',{current_password:cur,new_password:ne});token=out.access_token;sessionChanged();document.getElementById('pwCur').value='';document.getElementById('pwNew').value='';notify('密码已更新，其他设备已全部登出')}catch(e){notify('修改失败：'+e.message)}
}
async function setupMFA(){
  const current_password=$('mfaPassword').value,totp_code=$('mfaExistingCode').value.trim();
  if(!current_password)return notify('请先填写当前密码');
  try{
    const out=await api('/api/auth/mfa/totp','POST',{current_password,...(totp_code?{totp_code}:{})});
    const box=document.getElementById('mfaSecret');box.classList.remove('hidden');
    box.innerHTML='<div class="helper">新密钥仅本次显示。将它添加到认证器，再输入新认证器产生的验证码以确认绑定：</div><div class="mono" style="display:inline-block;margin:8px 0;font-size:14px;letter-spacing:1px">'+clean(out.secret)+'</div><div class="helper" style="word-break:break-all">'+clean(out.uri)+'</div><div class="field"><label for="mfaConfirmCode">新认证器的6位验证码</label><input id="mfaConfirmCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6"></div><button class="btn btn-primary" data-act="confirm-mfa">确认绑定</button>';
    $('mfaExistingCode').value='';notify('请用新认证器验证码确认绑定');
  }catch(e){notify(e.message)}
}
async function confirmMFA(){
  const current_password=$('mfaPassword').value,totp_code=$('mfaConfirmCode')?.value.trim();
  if(!/^\d{6}$/.test(totp_code||''))return notify('请输入新认证器中的6位验证码');
  try{const out=await api('/api/auth/mfa/totp/confirm','POST',{current_password,totp_code});token=out.access_token;sessionChanged();$('mfaPassword').value='';$('mfaSecret').replaceChildren();$('mfaSecret').classList.add('hidden');notify('两步验证已启用')}
  catch(e){notify(e.message)}
}
async function disableMFA(){
  const current_password=$('mfaPassword').value,totp_code=$('mfaExistingCode').value.trim();
  if(!current_password||!/^\d{6}$/.test(totp_code))return notify('请填写当前密码和现有认证器的6位验证码');
  try{const out=await api('/api/auth/mfa/totp','DELETE',{current_password,totp_code});token=out.access_token;sessionChanged();$('mfaPassword').value='';$('mfaExistingCode').value='';$('mfaSecret').replaceChildren();$('mfaSecret').classList.add('hidden');notify('已关闭两步验证')}
  catch(e){notify(e.message)}
}
async function logoutAllDevices(){
  try{await api('/api/auth/logout-all','POST');logout();notify('已在所有设备退出登录')}catch(e){notify(e.message)}
}

/* ------- 风险预警 ------- */
const RISK_METRICS={blood_pressure:'血压',blood_glucose:'血糖',weight:'体重'};
let riskOffset=0;
async function loadRiskView(){
  const version=sessionVersion;
  const level=$('riskLevel').value,status=$('riskStatus').value;
  try{
    const qs=new URLSearchParams({status,limit:'25',offset:String(riskOffset)});
    if(level)qs.set('level',level);
    const data=await api(`/api/community/risk-alerts?${qs}`);
    if(version!==sessionVersion||currentView!=='risk')return;
    $('riskOpenCount').textContent=data.open_count;
    $('riskRows').innerHTML=data.items.length?data.items.map(a=>{
      const time=new Date(a.created_at);const when=isNaN(time)?'':time.toLocaleString('zh-CN');
      const action=a.acknowledged_at?'<span class="sub">已处理</span>':`<button class="btn btn-sm" data-act="ack-risk" data-id="${a.id}">标记已处理</button>`;
      const levelTag=a.level==='critical'?'<span class="chip" style="background:#FDECE8;color:#C2453A">需尽快关注</span>':'<span class="chip">关注</span>';
      return '<tr><td>'+(a.resident_name||('#'+a.resident_id))+'</td><td>'+(RISK_METRICS[a.metric]||a.metric)+'</td><td>'+levelTag+'</td><td>'+clean(a.value_text)+'</td><td>'+when+'</td><td class="sub" style="max-width:320px">'+clean(a.message)+'</td><td>'+action+'</td></tr>';
    }).join(''):'<tr><td colspan="7" class="empty">当前筛选下没有预警记录</td></tr>';
    $('riskPaging').innerHTML='<button class="btn btn-sm" '+(riskOffset?'':'disabled')+' data-act="page-risk" data-step="-1">上一页</button><span class="sub">第 '+(Math.floor(riskOffset/25)+1)+' 页</span><button class="btn btn-sm" '+(riskOffset+25>=data.total?'':'disabled')+' data-act="page-risk" data-step="1">下一页</button>';
  }catch(e){notify(e.message)}
}
async function ackRisk(id){
  try{await api(`/api/community/risk-alerts/${id}/ack`,'POST');notify('已标记为处理完成');loadRiskView()}catch(e){notify(e.message)}
}
/* ------- 预约管理 ------- */
const APPT_STATUS={pending:'待确认',confirmed:'已确认',completed:'已完成',cancelled:'已取消'};
let apptOffset=0;
async function loadAppointmentsView(){
  const version=sessionVersion;
  const status=$('apptStatus').value;
  try{
    const data=await api(`/api/community/appointments?status=${status}&limit=25&offset=${apptOffset}`);
    if(version!==sessionVersion||currentView!=='appointments')return;
    $('apptRows').innerHTML=data.items.length?data.items.map(a=>{
      const buttons=[];
      if(a.status==='pending')buttons.push(`<button class="btn btn-sm" data-act="appt-confirm" data-id="${a.id}">确认</button>`);
      if(a.status==='confirmed')buttons.push(`<button class="btn btn-sm" data-act="appt-complete" data-id="${a.id}">完成</button>`);
      if(a.status==='pending'||a.status==='confirmed')buttons.push(`<button class="btn btn-sm btn-danger" data-act="appt-cancel" data-id="${a.id}">取消</button>`);
      return '<tr><td>'+(a.resident_name||('#'+a.resident_id))+'</td><td>'+clean(a.kind_label)+'</td><td>'+clean(a.preferred_date)+' '+clean(a.time_slot_label)+'</td><td class="sub" style="max-width:220px">'+clean(a.note||'—')+'</td><td>'+(APPT_STATUS[a.status]||a.status)+'</td><td><div class="ar-row" style="gap:6px">'+buttons.join(' ')+'</div></td></tr>';
    }).join(''):'<tr><td colspan="6" class="empty">当前状态下没有预约</td></tr>';
    $('apptPaging').innerHTML='<button class="btn btn-sm" '+(apptOffset?'':'disabled')+' data-act="page-appt" data-step="-1">上一页</button><span class="sub">第 '+(Math.floor(apptOffset/25)+1)+' 页</span><button class="btn btn-sm" '+(apptOffset+25>=data.total?'':'disabled')+' data-act="page-appt" data-step="1">下一页</button>';
  }catch(e){notify(e.message)}
}
function apptAction(id,action,label,needNote){
  const run=async note=>{
    try{await api(`/api/community/appointments/${id}/${action}`,'POST',{response_note:note||null});notify(label+'成功');loadAppointmentsView()}catch(e){notify(e.message)}
  };
  if(needNote){modal(label+'预约','可填写给居民看的回复说明（选填）。',field('回复说明','response_note','text','maxlength="300"'),async d=>{await run(d.get('response_note'))})}
  else run(null);
}
/* 员工登录后一次性弹窗：未处理预警。 */
async function maybeShowRiskPopup(){
  try{
    if(!me||me.role!=='staff'||sessionStorage.getItem('anlinRiskStaffPopup'))return;
    const data=await api('/api/community/risk-alerts?status=open&limit=4');
    if(!data.items.length)return;
    sessionStorage.setItem('anlinRiskStaffPopup','1');
    const lines=data.items.map(a=>`${a.resident_name||('#'+a.resident_id)}：${RISK_METRICS[a.metric]||a.metric} ${a.value_text}（${a.level==='critical'?'需尽快关注':'关注'}）`);
    await window.AnlinCore.confirmDialog('有 '+data.open_count+' 条未处理的风险预警：'+'\n· '+lines.join('\n· ')+(data.open_count>4?'\n…其余请在风险预警板块查看':'')+'\n\n以上来自常见参考范围，不构成医疗诊断。',{okLabel:'去处理',danger:data.items.some(a=>a.level==='critical')});
    switchView('risk');
  }catch(e){}
}

/* ---------- data-act 事件注册与原 oninput/overlay 绑定（CSP script-src 'self'） ---------- */
window.AnlinCore.bindActions({
  'switch-view': ds => switchView(ds.view),
  'logout': () => logout(),
  'show-create': () => showCreate(),
  'show-visit': () => showVisit(),
  'show-provision': () => showProvision(),
  'show-vital': ds => showVital(Number(ds.id)),
  'open-staff-report': ds => openStaffReport(Number(ds.id)),
  'finish-visit': ds => finishVisit(Number(ds.id)),
  'withdraw-consent': ds => withdrawConsent(Number(ds.id)),
  'page-residents': ds => pageResidents(Number(ds.step)),
  'review-claim': ds => reviewClaim(Number(ds.id), ds.approve === 'true'),
  'review-consent': ds => reviewConsentEvent(Number(ds.id), ds.approve === 'true'),
  'page-stats': ds => pageStats(Number(ds.step)),
  'toggle-admin-account': ds => toggleAdminAccount(Number(ds.id), ds.activate === 'true'),
  'change-password': () => changePassword(),
  'setup-mfa': () => setupMFA(),
  'confirm-mfa': () => confirmMFA(),
  'disable-mfa': () => disableMFA(),
  'logout-all': () => logoutAllDevices(),
  'close-modal': () => closeModal(),
  'ack-risk': ds => ackRisk(Number(ds.id)),
  'page-risk': ds => {riskOffset=Math.max(0,riskOffset+Number(ds.step)*25);loadRiskView()},
  'page-appt': ds => {apptOffset=Math.max(0,apptOffset+Number(ds.step)*25);loadAppointmentsView()},
  'appt-confirm': ds => apptAction(Number(ds.id),'confirm','确认',true),
  'appt-complete': ds => apptAction(Number(ds.id),'complete','完成',false),
  'appt-cancel': ds => apptAction(Number(ds.id),'cancel','取消',false),
});
window.AnlinCore.bindInputActions({
  'search-residents': v => searchResidents(v),
  'search-stats': v => searchStats(v),
});
(() => {
  const overlay = $('overlay');
  if (overlay) overlay.addEventListener('click', e => { if (e.target === overlay) closeModal() });
  const form = document.querySelector('.login-shade form');
  if (form) form.addEventListener('submit', e => { e.preventDefault(); login() });
  // keyboard access for the data-act nav items (replaces removed inline onkeydown)
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = e.target instanceof Element ? e.target.closest('[data-act="switch-view"]') : null;
    if (!el) return;
    e.preventDefault();
    switchView(el.dataset.view);
  });
  for (const [id, handler] of [['riskLevel', () => {riskOffset=0;loadRiskView()}],
                               ['riskStatus', () => {riskOffset=0;loadRiskView()}],
                               ['apptStatus', () => {apptOffset=0;loadAppointmentsView()}]]) {
    const el = $(id);
    if (el) el.addEventListener('change', handler);
  }
})();
// expose for tests and sibling modules that reach the shared runtime via window
Object.assign(window, {api, notify});
