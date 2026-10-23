/* Date-based measurement charts. No clinical thresholds or diagnosis. */
(function (root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const utcDate = value => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z');
  const dateLabel = (value, timezone, options={dateStyle:'medium',timeStyle:'short'}) => utcDate(value).toLocaleString('zh-CN',{...options,timeZone:timezone || 'Asia/Shanghai'});
  const contexts = {fasting:'空腹', before_meal:'餐前', after_meal:'餐后', unknown:'进餐情况未注明'};
  const labels = {blood_pressure:['血压','mmHg'], blood_glucose:['血糖','mmol/L'], weight:['体重','kg']};
  function valueText(point) {
    return point.type === 'blood_pressure' ? `${point.systolic} / ${point.diastolic}` : String(point.type === 'blood_glucose' ? point.glucose_mmol_l : point.weight_kg);
  }
  function geometry(points, metric, start, end, viewportWidth=740) {
    const keys = metric === 'blood_pressure' ? ['systolic','diastolic'] : [metric === 'blood_glucose' ? 'glucose_mmol_l':'weight_kg'];
    const values = points.flatMap(p => keys.map(k => Number(p[k]))).filter(Number.isFinite);
    if (!values.length) return null;
    const spread = Math.max(Math.max(...values)-Math.min(...values), metric === 'blood_glucose' ? 1 : 5);
    const low = Math.max(0, Math.floor((Math.min(...values)-spread*.22)*10)/10);
    const high = Math.ceil((Math.max(...values)+spread*.22)*10)/10;
    const svgWidth=Number.isFinite(viewportWidth) && viewportWidth>0 ? viewportWidth : 740;
    const compact=svgWidth<520;
    const left=compact?48:54, top=compact?18:24, width=Math.max(1,svgWidth-left-(compact?24:36)), height=compact?150:210;
    const svgHeight=compact?214:282, dateY=compact?198:267, dateTickCount=compact?3:5;
    const x = point => left + Math.max(0, Math.min(1, (utcDate(point.measured_at).getTime()-start)/Math.max(1,end-start)))*width;
    const y = value => top + (high-value)/(high-low)*height;
    return {keys,low,high,left,top,width,height,svgWidth,svgHeight,dateY,dateTickCount,x,y};
  }
  function draw(data, viewportWidth=740) {
    const points=data.points || [], metric=data.metric, info=labels[metric];
    if (!points.length) return '<div class="chart-empty"><span class="empty-chart-icon" aria-hidden="true">⌁</span><b>这段时间还没有记录</b><p>记下第一次测量，慢慢看见自己的变化。</p></div>';
    const start = new Date(data.window_start).getTime(), end = new Date(data.window_end).getTime()-1;
    const g=geometry(points,metric,start,end,viewportWidth), colors=['#176b5b','#b67b3c'];
    const ticks=Array.from({length:5},(_,i)=>{
      const val=g.low+(g.high-g.low)*i/4, y=g.y(val);
      return `<line x1="${g.left}" y1="${y}" x2="${g.left+g.width}" y2="${y}" stroke="#e5ece7" stroke-dasharray="3 5"/><text x="${g.left-12}" y="${y+4}" text-anchor="end" fill="#78867e" font-size="12">${Number(val.toFixed(1))}</text>`;
    }).join('');
    const lines=g.keys.map((key,i)=>`<polyline fill="none" stroke="${colors[i]}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round" points="${points.map(p=>`${g.x(p).toFixed(2)},${g.y(p[key]).toFixed(2)}`).join(' ')}"/>` + points.map(p=>`<circle class="trend-point" tabindex="0" role="button" data-point="${p.id}" aria-label="${escape(dateLabel(p.measured_at,data.timezone))}，${escape(valueText(p))} ${info[1]}，查看详情" cx="${g.x(p).toFixed(2)}" cy="${g.y(p[key]).toFixed(2)}" r="4.5" fill="white" stroke="${colors[i]}" stroke-width="2"><title>${escape(valueText(p))} ${info[1]}</title></circle>`).join('')).join('');
    const dateTicks=Array.from({length:g.dateTickCount},(_,i)=>{
      const fraction=i/(g.dateTickCount-1), d=new Date(start+(end-start)*fraction);
      return `<text x="${g.left+g.width*fraction}" y="${g.dateY}" text-anchor="middle" font-size="12" fill="#78867e">${d.toLocaleDateString('zh-CN',{timeZone:data.timezone,month:'numeric',day:'numeric'})}</text>`;
    }).join('');
    return `<div class="chart-legend"><span><i></i>${metric==='blood_pressure'?'收缩压（高压）':info[0]}</span>${metric==='blood_pressure'?'<span><i class="secondary"></i>舒张压（低压）</span>':''}<small>${info[1]}</small></div><svg class="health-chart" viewBox="0 0 ${g.svgWidth} ${g.svgHeight}" role="group" aria-label="${info[0]}测量趋势，横轴为实际日期">${ticks}${lines}${dateTicks}</svg>`;
  }
  function detail(point, timezone) {
    const info=labels[point.type];
    return `<div class="point-value">${escape(valueText(point))} <small>${info[1]}</small></div><div class="point-meta"><b>测量于 ${escape(dateLabel(point.measured_at,timezone))}</b><span>${point.source==='report'?'报告导入':'手动记录'}${contexts[point.measurement_context]?' · '+contexts[point.measurement_context]:''}</span>${point.created_at?`<span>录入于 ${escape(dateLabel(point.created_at,timezone,{dateStyle:'short',timeStyle:'short'}))}</span>`:''}${point.source_needs_review?'<span class="source-warning">来源报告已修订，这条记录需要重新核对</span>':''}</div>`;
  }
  function render(container, data) {
    const points=data.points || [];
    // Match SVG units to the visible container so 12px axis labels stay readable.
    // Measuring only when rendering avoids observers replacing a focused point.
    const viewportWidth=container.clientWidth;
    const latest=points.at(-1), days=new Set(points.map(p=>utcDate(p.measured_at).toLocaleDateString('sv-SE',{timeZone:data.timezone}))).size;
    container.innerHTML=(latest?`<div class="chart-summary"><div><span>最近一次</span><strong>${escape(valueText(latest))}<small>${labels[data.metric][1]}</small></strong></div><div><span>本期记录</span><strong>${points.length}<small>次</small></strong></div><div><span>记录天数</span><strong>${days}<small>天</small></strong></div></div>`:'')+draw(data,viewportWidth)+(latest?`<div class="point-detail" aria-live="polite">${detail(latest,data.timezone)}</div><p class="chart-footnote">点击图上的圆点查看记录。折线连接已记录数值，不代表未测量日期的情况。</p>`:'');
    const show = event => {
      const id=event.target.getAttribute?.('data-point');
      const point=points.find(p=>String(p.id)===id);
      if(point) container.querySelector('.point-detail').innerHTML=detail(point,data.timezone);
    };
    container.onclick=show;
    container.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){if(event.target.hasAttribute?.('data-point')){event.preventDefault();show(event)}}};
  }
  root.AnlinTrend={render,geometry,draw,valueText};
})(typeof window !== 'undefined' ? window : globalThis);
