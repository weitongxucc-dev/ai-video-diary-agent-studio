"""Pinned CC0 assets adapted through their upstream editable bindings."""
import json
from html.parser import HTMLParser
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplate

ASSETS = Path(__file__).parents[1] / 'template_assets' / 'openfilm'
ITEMS = (
    ('lower-corporate', '商务姓名条', 'title', 1200, 300),
    ('chapter-number', '编号章节', 'chapter', 1000, 450),
    ('callout-leader', '引线标注', 'callout', 600, 300),
    ('info-count', '动态数据', 'data', 800, 400),
    ('shop-specs', '产品参数卡', 'product', 700, 500),
    ('end-thanks', '结尾引导', 'ending', 1000, 500),
)
LABELS = {'Name': '姓名或主题', 'Role': '身份说明', 'Number': '数字', 'Label': '标签',
    'Title': '标题', 'Font': '字体', 'Color': '文字颜色', 'Accent': '强调色', 'Note': '补充说明',
    'Product': '产品名称', 'Card': '卡片底色'}


class _TextDefaults(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.stack, self.text = [], {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get('class', '').split()
        self.stack.append(classes)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        for classes in self.stack:
            for value in classes:
                self.text[value] = self.text.get(value, '') + data


def visual_templates():
    provenance = json.loads((ASSETS / 'provenance.json').read_text())
    for identity, name, category, width, height in ITEMS:
        source = (ASSETS / f'{identity}.html').read_text()
        saved = json.loads((ASSETS / f'{identity}.json').read_text())
        props = json.loads(saved['clip']['attrs']['data-props'])
        text = _TextDefaults(source).text
        bindings = []
        for index, prop in enumerate(props):
            is_text = prop['type'] == 'text'
            target = prop['at'] if is_text else prop['var']
            label = LABELS.get(prop['name'], prop['name'])
            if prop['name'].startswith(('Row ', 'Value ')):
                label = ('参数名 ' if prop['name'].startswith('Row ') else '参数值 ') + prop['name'].split()[-1]
            if prop['name'] in {'Button', 'Script line'}:
                label = {'Button': ('按钮文字', '按钮底色'), 'Script line': ('上行文字', '上行字体')}[prop['name']][not is_text]
            bindings.append({'key': f'p{index}', 'label': label,
                'kind': 'text' if is_text else 'variable', 'target': target,
                'default': text.get(target.removeprefix('.'), '').strip() if is_text else ''})
        file = next(item for item in provenance['files'] if item['file'] == f'{identity}.html')
        definition = VisualTemplate.model_validate({'category': category, 'width': width, 'height': height,
            'duration_seconds': saved['durationMs'] / 1000, 'source': source, 'bindings': bindings,
            'provenance': [{key: provenance[key] for key in ('repository', 'revision', 'license')}
                | {'path': file['path'], 'source_sha256': file['sha256']}]})
        yield identity, name, definition
