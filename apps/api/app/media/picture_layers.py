"""Produce bounded static and animated inputs on the picture window clock."""
def picture_window_layer_arguments(window, output, context, layers, filters, size):
    from app.montage import _write_subtitle_image, _write_cropped_graphic_input, _write_frame_subtitle_input
    if not layers:
        return ['-vf', filters]
    directory = output.parent / f'.{output.stem}-layers'
    directory.mkdir(exist_ok=False)
    arguments = []
    def check_layers():
        context.check()
        request = context.scheduler._get(context.grant.request_id)
        cap = request.resources.get('picture_temporary_limit_bytes')
        if cap is not None and sum(path.stat().st_size for path in directory.rglob('*') if path.is_file()) > cap:
            raise ValueError('画面窗口PNG与字幕manifest超过原临时卷许可分配')
    graph = [f'[0:v]{filters}[windowbase]']
    label = '[windowbase]'
    captions, caption_paths = [], []
    input_index = 1
    for index, layer in enumerate(layers):
        check_layers()
        if not 0 <= layer.first_frame < layer.last_frame <= window.frame_count:
            raise ValueError('烧录图层不属于当前完整画面帧窗口')
        path = directory / f'layer-{index}.png'
        if layer.kind == 'subtitle':
            _write_subtitle_image(layer.value, path, size=size)
            captions.append((layer.first_frame, layer.last_frame))
            caption_paths.append(path)
        elif layer.kind == 'graphic':
            if layer.value.get('kind') == 'template':
                from app.media.template_frames import render_template_frames
                manifest = render_template_frames(layer.value, directory / f'template-{index}', context,
                    size=size, first_frame=window.timeline_start_frame, frame_count=window.frame_count)
                arguments.extend(['-f', 'concat', '-safe', '0', '-protocol_whitelist', 'file',
                    '-threads', str(context.threads), '-i', str(manifest)])
                following = f'[graphic{input_index}]'
                graph.append(f'{label}[{input_index}:v]overlay=0:0:shortest=0:eof_action=pass:repeatlast=0{following}')
                label = following
                input_index += 1
                continue
            left, top = _write_cropped_graphic_input(layer.value, path, size)
            arguments.extend(['-loop', '1', '-framerate', '30', '-threads', str(context.threads), '-i', str(path)])
            following = f'[graphic{input_index}]'
            graph.append(f"{label}[{input_index}:v]overlay={left}:{top}:shortest=1:"
                f"enable='gte(n,{layer.first_frame})*lt(n,{layer.last_frame})'{following}")
            label = following
            input_index += 1
        else:
            raise ValueError('画面窗口图层类型无效')
    if captions:
        manifest, position = _write_frame_subtitle_input(captions, caption_paths, directory,
            size=size, frame_count=window.frame_count, crop_to_content=False, check=check_layers)
        arguments.extend(['-f', 'concat', '-safe', '0', '-protocol_whitelist', 'file',
            '-threads', str(context.threads), '-i', str(manifest)])
        graph.append(f'[{input_index}:v]setpts=PTS-STARTPTS,format=rgba[subtitleclock]')
        graph.append(f'{label}[subtitleclock]overlay={position[0]}:{position[1]}:'
            'shortest=0:eof_action=pass:repeatlast=0[windowcaptioned]')
        label = '[windowcaptioned]'
    check_layers()
    return arguments + ['-filter_complex', ';'.join(graph), '-map', label]

