"""Frozen delivery identities bind actual executable bytes without loose sources."""
from dataclasses import asdict
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app import audio_delivery_policy, device_resource_configuration as configuration, montage, packet_copy_policy
from app.config import Settings
from test_device_resource_profile import BACKENDS, DEVICE, profile_document, write_profile


FAMILIES = ('packet_copy', 'audio_encode', 'final_mux')


@pytest.fixture
def installed(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('backend identity must not run media, models or the executable')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    binary = tmp_path / 'installed-api'
    binary.write_bytes(b'isolated packaged implementation v1')
    binary.chmod(0o700)
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = tmp_path / name
        path.write_bytes(('isolated installed bytes ' + name).encode())
        path.chmod(0o700)
        tools[name] = path
    monkeypatch.setattr(configuration.shutil, 'which', lambda name: str(tools[name]))
    runtime = SimpleNamespace(platform=sys.platform, frozen=True, executable=str(binary))
    monkeypatch.setattr(configuration, 'sys', runtime)
    for module in (audio_delivery_policy, montage, packet_copy_policy):
        monkeypatch.setattr(module, '__file__', str(tmp_path / 'missing-sources' / (module.__name__ + '.py')))
    settings = Settings()
    return SimpleNamespace(binary=binary, tools=tools, runtime=runtime, settings=settings,
        identity=lambda name: configuration.backend_identity(name, None, settings=settings))


@pytest.mark.parametrize('family', FAMILIES)
def test_frozen_identity_uses_executable_when_module_sources_are_absent(installed, family):
    first = installed.identity(family)
    assert isinstance(first, str) and len(first) == 64
    assert installed.identity(family) == first
    installed.binary.write_bytes(b'isolated packaged implementation v2')
    assert installed.identity(family) not in (None, first)


@pytest.mark.parametrize('family', FAMILIES)
def test_development_identity_still_requires_its_source(installed, family):
    installed.runtime.frozen = False
    assert installed.identity(family) is None


@pytest.mark.parametrize('family', FAMILIES)
def test_development_identity_tracks_source_instead_of_python_binary(installed, tmp_path, monkeypatch, family):
    installed.runtime.frozen = False
    paths = {}
    for module in (audio_delivery_policy, montage, packet_copy_policy):
        path = tmp_path / (module.__name__ + '.py')
        path.write_bytes(('installed source ' + module.__name__).encode())
        monkeypatch.setattr(module, '__file__', str(path))
        paths[module.__name__] = path
    first = installed.identity(family)
    assert first is not None
    installed.binary.write_bytes(b'changed interpreter is not a replacement for source identity')
    assert installed.identity(family) == first
    source = paths[packet_copy_policy.__name__ if family == 'packet_copy' else audio_delivery_policy.__name__]
    source.write_bytes(b'changed delivery implementation source')
    assert installed.identity(family) not in (None, first)


def test_frozen_identity_keeps_operation_tools_and_fragment_contract(installed, monkeypatch):
    original = {family: installed.identity(family) for family in FAMILIES}
    assert None not in original.values() and len(set(original.values())) == 3
    monkeypatch.setattr(montage, 'PICTURE_FRAGMENT_CONTRACT', montage.PICTURE_FRAGMENT_CONTRACT + '-changed')
    assert installed.identity('packet_copy') not in (None, original['packet_copy'])
    assert installed.identity('audio_encode') == original['audio_encode']
    assert installed.identity('final_mux') == original['final_mux']
    installed.tools['ffprobe'].write_bytes(b'changed installed probing tool')
    assert all(installed.identity(family) not in (None, original[family]) for family in FAMILIES)


def test_changed_frozen_binary_invalidates_explicit_delivery_profile_without_replacing_it(installed, tmp_path):
    document = profile_document()
    path = write_profile(tmp_path, document)
    settings = Settings(device_resource_profile_path=str(path))
    def identities(name, budget):
        return BACKENDS[name] if name in BACKENDS else configuration.backend_identity(name, budget, settings=settings)
    def resolve():
        return configuration.resolve_device_resources(settings, {}, device=DEVICE, backend_identities=identities,
            select_packet_copy_preparation=True, select_audio_delivery_preparation=True)
    source_bytes = path.read_bytes()
    selected = resolve()
    assert path.read_bytes() == source_bytes
    for family in FAMILIES:
        budget = getattr(selected.budget, family)
        assert budget is not None and selected.evidence[family]['verification_status'] == 'unknown'
        document['budget'][family] = asdict(budget)
        document['backends'][family] = identities(family, budget)
    write_profile(tmp_path, document)
    explicit_bytes = path.read_bytes()
    explicit = resolve()
    assert all(getattr(explicit.budget, family) == getattr(selected.budget, family) for family in FAMILIES)
    installed.binary.write_bytes(b'replaced packaged implementation, old profile must no longer match')
    invalid = resolve()
    assert all(getattr(invalid.budget, family) is None for family in FAMILIES)
    assert all(invalid.unavailable[family] == 'backend_identity_changed_or_unavailable' for family in FAMILIES)
    assert invalid.budget.cpu_render == selected.budget.cpu_render
    assert path.read_bytes() == explicit_bytes
