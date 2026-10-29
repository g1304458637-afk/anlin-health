import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import vm from 'node:vm';
import test from 'node:test';

const root = resolve(import.meta.dirname, '../..');
const scripts = name => {
  // Page scripts are external files now (CSP script-src 'self'); load them in
  // the same order the browser would: app-core first, then the page bundle.
  const read = rel => readFileSync(resolve(root, rel), 'utf8');
  const inline = [...readFileSync(resolve(root, `backend/static/${name}.html`), 'utf8')
    .matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(Boolean);
  return [...inline, read('backend/static/js/app-core.js'), read(`backend/static/js/${name}-app.js`)];
};

function harness(name, route, initialStorage = {}) {
  const elements = new Map(), requests = [], events = [];
  const element = id => {
    if (!elements.has(id)) {
      const flags = new Set(['hidden']);
      elements.set(id, {
        id, value: '', checked: false, disabled: false, dataset: {}, style: {}, innerHTML: '', textContent: '',
        classList: {add: x => flags.add(x), remove: x => flags.delete(x), contains: x => flags.has(x), toggle: (x, force) => force ? flags.add(x) : flags.delete(x)},
        replaceChildren() { this.innerHTML = ''; this.textContent = ''; },
        addEventListener() {},
      });
    }
    return elements.get(id);
  };
  const storage = new Map(Object.entries(initialStorage));
  const context = vm.createContext({
    console, setTimeout, clearTimeout, AbortSignal, Date, Promise, Element: class {},
    document: {getElementById: element, querySelectorAll: () => [], querySelector: () => null, addEventListener: () => {}},
    sessionStorage: {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
    CustomEvent: class { constructor(type) { this.type = type; } },
    navigator: {}, confirm: () => true, prompt: () => null,
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      const result = await route(url, options);
      return {status: result.status ?? 200, ok: (result.status ?? 200) < 400, headers: {get: () => null}, json: async () => result.body ?? result};
    },
  });
  context.window = context;
  context.dispatchEvent = ev => events.push(ev.type);
  context.addEventListener = () => {};
  context.scrollTo = () => {};
  context.location = {href: ''};
  for (const script of scripts(name)) vm.runInContext(script, context, {filename: name});
  return {context, element, requests, events, run: code => vm.runInContext(code, context)};
}

test('all inline page scripts parse', () => {
  for (const name of ['portal', 'console']) for (const script of scripts(name)) assert.doesNotThrow(() => new Function(script));
});

test('family invite is shown to family and claim submission stays generic', async () => {
  let claimPayload, grantActive = true;
  const h = harness('portal', (url, options) => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/auth/login') {
      const username = JSON.parse(options.body).username;
      return {body: {access_token: username, user: {username, display_name: username, role: username === 'family' ? 'family' : 'resident', invite_code: 'FAMILY-CODE'}}};
    }
    if (url === '/api/family/shared-residents') return {body: grantActive ? [{resident_id: 7, grant_id: 9, name: '居民甲', can_profile: true, can_vitals: false}] : []};
    if (url === '/api/residents/7/family-grants/9/decline') {grantActive = false; return {status: 204, body: null};}
    if (url === '/api/communities') return {body: [{id: 1, name: '一社区'}]};
    if (url === '/api/residents/me') return {status: 404, body: {detail: '不存在'}};
    if (url === '/api/residents/claim-requests') {claimPayload = JSON.parse(options.body); return {status: 202, body: {status: 'submitted', message: '申请已提交，请携带有效证件到社区现场核验'}};}
    throw Error(`unexpected ${url}`);
  });
  h.element('luser').value = 'family'; h.element('lpwd').value = 'password';
  await h.element('loginBtn').onclick();
  await h.context.renderFamilyList();
  assert.match(h.element('famList').innerHTML, /data-act="decline-grant" data-rid="7" data-gid="9"/);
  await h.context.declineGrant(7, 9);
  assert.equal(grantActive, false);
  h.context.tab('v-me');
  assert.match(h.element('meShare').innerHTML, /FAMILY-CODE/);
  assert.match(h.element('meShare').innerHTML, /居民登录自己的账号/);
  h.element('logoutBtn').onclick();
  assert.equal(h.element('meShare').innerHTML, '');
  assert.equal(h.context.anlinAssistantAuth().user, null);
  h.element('luser').value = 'resident'; h.element('lpwd').value = 'password';
  await h.element('loginBtn').onclick();
  h.context.tab('v-me');
  assert.equal(h.element('claimCard').classList.contains('hidden'), false);
  Object.assign(h.element('claimId'), {value: '8'});
  Object.assign(h.element('claimName'), {value: '王先生'});
  Object.assign(h.element('claimPhone'), {value: '13000000000'});
  await h.element('claimBtn').onclick();
  assert.deepEqual(claimPayload, {resident_id: 8, full_name: '王先生', phone: '13000000000', note: null});
  assert.match(h.element('claimStatus').textContent, /现场核验/);
  assert.equal(h.requests.filter(r => r.url === '/api/residents/claim-requests' && (!r.options.method || r.options.method === 'GET')).length, 0);
  assert.ok(h.events.includes('anlin-session-change'));
});

