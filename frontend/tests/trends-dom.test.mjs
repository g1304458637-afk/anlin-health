import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import vm from 'node:vm';
import test from 'node:test';

const root=resolve(import.meta.dirname,'../..');
const context=vm.createContext({Date,Intl});context.window=context;
vm.runInContext(readFileSync(resolve(root,'backend/static/trends.js'),'utf8'),context);
const chart=context.AnlinTrend;
const point=(id,day,high=125,low=80)=>({id,type:'blood_pressure',systolic:high,diastolic:low,measured_at:`2026-10-${String(day).padStart(2,'0')}T00:00:00Z`,source:'manual'});

test('trend spacing follows elapsed time, and includes both blood pressure values',()=>{
  const points=[point(1,1),point(2,2,130,83),point(3,11,122,78)];
  const g=chart.geometry(points,'blood_pressure',Date.parse('2026-10-01T00:00:00Z'),Date.parse('2026-10-11T00:00:00Z'));
  assert.equal(g.keys.length,2);
  assert.ok(Math.abs((g.x(points[2])-g.x(points[1]))/(g.x(points[1])-g.x(points[0]))-9)<1e-8);
  assert.ok(g.y(130)<g.y(83));
  const html=chart.draw({metric:'blood_pressure',points,timezone:'Asia/Shanghai',window_start:'2026-09-30T16:00:00Z',window_end:'2026-10-11T16:00:00Z'});
  assert.equal((html.match(/<polyline/g)||[]).length,2);
  assert.match(html,/舒张压/);
  assert.match(html,/2026年10月1日/);
  assert.match(html,/viewBox="0 0 740 282"/);
  assert.equal((html.match(/text-anchor="middle"/g)||[]).length,5);
  assert.doesNotMatch(html,/NaN/);
});

for (const width of [314,390,960]) {
  test(`render uses the ${width}px container width without scaling down axis labels`,()=>{
    const points=[point(1,1),point(2,11,130,83)];
    const data={metric:'blood_pressure',points,timezone:'Asia/Shanghai',window_start:'2026-09-30T16:00:00Z',window_end:'2026-10-11T16:00:00Z'};
    const container={clientWidth:width,innerHTML:''};
    chart.render(container,data);
    const html=container.innerHTML, compact=width<520;
    assert.match(html,new RegExp(`viewBox="0 0 ${width} ${compact?214:282}"`));
    assert.equal((html.match(/text-anchor="middle"/g)||[]).length,compact?3:5);
    const textTags=html.match(/<text\b[^>]*>/g);
    assert.equal(textTags.length,compact?8:10);
    assert.ok(textTags.every(tag=>tag.includes('font-size="12"')));
    assert.equal((html.match(/<polyline/g)||[]).length,2);
    assert.equal((html.match(/class="trend-point"/g)||[]).length,4);
    for (const match of html.matchAll(/<circle[^>]*cx="([\d.]+)" cy="([\d.]+)"/g)) {
      assert.ok(Number(match[1])>=48 && Number(match[1])<=width-24);
      assert.ok(Number(match[2])>=18 && Number(match[2])<(compact?198:267));
    }
    assert.doesNotMatch(html,/NaN|Infinity/);
  });
}

test('missing or hidden container dimensions keep the default exported chart layout',()=>{
  const data={metric:'blood_pressure',points:[point(1,1)],timezone:'Asia/Shanghai',window_start:'2026-09-30T16:00:00Z',window_end:'2026-10-01T16:00:00Z'};
  for (const width of [undefined,0,NaN]) {
    const container={clientWidth:width,innerHTML:''};
    chart.render(container,data);
    assert.match(container.innerHTML,/viewBox="0 0 740 282"/);
  }
});

test('mobile chart points still support click and keyboard details after rendering again',()=>{
  const detail={innerHTML:''},container={clientWidth:314,innerHTML:'',querySelector:()=>detail};
  const data={metric:'blood_pressure',points:[point(1,1,125,80),point(2,2,130,83)],timezone:'Asia/Shanghai',window_start:'2026-09-30T16:00:00Z',window_end:'2026-10-02T16:00:00Z'};
  chart.render(container,data);
  chart.render(container,data);
  container.onclick({target:{getAttribute:()=> '1'}});
  assert.match(detail.innerHTML,/125 \/ 80/);
  for (const key of ['Enter',' ']) {
    let prevented=false;
    container.onkeydown({key,target:{hasAttribute:()=>true,getAttribute:()=> '2'},preventDefault:()=>{prevented=true;}});
    assert.equal(prevented,true);
    assert.match(detail.innerHTML,/130 \/ 83/);
  }
});

test('empty date windows never fabricate a line or a value',()=>{
  const html=chart.draw({metric:'weight',points:[]});
  assert.match(html,/这段时间还没有记录/);
  assert.doesNotMatch(html,/<polyline|<circle/);
});

test('point details retain measurement context and source-review warnings',()=>{
  const detail={innerHTML:''},container={innerHTML:'',querySelector:()=>detail};
  const p={id:1,type:'blood_glucose',glucose_mmol_l:5.6,measurement_context:'after_meal',source:'report',source_needs_review:true,measured_at:'2026-10-01T16:30:00Z',created_at:'2026-10-02T06:00:00Z'};
  chart.render(container,{metric:'blood_glucose',points:[p],timezone:'Asia/Shanghai',window_start:'2026-10-01T16:00:00Z',window_end:'2026-10-02T16:00:00Z'});
  assert.match(container.innerHTML,/餐后/);
  assert.match(container.innerHTML,/需要重新核对/);
  assert.match(container.innerHTML,/2026年10月2日/);
  container.onclick({target:{getAttribute:()=> '1'}});
  assert.match(detail.innerHTML,/00:30/);
});
