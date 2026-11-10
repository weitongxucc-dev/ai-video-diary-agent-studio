"""Grant-bound offline HTML frames. The desktop owns the Chromium executable.

No user code executes in Python or Electron's main process. Source goes into
the renderer's opaque sandbox; all process, output and deadline monitoring is
the same granted broker used by the surrounding picture unit.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from app.creation.visual_template_contracts import VisualTemplateOverlay
from app.granted_subprocess import run_granted_command


def renderer_paths():
    executable = os.environ.get('YAOHE_TEMPLATE_ELECTRON')
    runner = os.environ.get('YAOHE_TEMPLATE_RENDERER')
    if not executable or not runner or not Path(executable).is_file() or not Path(runner).is_file():
        raise ValueError('可编辑动画需要桌面附带的隔离渲染器，请从桌面打开并准备媒体能力；没有网络渲染回退')
    return Path(executable), Path(runner)


def renderer_identity():
    executable, runner = renderer_paths()
    facts = []
    from app.media.template_document import CLOCK_PATH
    implementation = ((Path(sys.executable),) if getattr(sys, 'frozen', False) else
        (Path(__file__), Path(__file__).with_name('template_document.py')))
    for path in (executable, runner, CLOCK_PATH, *implementation):
        with path.open('rb') as source:
            facts.append(hashlib.file_digest(source, 'sha256').hexdigest())
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()


def frame_command(value, directory, *, size, first_frame, frame_count, fps=30):
    overlay = VisualTemplateOverlay.model_validate(value)
    if overlay.definition.dependencies:
        raise ValueError('模板依赖未安装：' + '、'.join(overlay.definition.dependencies) + '。请改为自包含源码；未联网加载或替换模板')
    executable, runner = renderer_paths()
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    spec = directory / 'render.json'
    from app.media.template_document import template_document
    spec.write_text(json.dumps({'document': template_document(overlay.definition), 'overlay': overlay.model_dump(), 'width': size[0], 'height': size[1],
        'first_frame': first_frame, 'frame_count': frame_count, 'fps': fps}, ensure_ascii=False))
    # Chromium receives no inherited model credentials, member tokens or user
    # HOME. Its own sandbox remains enabled, including in packaged builds.
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
        'TMPDIR': str(directory), 'HOME': str(directory)}
    # Development Electron needs an app entry; packaged Electron uses its own.
    entry = runner.parent.parent / 'main.js'
    arguments = [str(executable)]
    if entry.is_file():
        arguments.append(str(entry))
    return [*arguments, '--yaohe-template-renderer', str(spec)], environment


def frame_manifest(directory, frame_count, fps=30):
    directory = Path(directory)
    proof = json.loads((directory / 'frames.json').read_text())
    expected = [f'frame-{index:07d}.png' for index in range(frame_count)]
    if proof['frames'] != expected:
        raise ValueError('动画帧回执不完整，未采用半成品')
    manifest = directory / 'frames.ffconcat'
    lines = ['ffconcat version 1.0']
    for name in expected:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError('动画输出缺帧，未采用半成品')
        lines.extend([f"file '{name}'", f'option framerate {fps}', f'duration {1/fps:.17f}'])
    manifest.write_text('\n'.join(lines) + '\n')
    return manifest


def render_template_frames(value, directory, context, *, size, first_frame, frame_count):
    overlay = VisualTemplateOverlay.model_validate(value)
    missing = [binding.label for binding in overlay.definition.bindings
        if binding.kind == 'text' and binding.key not in overlay.definition.values]
    if missing:
        raise ValueError('新作品尚未填写模板输入：' + '、'.join(missing) + '。请填写本次主题，未沿用旧作品正文')
    budget = context.scheduler.resources.budget.cpu_render
    if budget is None or budget.max_template_count < 1:
        raise ValueError('当前设备准备未测量HTML动画，请按实际动画图层数重新准备媒体能力')
    command, environment = frame_command(value, directory, size=size,
        first_frame=first_frame, frame_count=frame_count)
    context.check()
    request = context.scheduler._get(context.grant.request_id)
    temporary_limit = request.resources.get('picture_temporary_limit_bytes', budget.additional_disk_bytes)
    result = run_granted_command(context.scheduler, context.grant, command,
        env=environment, timeout_seconds=context.deadline_monotonic-time.monotonic(),
        max_stdout_bytes=65536, max_stderr_bytes=65536, cancel_event=context.cancel_event,
        output_paths=(Path(directory),),
        max_output_file_bytes=temporary_limit)
    if result.returncode:
        raise ValueError('模板源码执行失败：' + result.stderr.decode('utf-8', errors='replace')[:1500])
    context.check()
    return frame_manifest(directory, frame_count)