test('restored portal session announces auth to the assistant', async () => {
  const h = harness('portal', url => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/me') return {body: {id: 9, username: 'family', display_name: '家属', role: 'family', invite_code: 'FAMILY-CODE'}};
    if (url === '/api/family/shared-residents') return {body: []};
    throw Error(`unexpected ${url}`);
  }, {token: 'saved-token'});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.context.anlinAssistantAuth().token, 'saved-token');
  assert.equal(h.context.anlinAssistantAuth().user.id, 9);
  assert.ok(h.events.includes('anlin-session-change'));
});

test('resident sees effective consent and can withdraw and grant again', async () => {
  let active = true, expired = false;
  const changes = [];
  const date = delta => {const d = new Date(); d.setDate(d.getDate() + delta); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;};
  const h = harness('portal', (url, options) => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/auth/login') return {body: {access_token: 'resident-token', user: {id: 1, username: 'resident', display_name: '居民甲', role: 'resident', invite_code: 'OWN-CODE'}}};
    if (url === '/api/communities') return {body: [{id: 1, name: '一社区'}]};
    if (url === '/api/residents/2' && expired) return {status: 401, body: {detail: 'expired'}};
    if (url === '/api/residents/me' || url === '/api/residents/2') return {body: {id: 2, name: '居民甲', birth_year: 1950, community_id: 1, consent_active: active}};
    if (url === '/api/residents/2/consents' && options.method === 'POST') {const change = JSON.parse(options.body); changes.push(change); active = change.granted; return {status: 201, body: {granted: active}};}
    if (url === '/api/residents/2/consents') return {body: [{granted: active, created_at: '2026-10-23T03:00:00Z', review_status: 'approved'}]};
    if (url === '/api/residents/2/family-grants') return {body: []};
    if (url.startsWith('/api/residents/2/reminders')) return {body: [{id: 1, title: '今日测量', due_date: date(0)}, {id: 2, title: '明日测量', due_date: date(1)}]};
    if (url.startsWith('/api/residents/2/vitals')) return {body: []};
    throw Error(`unexpected ${url}`);
  });
  h.element('luser').value = 'resident'; h.element('lpwd').value = 'password';
  await h.element('loginBtn').onclick();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(h.element('homeReminders').innerHTML, /今日测量/);
  assert.doesNotMatch(h.element('homeReminders').innerHTML, /明日测量/);
  h.context.tab('v-me');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(h.element('consentState').innerHTML, /已授权社区健康服务/);
  await h.element('consentBtn').onclick();
  assert.deepEqual(changes[0], {granted: false});
  assert.match(h.element('consentState').innerHTML, /未授权社区健康服务/);
  await h.element('consentBtn').onclick();
  assert.deepEqual(changes[1], {granted: true});
  expired = true;
  h.context.tab('v-me');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.context.anlinAssistantAuth().user, null);
  assert.equal(h.element('meProfile').innerHTML, '');
  assert.equal(h.element('meShare').innerHTML, '');
});

