"""Render the manually synthesized, source-linked company report offline."""
from pathlib import Path
import argparse
import datetime as dt
import html
import json
import os
import sys
from urllib.parse import quote

assets = Path(__file__).resolve().parent
project = assets.parent
sys.path.insert(0, str(project))
import bilibili_transcribe as core

parser = argparse.ArgumentParser()
parser.add_argument('--task-dir', type=Path, required=True, help='Directory containing batch_manifest.json and 研究证据/')
parser.add_argument('--final', action='store_true')
parser.add_argument('--output-dir', type=Path, help='Render to a staging directory before publishing')
args = parser.parse_args()
root = args.task_dir.expanduser().resolve() / '研究证据'
manifest = json.loads((root.parent / 'batch_manifest.json').read_text())
catalog = {x['bvid']: x for x in manifest['items']}
done = sum(x['status'] == 'completed' for x in manifest['items'])
total = len(catalog)
scope = dict(date_from=manifest['date_from'], date_to=manifest['date_to'], videos=total, completed=done, final=args.final)
framework = json.loads((root / 'framework_notes.json').read_text())
context = json.loads((root / 'external_context.json').read_text())
frequency = json.loads((root / 'frequency.json').read_text())
framework['method'] = framework['method'].replace('没有独立核验视频所引财报、招股书、媒体消息或模型测评。', '本次为每条观点补充公开财报、申报文件、产品公告或新闻背景；背景页逐项标明来源与尚未确认之处。频道私有模型及未取得的原始数据未作独立复算。')
companies = []
for filename in ['company_notes.json', 'labs_notes.json', 'supply_notes.json', 'extras_notes.json']:
    companies.extend(json.loads((root / filename).read_text())['companies'])
if args.final:
    assert total > 0 and done == total == len(manifest['items']), 'All catalog videos must complete first'
    assert all(not c.get('pending') for c in companies), 'Complete outstanding research notes'
assert len({c['id'] for c in companies}) == len(companies)
order = ['microsoft', 'nvidia', 'alphabet', 'amazon', 'meta', 'apple', 'tesla', 'openai', 'anthropic']
companies.sort(key=lambda c: order.index(c['id']) if c['id'] in order else len(order))
out = args.output_dir.resolve() if args.output_dir else root.parent / 'AI公司观点整理'
out.mkdir(parents=True, exist_ok=True)
e = html.escape
used = set()


def timestamp(s):
    s = int(s)
    return f'{s // 60:02d}:{s % 60:02d}'


def local(path):
    return quote(os.path.relpath(path, out), safe='/')


def refs(sources):
    result = []
    for bvid, start, end in sources:
        item = catalog[bvid]
        assert item['status'] == 'completed', bvid
        assert 0 <= start < end <= item['audio_duration_seconds'] + 2, (bvid, start, end)
        used.add(bvid)
        label = f"{item['date'][5:]} {timestamp(start)}–{timestamp(end)}"
        result.append(f'<a class="source" href="{e(item["url"], quote=True)}" target="_blank" title="{e(item["title"], quote=True)}">{label} ↗</a> <a class="local-source" href="{local(item["srt"])}">时间轴</a>')
    return ' '.join(result)


nav = ''.join(f'<a href="#{c["id"]}">{e(c["name"])}</a>' for c in companies)
comparison_order = sorted(companies, key=lambda c: (-frequency['companies'][c['id']]['mention_count'], -int(c['last_substantive_date'].replace('-', '')), c['id']))
overview_rows = []
for rank, c in enumerate(comparison_order, 1):
    f = frequency['companies'][c['id']]
    group = '合集 / 行业' if f['kind'] == '公司并集/产业链关键词' else '单一公司'
    overview_rows.append(f'<tr data-company="{c["id"]}" data-mentions="{f["mention_count"]}" data-videos="{f["video_count"]}" data-date="{c["last_substantive_date"]}" data-group="{int(group != "单一公司")}"><td><span class="rank">{rank:02d}</span><a href="#{c["id"]}">{e(c["name"])}</a><small>{e(c["group"])} · {group}</small></td><td class="frequency-cell"><button class="frequency-button" data-frequency="{c["id"]}" aria-label="查看{e(c["name"], quote=True)}出现频率的统计依据"><b>{f["mention_count"]}次</b><small>{f["video_count"]} / {total}期 · 查看统计</small></button></td><td class="date-cell">{c["last_substantive_date"]}</td><td>{e(c["stance"])}</td><td>{e("；".join(c["watch"][:3]))}</td></tr>')
