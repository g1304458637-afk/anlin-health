"""OPTIONAL visual/browser test of admin HTML with FAKE API responses.

This does NOT test real backend integration. It uses Playwright, Chromium, and
entirely fictional in-memory data; all network calls are mocked in-browser.
Install Playwright separately and have Chrome/Chromium available.
"""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / 'backend/static/console.html').read_text(encoding='utf-8')
OUTPUT = ROOT / 'assets/管理工作台_静态效果图.png'

MOCK = r"""() => {
  window._fake = {
    residents: [
      {id:1,name:'王静（虚构）',birth_year:1957,owner_user_id:3,consent_active:true},
      {id:2,name:'陈平（虚构）',birth_year:1950,owner_user_id:null,consent_active:true},
      {id:3,name:'赵桂芳（虚构）',birth_year:1949,owner_user_id:null,consent_active:true}],
    visits:[{id:1,resident_id:2,staff_id:1,status:'planned',due_date:'2026-10-22'}],
    vitals:[]};
  const j=(body, status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
  window.fetch = async (url, opts={}) => {
    const u=String(url),method=(opts.method||'GET').toUpperCase(),data=opts.body?JSON.parse(opts.body):{};
    if(u==='/api/auth/dev-login')return j({access_token:'FAKE_UI_ONLY',user:{id:1,role:'staff',display_name:'社区工作者',community_id:1}});
    if(u.startsWith('/api/community/residents')&&method==='GET')return j(window._fake.residents);
    if(u==='/api/community/visits'&&method==='GET')return j(window._fake.visits);
    if(u==='/api/community/stats')return j({resident_count:window._fake.residents.length});
    if(u==='/api/community/residents'&&method==='POST'){
      const row={id:window._fake.residents.length+1,owner_user_id:null,...data,consent_active:true};window._fake.residents.push(row);return j(row,201);
    }
    if(u.match(/\/api\/residents\/\d+\/vitals/)&&method==='POST'){
      const row={id:window._fake.vitals.length+1,...data};window._fake.vitals.push(row);return j(row,201);
    }
    if(u==='/api/community/visits'&&method==='POST'){
      const row={id:window._fake.visits.length+1,staff_id:1,status:'planned',...data};window._fake.visits.push(row);return j(row,201);
    }
    if(u.endsWith('/finish')&&method==='PATCH'){
      const id=Number(u.match(/visits\/(\d+)/)[1]),v=window._fake.visits.find(x=>x.id===id);v.status='done';return j(v);
    }
    return j({detail:'未覆盖的 UI 模拟请求 '+u},404);
  };
}"""


def main():
    with sync_playwright() as p:
        args=['--no-sandbox','--disable-dev-shm-usage']
        opts={'headless':True,'args':args}
        exe=shutil.which('chromium') or shutil.which('google-chrome')
        if exe:opts['executable_path']=exe
        b=p.chromium.launch(**opts)
        page=b.new_page(viewport={'width':1440,'height':900}, device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content(HTML, wait_until='load')
        page.evaluate(MOCK)
        page.locator('#password').fill('FAKE_LOCAL_ONLY')
        page.locator('.login-card button[type=submit]').click()
        page.wait_for_function("document.querySelector('#residentCount').textContent==='3'")
        assert not page.locator('#loginShade').is_visible()
        page.locator('#newResident').click()
        page.locator('input[name=name]').fill('孙林（虚构）')
        page.locator('input[name=birth_year]').fill('1959')
        page.locator('input[name=evidence_ref]').fill('DEMO-NONREAL-123')
        page.locator('input[name=consent]').check()
        page.locator('#modalSubmit').click()
        page.wait_for_function("document.querySelector('#residentCount').textContent==='4'")
        page.get_by_role('button',name='＋ 记录指标').first.click()
        page.locator('#metricType').select_option('blood_glucose')
        page.locator('input[name=glucose]').fill('5.8')
        page.locator('#modalSubmit').click()
        page.wait_for_function("document.querySelector('#overlay').style.display==='none'")
        page.screenshot(path=str(OUTPUT),full_page=True)
        if errors:raise RuntimeError(f'JavaScript browser errors: {errors}')
        assert page.evaluate('window._fake.vitals.length')==1
        assert page.evaluate('window._fake.residents.length')==4
        print('UI mock smoke passed: login, resident registration, vitals entry; saved screenshot:', OUTPUT)
        b.close()

if __name__=='__main__':main()