test('console uses aggregate stats and clears role specific data on logout', async () => {
  const mfaCalls = [];
  const h = harness('console', (url, options) => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/auth/login') return {body: {access_token: 'staff-token', user: {id: 5, username: 'staff', display_name: '工作人员', role: 'staff', community_id: 1}}};
    if (url.startsWith('/api/community/residents?')) return {body: [{id: 2, name: '居民甲', birth_year: 1950, consent_active: true}]};
    if (url.startsWith('/api/community/visits?')) return {body: []};
    if (url === '/api/community/visits/stats') return {body: {total: 130, planned: 125, done: 5, overdue: 12}};
    if (url === '/api/community/stats') return {body: {resident_count: 1}};
    if (url === '/api/community/consent-reviews') return {body: [{id: 10, resident_id: 2, actor_id: 5, granted: true, evidence_ref: 'PAPER-001', created_at: '2026-10-23T03:00:00Z'}]};
    if (url === '/api/expired') return {status: 401, body: {detail: 'expired'}};
    if (url.startsWith('/api/community/health-stats?')) return {body: {resident_count: 1, totals: {blood_pressure: 500, blood_glucose: 2, weight: 1}, daily_counts: [], residents: [{id: 2, name: '居民甲', blood_pressure: null, blood_glucose: null, weight: null, record_count: 503}], total_residents: 1, limit: 25, offset: 0}};
    if (url.startsWith('/api/auth/mfa/totp')) {mfaCalls.push({url, method: options.method, body: JSON.parse(options.body)}); return {body: url.endsWith('/confirm') || options.method === 'DELETE' ? {access_token: 'rotated-token'} : {secret: 'STAGEDSECRET', uri: 'otpauth://totp/test'}};}
    throw Error(`unexpected ${url}`);
  });
  h.element('username').value = 'staff'; h.element('password').value = 'password';
  await h.context.login();
  assert.equal(h.element('visitCount').textContent, 125);
  h.context.switchView('settings');
  assert.equal(h.element('adminAccountsPanel').classList.contains('hidden'), true);
  h.context.switchView('stats');
  await h.context.loadStats();
  assert.match(h.element('statChips').innerHTML, /500/);
  assert.equal(h.requests.filter(r => r.url.includes('/vitals')).length, 0);
  await h.context.loadConsentReviews();
  assert.match(h.element('consentQueue').innerHTML, /需其他工作人员复核/);
  assert.doesNotMatch(h.element('consentQueue').innerHTML, /data-id="10"[^>]*data-approve="true"/);
  h.context.showProvision();
  assert.match(h.element('modalFields').innerHTML, /initial_password/);
  h.element('mfaPassword').value = 'current-password';
  await h.context.setupMFA();
  assert.equal(mfaCalls[0].body.current_password, 'current-password');
  assert.match(h.element('mfaSecret').innerHTML, /STAGEDSECRET/);
  h.element('mfaConfirmCode').value = '123456';
  await h.context.confirmMFA();
  assert.equal(mfaCalls[1].url, '/api/auth/mfa/totp/confirm');
  assert.equal(h.context.anlinAssistantAuth().token, 'rotated-token');
  h.element('mfaPassword').value = 'current-password'; h.element('mfaExistingCode').value = '123456';
  await h.context.disableMFA();
  assert.equal(mfaCalls[2].method, 'DELETE');
  await assert.rejects(h.context.api('/api/expired'), /登录已失效/);
  assert.equal(h.element('latestBody').innerHTML, '');
  assert.equal(h.element('residentRows').innerHTML, '');
  assert.equal(h.element('newResident').disabled, false);
  assert.equal(h.context.anlinAssistantAuth().user, null);
});

