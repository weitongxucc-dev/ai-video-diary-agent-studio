"""Shared pre-generation estimate; actual audio duration is checked separately."""
import re

CHINESE_CHARACTERS_PER_SECOND = 4.0
LATIN_WORDS_PER_SECOND = 2.5


def estimate_speech_seconds(text: str) -> float:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
    return chinese_chars / CHINESE_CHARACTERS_PER_SECOND + latin_words / LATIN_WORDS_PER_SECOND
