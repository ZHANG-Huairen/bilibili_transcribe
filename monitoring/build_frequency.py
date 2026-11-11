"""Count auditable, explicit entity mentions in the current source ASR transcripts."""
import argparse
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--task-dir', type=Path, required=True, help='Directory containing batch_manifest.json and 研究证据/')
args = parser.parse_args()
root = args.task_dir.expanduser().resolve() / '研究证据'
manifest = json.loads((root.parent / 'batch_manifest.json').read_text())
companies = []
for name in ['company_notes.json', 'labs_notes.json', 'supply_notes.json', 'extras_notes.json']:
    companies.extend(json.loads((root / name).read_text())['companies'])
patterns = {
    'microsoft': r'微软|Microsoft|MSFT',
    'alphabet': r'谷歌|Google|Alphabet|GOOGL',
    'amazon': r'亚马逊|亚玛逊|Amazon|AWS',
    'meta': r'(?<![A-Za-z])(?:Meta|Mata)(?![A-Za-z])|脸书',
    'apple': r'苹果|(?<![A-Za-z])Apple(?![A-Za-z])|AAPL',
    'tesla': r'特斯拉|Tesla|TSLA',
    'anthropic': r'Anthr[oou]pic|Anthropec|Antropic|安[特托]罗[匹皮普]克',
    'openai': r'Open\s?AI',
    'oracle': r'甲骨文|Oracle|(?<![A-Za-z])OCI(?![A-Za-z])',
    'coreweave': r'Core\s?(?:Weave|Wave|Weef|Vive|Wheave)|Call\s?Weave|QualWave|QoWiV|Covive|科尔维夫',
    'neoclouds': r'Nebius|Nabius|内比[奥乌]斯|(?<![A-Za-z])IREN(?![A-Za-z])|(?<![A-Za-z])Iron(?![A-Za-z])|Nscale|Nscal|Nanscale',
    'amd': r'(?<![A-Za-z])AMD(?![A-Za-z])|超威',
    'intel': r'英特尔|Intel',
    'memory': r'海[力利][士市式时]|Hynix|西部数据|Western\s?Digital|(?<![A-Za-z])WDC(?![A-Za-z])|存储|内存|HBM|DRAM|NAND',
    'broadcom': r'博通|伯通|搏通|波通|Broadcom|AVGO',
    'optical': r'Marvell?|Mavo|Lum[ei]nt[oua]m|Lumantem|Lumentang|Lumenton|迈威尔|美满电子|鲁门特姆|光互联|光通信|光模块',
    'palantir': r'Palantir|Palentier|Balantir|帕兰[提蒂]尔|(?<![A-Za-z])PLTR(?![A-Za-z])',
    'cybersecurity': r'网络安全|网安|资安|资讯安全|Cybersecurity|CrowdStrike|Palo\s?Alto|Zscaler',
    'accenture': r'埃森哲|Accenture|Eisner',
    'micron': r'美光|每光|Micron',
    'nvidia': r'英[伟维]达|因[伟维]达|因为达|NVIDIA|NVDA',
    'tsmc': r'台积电|TSMC',
    'spacex-ai': r'Space\s?X|(?<![A-Za-z])XAI(?![A-Za-z])',
    'asml': r'阿斯[麦邁迈曼]|ASML',
    'power': r'Constellation|星座能源|Vistra|Talen|Telen|NRG|核电|电力公司|电网|发电设备',
}
groups = {'memory', 'optical', 'power', 'cybersecurity', 'neoclouds'}
data = {}
for company in companies:
    pattern = re.compile(patterns[company['id']], re.I)
    videos = []
    for item in manifest['items']:
        segments = json.loads(Path(item['segments_json']).read_text())['segments']
        hits = []
        for segment in segments:
            matches = list(pattern.finditer(segment['text']))
            if matches:
                hits.append({'start': segment['start'], 'text': segment['text'], 'matches': [m.group() for m in matches]})
        if hits:
            videos.append({'bvid': item['bvid'], 'date': item['date'], 'title': item['title'],
                           'mentions': sum(len(h['matches']) for h in hits), 'segments': len(hits), 'evidence': hits})
    data[company['id']] = {'video_count': len(videos), 'mention_count': sum(v['mentions'] for v in videos),
                           'latest_opinion_date': company['last_substantive_date'],
                           'latest_mention_date': max((v['date'] for v in videos), default=None),
                           'kind': '公司并集/产业链关键词' if company['id'] in groups else '公司及明确别名',
                           'pattern': patterns[company['id']], 'videos': videos}
result = {'scope': {'videos': len(manifest['items']), 'date_from': manifest['date_from'], 'date_to': manifest['date_to']}, 'method': f'视频覆盖数为{len(manifest["items"])}期中至少匹配一次的期数；提及次数为公司名称、股票代码、明确云业务名称（AWS/OCI）及ASR识别别名的不重叠匹配次数。不将CEO姓名或仅提及Gemini/Claude/ChatGPT/Copilot/Grok等产品的段落自动计为公司名称；频率是名称统计，不是全部语义讨论量。产业链专题按列示实体及行业关键词计数，另作标识。最新观点日期沿用已整理的实质点评日期，不用每日行情提及代替。', 'companies': data}
(root / 'frequency.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
rank = sorted(companies, key=lambda c: (-data[c['id']]['video_count'], -int(c['last_substantive_date'].replace('-', '')), c['id']))
print([(c['name'], data[c['id']]['video_count'], data[c['id']]['mention_count'], c['last_substantive_date']) for c in rank])