test('console visit totals stay independent of a 25 item page and homepage uses only eight planned visits', async () => {
  const visits = Array.from({length: 200}, (_, i) => ({id: i + 1, resident_id: 2, staff_id: 5, due_date: '2026-10-23', status: i < 130 ? 'planned' : 'done'}));
  const h = harness('console', url => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url.startsWith('/api/community/residents?')) return {body: [{id: 2, name: '居民甲', birth_year: 1950, consent_active: true}]};
    if (url === '/api/community/stats') return {body: {resident_count: 1}};
    if (url === '/api/community/visits/stats') return {body: {total: 200, planned: 130, done: 70, overdue: 11}};
    if (url.startsWith('/api/community/visits?')) {
      const params = new URL(url, 'http://localhost').searchParams;
      const filtered = visits.filter(v => !params.has('status') || v.status === params.get('status'));
      const offset = Number(params.get('offset')), limit = Number(params.get('limit'));
      return {body: filtered.slice(offset, offset + limit)};
    }
    throw Error(`unexpected ${url}`);
  });
  h.run("token='staff-token';me={id:5,role:'staff',community_id:1};");
  await h.context.refresh();
  assert.equal(h.element('visitCount').textContent, 130);
  assert.equal((h.element('visitRows').innerHTML.match(/class="visit"/g) || []).length, 8);
  assert.ok(h.requests.some(r => r.url === '/api/community/visits?status=planned&limit=8&offset=0'));
  h.run("currentView='visits'");
  await h.context.loadVisitList();
  assert.match(h.element('visitPageLabel').textContent, /第 1 \/ 6 页 · 共 130 项/);
  for (let i = 0; i < 5; i++) await h.context.pageVisits(1);
  assert.equal((h.element('visitList').innerHTML.match(/class="visit"/g) || []).length, 5);
  assert.equal(h.element('visitCount').textContent, 130);
  assert.equal(h.element('visitNext').disabled, true);
  assert.match(h.element('visitSummary').textContent, /待完成 130 · 已完成 70 · 逾期 11/);
  await h.context.filterVisits('done');
  assert.match(h.element('visitPageLabel').textContent, /第 1 \/ 3 页 · 共 70 项/);
  assert.equal(h.element('visitPrev').disabled, true);
  await h.context.filterVisits('all');
  assert.match(h.element('visitPageLabel').textContent, /共 200 项/);
  assert.equal(h.element('visitCount').textContent, 130);
  assert.equal((h.element('visitRows').innerHTML.match(/class="visit"/g) || []).length, 8);
  h.context.logout();
  assert.equal(h.element('visitList').innerHTML, '');
  assert.equal(h.element('visitSummary').textContent, '');
});

test('console ignores old visit filter and logged out responses', async () => {
  let releaseOld;
  const h = harness('console', url => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/community/visits/stats') return {body: {total: 2, planned: 1, done: 1, overdue: 0}};
    if (url.includes('status=planned')) return new Promise(resolve => { releaseOld = resolve; });
    if (url.includes('status=done')) return {body: [{id: 22, resident_id: 2, due_date: '2026-10-23', status: 'done'}]};
    throw Error(`unexpected ${url}`);
  });
  h.run("token='staff-token';me={id:5,role:'staff'};currentView='visits';");
  const old = h.context.loadVisitList();
  await h.context.filterVisits('done');
  const expected = h.element('visitList').innerHTML;
  assert.match(expected, /走访已完成/);
  releaseOld({body: [{id: 11, resident_id: 3, due_date: '2026-10-23', status: 'planned'}]});
  await old;
  assert.equal(h.element('visitList').innerHTML, expected);
  assert.equal(h.element('visitStatus').value, 'done');
  const beforeLogout = h.context.filterVisits('planned');
  h.context.logout();
  releaseOld({body: [{id: 11, resident_id: 3, due_date: '2026-10-23', status: 'planned'}]});
  await beforeLogout;
  assert.equal(h.element('visitList').innerHTML, '');
  assert.equal(h.element('visitCount').textContent, '—');
  assert.equal(h.element('visitSummary').textContent, '');
});

