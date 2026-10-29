/* 安邻健康 · 页面共享运行时。
 * 两套页面（居民门户 / 社区工作台）此前各自内联一份 api()/esc()/toast 实现，
 * 且已出现行为漂移；此模块是唯一权威版本。页面脚本通过 window.AnlinCore 取用。
 */
(function () {
  'use strict';

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function fmtTime(s) {
    if (!s) return '';
    const raw = String(s);
    const utc = /[zZ]|[+-]\d\d:\d\d$/.test(raw) ? raw : raw + 'Z';
    return new Date(utc).toLocaleString('zh-CN', {dateStyle: 'short', timeStyle: 'short'});
  }

  const fmtD = s => s ? String(s).slice(0, 10) : '';

  function localDay(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function localDate() { return localDay(new Date()); }

  function localInputDate(d) {
    d = d || new Date();
    return `${localDay(d)}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  /* 统一的 api() 工厂：令牌、会话版本、401 处理和错误文案归页面注入，
   * 超时/204/过期响应丢弃等行为只有这一份。 */
  function createApi({getToken, getVersion, onAuthExpired, describeError}) {
    return async function api(path, method = 'GET', data) {
      const requestVersion = getVersion();
      const res = await fetch(path, {
        method, signal: AbortSignal.timeout(15000),
        headers: {...(getToken() ? {Authorization: 'Bearer ' + getToken()} : {}), ...(data ? {'Content-Type': 'application/json'} : {})},
        body: data ? JSON.stringify(data) : undefined
      });
      if (requestVersion !== getVersion()) throw new Error('会话已切换');
      if (res.status === 204) return null;
      let body = {};
      try { body = await res.json(); } catch (e) {}
      if (requestVersion !== getVersion()) throw new Error('会话已切换');
      if (!res.ok) {
        if (res.status === 401) { onAuthExpired(); throw new Error('登录已失效，请重新登录'); }
        throw new Error(describeError(body.detail));
      }
      return body;
    };
  }

  function makeToast(getElement, hideAfterMs) {
    return function toast(text) {
      const el = getElement();
      if (!el) return;
      el.textContent = text;
      el.classList.remove('hidden');
      clearTimeout(el._t);
      el._t = setTimeout(() => el.classList.add('hidden'), hideAfterMs);
    };
  }

  /* ---------- 对话框：替代原生 prompt()/confirm() ---------- */
  // 真浏览器中渲染页内模态；沙箱/测试环境（无 createElement）回退原生对话框，
  // 行为与历史版本一致。
  const canBuildDialog = () => typeof document.createElement === 'function' && document.body;

  function ensureDialogHost() {
    let host = document.getElementById('anlinDialog');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'anlinDialog';
    host.className = 'anlin-dialog-overlay';
    host.innerHTML = '<div class="anlin-dialog" role="dialog" aria-modal="true"><p class="anlin-dialog-text"></p>' +
      '<input class="anlin-dialog-input hidden" type="text"><div class="anlin-dialog-actions">' +
      '<button type="button" class="anlin-dialog-cancel">取消</button>' +
      '<button type="button" class="anlin-dialog-ok">确定</button></div></div>';
    document.body.appendChild(host);
    host.addEventListener('click', e => { if (e.target === host) settleDialog(null); });
    return host;
  }

  let dialogSettle = null;
  function settleDialog(value) {
    const host = document.getElementById('anlinDialog');
    if (host) host.classList.add('hidden');
    const done = dialogSettle;
    dialogSettle = null;
    if (done) done(value);
  }

  function askDialog({text, mode, initial, okLabel, danger}) {
    if (!canBuildDialog()) return Promise.resolve(mode === 'input' ? window.prompt(text, initial ?? '') : window.confirm(text));
    if (dialogSettle) settleDialog(null);
    const host = ensureDialogHost();
    host.querySelector('.anlin-dialog-text').textContent = text;
    const input = host.querySelector('.anlin-dialog-input');
    const ok = host.querySelector('.anlin-dialog-ok');
    const cancel = host.querySelector('.anlin-dialog-cancel');
    ok.textContent = okLabel || '确定';
    ok.className = 'anlin-dialog-ok' + (danger ? ' danger' : '');
    if (mode === 'input') {
      input.classList.remove('hidden');
      input.value = initial ?? '';
    } else {
      input.classList.add('hidden');
      input.value = '';
    }
    host.classList.remove('hidden');
    if (mode === 'input') setTimeout(() => input.focus(), 0);
    return new Promise(resolve => {
      dialogSettle = resolve;
      ok.onclick = () => settleDialog(mode === 'input' ? input.value.trim() : true);
      cancel.onclick = () => settleDialog(mode === 'input' ? null : false);
      input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); ok.onclick(); } };
    });
  }

  const confirmDialog = (text, opts = {}) => askDialog({text, mode: 'confirm', okLabel: opts.okLabel || '确定', danger: opts.danger !== false});
  const promptDialog = (text, initial) => askDialog({text, mode: 'input', initial, okLabel: '确定'});

  /* ---------- data-act 事件委托：替代内联 onclick 属性（CSP script-src 'self'） ---------- */
  function bindActions(registry) {
    document.addEventListener('click', e => {
      const el = e.target instanceof Element ? e.target.closest('[data-act]') : null;
      if (!el) return;
      const fn = registry[el.dataset.act];
      if (!fn) return;
      e.preventDefault();
      fn(el.dataset, el);
    });
  }

  function bindInputActions(registry) {
    document.addEventListener('input', e => {
      const el = e.target instanceof Element ? e.target.closest('[data-input-act]') : null;
      if (!el) return;
      const fn = registry[el.dataset.inputAct];
      if (fn) fn(e.target.value, el);
    });
  }

  window.AnlinCore = {esc, clean: esc, fmtTime, fmtD, localDay, localDate, localInputDate,
                       createApi, makeToast, confirmDialog, promptDialog, bindActions, bindInputActions};
})();
