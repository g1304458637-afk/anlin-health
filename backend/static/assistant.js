(function () {
  'use strict';
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  };
  const host = el('div', 'anlin-assistant');
  host.hidden = true;
  const launch = el('button', 'aa-launch', '问答助手');
  launch.type = 'button'; launch.setAttribute('aria-expanded', 'false');
  launch.setAttribute('aria-controls', 'anlinAssistantPanel');
  const panel = el('section', 'aa-panel');
  panel.id = 'anlinAssistantPanel'; panel.hidden = true;
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', '安邻问答助手');
  const head = el('div', 'aa-head');
  const title = el('div');
  const titleLabel = el('span', '', '安邻问答助手');
  const status = el('small', 'aa-status', '正在连接'); title.append(titleLabel, status);
  const close = el('button', 'aa-close', '×'); close.type = 'button'; close.setAttribute('aria-label', '关闭助手');
  head.append(title, close);
  const feed = el('div', 'aa-feed'); feed.setAttribute('aria-live', 'polite'); feed.setAttribute('role', 'log');
  const suggestions = el('div', 'aa-suggestions');
  const reportContext = el('div', 'aa-report-context'); reportContext.hidden = true;
  const reportChip = el('span', 'aa-report-chip');
  const exitReport = el('button', 'aa-report-exit', '退出报告问答'); exitReport.type = 'button';
  reportContext.append(reportChip, exitReport);
  const permission = el('label', 'aa-permission');
  const allow = document.createElement('input'); allow.type = 'checkbox';
  const permissionText = el('span'); permission.append(allow, permissionText);
  const reportPermission = el('label', 'aa-permission aa-report-permission'); reportPermission.hidden = true;
  const allowReport = document.createElement('input'); allowReport.type = 'checkbox';
  reportPermission.append(allowReport, el('span', '', '允许将这份已确认报告的字段、问题和本次对话发送至 DeepSeek（仅当前报告与本次会话）'));
  const form = el('form', 'aa-form');
  const input = document.createElement('textarea'); input.rows = 2; input.maxLength = 2000;
  input.placeholder = '例如：怎么授权家人查看？'; input.setAttribute('aria-label', '输入问题');
  const send = el('button', 'aa-send', '发送'); send.type = 'submit';
  const clear = el('button', 'aa-clear', '清空对话'); clear.type = 'button';
  const foot = el('div', 'aa-foot', '问题与获准查询的结果会发送至 DeepSeek。请勿输入身份证、密码等信息。');
  form.append(input, send); panel.append(head, reportContext, feed, suggestions, permission, reportPermission, form, clear, foot);
  host.append(launch, panel); document.body.appendChild(host);
  let history = [], identity = '', generation = 0, controller = null, configured = false, busy = false;
  let mode = 'general', selectedReport = null;
  const auth = () => typeof window.anlinAssistantAuth === 'function' ? window.anlinAssistantAuth() : null;
  const append = (who, text) => {
    const node = el('div', 'aa-message aa-' + who, text); feed.appendChild(node); feed.scrollTop = feed.scrollHeight; return node;
  };
  const reset = () => {
    generation++; if (controller) controller.abort(); controller = null;
    history = []; feed.replaceChildren(); input.value = ''; busy = false;
    send.disabled = !configured; send.textContent = '发送';
    append('assistant', mode === 'report' ? '请先单独勾选报告问答许可。回答只依据这份已确认报告的字段和来源，不替代医生诊断。' : '我可以帮您找到功能、了解操作步骤。需要查询待办或汇总时，请先勾选下方允许。');
  };
  const updateMode = () => {
    const report = mode === 'report';
    titleLabel.textContent = report ? '报告问答' : '安邻问答助手';
    input.placeholder = report ? '例如：这份报告的血糖是多少？' : '例如：怎么授权家人查看？';
    reportContext.hidden = !report; suggestions.hidden = report;
    panel.className = report ? 'aa-panel aa-report-mode' : 'aa-panel';
    permission.hidden = report; reportPermission.hidden = !report;
    reportChip.textContent = report && selectedReport ? `${selectedReport.filename} · 版本 ${selectedReport.revision}` : '';
    foot.textContent = report ? '仅在勾选后发送本报告已确认字段、问题和本次对话至 DeepSeek。来源摘录仅在本地显示供核查，不能替代医生诊断。' : '问题与获准查询的结果会发送至 DeepSeek。请勿输入身份证、密码等信息。';
  };
  const exitReportMode = () => {
    mode = 'general'; selectedReport = null; allowReport.checked = false; allow.checked = false;
    updateMode(); reset();
  };
  const setOpen = (open) => {
    panel.hidden = !open; launch.setAttribute('aria-expanded', String(open));
    if (open) input.focus(); else launch.focus();
  };
  launch.onclick = () => setOpen(panel.hidden);
  close.onclick = () => setOpen(false);
  panel.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
  clear.onclick = () => { reset(); input.focus(); };
  allow.onchange = () => { reset(); append('assistant', allow.checked ? '本次会话已允许只读查询当前身份的数据；您可以随时取消。' : '已取消数据查询许可并清空对话。'); };
  allowReport.onchange = () => { reset(); append('assistant', allowReport.checked ? '已允许针对当前报告提问。可随时取消许可并清空对话。' : '已取消报告问答许可并清空对话。'); };
  exitReport.onclick = () => { exitReportMode(); input.focus(); };
  async function syncSession() {
    const current = auth();
    const next = current && current.user && current.token ? String(current.user.id) + ':' + current.token : '';
    if (next === identity) return;
    identity = next; configured = false; allow.checked = false; allowReport.checked = false;
    mode = 'general'; selectedReport = null; updateMode(); reset();
    host.hidden = !next; panel.hidden = true; launch.setAttribute('aria-expanded', 'false');
    if (!next) return;
    const role = current.user.role;
    permissionText.textContent = role === 'resident' ? '允许查询我的待办数量和日期（仅本次会话）' : role === 'family' ? '允许查询我的有效授权数量（仅本次会话）' : '允许查询当前身份可见的业务汇总（仅本次会话）';
    suggestions.replaceChildren();
    const examples = role === 'staff' ? ['如何开通居民账号？', '同意证据怎么复核？', '本社区有多少待办随访？'] : role === 'admin' ? ['管理员能查看什么？', '怎么开通两步验证？'] : role === 'family' ? ['如何让长辈授权我？', '我能看到哪些数据？'] : ['如何授权家人？', '怎么认领已有档案？', '我有哪些待办？'];
    examples.forEach(text => { const b = el('button', '', text); b.type = 'button'; b.onclick = () => { input.value = text; input.focus(); }; suggestions.appendChild(b); });
    status.textContent = '正在检查服务';
    const ticket = generation;
    try {
      const response = await fetch('/api/assistant/status', {headers: {Authorization: 'Bearer ' + current.token}});
      const data = await response.json();
      if (identity !== next) return;
      configured = response.ok && data.configured === true;
      status.textContent = configured ? data.provider + ' · ' + data.model : '问答服务尚未配置';
      send.disabled = !configured;
    } catch (error) { if (ticket === generation) status.textContent = '服务检查失败，请重新登录后重试'; }
  }
  function renderGeneralSources(bubble, data) {
    if (Array.isArray(data.sources)) {
      const sources = el('div', 'aa-sources');
      data.sources.forEach(source => {
        if (typeof source.url !== 'string' || !/^\/help#[a-z-]+$/.test(source.url)) return;
        const a = el('a', '', source.title); a.href = source.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; sources.appendChild(a);
      }); bubble.appendChild(sources);
    }
    const targets = new Set(['v-home', 'v-vitals', 'v-quiz', 'v-remind', 'v-me', 'v-family', 'overview', 'residents', 'visits', 'stats', 'settings']);
    (Array.isArray(data.actions) ? data.actions : []).forEach(action => {
      if (!targets.has(action.target)) return;
      const button = el('button', 'aa-nav', action.label || '打开相关功能'); button.type = 'button';
      button.onclick = () => {
        if (action.target.startsWith('v-') && typeof window.tab === 'function') window.tab(action.target);
        else if (typeof window.switchView === 'function') window.switchView(action.target);
        setOpen(false);
      }; bubble.appendChild(button);
    });
  }
  function renderReportSources(bubble, data, report) {
    const evidence = {supported:'以下来源可供核对。',insufficient_evidence:'这份报告中没有足够依据回答，请核对原件或咨询专业人员。',medical_guidance_limited:'此问题涉及医疗判断，助手只展示报告依据，请咨询医生。'};
    if (evidence[data.evidence_status]) bubble.appendChild(el('p', 'aa-evidence-status', evidence[data.evidence_status]));
    if (!Array.isArray(data.sources) || !data.sources.length) return;
    const list = el('div', 'aa-report-sources');
    data.sources.forEach(source => {
      if (Number(source.report_id) !== report.id || Number(source.revision) !== report.revision) return;
      const page = Number(source.page), sourceId = source.id;
      const itemId = typeof sourceId === 'string' ? /^p([1-5])-i[1-9]\d*$/.exec(sourceId) : null;
      const headerId = typeof sourceId === 'string' && /^meta:(title|patient_name|specimen_collected_at|reported_at)$/.test(sourceId);
      if (!Number.isInteger(page) || page < 1 || page > 5 || (!headerId && (!itemId || Number(itemId[1]) !== page))) return;
      const card = el('article', 'aa-report-source');
      card.appendChild(el('strong', '', `${source.filename || report.filename} · ${Number.isInteger(page) && page >= 1 ? `第 ${page} 页` : '页码待核对'}`));
      card.appendChild(el('small', '', source.source_kind === 'model_transcription' ? '原文转录（请对照原件）' : 'PDF 原文摘录'));
      card.appendChild(el('p', 'aa-source-quote', source.quote || '（无原文摘录）'));
      const fields = source.confirmed_fields;
      if (fields && typeof fields === 'object' && !Array.isArray(fields)) {
        const line = el('p', 'aa-source-fields');
        if ('field' in fields) line.textContent = `已确认字段：${fields.label || fields.field}：${fields.value ?? ''}`;
        else line.textContent = `已确认字段：${fields.name ?? ''} ${fields.value ?? ''} ${fields.unit ?? ''}；参考范围 ${fields.reference_range || '未提供'}；标记 ${fields.flag || '未标记'}`;
        card.appendChild(line);
      }
      if (Array.isArray(source.edited_fields) && source.edited_fields.length) {
        const fieldLabels = {name:'项目名称',value:'结果',unit:'单位',reference_range:'参考范围',flag:'标记',title:'报告名称',patient_name:'报告患者姓名',specimen_collected_at:'采样时间',reported_at:'报告时间'};
        card.appendChild(el('p', 'aa-source-edits', `人工核对后修订：${source.edited_fields.map(name => fieldLabels[name] || '其他字段').join('、')}`));
      }
      if (typeof window.anlinReports?.open === 'function') {
        const jump = el('button', 'aa-source-jump', '查看原件页'); jump.type = 'button';
        jump.onclick = () => {
          if (mode !== 'report' || selectedReport?.id !== report.id || selectedReport?.revision !== report.revision) return;
          setOpen(false);
          window.anlinReports.open(report.resident_id, report.resident_name, {reportId:report.id,page,expectedRevision:report.revision});
        };
        card.appendChild(jump);
      }
      list.appendChild(card);
    });
    bubble.appendChild(list);
  }
  form.onsubmit = async event => {
    event.preventDefault(); const message = input.value.trim(); const current = auth();
    if (!message || busy || !configured || !current || !current.token) return;
    const isReport = mode === 'report', report = selectedReport;
    if (isReport && (!report || !allowReport.checked)) { append('error', '请先勾选这份已确认报告的问答许可。'); return; }
    const ticket = generation, tokenAtStart = current.token;
    append('user', message); input.value = ''; busy = true; send.disabled = true; send.textContent = '回答中…';
    controller = new AbortController(); const activeController = controller;
    const timer = setTimeout(() => activeController.abort(), 75000);
    try {
      const path = isReport ? '/api/assistant/report-chat' : '/api/assistant/chat';
      const payload = isReport ? {report_id:report.id,message,history:history.slice(-12),allow_report_data:true} : {message,history:history.slice(-12),allow_health_data:allow.checked};
      const response = await fetch(path, {method:'POST',signal:activeController.signal,
        headers:{Authorization:'Bearer ' + current.token,'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const data = await response.json().catch(() => ({}));
      if (ticket !== generation || !auth() || auth().token !== tokenAtStart) return;
      if (isReport && [401,403,404,409].includes(response.status)) {
        exitReportMode(); append('error', response.status === 401 ? '登录已过期，请重新登录后选择报告。' : '报告状态或授权已变化，请重新选择已确认报告。'); return;
      }
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '提问失败，请重试');
      if (isReport && (Number(data.report?.id) !== report.id || Number(data.report?.revision) !== report.revision || Number(data.report?.resident_id) !== report.resident_id)) {
        exitReportMode(); append('error', '报告版本已变化，请重新选择已确认报告。'); return;
      }
      const answer = typeof data.answer === 'string' ? data.answer : '未能取得有效回答。';
      history.push({role:'user',content:message},{role:'assistant',content:answer.slice(0,3000)});
      history = history.slice(-12);
      const bubble = append('assistant', answer);
      if (isReport) renderReportSources(bubble, data, report); else renderGeneralSources(bubble, data);
      feed.scrollTop = feed.scrollHeight;
    } catch (error) {
      if (ticket === generation) append('error', error.name === 'AbortError' ? '回答超时，请稍后重试。' : error.message);
    } finally {
      clearTimeout(timer);
      if (ticket === generation) { busy = false; send.disabled = !configured; send.textContent = '发送'; controller = null; }
    }
  };
  function openReport(meta) {
    const current = auth();
    if (!current?.token || !['resident','staff'].includes(current.user?.role)) return false;
    const report = {id:Number(meta?.id),resident_id:Number(meta?.resident_id),resident_name:String(meta?.resident_name || ''),filename:String(meta?.filename || ''),title:String(meta?.title || ''),revision:Number(meta?.revision)};
    if (![report.id,report.resident_id,report.revision].every(n => Number.isInteger(n) && n > 0)) return false;
    const sameReport = mode === 'report' && selectedReport?.id === report.id && selectedReport?.revision === report.revision && selectedReport?.resident_id === report.resident_id;
    if (!sameReport) { mode = 'report'; selectedReport = report; allow.checked = false; allowReport.checked = false; updateMode(); reset(); }
    setOpen(true); return true;
  }
  window.anlinAssistant = {openReport,exitReportMode};
  input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
  window.addEventListener('anlin-session-change', syncSession);
  syncSession();
})();
