"""Generate the fixture's two voiced syllables without recorded speech or a voice model."""

from pathlib import Path
import math
import struct
import wave


SAMPLE_RATE = 16_000
SAMPLES = 10_240
FUNDAMENTAL = 135
SYLLABLES = (
    (0.035, 0.278, ((680, 85, 1.0), (1180, 115, 0.36), (2480, 145, 0.14))),
    (0.330, 0.610, ((325, 75, 1.0), (840, 95, 0.52), (2200, 150, 0.13))),
)

audio = [0.0] * SAMPLES
for start, end, formants in SYLLABLES:
    spectrum = []
    for harmonic in range(1, 26):
        frequency = harmonic * FUNDAMENTAL
        strength = sum(weight * math.exp(-0.5 * ((frequency - center) / spread) ** 2)
                       for center, spread, weight in formants) / math.sqrt(harmonic)
        spectrum.append((frequency, strength))
    for index in range(round(start * SAMPLE_RATE), round(end * SAMPLE_RATE)):
        position = index / SAMPLE_RATE - start
        attack = min(1.0, position / 0.025)
        decay = min(1.0, (end - start - position) / 0.035)
        pitch_contour = position * (1 - position * 0.15)
        audio[index] = attack * decay * sum(amplitude * math.sin(math.tau * frequency * pitch_contour)
                                            for frequency, amplitude in spectrum)

peak = max(map(abs, audio))
pcm = struct.pack(f"<{SAMPLES}h", *(round(15_500 * value / peak) for value in audio))
with wave.open(str(Path(__file__).with_name("driver.wav")), "wb") as output:
    output.setparams((1, 2, SAMPLE_RATE, SAMPLES, "NONE", "not compressed"))
    output.writeframes(pcm)
