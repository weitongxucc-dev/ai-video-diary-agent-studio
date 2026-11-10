"""MIT Remotion-ecosystem designs adapted to the existing deterministic HTML host."""
import json
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplate

ASSETS = Path(__file__).parents[1] / 'template_assets' / 'rendercomp'


def rendercomp_templates():
    manifest = json.loads((ASSETS / 'provenance.json').read_text())
    for identity, name, filename, title, subtitle, labels, values in (
        ('rendercomp-bar-chart', '季度数据·弹性柱状图', 'bar-chart', '季度营收', '同比增长 28%',
            ['第一季度', '第二季度', '第三季度', '第四季度'], ['120', '180', '240', '320']),
        ('rendercomp-kpi', '业绩指标·滚动数字', 'kpi-counter', '季度成果', '本期表现',
            ['营收', '增长率', '客户数', '推荐指数'], ['2840000', '127', '384', '72']),
    ):
        original = manifest['files'][0 if filename == 'bar-chart' else 1]
        bindings = [{'key':'title', 'label':'标题', 'kind':'text', 'target':'.title', 'default':title},
            {'key':'subtitle', 'label':'副标题', 'kind':'text', 'target':'.subtitle', 'default':subtitle}]
        for index, (label, value) in enumerate(zip(labels, values), 1):
            bindings += [{'key':f'label{index}', 'label':f'项目 {index}', 'kind':'text', 'target':f'.l{index}', 'default':label},
                {'key':f'value{index}', 'label':f'数值 {index}', 'kind':'text', 'target':f'.v{index}', 'default':value}]
        bindings += [{'key':key, 'label':label, 'kind':'variable', 'target':'--'+key, 'default':''}
            for key, label in [('background','背景颜色'), ('color','文字颜色'), ('accent','强调色'), ('font','字体')]]
        yield identity, name, VisualTemplate.model_validate({
            'category':'data', 'width':1200, 'height':900, 'duration_seconds':5,
            'source':(ASSETS / f'{filename}.html').read_text(), 'bindings':bindings,
            'provenance':[{key:manifest[key] for key in ('repository','revision','license')}
                | {'path':original['path'], 'source_sha256':original['sha256']}],
        })
