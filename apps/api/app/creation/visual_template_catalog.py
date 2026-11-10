"""Pinned CC0 assets adapted through their upstream editable bindings."""
import json
from html.parser import HTMLParser
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplate
from app.creation.rendercomp_template_catalog import rendercomp_templates

ASSETS = Path(__file__).parents[1] / 'template_assets' / 'openfilm'
# Keep published IDs/order stable; additional resources come from the pinned catalog.
FIRST = ('lower-corporate', 'chapter-number', 'callout-leader', 'info-count', 'shop-specs', 'end-thanks')
GROUPS = {'title':'title', 'lower':'title', 'callout':'callout', 'social':'social',
    'commerce':'product', 'info':'data', 'chapter':'chapter', 'ending':'ending',
    'variety':'reaction', 'bubble':'chat', 'news':'news', 'location':'location', 'festival':'festival'}

LABELS = {'Name': '姓名或主题', 'Role': '身份说明', 'Number': '数字', 'Label': '标签',
    'Title': '标题', 'Font': '字体', 'Color': '文字颜色', 'Accent': '强调色', 'Note': '补充说明',
    'Product': '产品名称', 'Card': '卡片底色', 'Subtitle':'副标题', 'Top line':'上行文字',
    'Tag':'角标', 'Credit':'署名', 'Box':'底框', 'Slab':'主色块', 'Second slab':'副色块',
    'Second line':'第二行', 'Second accent':'第二强调色', 'Pill':'胶囊底色', 'Text on white':'浅底文字颜色',
    'Frame and name glow':'姓名与边框光色', 'Role glow':'身份光色', 'Handle':'账号', 'Initial':'头像文字',
    'Text':'文字', 'Step':'步骤', 'Tip':'提示', 'Left heading':'左标题', 'Right heading':'右标题',
    'Good':'优点颜色', 'Bad':'缺点颜色', 'Clicked':'点击后文字', 'Clicked button':'点击后底色',
    'Likes':'点赞数', 'Comments':'评论数', 'Shares':'分享数', 'Heart':'心形颜色', 'Circles':'圆形底色',
    'Count':'数量', 'Marker':'标记颜色', 'Price':'价格', 'Old price':'原价', 'Big text':'大字',
    'Small text':'小字', 'Burst':'前景爆炸形', 'Back burst':'背景爆炸形', 'Value':'数值', 'Unit':'单位',
    'Condition':'使用条件', 'Code':'代码', 'Paper':'纸张底色', 'Ends in':'倒计时说明', 'Timer':'计时',
    'Color from':'渐变起色', 'Color to':'渐变终色', 'Heading':'主标题', 'Left value':'左数值',
    'Right value':'右数值', 'Middle':'中间文字', 'Left':'左侧颜色', 'Right':'右侧颜色',
    'Word':'词语', 'Sound':'发音', 'Kind':'类型', 'Meaning':'释义', 'Author':'作者', 'Part':'部分',
    'Last line':'最后一行', 'Roles':'身份颜色', 'Dots':'圆点颜色', 'Fill':'填充颜色', 'Rays':'射线颜色',
    'Stroke':'描边颜色', 'Speed lines':'速度线颜色', 'First color':'第一色', 'Second color':'第二色',
    'Third color':'第三色', 'Gradient top':'渐变上色', 'Gradient bottom':'渐变下色', 'Marks':'标记颜色',
    'My bubbles':'我的气泡', 'Their bubbles':'对方气泡', 'Their text':'对方文字', 'App':'应用名',
    'Time':'时间', 'From':'发送者', 'Message':'消息', 'App badge':'应用图标色', 'Thought':'想法',
    'Cloud':'气泡底色', 'Headline':'新闻标题', 'Strip':'底栏', 'News':'新闻内容', 'Bar':'条带底色',
    'Section':'栏目', 'Source':'信息来源', 'Place':'地点', 'Region':'地区', 'Coordinates':'坐标',
    'Pin':'图钉颜色', 'Month':'月份', 'Day':'日期', 'Weekday':'星期', 'Year':'年份', 'Band':'色带',
    'Temperature':'气温', 'City':'城市', 'Sky':'天气', 'Sun':'太阳颜色', 'Greeting':'祝福',
    'Fireworks':'烟花颜色', 'Second fireworks':'第二烟花颜色', 'Tree':'树颜色', 'Star':'星星颜色', 'Ribbon':'丝带颜色'}


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
    catalog = json.loads((ASSETS / 'catalog.json').read_text())['items']
    catalog.sort(key=lambda item: FIRST.index(item['src'][:-5]) if item['src'][:-5] in FIRST else len(FIRST))
    for item in catalog:
        identity = item['src'][:-5]
        name, category, width, height = item['name']['zh-CN'], GROUPS[item['group']], item['w'], item['h']
        source = (ASSETS / f'{identity}.html').read_text()
        saved = json.loads((ASSETS / f'{identity}.json').read_text())
        props = json.loads(saved['clip']['attrs']['data-props'])
        text = _TextDefaults(source).text
        bindings = []
        for index, prop in enumerate(props):
            is_text = prop['type'] == 'text'
            target = prop['at'] if is_text else prop['var']
            label = LABELS.get(prop['name'], prop['name'])
            numbered = {'Row':'参数名', 'Value':'参数值', 'Left':'左项', 'Right':'右项',
                'Point':'要点', 'Year':'年份', 'Event':'事件', 'Line':'文字行', 'Role':'身份', 'Name':'姓名', 'Message':'消息'}
            prefix, _, number = prop['name'].rpartition(' ')
            if prefix in numbered and number.isdigit():
                label = numbered[prefix] + ' ' + number
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

    yield from rendercomp_templates()


def trusted_inline_visual(definition):
    """Only bundled code and bindings may execute as ordinary UI prototypes.

    Authored source is still editable and executable through task media work,
    with its existing process/deadline/output permit rather than the UI thread.
    """
    actual = [(item.key, item.kind, item.target) for item in definition.bindings]
    return any(definition.source == item.source
        and actual == [(binding.key, binding.kind, binding.target) for binding in item.bindings]
        for _identity, _name, item in visual_templates())