overview = ''.join(overview_rows)
themes = ''.join(f'<article class="theme"><h3>{e(t["title"])}</h3><p>{e(t["text"])}</p><p class="attribution">{e(t["attribution"])}</p><div>{refs(t["sources"])}</div></article>' for t in framework['themes'])
cards = []
for c in companies:
    point_html = []
    for n, p in enumerate(c['points']):
        key = f'{c["id"]}:{n}'
        pc = context['points'][key]
        pc['company'] = c['name']
        pc['opinion'] = p['text']
        pc['attribution'] = p['attribution']
        pc['video_dates'] = sorted({catalog[s[0]]['date'] for s in p['sources']})
        pc['video_refs_html'] = refs(p['sources'])
        status_class = ' partial' if pc['status'] != '公开背景已查阅' else ''
        point_html.append(f'<li id="point-{c["id"]}-{n}"><p>{e(p["text"])}</p><p class="attribution">归属：{e(p["attribution"])}</p><div class="refs">{refs(p["sources"])}</div><div class="context-entry"><button class="context-button" data-context="{key}" aria-haspopup="dialog" aria-label="查看{e(c["name"], quote=True)}第{n+1}条观点的外部背景">查看背景 <span>{len(pc["events"])}份背景</span> ↗</button><span class="context-status{status_class}">{e(pc["status"])}</span></div></li>')
        p['external_context_key'] = key
    points = ''.join(point_html)
    checks = ''.join(f'<li>{e(w)}</li>' for w in c['watch'])
    open_attr = ' open' if c['id'] in order else ''
    cards.append(f'<details class="company" id="{c["id"]}"{open_attr}><summary><span><b>{e(c["name"])}</b><small>{e(c["group"])} · 最近实质观点 {e(c["last_substantive_date"])}</small></span></summary><div class="company-body"><p class="stance">{e(c["stance"])}</p><p>{e(c["summary"])}</p><ol>{points}</ol><h4>频道观点对应的后续观察项</h4><ul class="watch">{checks}</ul><a href="{quote(core.sanitize_title(c["name"]), safe="")}_观点整理.txt">本公司文本版</a></div></details>')
    lines = [c['name'], c['stance'], '', c['summary'], '']
    for n, p in enumerate(c['points'], 1):
        lines.extend([f'{n}. {p["text"]}', f'归属：{p["attribution"]}'])
        for bvid, start, end in p['sources']:
            item = catalog[bvid]
            lines.append(f'来源：{item["date"]} {timestamp(start)}–{timestamp(end)} {item["title"]} {item["url"]}')
        pc = context['points'][p['external_context_key']]
        lines.append('外部背景关联：' + pc['connection'])
        if pc['limit']:
            lines.append('核对边界：' + pc['limit'])
        for source_key in dict.fromkeys(s for event_key in pc['events'] for s in context['events'][event_key]['sources']):
            s = context['sources'][source_key]
            lines.append(f'背景资料：{s["date"]} {s["title"]} {s["url"]}')
        lines.append('')
    lines.extend(['后续观察项：', *['- ' + w for w in c['watch']], '', framework['method']])
    (out / (core.sanitize_title(c['name']) + '_观点整理.txt')).write_text('\n'.join(lines) + '\n', encoding='utf-8')
