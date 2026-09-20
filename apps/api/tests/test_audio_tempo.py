import array
import subprocess
import wave

import pytest

from app.audio_timeline import render_stretched_audio


@pytest.mark.parametrize("rate", [.5, 2, 4])
def test_local_tempo_preserves_pitch_and_exact_duration(tmp_path, rate):
    source = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
        "-c:a", "pcm_s16le", str(source)], check=True)
    duration = round(2000 / rate)
    output = render_stretched_audio(source, 0, 2000, rate, duration, tmp_path / "tempo.wav")
    with wave.open(str(output)) as wav:
        assert wav.getnframes() / wav.getframerate() == pytest.approx(duration / 1000, abs=.001)
        wav.setpos(round(wav.getframerate() * .1))
        samples = array.array("h", wav.readframes(round(wav.getframerate() * .2)))
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        assert crossings / .2 == pytest.approx(440, abs=10)
