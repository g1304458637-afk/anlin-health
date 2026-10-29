"""One-shot conversion: inline onclick/oninput/onsubmit -> data-act delegation.

Every replacement asserts exactly one hit so a drifted source fails loudly.
Also swaps native prompt()/confirm() for the shared dialog and routes both
pages' duplicated api()/esc()/toast helpers through static/js/app-core.js.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def apply(text: str, pairs: list[tuple[str, str]], label: str) -> str:
    for old, new in pairs:
        count = text.count(old)
        assert count == 1, f"{label}: expected 1 hit, found {count} for: {old[:90]!r}"
        text = text.replace(old, new)
    return text


# ---------------------------------------------------------------- portal-app.js
portal_js_pairs = [
    # shared runtime
    ("function toast(t){const el=$('toast');el.textContent=t;el.classList.remove('hidden');clearTimeout(el._t);el._t=setTimeout(()=>el.classList.add('hidden'),2600)}",
     "const toast=window.AnlinCore.makeToast(()=>$('toast'),2600);"),
    ("const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));",
     "const {esc,fmtTime,fmtD,localDay,localDate,localInputDate}=window.AnlinCore;"),
    ("""const localDate=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
const fmtTime=s=>{if(!s)return '';const raw=String(s);const utc=/[zZ]|[+-]\\d\\d:\\d\\d$/.test(raw)?raw:raw+'Z';return new Date(utc).toLocaleString('zh-CN',{dateStyle:'short',timeStyle:'short'})};