timeline = ''.join(f'<tr><td>{e(t["date"])}</td><td>{e(t["company"])}</td><td>{e(t["text"])}<div>{refs(t["sources"])}</div></td></tr>' for t in framework['chronology'])
metrics = ''.join(f'<tr><th>{e(a)}</th><td>{e(b)}</td></tr>' for a, b in framework['metric_rules'])
source_rows = ''.join(f'<tr><td>{i["date"]}</td><td><a href="{i["url"]}" target="_blank">{e(i["title"])}</a></td><td>{i["duration"]}</td><td><a href="{local(i["readable_transcript"])}">简体分段</a> · <a href="{local(i["raw_transcript"])}">原始稿</a> · <a href="{local(i["srt"])}">时间轴</a></td></tr>' if i['status'] == 'completed' else f'<tr><td>{i["date"]}</td><td>{e(i["title"])}</td><td>{i["duration"]}</td><td>处理中</td></tr>' for i in manifest['items'])
status = '完整稿' if args.final else f'研究草稿 · {done}/{total}视频已完成'
total_seconds = sum(float(i.get('audio_duration_seconds') or sum(int(v) * 60 ** n for n, v in enumerate(reversed(i['duration'].split(':'))))) for i in manifest['items'])
hours, minutes = divmod(round(total_seconds / 60), 60)
chars = sum(len(Path(i['raw_transcript']).read_text()) for i in manifest['items'] if i['status'] == 'completed')
document = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>美投侃热点：AI公司与产业链观点整理</title>
<style>
:root{--ink:#1f2c3d;--muted:#66758a;--blue:#166f91;--line:#dbe3e9;--bg:#f4f7fa}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 system-ui,-apple-system,"PingFang SC",sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.layout{display:grid;grid-template-columns:235px minmax(0,1fr);gap:30px;max-width:1520px;margin:auto;padding:30px 28px}aside{position:sticky;top:20px;height:calc(100vh - 40px);overflow:auto;padding:10px 15px 20px 0;border-right:1px solid var(--line)}aside>a{display:block;font-size:14px;padding:5px 0}main{min-width:0}h1{font-size:34px;line-height:1.3;margin:10px 0 18px}h2{font-size:25px;margin:38px 0 14px}h3{font-size:19px;line-height:1.5;margin:0 0 10px}h4{margin-bottom:8px}.eyebrow,.muted,small{color:var(--muted)}small{display:block;font-size:12px}.badge{display:inline-block;padding:3px 11px;border:1px solid #a8c8d7;border-radius:6px;background:#e9f4f8;font-size:13px}.intro,.theme,.company,.panel{background:white;border:1px solid var(--line);border-radius:10px}.intro,.panel{padding:22px}.intro p:first-child{font-size:20px;font-weight:550;margin-top:0}.method{font-size:13px;color:var(--muted)}.stats{display:flex;gap:24px;flex-wrap:wrap;margin:20px 0}.stats b{display:block;font-size:26px;line-height:1.3}.stats span{font-size:13px;color:var(--muted)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px;background:white}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid var(--line);vertical-align:top}thead{background:#edf3f7}tr:last-child td,tr:last-child th{border-bottom:0}td:first-child{min-width:155px}.themes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.theme{padding:20px}.theme p{font-size:14px}.attribution{font-size:12px;color:var(--muted);margin:4px 0}.source,.local-source{font-size:12px;display:inline-block;margin:3px 5px 3px 0}.source{background:#edf5f8;padding:2px 6px;border-radius:4px}.company{margin:16px 0;scroll-margin-top:20px}.company summary{cursor:pointer;list-style:none;padding:20px 22px;background:#fff;border-radius:10px}.company summary b{font-size:22px}.company summary:before{content:'▸';float:right;font-size:21px;color:var(--muted)}.company[open] summary:before{content:'▾'}.company-body{padding:0 22px 24px}.stance{border-left:4px solid var(--blue);padding:8px 13px;background:#f0f7fa;font-weight:550}.company ol{padding-left:24px}.company ol>li{padding:10px 0 14px 3px;border-bottom:1px solid #eef2f5}.company ol>li p{margin:5px 0}.watch{display:flex;flex-wrap:wrap;gap:6px 22px;padding-left:19px;font-size:14px}.toolbar{display:flex;gap:10px;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:2}.toolbar input{flex:1;min-width:90px;padding:10px;border:1px solid #b8c6d1;border-radius:6px;font:inherit}.toolbar button{padding:8px 12px;border:1px solid #b8c6d1;background:white;border-radius:6px;cursor:pointer}.chain{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.chain>div{background:#eaf1f6;border:1px solid #cfdce7;border-radius:7px;padding:14px;font-size:14px}.chain b{display:block}.chain small{margin-top:5px}.footer{font-size:13px;color:var(--muted);padding:30px 0}#timeline td:first-child{min-width:180px}
@media(max-width:900px){.layout{display:block;padding:20px 15px}aside{position:static;height:auto;border:0;max-height:250px;margin-bottom:22px}aside>a{display:inline-block;margin-right:12px}.themes,.chain{grid-template-columns:1fr}h1{font-size:28px}.toolbar{flex-wrap:wrap}.toolbar input{flex-basis:100%}}
@media print{body{background:white;font-size:12px}.layout{display:block;padding:0}aside,.toolbar{display:none}.themes{display:block}.theme,.company{break-inside:avoid;margin:10px 0}.company-body{display:block!important}.source{background:none}.company summary{padding:12px 20px}h1{font-size:28px}a{color:inherit}}
</style><style>''' + (assets / 'context_ui.css').read_text() + '''</style></head><body><div class="layout"><aside><strong>公司与产业链</strong><a href="#overview">总览</a><a href="#framework">共同判断</a><a href="#changes">观点变化</a>''' + nav + '''<a href="#metrics">口径与归属</a><a href="#sources">__VIDEOS__期视频资料</a></aside><main>
<p class="eyebrow">美投侃热点 · __DATE_FROM__—__DATE_TO__</p><h1>AI公司与产业链观点整理</h1><span class="badge">''' + e(status) + '''</span>
<div class="stats"><div><b>''' + str(done) + ''' / __VIDEOS__</b><span>视频已完成转写</span></div><div><b>__DURATION__</b><span>清单合计时长</span></div><div><b>''' + str(len(companies)) + '''</b><span>公司 / 产业链专题</span></div><div><b>''' + f'{chars/10000:.1f}万' + '''</b><span>原始转写字符</span></div></div>
<section class="intro"><p>''' + e(framework['headline']) + '''</p><p class="method">''' + e(framework['method']) + '''</p><p class="method">具体立场对应视频当时的信息与估值。媒体传闻、公司指引、机构预测和频道自建模型都保留原有条件；本报告没有使用只在付费内容中出现的未公开模型或结论。</p></section>
<h2 id="overview">按公司快速比较</h2><p class="muted">默认按名称提及次数降序，同次数按最新实质观点日期降序。可切换为观点最新优先，或按涉及视频期数排序。</p><div class="comparison-controls"><label for="sort-companies">排序<select id="sort-companies"><option value="mentions">提及频率优先：次数 → 最新日期</option><option value="date">观点最新优先：日期 → 提及次数</option><option value="videos">涉及视频优先：期数 → 最新日期</option></select></label><label class="single-filter"><input type="checkbox" id="single-company">只看单一公司</label><span id="sort-summary" role="status" aria-live="polite"></span></div><p class="method">频率统计覆盖__VIDEOS__期原始转写，公司名称、股票代码、明确云业务名与常见识别别名均列入可检查的匹配规则；点击次数可看命中视频和片段。行业合集按公司并集/行业关键词统计，与单家公司口径不同，合计之间可重叠。最新日期指本报告收录的实质判断，非每日行情提及。</p><div class="table-wrap"><table class="comparison-table"><thead><tr><th>公司 / 链条</th><th>出现频率</th><th>最新观点</th><th>频道观点概括</th><th>主要观察项</th></tr></thead><tbody id="comparison-body">''' + overview + '''</tbody></table></div>
<h2>产业链位置</h2><div class="chain"><div><b>终端需求与应用</b>企业、开发者、个人用户<small>ChatGPT / Claude / Copilot / Muse / PLTR / 苹果生态</small></div><div><b>模型与云平台</b>OpenAI / Anthropic / 三大云 / OCI / 小云<small>使用量、价格、渠道、算力租赁与融资</small></div><div><b>计算、制造与互联</b>NVIDIA / AMD / Intel / 博通 / 台积电 / ASML<small>存储、先进封装、光通信与网络</small></div><div><b>物理基建与资金</b>电力、电网、冷却、土地、许可、资本市场<small>建设 → 通电 → 验收 → 产生收入</small></div></div><p class="method">位置图由整理者归纳。模型、云和芯片之间还存在投资、供应商融资、采购和客户关系，资金并非单向流动。</p>
<h2 id="framework">贯穿多期视频的判断</h2><div class="themes">''' + themes + '''</div>
<h2 id="changes">保留时间顺序的观点变化</h2><div class="table-wrap" id="timeline"><table><thead><tr><th>日期</th><th>公司</th><th>变化与条件</th></tr></thead><tbody>''' + timeline + '''</tbody></table></div>
<h2>逐家公司查看</h2><div class="context-guide"><b>每条观点都可打开背景</b><p>“背景与关联”说明事件和这条点评的关系；“关键数据”列出数字、期间与口径；“原始资料”提供链接与核对边界。共__POINTS__条观点、__EVENTS__份背景卡、__SOURCES__项来源。背景页保留频道原判断，同时注明公开材料能支持到哪里。</p><p class="method">研究截至__AS_OF__。来源日期按原文，视频按北京时间；较晚发布的信息只供后续核对。未取得的私有模型、同口径预期和历史快照均明示，不填造数字。</p></div><div class="toolbar"><input id="search" placeholder="搜索公司、观点、风险或指标" aria-label="搜索公司观点"><button id="expand">全部展开</button><button id="collapse">全部收起</button></div><div id="company-list">''' + ''.join(cards) + '''</div>
<h2 id="metrics">阅读时需要分清的口径</h2><div class="table-wrap"><table><tbody>''' + metrics + '''</tbody></table></div>
<h2 id="sources">视频与转写资料</h2><p class="method">以下是完整任务清单。时间轴链接便于回听定位；转写稿保留原始识别与简体分段两个版本。转写没有经过逐字人工核对，专名和重要数字应结合原片确认。</p><div class="table-wrap"><table><thead><tr><th>日期</th><th>视频</th><th>时长</th><th>转写</th></tr></thead><tbody>''' + source_rows + '''</tbody></table></div>
<p class="footer">整理截至本次抓取的公开视频。生成时间：''' + dt.datetime.now().strftime('%Y-%m-%d %H:%M') + '''。<a href="观点结构化数据.json">结构化观点数据</a> · <a href="背景资料与核对.json">外部背景与来源</a> · <a href="出现频率统计.json">频率统计与片段依据</a> · <a href="../转写目录.html">返回完整转写目录</a></p>
</main></div><script>
const cards=[...document.querySelectorAll('.company')];
document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();cards.forEach(c=>{c.hidden=!c.textContent.toLowerCase().includes(q);if(q&&!c.hidden)c.open=true;});});
document.getElementById('expand').onclick=()=>cards.forEach(c=>c.open=true);
document.getElementById('collapse').onclick=()=>cards.forEach(c=>c.open=false);
function openHash(){const el=document.getElementById(location.hash.slice(1));if(el?.classList.contains('company')){el.hidden=false;el.open=true;}}
window.addEventListener('hashchange',openHash);openHash();window.addEventListener('beforeprint',()=>cards.forEach(c=>c.open=true));
</script></body></html>'''
def json_for_html(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')

dialog = '''<dialog id="context-dialog" aria-labelledby="context-title"><div class="dialog-frame"><header class="dialog-header"><div><p class="eyebrow" id="context-kicker">观点的外部背景</p><h2 id="context-title"></h2></div><button id="close-context" aria-label="关闭背景弹窗">关闭 ×</button></header><div id="context-opinion"></div><div class="context-tabs" role="tablist" aria-label="背景内容"><button id="tab-summary" role="tab" aria-selected="true" aria-controls="panel-summary" tabindex="0">背景与关联</button><button id="tab-data" role="tab" aria-selected="false" aria-controls="panel-data" tabindex="-1">关键数据</button><button id="tab-sources" role="tab" aria-selected="false" aria-controls="panel-sources" tabindex="-1">原始资料</button></div><section id="panel-summary" role="tabpanel" aria-labelledby="tab-summary" tabindex="0"></section><section id="panel-data" role="tabpanel" aria-labelledby="tab-data" tabindex="0" hidden></section><section id="panel-sources" role="tabpanel" aria-labelledby="tab-sources" tabindex="0" hidden></section></div></dialog>'''
scope['as_of'] = context['as_of']
payload = dict(scope=scope, context=context, frequency=frequency, companyNames={c['id']: c['name'] for c in companies}, videoURLs={b: i['url'] for b, i in catalog.items()})
document = document.replace('__VIDEOS__', str(total)).replace('__DATE_FROM__', scope['date_from']).replace('__DATE_TO__', scope['date_to']).replace('__AS_OF__', context['as_of']).replace('__DURATION__', f'{hours}h {minutes:02d}m')
document = document.replace('__POINTS__', str(len(context['points']))).replace('__EVENTS__', str(len(context['events']))).replace('__SOURCES__', str(len(context['sources'])))
document = document.replace('</body>', dialog + '<script type="application/json" id="background-data">' + json_for_html(payload) + '</script><script>' + (assets / 'context_ui.js').read_text() + '</script></body>')
(out / '美投侃热点_AI公司与产业链观点整理.html').write_text(document, encoding='utf-8')
data = dict(scope=scope, framework=framework, companies=companies, external_context_file='背景资料与核对.json', frequency_file='出现频率统计.json')
(out / '观点结构化数据.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
(out / '背景资料与核对.json').write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding='utf-8')
(out / '出现频率统计.json').write_text(json.dumps(frequency, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'report': str(out), 'companies': len(companies), 'cited_videos': len(used), 'completed': done, 'final': args.final}, ensure_ascii=False))
