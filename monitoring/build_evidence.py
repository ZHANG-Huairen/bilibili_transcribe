"""Generate source excerpts only; keyword matches are not attributed opinions."""
import argparse
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--task-dir', type=Path, required=True, help='Directory containing batch_manifest.json and 研究证据/')
args = parser.parse_args()
root = args.task_dir.expanduser().resolve() / '研究证据'
manifest = json.loads((root.parent / 'batch_manifest.json').read_text())
patterns = {
    '微软': r'微软|Microsoft|Azure|阿[朱祖珠]尔|纳德拉',
    '英伟达': r'英[伟维]达|因[伟维]达|因为达|黄仁[勋熏]|NVIDIA|Nvidia|NVDA',
    '谷歌': r'谷歌|Google|Alphabet|Gemini|杰米|TPU',
    '亚马逊': r'亚马逊|Amazon|AWS',
    'Meta': r'Meta|META|meta|脸书|扎克|小扎|扎[马吗]|Llama|Muse',
    '苹果': r'苹果|Apple|apple|库克',
    '特斯拉': r'特斯拉|Tesla|马斯克',
    'OpenAI': r'Open\s?AI|openai|ChatGPT|GPT|奥特曼',
    'Anthropic': r'Anthropic|anthropic|Claude|克劳德|蚂蚁|安[特托]罗|人类而生',
    '甲骨文与小云': r'甲骨文|Oracle|Core\s?[Ww]eave|CoreWave|小云|Nebius|Nabius|Iren|Iron|新云|内比',
    '芯片制造与存储': r'台积电|TSMC|英特尔|Intel|AMD|阿斯麦|ASML|美光|Micron|海力士|Hynix|DRAM|HBM|NAND|西部数据|闪迪|存储|内存',
    '芯片与光互联': r'TPU|ASIC|博通|Broadcom|Marvell|迈威|光[纤模通]|光与光|康宁|Lumentum|Coherent|Ciena|Arista|网络设备|交换机',
    '电力能源与基建': r'电力|电网|核电|核能|电站|供电|通电|电气|数据中心|资本[开支支出]|CapEx|发电|电费|燃气|变压器',
    '软件应用与网安': r'PLTR|Palantir|帕[兰蓝]提|ServiceNow|Salesforce|赛富时|Adobe|软件|网络安全|网安|CrowdStrike|Palo\s?Alto|Zscaler|杀手级|智能体|[Aa]gent',
    'AI融资与需求': r'融资|发债|债务|循环|垃圾债|垃圾级|现金流|自由现金|供给过剩|需求|推理|训练|折旧|泡沫|变现|盈利',
}
summary = {}
for name, pattern in patterns.items():
    blocks = []
    for item in reversed(manifest['items']):
        if item['status'] != 'completed':
            continue
        segments = json.loads(Path(item['segments_json']).read_text())['segments']
        ranges = []
        for n, segment in enumerate(segments):
            if not re.search(pattern, segment['text'], re.I):
                continue
            a, b = max(0, n - 2), min(len(segments) - 1, n + 3)
            if ranges and a <= ranges[-1][1] + 1:
                ranges[-1][1] = max(ranges[-1][1], b)
            else:
                ranges.append([a, b])
        for a, b in ranges:
            blocks.append({'date': item['date'], 'bvid': item['bvid'], 'title': item['title'],
                           'start': segments[a]['start'], 'end': segments[b]['end'],
                           'text': ''.join(s['text'] for s in segments[a:b + 1])})
    (root / f'{name}.json').write_text(json.dumps(blocks, ensure_ascii=False, indent=2))
    summary[name] = {'blocks': len(blocks), 'characters': sum(len(b['text']) for b in blocks)}
print(json.dumps({'completed_videos': sum(i['status'] == 'completed' for i in manifest['items']), 'topics': summary}, ensure_ascii=False))