function localDay(d){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function localInputDate(d){return `${localDay(d)}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`}""",
     ""),
    ("""async function api(path,method='GET',data){
  const requestVersion=sessionVersion;
  const res=await fetch(path,{method,signal:AbortSignal.timeout(15000),headers:{...(token?{Authorization:'Bearer '+token}:{}),...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});
  if(requestVersion!==sessionVersion)throw new Error('会话已切换');
  if(res.status===204)return null;
  let body={};try{body=await res.json()}catch(e){}
  if(requestVersion!==sessionVersion)throw new Error('会话已切换');
  if(!res.ok){
    if(res.status===401){logout();throw new Error('登录已失效，请重新登录')}
    let d=body.detail;if(typeof d!=='string')d=Array.isArray(d)?d.map(x=>x.msg).join('；'):'操作失败';
    throw new Error(d);
  }
  return body;
}""",
     "const api=window.AnlinCore.createApi({getToken:()=>token,getVersion:()=>sessionVersion,onAuthExpired:logout,\n"
     "  describeError:d=>typeof d==='string'?d:Array.isArray(d)?d.map(x=>x.msg).join('；'):'操作失败'});"),
    # native dialogs -> shared dialog
    ("const code=prompt('此账号已开启两步验证，请输入认证器中的6位验证码：');",
     "const code=await window.AnlinCore.promptDialog('此账号已开启两步验证，请输入认证器中的6位验证码：');"),
    ("if(!confirm('删除这条记录？删除后无法找回。'))return;",
     "if(!(await window.AnlinCore.confirmDialog('删除这条记录？删除后无法找回。')))return;"),
    ("if(!confirm(granted?'确认重新授权社区工作人员查看您的档案和测量数据？':'确认撤回社区服务授权？'))return;",
     "if(!(await window.AnlinCore.confirmDialog(granted?'确认重新授权社区工作人员查看您的档案和测量数据？':'确认撤回社区服务授权？')))return;"),
    ("""const name=prompt('姓名：',resident.name);if(name===null)return;
  const phone=prompt('联系电话（可留空）：',resident.phone||'');""",
     """const name=await window.AnlinCore.promptDialog('姓名：',resident.name);if(name===null)return;
  const phone=await window.AnlinCore.promptDialog('联系电话（可留空）：',resident.phone||'');"""),
    ("if(!confirm('更换后旧的邀请码立即失效，确定？'))return;",
     "if(!(await window.AnlinCore.confirmDialog('更换后旧的邀请码立即失效，确定？')))return;"),
    ("if(!confirm('撤回后家人立即失去查看权限，确定？'))return;",
     "if(!(await window.AnlinCore.confirmDialog('撤回后家人立即失去查看权限，确定？')))return;"),
    ("if(!confirm('拒绝这项授权后将不能再查看该居民的数据，确定？'))return;",
     "if(!(await window.AnlinCore.confirmDialog('拒绝这项授权后将不能再查看该居民的数据，确定？')))return;"),
    ("if(!confirm('退出所有设备？'))return;",
     "if(!(await window.AnlinCore.confirmDialog('退出所有设备？')))return;"),
    # template onclick -> data-act
    ("onclick=\"tab('${t[0]}')\"", "data-act=\"tab\""),
    ("onclick=\"setVT('${k}')\"", "data-act=\"set-vt\" data-metric=\"${k}\""),
    ("onclick=\"delVital(${v.id})\"", "data-act=\"del-vital\" data-id=\"${v.id}\""),
    ("onclick=\"pageVitals(-1)\"", "data-act=\"page-vitals\" data-step=\"-1\""),
    ("onclick=\"pageVitals(1)\"", "data-act=\"page-vitals\" data-step=\"1\""),
    ("onclick=\"openQuiz(${q.id})\"", "data-act=\"open-quiz\" data-id=\"${q.id}\""),
    ("onclick=\"doneRemind(${r.id})\"", "data-act=\"done-remind\" data-id=\"${r.id}\""),
    ("onclick=\"copyInvite()\"", "data-act=\"copy-invite\""),
    ("onclick=\"rotateInvite()\"", "data-act=\"rotate-invite\""),
    ("onclick=\"promptEdit()\"", "data-act=\"prompt-edit\""),
    ("onclick=\"revokeGrant('+g.id+')\"", "data-act=\"revoke-grant\" data-id=\"'+g.id+'\""),
    ("onclick=\"openFam(${r.resident_id})\"", "data-act=\"open-fam\" data-rid=\"${r.resident_id}\""),
    ("onclick=\"declineGrant(${r.resident_id},${r.grant_id})\"", "data-act=\"decline-grant\" data-rid=\"${r.resident_id}\" data-gid=\"${r.grant_id}\""),
    ("onclick=\"renderFamilyList()\" style=\"margin-bottom:14px\"", "data-act=\"family-back\" style=\"margin-bottom:14px\""),
    # escape hardening
    ("<p>${esc(comm)} · ${resident.birth_year} 年出生", "<p>${esc(comm)} · ${esc(resident.birth_year)} 年出生"),
    ("${{measure:'测量',questionnaire:'问卷',followup:'随访'}[r.type]||r.type}",
     "${esc({measure:'测量',questionnaire:'问卷',followup:'随访'}[r.type]||r.type)}"),
]

portal_registry = """
/* ---------- data-act 事件注册（替代内联 onclick，配合 CSP script-src 'self'） ---------- */
window.AnlinCore.bindActions({
  'tab': ds => tab(ds.v),
  'trend-range': ds => setTrendRange(Number(ds.days)),
  'set-vt': ds => setVT(ds.metric),
  'del-vital': ds => delVital(Number(ds.id)),
  'page-vitals': ds => pageVitals(Number(ds.step)),
  'open-quiz': ds => openQuiz(Number(ds.id)),
  'done-remind': ds => doneRemind(Number(ds.id)),
  'copy-invite': () => copyInvite(),
  'rotate-invite': () => rotateInvite(),
  'prompt-edit': () => promptEdit(),
  'revoke-grant': ds => revokeGrant(Number(ds.id)),
  'open-fam': ds => openFam(Number(ds.rid)),
  'decline-grant': ds => declineGrant(Number(ds.rid), Number(ds.gid)),
  'family-back': () => renderFamilyList(),
});
"""

# --------------------------------------------------------------- console-app.js
console_js_pairs = [
    ("const clean=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));",
     "const clean=window.AnlinCore.esc;"),
    ("function notify(text){$('toast').textContent=text;$('toast').classList.remove('hidden');setTimeout(()=>$('toast').classList.add('hidden'),4000)}",
     "const notify=window.AnlinCore.makeToast(()=>$('toast'),4000);"),
    ("const code=prompt('此账号已开启两步验证，请输入认证器中的6位验证码：');",
     "const code=await window.AnlinCore.promptDialog('此账号已开启两步验证，请输入认证器中的6位验证码：');"),
    ("async function finishVisit(id){if(!confirm('确认已完成本次走访？'))return;",
     "async function finishVisit(id){if(!(await window.AnlinCore.confirmDialog('确认已完成本次走访？')))return;"),
    ("const ref=prompt('撤回该居民的社区护理授权：\\n请输入机构留存的线下撤回证据编号：');",
     "const ref=await window.AnlinCore.promptDialog('撤回该居民的社区护理授权：请输入机构留存的线下撤回证据编号：');"),
    ("if(!approve){const inp=prompt('驳回原因（可留空）：');if(inp===null)return;if(inp)note=inp}",
     "if(!approve){const inp=await window.AnlinCore.promptDialog('驳回原因（可留空）：');if(inp===null)return;if(inp)note=inp}"),
    ("if(!activate&&!confirm(`停用 ${target.display_name}（@${target.username}）？其当前会话会立即失效。`))return;",
     "if(!activate&&!(await window.AnlinCore.confirmDialog(`停用 ${target.display_name}（@${target.username}）？其当前会话会立即失效。`)))return;"),
    ("onclick=\"showVital(${r.id})\"", "data-act=\"show-vital\" data-id=\"${r.id}\""),
    ("onclick=\"openStaffReport(${r.id})\"", "data-act=\"open-staff-report\" data-id=\"${r.id}\""),
    ("onclick=\"finishVisit(${v.id})", "data-act=\"finish-visit\" data-id=\"${v.id}\""),
    ("onclick=\"showVital('+r.id+')\"", "data-act=\"show-vital\" data-id=\"'+r.id+'\""),
    ("onclick=\"openStaffReport('+r.id+')\"", "data-act=\"open-staff-report\" data-id=\"'+r.id+'\""),
    ("onclick=\"withdrawConsent('+r.id+')\"", "data-act=\"withdraw-consent\" data-id=\"'+r.id+'\""),
    ("onclick=\"pageResidents(-1)\"", "data-act=\"page-residents\" data-step=\"-1\""),
    ("onclick=\"pageResidents(1)\"", "data-act=\"page-residents\" data-step=\"1\""),
    ("onclick=\"reviewClaim('+c.id+',true)\"", "data-act=\"review-claim\" data-id=\"'+c.id+'\" data-approve=\"true\""),
    ("onclick=\"reviewClaim('+c.id+',false)\"", "data-act=\"review-claim\" data-id=\"'+c.id+'\" data-approve=\"false\""),
    ("onclick=\"reviewConsentEvent('+ev.id+',true)\"", "data-act=\"review-consent\" data-id=\"'+ev.id+'\" data-approve=\"true\""),
    ("onclick=\"reviewConsentEvent('+ev.id+',false)\"", "data-act=\"review-consent\" data-id=\"'+ev.id+'\" data-approve=\"false\""),
    ("onclick=\"finishVisit('+v.id+')\"", "data-act=\"finish-visit\" data-id=\"'+v.id+'\""),
    ("onclick=\"pageStats(-1)\"", "data-act=\"page-stats\" data-step=\"-1\""),
    ("onclick=\"pageStats(1)\"", "data-act=\"page-stats\" data-step=\"1\""),
    ("onclick=\"toggleAdminAccount(${u.id},${!u.is_active})\"", "data-act=\"toggle-admin-account\" data-id=\"${u.id}\" data-activate=\"${!u.is_active}\""),
    ("onclick=\"confirmMFA()\"", "data-act=\"confirm-mfa\" data-label=\"confirm\""),
    # role fallback escaping drift spotted in review
    ("const role={staff:'社区工作人员',admin:'系统管理员',resident:'居民',family:'家庭成员'}[me.role]||me.role;",
     "const role=clean({staff:'社区工作人员',admin:'系统管理员',resident:'居民',family:'家庭成员'}[me.role]||me.role);"),
]

console_registry = """
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
})();
"""

portal_html_pairs = [
    ("onclick=\"tab('v-me')\"", "data-act=\"tab\" data-v=\"v-me\""),
    ("onclick=\"tab('v-vitals')\"", "data-act=\"tab\" data-v=\"v-vitals\""),
    ("onclick=\"tab('v-remind')\"", "data-act=\"tab\" data-v=\"v-remind\""),
    ("onclick=\"setTrendRange(7)\"", "data-act=\"trend-range\" data-days=\"7\""),
    ("onclick=\"setTrendRange(30)\"", "data-act=\"trend-range\" data-days=\"30\""),
    ("onclick=\"setTrendRange(90)\"", "data-act=\"trend-range\" data-days=\"90\""),
]
# the same tab() pair appears several times: allow duplicates for the html
def apply_html(text, pairs, label):
    for old, new in pairs:
        count = text.count(old)
        assert count >= 1, f"{label}: no hit for {old!r}"
        text = text.replace(old, new)
    return text

console_html_pairs = [
    ("onclick=\"switchView('overview')\"", "data-act=\"switch-view\" data-view=\"overview\""),
    ("onclick=\"switchView('residents')\"", "data-act=\"switch-view\" data-view=\"residents\""),
    ("onclick=\"switchView('visits')\"", "data-act=\"switch-view\" data-view=\"visits\""),
    ("onclick=\"switchView('stats')\"", "data-act=\"switch-view\" data-view=\"stats\""),
    ("onclick=\"switchView('settings')\"", "data-act=\"switch-view\" data-view=\"settings\""),
    ("onclick=\"logout()\"", "data-act=\"logout\""),
    ("onclick=\"showCreate()\"", "data-act=\"show-create\""),
    ("oninput=\"searchResidents(this.value)\"", "data-input-act=\"search-residents\""),
    ("onclick=\"showVisit()\"", "data-act=\"show-visit\""),
    ("onclick=\"showProvision()\"", "data-act=\"show-provision\""),
    ("oninput=\"searchStats(this.value)\"", "data-input-act=\"search-stats\""),
    ("onclick=\"changePassword()\"", "data-act=\"change-password\""),
    ("onclick=\"setupMFA()\"", "data-act=\"setup-mfa\""),
    ("onclick=\"disableMFA()\"", "data-act=\"disable-mfa\""),
    ("onclick=\"logoutAllDevices()\"", "data-act=\"logout-all\""),
    ("onsubmit=\"event.preventDefault();login()\"", ""),
    ("onclick=\"if(event.target===this)closeModal()\"", ""),
    ("onclick=\"closeModal()\"", "data-act=\"close-modal\""),
]


def main():
    p = STATIC / "js" / "portal-app.js"
    text = apply(p.read_text(encoding="utf-8"), portal_js_pairs, "portal-app.js")
    assert "bindActions" not in text
    marker = "})();"
    idx = text.rfind(marker)
    text = text[:idx] + portal_registry + text[idx:]
    p.write_text(text, encoding="utf-8")

    c = STATIC / "js" / "console-app.js"
    text = apply(c.read_text(encoding="utf-8"), console_js_pairs, "console-app.js")
    assert "bindActions" not in text
    c.write_text(text.rstrip("\n") + "\n" + console_registry, encoding="utf-8")

    ph = STATIC / "portal.html"
    ph.write_text(apply_html(ph.read_text(encoding="utf-8"), portal_html_pairs, "portal.html"), encoding="utf-8")
    ch = STATIC / "console.html"
    ch.write_text(apply_html(ch.read_text(encoding="utf-8"), console_html_pairs, "console.html"), encoding="utf-8")
    for f in ("portal.html", "console.html", "js/portal-app.js", "js/console-app.js"):
        body = (STATIC / f).read_text(encoding="utf-8")
        # Quoted onclick="..." inside markup is an inline CSP-blocked handler;
        # programmatic el.onclick = fn assignments from external files are fine.
        assert 'onclick="' not in body, f"inline handler left in {f}"
        assert "oninput=" not in body, f"inline input handler left in {f}"
        assert "onsubmit=" not in body, f"inline submit handler left in {f}"
    print("conversion complete; no inline handlers remain")


if __name__ == "__main__":
    main()
