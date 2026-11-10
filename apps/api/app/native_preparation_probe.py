"""Measure the real task shell/Pillow/FFmpeg path, independently of CPU encode.

The requested shape bounds a finite generic process envelope. It is not a
promise that arbitrary programs finish within that envelope.
"""
from pathlib import Path
from types import SimpleNamespace

from app.creation.native_task_operations import NativeTaskOperations, native_environment


def require_native_dependencies(settings):
    operations = NativeTaskOperations(None, None, Path(settings.model_runtime_python_path).parent,
        python_path=settings.model_runtime_python_path)
    tools = operations.tool_facts()
    if not tools['python']['available'] or not tools['ffmpeg']['available']:
        raise ValueError('程序化准备需要本机已配置的Python/Pillow与FFmpeg，未替换其他环境')
    packages = list((Path(tools['python']['runtime_root'])/'lib').glob('python*/site-packages/PIL/Image.py'))
    if len(packages) != 1:
        raise ValueError('本机程序运行环境缺少Pillow，请先在已配置的Python环境安装Pillow；未切换其他解释器')
    return tools


def native_probe(root, settings, shape, seconds):
    root = Path(root) / 'native-probe'
    for name in ('control', 'work', 'outputs', 'inputs', 'tmp'):
        (root/name).mkdir(parents=True, exist_ok=True)
    operations = NativeTaskOperations(None, None, root, python_path=settings.model_runtime_python_path)
    tools = require_native_dependencies(settings)
    profile = operations.sandbox(root, SimpleNamespace(id='preparation'), tools)
    script = root/'work'/'probe.py'
    frames = max(1, round(seconds*shape.frame_rate))
    program = f'''from PIL import Image, ImageDraw
from pathlib import Path
import subprocess
root=Path({str(root/'outputs')!r})
for i in range({frames}):
    image=Image.new('RGBA',({shape.width_px},{shape.height_px}),(0,0,0,0))
    ImageDraw.Draw(image).rectangle((i,i,{shape.width_px}//2+i,{shape.height_px}//2+i),fill=(255,200,20,160))
    image.save(root/('frame-%07d.png'%i))
subprocess.run([{tools['ffmpeg']['path']!r},'-v','error','-y','-threads','1','-framerate',{str(shape.frame_rate)!r},'-i',str(root/'frame-%07d.png'),'-c:v','prores_ks','-profile:v','4','-pix_fmt','yuva444p10le','-threads','1',str(root/'sample.mov')],check=True)
print('native-probe-complete')
'''
    script.write_text(program)
    executable = tools['python']['path']
    command = ['/usr/bin/sandbox-exec', '-f', str(profile), executable, str(script)]
    environment = native_environment(root, tools)
    return command, environment, root/'outputs', {'frames': frames,
        'script_bytes': len(program.encode()), 'tools': tools,
        'output_capacity_bytes': shape.width_px*shape.height_px*4*frames,
        'source_bytes': shape.source_bytes or 0}
