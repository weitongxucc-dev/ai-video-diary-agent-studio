"""Rebuildable local portrait processing before framing and color adjustments."""
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock

PORTRAIT_INPUT_CRF = "20"

_portrait_lock=Lock()


def _sha256(path):
    digest=hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalPortrait:
    def __init__(self, worker_path: str | None, cache_root: Path):
        self.worker=Path(worker_path).expanduser() if worker_path else None
        self.cache_root=cache_root

    @property
    def available(self):
        return sys.platform=="darwin" and self.worker is not None and self.worker.is_file() and os.access(self.worker,os.X_OK)

    def fingerprint(self):
        if not self.available:
            raise ValueError("本地人像组件未就绪")
        return hashlib.sha256(f"{_sha256(self.worker)}:{platform.mac_ver()[0]}:input-crf={PORTRAIT_INPUT_CRF}".encode()).hexdigest()

    def process(self, source: Path, start: float, end: float, strength: float, skin_exposure: float):
        if not self.available:
            raise ValueError("本地人像组件未就绪，请先安装 macOS 人像处理组件")
        if not 0<=strength<=1 or not -.5<=skin_exposure<=.5 or not 0<=start<end:
            raise ValueError("人像处理参数或源区间无效")
        source_stat=source.stat()
        source_sha=_sha256(source)
        identity={"source_sha256":source_sha,"start":start,"end":end,"strength":strength,"skin_exposure":skin_exposure,
                  "runtime_fingerprint":self.fingerprint(),"format_version":1}
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        output=self.cache_root/f"{key}.mp4"
        manifest=self.cache_root/f"{key}.json"
        with _portrait_lock:
            self.cache_root.mkdir(parents=True,exist_ok=True)
            if output.is_file() and manifest.is_file():
                saved=json.loads(manifest.read_text())
                if saved["output_sha256"]==_sha256(output):
                    return output,saved
            with TemporaryDirectory(prefix="portrait-",dir=self.cache_root) as directory:
                root=Path(directory)
                prepared=root/"source.mp4"
                processed=root/"portrait.mp4"
                subprocess.run(["ffmpeg","-v","error","-y","-protocol_whitelist","file,pipe","-ss",str(start),"-t",str(end-start),
                    "-i",str(source),"-vf","scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30,setpts=PTS-STARTPTS",
                    "-an","-c:v","libx264","-preset","veryfast","-crf",PORTRAIT_INPUT_CRF,"-pix_fmt","yuv420p",str(prepared)],capture_output=True,check=True,timeout=600)
                request=root/"request.json"
                response=root/"response.json"
                request.write_text(json.dumps({"source_path":str(prepared),"output_path":str(processed),"strength":strength,"skin_exposure":skin_exposure}))
                run=subprocess.run([str(self.worker),"--request",str(request),"--response",str(response)],capture_output=True,timeout=600)
                if run.returncode:
                    failure=json.loads(response.read_text()) if response.is_file() else {}
                    if failure.get("reason")=="no_face":
                        raise ValueError("源片段没有检测到清晰人脸，请选择正脸可见的片段")
                    raise ValueError("本地人像处理失败，请检查视频格式和本地组件")
                result=json.loads(response.read_text())
                if result["frame_count"]<1 or result["face_frame_count"]/result["frame_count"]<.9:
                    raise ValueError("这段人脸识别不稳定，暂不能连续优化；请缩小到正脸清晰的范围")
                metadata=json.loads(subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=duration,nb_frames","-of","json",str(processed)],capture_output=True,text=True,check=True,timeout=30).stdout)["streams"][0]
                duration=float(metadata["duration"])
                if abs(duration-(end-start))>.04 or int(metadata["nb_frames"])!=result["frame_count"]:
                    raise ValueError("人像处理改变了视频时长或帧数，未应用结果")
                if source.stat().st_size!=source_stat.st_size or source.stat().st_mtime_ns!=source_stat.st_mtime_ns:
                    raise ValueError("源素材在处理期间发生变化，请重新预览")
                saved={**identity,**result,"duration_seconds":duration,"output_sha256":_sha256(processed)}
                processed.replace(output)
                response.write_text(json.dumps(saved))
                response.replace(manifest)
                return output,saved
