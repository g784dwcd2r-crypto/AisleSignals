import importlib.util
import sys
from pathlib import Path
import zipfile

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'scripts/local-vision.py'
spec = importlib.util.spec_from_file_location('local_vision_setup_tests', SOURCE)
vision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vision)


@pytest.mark.parametrize('system,machine,expected', [
    ('darwin', 'arm64', 'ollama/llama-server'),
    ('win32', 'AMD64', 'ollama-windows-amd64/lib/ollama/llama-server.exe'),
    ('win32', 'x86_64', 'ollama-windows-amd64/lib/ollama/llama-server.exe'),
    ('win32', 'ARM64', 'ollama-windows-arm64/lib/ollama/llama-server.exe'),
])
def test_runtime_selects_native_pinned_server(system, machine, expected, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', system)
    monkeypatch.setattr(vision.platform, 'machine', lambda: machine)
    assert vision.default_server(tmp_path) == tmp_path / expected
    assert len(vision.runtime_spec()['sha256']) == 64


@pytest.mark.parametrize('system,machine', [('darwin', 'x86_64'), ('win32', 'x86'), ('linux', 'x86_64')])
def test_unsupported_platform_requires_explicit_server(system, machine):
    assert vision.runtime_spec(system, machine) is None


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/absolute', 'lib/../../escape', r'lib\..\..\escape'])
def test_archive_rejects_paths_before_extracting(name, tmp_path):
    archive = tmp_path / 'runtime.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('normal.txt', b'first')
        bundle.writestr(name, b'bad')
    with pytest.raises(ValueError):
        vision.extract_windows_archive(archive, tmp_path / 'dest')
    assert not (tmp_path / 'dest/normal.txt').exists()


def test_archive_preserves_server_and_native_library_layout(tmp_path):
    archive = tmp_path / 'runtime.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('lib/ollama/llama-server.exe', b'fixture')
        bundle.writestr('lib/ollama/cpu/ggml.dll', b'fixture-library')
    vision.extract_windows_archive(archive, tmp_path / 'dest')
    assert (tmp_path / 'dest/lib/ollama/cpu/ggml.dll').read_bytes() == b'fixture-library'


def test_archive_refuses_symlink_members(tmp_path):
    archive = tmp_path / 'runtime.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        item = zipfile.ZipInfo('link')
        item.create_system = 3
        item.external_attr = 0o120777 << 16
        bundle.writestr(item, 'outside')
    with pytest.raises(ValueError):
        vision.extract_windows_archive(archive, tmp_path / 'dest')
