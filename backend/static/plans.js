/* Recurring plan UI. No health information is retained in browser storage. */
(() => {
  'use strict';
  let state = null, generation = 0;
  const names = {active:'进行中',paused:'已暂停',ended:'已结束',pending:'待完成',completed:'已完成',cancelled:'已取消'};
  const days = ['一','二','三','四','五','六','日'];
  const node = (tag, cls, text) => {const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n;};
  const add = (parent,...children) => {children.forEach(c=>parent.appendChild(c)); return parent;};
  const button = (text, action, cls='ap-button') => {const b=node('button',cls,text);b.type='button';b.addEventListener('click',action);return b;};
  const alive = s => state === s && s.generation === generation;
  const dateInZone = zone => {const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}`;};
  const when = (value,zone) => new Intl.DateTimeFormat('zh-CN',{timeZone:zone,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
  function field(label, input) {const wrap=node('label','ap-field');add(wrap,node('span','',label),input);return wrap;}
  function input(type,value='') {const n=node('input');n.type=type;n.value=value;return n;}
  function select(options) {const n=node('select');options.forEach(([value,label])=>{const o=node('option','',label);o.value=value;add(n,o);});return n;}
  function message(s,text,error=false) {if(!alive(s))return;s.message.textContent=text;s.message.className='ap-message'+(error?' ap-error':'');}
  function reset() {generation++;if(state){state.container.replaceChildren();state=null;}}
  async function refresh(s) {
    const seq=++s.sequence;message(s,'正在更新计划与任务…');
    try {
      const [plans,tasks]=await Promise.all([s.request(`/api/residents/${s.residentId}/plans`),s.request(`/api/residents/${s.residentId}/plan-occurrences?days=7&status=${s.filter}&limit=20&offset=${s.offset}`)]);
      if(!alive(s)||seq!==s.sequence)return;
      if(s.offset>0&&s.offset>=tasks.total){s.offset=Math.max(0,Math.floor((tasks.total-1)/20)*20);return refresh(s);}
      s.plans=plans;s.tasks=tasks;renderPlans(s);renderTasks(s);message(s,'');
    } catch(e) {if(alive(s)&&seq===s.sequence)message(s,e.message||'暂时无法读取，请稍后重试。',true);}
  }
  async function mutate(s,path,body,success) {
    if(!alive(s)||s.busy)return false;
    s.busy=true;s.container.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      await s.request(path,'POST',body);
      if(!alive(s))return false;
      await refresh(s);if(!alive(s))return false;
      message(s,success);s.notify?.(success);return true;
    } catch(e) {if(alive(s))message(s,e.message||'操作未完成，请检查后重试。',true);return false;}
    finally {if(alive(s)){s.busy=false;s.container.querySelectorAll('button').forEach(b=>b.disabled=false);updatePaging(s);}}
  }
  function planAction(s,p,action) {
    const text=action==='pause'?'暂停后，尚未到时的任务会取消；已经到期的待办保留。确认暂停？':action==='end'?'结束后不可恢复，尚未到时的任务会取消，已经到期的待办保留。确认结束？':null;
    if(text&&!window.confirm(text))return;
    mutate(s,`/api/residents/${s.residentId}/plans/${p.id}/${action}`,undefined,{pause:'计划已暂停',resume:'计划已恢复',end:'计划已结束'}[action]);
  }
  function renderPlans(s) {
    s.planList.replaceChildren();
    if(!s.plans.length){add(s.planList,node('p','ap-muted','还没有周期计划。添加后，每次任务会独立记录。'));return;}
    for(const p of s.plans){
      const row=node('article','ap-plan'),info=node('div','ap-grow'),top=node('div','ap-row');
      add(top,node('strong','',p.title),node('span','ap-status',names[p.status]));
      const frequency=p.frequency==='daily'?'每天':'每周'+p.weekdays.map(d=>days[d]).join('、');
      add(info,top,node('p','ap-muted',`${frequency} ${p.local_time} · ${p.timezone}`),node('p','ap-muted',`${p.start_date} 起${p.end_date?'，至 '+p.end_date:'，无结束日期'}`));add(row,info);
      if(p.status!=='ended'){const actions=node('div','ap-actions');add(actions,button(p.status==='active'?'暂停':'恢复',()=>planAction(s,p,p.status==='active'?'pause':'resume')),button('结束',()=>planAction(s,p,'end'),'ap-button ap-quiet'));add(row,actions);}
      add(s.planList,row);
    }
  }
  function updatePaging(s) {if(!s.prev||!s.next||!s.tasks)return;s.prev.disabled=s.busy||s.offset===0;s.next.disabled=s.busy||s.offset+s.tasks.items.length>=s.tasks.total;}
  function renderTasks(s) {
    s.taskList.replaceChildren();s.paging.replaceChildren();
    if(!s.tasks.items.length)add(s.taskList,node('p','ap-muted','这个范围内暂无任务。延后到七天以后的任务会在临近时出现。'));
    for(const t of s.tasks.items){
      const row=node('article','ap-task'),info=node('div','ap-grow');
      const late=t.status==='pending'&&new Date(t.due_at).getTime()<Date.now();
      add(info,node('strong','',t.title),node('p','ap-muted',`${when(t.due_at,t.timezone)} · ${t.timezone}`),node('span',late?'ap-status ap-overdue':'ap-status',late?'已到期 · 待完成':names[t.status]));
      if(t.due_at!==t.scheduled_at)add(info,node('p','ap-muted',`原安排：${when(t.scheduled_at,t.timezone)}`));
      add(row,info);
      if(t.status==='pending'){
        const actions=node('div','ap-actions'),base=`/api/residents/${s.residentId}/plan-occurrences/${t.id}/`;
        add(actions,button('完成',()=>mutate(s,base+'complete',undefined,'已记录完成'),'ap-button ap-primary'),button('延后1小时',()=>mutate(s,base+'snooze',{due_at:new Date(Math.max(Date.now(),new Date(t.due_at).getTime())+3600000).toISOString()},'任务已延后')),button('取消本次',()=>{if(window.confirm('取消这一次任务？后续周期计划仍会继续。'))mutate(s,base+'cancel',undefined,'本次任务已取消');},'ap-button ap-quiet'));add(row,actions);
      }
      add(s.taskList,row);
    }
    s.prev=button('上一页',()=>{s.offset=Math.max(0,s.offset-20);refresh(s);});s.next=button('下一页',()=>{s.offset+=20;refresh(s);});
    add(s.paging,s.prev,node('span','ap-muted',`第 ${Math.floor(s.offset/20)+1} 页 · 共 ${s.tasks.total} 项`),s.next);updatePaging(s);
  }
  function buildForm(s) {
    const details=node('details','ap-create'),summary=node('summary','','＋ 添加周期计划');add(details,summary);
    const form=node('form','ap-form'),title=input('text'),type=select([['measure','测量'],['questionnaire','问卷'],['followup','随访'],['general','其他日常安排']]);title.required=true;title.maxLength=100;title.placeholder='例如：晚饭后记录一次测量';
    const frequency=select([['daily','每天'],['weekly','每周指定日期']]),localTime=input('time','08:00'),zone=select([['Asia/Shanghai','北京时间 · Asia/Shanghai'],['UTC','协调世界时 · UTC'],['Asia/Tokyo','东京 · Asia/Tokyo'],['America/New_York','纽约 · America/New_York'],['Europe/London','伦敦 · Europe/London']]);localTime.required=true;
    const start=input('date',dateInZone('Asia/Shanghai')),end=input('date');start.required=true;
    const weekdays=node('fieldset','ap-weekdays');add(weekdays,node('legend','','每周哪几天'));const checks=days.map((day,i)=>{const c=input('checkbox');c.value=String(i);add(weekdays,field('周'+day,c));return c;});weekdays.hidden=true;
    frequency.addEventListener('change',()=>{weekdays.hidden=frequency.value!=='weekly';});
    zone.addEventListener('change',()=>{start.value=dateInZone(zone.value);});
    const grid=node('div','ap-form-grid');add(grid,field('计划名称',title),field('事项类型',type),field('重复方式',frequency),field('当地时间',localTime),field('时区',zone),field('开始日期',start),field('结束日期（可选）',end));
    const submit=node('button','ap-button ap-primary','保存计划');submit.type='submit';
    add(form,grid,weekdays,node('p','ap-muted','每次只生成今天起七个当地日期的任务。暂停不会清除已到期任务；恢复不会恢复您手动取消的任务。'),submit);
    form.addEventListener('submit',async e=>{e.preventDefault();if(s.busy||!alive(s))return;const chosen=frequency.value==='weekly'?checks.filter(c=>c.checked).map(c=>Number(c.value)):[];if(frequency.value==='weekly'&&!chosen.length){message(s,'请至少选择一个星期。',true);return;}
      const ok=await mutate(s,`/api/residents/${s.residentId}/plans`,{title:title.value.trim(),type:type.value,frequency:frequency.value,weekdays:chosen,local_time:localTime.value,timezone:zone.value,start_date:start.value,end_date:end.value||null},'周期计划已保存');
      if(ok&&alive(s)){title.value='';details.open=false;}
    });add(details,form);return details;
  }
  async function mount(container,options) {
    if(!container||!options||typeof options.request!=='function')throw new Error('计划模块需要容器和请求函数');
    const residentId=Number(options.residentId);if(!Number.isInteger(residentId)||residentId<1)throw new Error('居民档案无效');
    reset();const root=node('section','anlin-plans');
    const s={container,residentId,request:options.request,notify:options.notify,generation,sequence:0,busy:false,offset:0,filter:'pending',plans:[],tasks:null};state=s;
    const head=node('div','ap-heading');add(head,node('div','', '周期健康计划'),button('刷新',()=>refresh(s),'ap-button ap-quiet'));
    s.message=node('p','ap-message');s.message.setAttribute('role','status');s.message.setAttribute('aria-live','polite');
    s.planList=node('div','ap-plan-list');s.taskList=node('div','ap-task-list');s.paging=node('div','ap-paging');
    const filter=select([['pending','待完成'],['completed','已完成'],['cancelled','已取消'],['all','全部状态']]);filter.addEventListener('change',()=>{s.filter=filter.value;s.offset=0;refresh(s);});
    add(root,head,node('p','ap-muted','为日常安排设置重复计划。任务只在站内显示，不会发送短信或系统推送；完成标记由您确认。'),s.message,buildForm(s),s.planList);
    const taskHead=node('div','ap-row');add(taskHead,node('h3','','未来七天与逾期待办'),field('任务状态',filter));add(root,taskHead,s.taskList,s.paging);add(container,root);await refresh(s);
  }
  window.AnlinPlans={mount,reset};window.addEventListener('anlin-session-change',reset);
})();
