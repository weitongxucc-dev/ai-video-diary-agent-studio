import pytest
import json
from types import SimpleNamespace
from pathlib import Path
from dataclasses import replace

from app.production import SubtitleCue
from app.subtitle_alignment import align_subtitle_track, recognize_subtitle_timing, synchronize_translated_subtitles


def token(text, start, end, p=.9):
    return {"text":text,"offsets":{"from":start,"to":end},"p":p}


def track(*texts):
    from app.production import SubtitleTrack
    return SubtitleTrack(id="s",workspace_id="w",task_id="t",timeline_version_id="v",language="en-US",
        source_language=None,style={},generation_method="timeline",model_id=None,artifact_ref="old.srt",transform_id="old",status="valid",
        cues=[SubtitleCue(id=str(i),source_cue_id=None,text=text,start_seconds=i,end_seconds=i+1) for i,text in enumerate(texts)])


def test_recognition_times_preserve_user_text_and_gaps():
    original=track("Check subtitle.", "听声音。")
    result={"transcription":[{"tokens":[token(" Check",200,450),token(" subtit",450,700),token("le",700,900),
        token(".",900,920),token("听",1500,1700),token("声音",1700,2000)]}]}
    changed=align_subtitle_track(original,result,3)
    assert [cue.text for cue in changed.cues]==[cue.text for cue in original.cues]
    assert [(cue.start_seconds,cue.end_seconds) for cue in changed.cues]==[(.2,.9),(1.5,2)]
    assert changed.artifact_ref is None
    assert original.artifact_ref=="old.srt"


@pytest.mark.parametrize("tokens", [
    [token("wrong",0,500)], [token("hello",0,500,.2)], [token("hello",-1,500)],
    [token("hello",0,4000)], [token("hello",0,0)], [token("hel",0,500),token("lo",400,600)],
    [token("hello",float('nan'),500)], [{"text":"hello"}], [None],
])
def test_unsafe_recognition_does_not_propose_times(tokens):
    with pytest.raises(ValueError):
        align_subtitle_track(track("hello"),{"transcription":[{"tokens":tokens}]},3)


def test_rejects_cut_inside_recognized_word_and_missing_schema():
    with pytest.raises(ValueError,match="断开"):
        align_subtitle_track(track("hel","lo"),{"transcription":[{"tokens":[token("hello",0,500)]}]},3)
    for result in ({}, {"transcription":[{}]}, {"transcription":"invalid"}):
        with pytest.raises(ValueError):
            align_subtitle_track(track("hello"),result,3)


def test_recognition_uses_local_cli_even_when_global_asr_is_cloud(tmp_path, monkeypatch):
    settings=SimpleNamespace(asr_provider="tencentcloud",local_whisper_backend="whisper_cpp",local_whisper_configured=True,
        local_whisper_cli_path="/local/whisper-cli",local_whisper_model_path="/external/model.bin")
    result={"transcription":[{"tokens":[token("hello",100,500)]}]}
    calls=[]
    def run(command, **kwargs):
        calls.append(command)
        Path(command[command.index('-of')+1]+'.json').write_text(json.dumps(result))
    monkeypatch.setattr('app.subtitle_alignment.subprocess.run',run)
    assert recognize_subtitle_timing(settings,tmp_path/'voice.wav','en-US',tmp_path)==result
    assert calls[0][0]=='/local/whisper-cli'
    assert calls[0][calls[0].index('-l')+1]=='en'
    assert '--prompt' not in calls[0]
    assert recognize_subtitle_timing(settings,tmp_path/'voice.wav','zh-CN',tmp_path)==result
    assert calls[1][calls[1].index('-l')+1]=='zh'
    assert calls[1][calls[1].index('--prompt')+1]=='以下使用简体中文。'
    settings.local_whisper_configured=False
    with pytest.raises(ValueError,match="本地"):
        recognize_subtitle_timing(settings,tmp_path/'voice.wav','en-US',tmp_path)
    assert len(calls)==2


def test_bilingual_alignment_preserves_translation_text_and_visibility():
    source=track("First.", "Second.")
    source=replace(source,cues=[replace(source.cues[0],start_seconds=.2,end_seconds=.7),replace(source.cues[1],start_seconds=1.5,end_seconds=2.2)])
    translation=replace(track("第二句", "第一句"),id="translated",language="zh-CN",source_language="en-US",style={"enabled":False},
        cues=[SubtitleCue(id="second",source_cue_id="1",text="第二句",start_seconds=0,end_seconds=1),
              SubtitleCue(id="first",source_cue_id="0",text="第一句",start_seconds=1,end_seconds=2)])
    result=synchronize_translated_subtitles([source,translation],source,3)
    assert result[1].style==translation.style
    assert [(cue.text,cue.start_seconds,cue.end_seconds) for cue in result[1].cues]==[("第一句",.2,.7),("第二句",1.5,2.2)]
    assert result[1].artifact_ref is None
    assert translation.cues[0].start_seconds==0


@pytest.mark.parametrize("refs", [[],[None],["missing"],["0","0"]])
def test_bilingual_alignment_rejects_broken_correspondence(refs):
    source=track("First.")
    translation=replace(source,id="translation",source_language="en-US",language="zh-CN",cues=[
        SubtitleCue(id=f"t{i}",source_cue_id=ref,text="译文",start_seconds=i,end_seconds=i+1) for i,ref in enumerate(refs)])
    with pytest.raises(ValueError,match="对应"):
        synchronize_translated_subtitles([source,translation],source,3)
    with pytest.raises(ValueError,match="双语"):
        synchronize_translated_subtitles([source],source,3)
    with pytest.raises(ValueError,match="双语"):
        synchronize_translated_subtitles([source,replace(translation,language=source.language)],source,3)
