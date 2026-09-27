import subprocess
import pytest
from app.silence_edits import detect_silence_cuts


def test_real_silence_detection_keeps_word_edges_and_uses_frame_boundaries(tmp_path):
    source=tmp_path/'speech.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'aevalsrc=if(between(t\\,1\\,2)\\,0\\,0.2*sin(2*PI*440*t)):s=48000:d=3',str(source)],check=True)
    cuts=detect_silence_cuts(source,3,10)
    assert len(cuts)==1
    left,right=cuts[0]
    assert 11120<=left<=11167 and 11833<=right<=11880
    assert abs(left/1000*30-round(left/1000*30))<.02


@pytest.mark.parametrize('expression', ['0','0.2*sin(2*PI*440*t)'])
def test_no_speech_or_no_silence_does_not_propose_a_cut(tmp_path,expression):
    source=tmp_path/'input.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'aevalsrc={expression}:s=48000:d=1',str(source)],check=True)
    with pytest.raises(ValueError):detect_silence_cuts(source,1)
