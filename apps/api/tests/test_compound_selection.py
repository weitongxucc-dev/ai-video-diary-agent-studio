import pytest
from app.compound_edits import compile_compound_selection


def test_followups_keep_stable_numbering_and_current_selection():
    steps=[{'index':0,'enabled':True,'operation_type':'AdjustVisualEffects'},
           {'index':1,'enabled':True,'operation_type':'EditAudioTimeline'},
           {'index':2,'enabled':True,'operation_type':'EditAudioTimeline'}]
    assert compile_compound_selection('取消第二项',steps)==[0,2]
    assert compile_compound_selection('只保留第1项和第3项',steps)==[0,2]
    assert compile_compound_selection('只保留画面修改',steps)==[0]
    assert compile_compound_selection('取消声音修改',steps)==[0]
    steps[1]['enabled']=False
    assert compile_compound_selection('恢复第2项',steps)==[0,1,2]
    assert compile_compound_selection('恢复全部修改',steps)==[0,1,2]
    assert compile_compound_selection('取消第3项',steps)==[0]
    for command in ('取消第99项','只保留第1项，然后再加音乐','取消第0项','取消第2项','换个更好的素材','取消一二项'):
        with pytest.raises(ValueError):compile_compound_selection(command,steps)
    with pytest.raises(ValueError,match='没有剩余'):
        compile_compound_selection('取消第1项和第3项',steps)
