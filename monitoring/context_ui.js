/* Offline, self-contained background viewer. No external requests or libraries. */
(() => {
  'use strict';
  const payload = JSON.parse(document.getElementById('background-data').textContent);
  const {context, frequency, companyNames, videoURLs, scope} = payload;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const link = (url, label, cls='') => `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
  const body = document.getElementById('comparison-body');
  const rows = [...body.rows];
  const sortSelect = document.getElementById('sort-companies');
  const singleFilter = document.getElementById('single-company');
  function sortComparison() {
    const mode = sortSelect.value;
    rows.sort((a,b) => {
      const A=a.dataset, B=b.dataset;
      const byDate=B.date.localeCompare(A.date);
      const byMentions=Number(B.mentions)-Number(A.mentions);
      const byVideos=Number(B.videos)-Number(A.videos);
      return (mode==='date' ? byDate||byMentions : mode==='videos' ? byVideos||byDate : byMentions||byDate) || A.company.localeCompare(B.company);
    });
    let rank=0;
    for (const row of rows) {
      row.hidden=singleFilter.checked && row.dataset.group==='1';
      if(!row.hidden) row.querySelector('.rank').textContent=String(++rank).padStart(2,'0');
      body.append(row);
    }
    document.getElementById('sort-summary').textContent=`显示 ${rank} 个公司 / 专题`;
  }
  sortSelect.addEventListener('change',sortComparison);
  singleFilter.addEventListener('change',sortComparison);
  sortComparison();

  const dialog=document.getElementById('context-dialog');
  const panels=['summary','data','sources'];
  const tabs=panels.map(id=>document.getElementById(`tab-${id}`));
  let previousFocus=null, previousOverflow='';
  function selectTab(index, focus=false) {
    tabs.forEach((tab,i)=>{
      tab.setAttribute('aria-selected',String(i===index));
      tab.tabIndex=i===index?0:-1;
      document.getElementById(`panel-${panels[i]}`).hidden=i!==index;
    });
    if(focus) tabs[index].focus();
  }
  tabs.forEach((tab,i)=>{
    tab.addEventListener('click',()=>selectTab(i));
    tab.addEventListener('keydown',e=>{
      let next=i;
      if(e.key==='ArrowRight')next=(i+1)%3;
      else if(e.key==='ArrowLeft')next=(i+2)%3;
      else if(e.key==='Home')next=0;
      else if(e.key==='End')next=2;
      else return;
      e.preventDefault();selectTab(next,true);
    });
  });
  function openDialog(title,kicker,opinion,contents,labels) {
    previousFocus=document.activeElement;
    document.getElementById('context-title').textContent=title;
    document.getElementById('context-kicker').textContent=kicker;
    document.getElementById('context-opinion').innerHTML=opinion;
    panels.forEach((id,i)=>{
      const panel=document.getElementById(`panel-${id}`);
      panel.innerHTML=contents[i];panel.scrollTop=0;
      tabs[i].textContent=labels[i];
    });
    selectTab(0);
    previousOverflow=document.body.style.overflow;
    document.body.style.overflow='hidden';
    dialog.showModal();
    document.getElementById('close-context').focus();
  }
  document.getElementById('close-context').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
  dialog.addEventListener('close',()=>{document.body.style.overflow=previousOverflow;previousFocus?.focus();});
  const lateNote=(date,pc)=>{
    const first=pc.video_dates[0], last=pc.video_dates.at(-1);
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date))return '';
    if(date>last)return '<span class="later">晚于该条所引视频 · 后续核对</span>';
    if(date>first)return '<span class="later">晚于该条较早视频 · 按日期对照</span>';
    return '';
  };
  const sourceLinks=ev=>`<div class="context-source-links">${ev.sources.map(id=>link(context.sources[id].url,context.sources[id].title)).join('')}</div>`;
  const eventHeader=(ev,pc)=>`<div class="event-meta"><span class="event-date">${esc(ev.date)}</span>${lateNote(ev.date,pc)}</div><h3>${esc(ev.title)}</h3>`;
  const limitBox=(title,text)=>text?`<div class="context-limit"><b>${esc(title)}</b>${esc(text)}</div>`:'';
  function showContext(key) {
    const pc=context.points[key];
    const items=pc.events.map(id=>context.events[id]);
    const sourceIds=[...new Set(items.flatMap(x=>x.sources))];
    const opinion=`<p class="opinion-label">频道观点 · ${pc.video_dates.map(esc).join(' / ')} · ${esc(pc.attribution)}</p><p>${esc(pc.opinion)}</p><div>${pc.video_refs_html}</div>`;
    const summary=`<div class="context-connection"><b>这些材料与本条观点的关系</b>${esc(pc.connection)}</div>${limitBox('本条核对边界',pc.limit)}${items.map(ev=>`<article class="background-event">${eventHeader(ev,pc)}<p>${esc(ev.summary)}</p><p class="event-boundary">${esc(ev.caveat)}</p>${sourceLinks(ev)}</article>`).join('')}`;
    const data=`<p class="modal-method">以下数字只取自所列公开资料；公司预测、市场报价、额度上限与已实现业绩分别标注。无可靠数字时保留定性背景。</p>${items.map(ev=>`<article class="background-event">${eventHeader(ev,pc)}${ev.facts.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>指标</th><th>披露值</th><th>期间 / 口径 / 状态</th></tr></thead><tbody>${ev.facts.map(f=>`<tr>${f.map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<p class="empty-data">这份背景以产品、事件或方法为主，没有补入无法确认的数字。</p>'}<p class="event-boundary">${esc(ev.caveat)}</p>${sourceLinks(ev)}</article>`).join('')}`;
    const sources=`${limitBox('能核实到哪里',`${pc.status}。${pc.limit||'公开资料提供事件和数据背景；频道对估值、趋势、护城河或未来概率的判断仍保留原归属。'}`)}<p class="modal-method">${esc(context.method)}</p>${sourceIds.map((id,i)=>{const s=context.sources[id];return `<article class="source-item"><h3>${i+1}. ${link(s.url,s.title)}</h3><div class="event-meta"><span class="event-date">${esc(s.date)}</span>${esc(s.kind)} ${lateNote(s.date,pc)}</div><p>查阅位置：${esc(s.locator)}</p><p>读取情况：${esc(s.access)}</p>${link(s.url,s.url,'source-link')}</article>`;}).join('')}`;
    openDialog(pc.company,`观点的外部背景 · 研究截至${esc(scope.as_of)}`,opinion,[summary,data,sources],['背景与关联','关键数据','原始资料']);
  }
  function showFrequency(id) {
    const f=frequency.companies[id];
    const videos=[...f.videos].sort((a,b)=>b.date.localeCompare(a.date));
    const aliases=[...new Set(videos.flatMap(v=>v.evidence.flatMap(h=>h.matches)))].sort((a,b)=>a.localeCompare(b));
    const time=s=>`${Math.floor(s/60).toString().padStart(2,'0')}:${Math.floor(s%60).toString().padStart(2,'0')}`;
    const urlAt=(bvid,start)=>{const u=new URL(videoURLs[bvid]);u.searchParams.set('t',String(Math.floor(start)));return u.href;};
    const stats=`<div class="frequency-stats"><div><b>${f.mention_count}</b><small>名称 / 别名提及次数</small></div><div><b>${f.video_count} / ${scope.videos}</b><small>至少涉及一次的视频</small></div><div><b>${esc(f.latest_opinion_date)}</b><small>最新实质观点日期</small></div></div>`;
    const method=`<p>${esc(frequency.method)}</p><p>本项口径：<b>${esc(f.kind)}</b>。同一视频重复出现累计次数，视频覆盖只计一期。SpaceX统计含公司其他业务的名称；行业合集可和单一公司重叠。它衡量出现频率，不衡量赞同程度或研究深度。</p><p class="event-boundary">原始ASR会漏识别或误识别；下列列出实际匹配的名称和片段供检查，不能将机器统计当人工逐字校对。</p>`;
    const summary=stats+method+`<h3>这次实际匹配到的名称 / 别名</h3><div class="frequency-aliases">${aliases.map(x=>`<span>${esc(x)}</span>`).join('')}</div>`;
    const table=`<div class="table-wrap"><table class="frequency-count-table"><thead><tr><th>日期</th><th>次数</th><th>视频</th></tr></thead><tbody>${videos.map(v=>`<tr><td>${esc(v.date)}</td><td>${v.mentions}</td><td>${link(videoURLs[v.bvid],v.title)}</td></tr>`).join('')}</tbody></table></div>`;
    const evidence=`<p class="modal-method">按日期倒序。展开一期开启该期的全部匹配片段；时间链接回到视频相应位置。</p>${videos.map(v=>`<details class="frequency-video"><summary>${esc(v.date)} · ${v.mentions}次 · ${esc(v.title)}</summary><ul>${v.evidence.map(h=>`<li>${link(urlAt(v.bvid,h.start),time(h.start))} ${esc(h.text)}<small>匹配：${h.matches.map(esc).join('、')}</small></li>`).join('')}</ul></details>`).join('')}`;
    openDialog(companyNames[id],`出现频率 · ${scope.videos}期原始转写`,`<p>统计期间：${esc(scope.date_from)}—${esc(scope.date_to)} · ${esc(f.kind)}</p>`,[summary,table,evidence],['统计口径','涉及视频','识别片段']);
  }
  document.addEventListener('click',e=>{
    const contextButton=e.target.closest('[data-context]');
    if(contextButton){showContext(contextButton.dataset.context);return;}
    const frequencyButton=e.target.closest('[data-frequency]');
    if(frequencyButton)showFrequency(frequencyButton.dataset.frequency);
  });
})();