test('console manual vital records submit the actual local measurement time', async () => {
  let submitted, save, fields;
  const h = harness('console', (url, options) => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/residents/2/vitals') {submitted = JSON.parse(options.body); return {body: {id: 1}};}
    throw Error(`unexpected ${url}`);
  });
  h.run("me={id:5,role:'staff'};residents=[{id:2,name:'居民甲'}];");
  h.context.modal = (_title, _description, markup, onSubmit) => { fields = markup; save = onSubmit; };
  h.context.showVital(2);
  assert.match(fields, /实际测量时间/);
  assert.match(fields, /name="measured_at" type="datetime-local"/);
  const local = '2026-01-13T08:15';
  await save(new Map([['type', 'weight'], ['weight', '65.3'], ['measured_at', local]]));
  assert.deepEqual(submitted, {type: 'weight', weight_kg: 65.3, measured_at: new Date(local).toISOString()});
  await assert.rejects(save(new Map([['type', 'weight'], ['weight', '65'], ['measured_at', '']])), /有效的实际测量时间/);
});

test('admin can create fixed staff accounts and manage only nonadmin users', async () => {
  let users = [
    {id: 1, username: 'admin', display_name: '管理员', role: 'admin', community_id: null, is_active: true, mfa_enabled: true},
    {id: 2, username: 'staff_a', display_name: '员工甲', role: 'staff', community_id: 4, is_active: true, mfa_enabled: false},
  ];
  let created;
  const h = harness('console', (url, options) => {
    if (url === '/api/auth/demo-status') return {body: {enabled: false}};
    if (url === '/api/auth/login') return {body: {access_token: 'admin-token', user: {id: 1, username: 'admin', display_name: '管理员', role: 'admin', community_id: null}}};
    if (url === '/api/admin/summary') return {body: {communities: [{id: 4, name: '甲社区', active_resident_count: 1}]}};
    if (url === '/api/communities') return {body: [{id: 4, name: '甲社区'}]};
    if (url === '/api/admin/users' && options.method === 'GET') return {body: users};
    if (url === '/api/admin/users' && options.method === 'POST') {created = JSON.parse(options.body); users = [...users, {id: 3, username: created.username, display_name: created.display_name, role: 'staff', community_id: 4, is_active: true, mfa_enabled: false}]; return {status: 201, body: users.at(-1)};}
    if (url === '/api/admin/users/2/deactivate') {users[1] = {...users[1], is_active: false}; return {status: 204, body: null};}
    throw Error(`unexpected ${url}`);
  });
  h.element('username').value = 'admin'; h.element('password').value = 'password';
  await h.context.login();
  assert.equal(h.element('newResident').disabled, true);
  h.context.switchView('settings');
  await h.context.loadAdminAccounts();
  assert.equal(h.element('adminAccountsPanel').classList.contains('hidden'), false);
  assert.match(h.element('adminAccountRows').innerHTML, /data-act="toggle-admin-account" data-id="2" data-activate="false"/);
  assert.doesNotMatch(h.element('adminAccountRows').innerHTML, /data-id="1"/);
  Object.assign(h.element('adminUsername'), {value: 'staff_b'});
  Object.assign(h.element('adminDisplayName'), {value: '员工乙'});
  Object.assign(h.element('adminCommunity'), {value: '4'});
  Object.assign(h.element('adminInitialPassword'), {value: 'StrongPass2026!'});
  await h.context.createStaffAccount();
  assert.deepEqual(created, {username: 'staff_b', display_name: '员工乙', community_id: 4, initial_password: 'StrongPass2026!'});
  assert.equal(h.element('adminInitialPassword').value, '');
  assert.match(h.element('adminAccountRows').innerHTML, /staff_b/);
  await h.context.toggleAdminAccount(1, false);
  assert.equal(h.requests.filter(r => r.url.endsWith('/1/deactivate')).length, 0);
  await h.context.toggleAdminAccount(2, false);
  assert.equal(users[1].is_active, false);
  h.element('adminInitialPassword').value = 'unsent-secret';
  h.context.switchView('overview');
  assert.equal(h.element('adminAccountsPanel').classList.contains('hidden'), true);
  assert.equal(h.element('adminAccountRows').innerHTML, '');
  assert.equal(h.element('adminUsername').value, '');
  assert.equal(h.element('adminInitialPassword').value, '');
  h.context.logout();
  assert.equal(h.context.anlinAssistantAuth().user, null);
});
