"""A compiled picture clock stays exact across PCM, AAC and read-only currentness."""
from copy import deepcopy

import pytest

from app.creation.final_delivery_adoption import delivery_clock_matches_plan


def clock():
    return {'picture': {'packet_count': 275, 'time_base_num': 1, 'time_base_den': 90000,
            'first_pts': 0, 'last_end_pts': 825000},
        'audio': {'sample_count': 440000, 'sample_rate': 48000, 'channels': 2,
            'time_base_num': 1, 'time_base_den': 48000, 'first_pts': -1024,
            'last_end_pts': 440000, 'initial_skip_samples': 1024}}


def test_fractional_editorial_seconds_use_the_original_compiled_frame_clock():
    actual = clock()
    original = deepcopy(actual)
    assert delivery_clock_matches_plan(actual, 275, 9.167)
    assert actual == original


@pytest.mark.parametrize('part,key,value', [
    ('picture', 'packet_count', 274), ('picture', 'last_end_pts', 824999),
    ('picture', 'first_pts', 1), ('picture', 'time_base_num', 2),
    ('picture', 'time_base_den', 89999), ('audio', 'sample_count', 439999),
    ('audio', 'last_end_pts', 439999), ('audio', 'sample_rate', 44100),
    ('audio', 'time_base_den', 44100), ('audio', 'channels', 1),
])
def test_read_currentness_rejects_changed_exact_packet_or_sample_clock(part, key, value):
    actual = clock()
    actual[part][key] = value
    assert not delivery_clock_matches_plan(actual, 275, 9.167)


def test_plan_or_sealed_picture_frame_changes_cannot_reuse_the_old_delivery():
    assert not delivery_clock_matches_plan(clock(), 275, 9.2)
    assert not delivery_clock_matches_plan(clock(), 274, 9.167)
