"""Storage-selection and installation regressions; all models/servers are synthetic."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from scripts import pilot_preflight as pf
from scripts import run_pilot as launcher

SOURCE = Path(__file__).resolve().parents[2]
SUBPROCESS_POPEN = subprocess.Popen


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(SOURCE / "scripts/local-vision.py", root / "scripts/local-vision.py")
    vision = pf.model_module(root)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(launcher, "source_root", lambda: root)
    monkeypatch.setattr(launcher, "model_module", lambda _: vision)
    monkeypatch.setattr(vision.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("Real downloads are forbidden in this test"))
    return root, vision


def config_file(path, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def call_main(monkeypatch, module, args):
    monkeypatch.setattr(sys, "argv", [str(module.__file__), *map(str, args)])
    return module.main()


@pytest.mark.parametrize("selection", ["default", "saved", "data", "data-config", "explicit", "override"])
def test_setup_start_and_direct_cli_share_one_selection(workspace, tmp_path, monkeypatch, selection):
    root, vision = workspace
    data = tmp_path / "private workspace"
    runtime = tmp_path / "model folder"
    args = []
    expected = root / ".local/vision-runtime"
    if selection == "saved":
        config_file(root / ".local/pilot/config.json", vision_runtime_dir=str(runtime))
        expected = runtime
    if selection in {"data", "data-config", "explicit", "override"}:
        args = ["--data-dir", data]
        # A selected data directory never falls back to another workspace's config.
        config_file(root / ".local/pilot/config.json", schema_version=999)
        expected = data / "vision-runtime"
    if selection in {"data-config", "explicit", "override"}:
        config_file(data / "config.json", vision_runtime_dir="relative models", vision_port=11437)
        expected = root / "relative models"
    if selection in {"explicit", "override"}:
        selected = config_file(tmp_path / "another config.json", vision_runtime_dir=str(runtime), data_dir="ignored data")
        args += ["--config", selected]
        expected = runtime
    if selection == "override":
        expected = tmp_path / "override models"
        args += ["--runtime-dir", expected]
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    setups = []
    monkeypatch.setattr(vision, "setup", lambda runtime_only=False: setups.append((vision.RUNTIME, runtime_only)))
    checked = []
    monkeypatch.setattr(launcher, "preflight", lambda value: checked.append(value) or {"launch_status": "PASS", "checks": []})
    runs = []
    monkeypatch.setattr(vision, "run", lambda server, port: runs.append((vision.RUNTIME, server, port)) or 0)

    assert call_main(monkeypatch, launcher, ["model-setup", *args]) == 0
    assert call_main(monkeypatch, launcher, ["--check", *args]) == 0
    assert call_main(monkeypatch, vision, ["setup", *args]) == 0
    assert call_main(monkeypatch, vision, ["run", *args]) == 0
    assert setups == [(expected, False), (expected, False)]
    assert checked[0].runtime_dir == expected
    assert runs[0] == (expected, str(checked[0].vision_server) if checked[0].vision_server else None, checked[0].vision_port)
    if args and args[0] == "--data-dir":
        assert checked[0].data_dir == data
    assert {path: path.read_bytes() for path in before} == before
    assert not list(tmp_path.rglob("api-token"))
    assert not list(tmp_path.rglob("*.db"))


def test_explicit_custom_server_is_not_relocated_by_runtime_override(workspace, tmp_path, monkeypatch):
    root, vision = workspace
    external = tmp_path / "independent server"
    external.write_bytes(b"synthetic existing compatible executable")
    selected = config_file(tmp_path / "config.json", vision_server=str(external), vision_runtime_dir="prior models")
    runtime = tmp_path / "selected models"
    resolved = pf.resolve_config(selected, root=root, runtime_dir=runtime)
    assert resolved.vision_server == external
    assert resolved.runtime_dir == runtime
    calls = []
    monkeypatch.setattr(vision, "run", lambda server, port: calls.append(server) or 0)
    assert call_main(monkeypatch, vision, ["run", "--config", selected, "--runtime-dir", runtime]) == 0
    assert calls == [str(external)]
    assert external.read_bytes() == b"synthetic existing compatible executable"


def test_unflagged_legacy_data_only_config_keeps_original_model_default(workspace, tmp_path):
    root, _ = workspace
    selected = config_file(root / ".local/pilot/config.json", data_dir=str(tmp_path / "existing data"))
    config = pf.resolve_config(root=root)
    assert config.data_dir == tmp_path / "existing data"
    assert config.runtime_dir == root / ".local/vision-runtime"
    assert config == pf.load_config(selected, root=root)


@pytest.mark.parametrize("system,machine,relative_server", [
    ("darwin", "arm64", "ollama/llama-server"),
    ("win32", "AMD64", "ollama-windows-amd64/lib/ollama/llama-server.exe"),
    ("win32", "ARM64", "ollama-windows-arm64/lib/ollama/llama-server.exe"),
])
def test_packaged_defaults_stay_external_and_select_native_server(workspace, tmp_path, monkeypatch, system, machine, relative_server):
    root, vision = workspace
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(vision.platform, "machine", lambda: machine)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "synthetic home"))
    # Replace only the preflight module's OS view; never change the host's Path class.
    monkeypatch.setattr(pf, "os", SimpleNamespace(name="nt" if system == "win32" else "posix", path=os.path,
                                                environ={"LOCALAPPDATA": str(tmp_path / "Local AppData")},
                                                geteuid=getattr(os, "geteuid", lambda: 0)))
    expected_data = (tmp_path / "Local AppData" if system == "win32"
                     else tmp_path / "synthetic home/Library/Application Support") / "AisleSignalsPilot"
    config = pf.resolve_config(root=root)
    assert config.data_dir == expected_data
    assert config.runtime_dir == expected_data / "vision-runtime"
    assert config.vision_server == config.runtime_dir / relative_server
    assert not config.runtime_dir.is_relative_to(root)
    override = pf.resolve_config(root=root, data_dir=tmp_path / "private data")
    assert override.runtime_dir == tmp_path / "private data/vision-runtime"
    assert override.vision_server == override.runtime_dir / relative_server
    setups = []
    monkeypatch.setattr(vision, "setup", lambda runtime_only=False: setups.append(vision.RUNTIME))
    assert call_main(monkeypatch, launcher, ["model-setup", "--runtime-only"]) == 0
    assert setups == [expected_data / "vision-runtime"]
    configured = tmp_path / "configured bundle models"
    config_file(expected_data / "config.json", vision_runtime_dir=str(configured))
    assert call_main(monkeypatch, launcher, ["model-setup", "--runtime-only"]) == 0
    assert setups[-1] == configured
    assert pf.resolve_config(root=root).vision_server == configured / relative_server


def test_relative_cli_paths_use_cwd_but_config_values_use_source_root(workspace, tmp_path, monkeypatch):
    root, vision = workspace
    cwd = tmp_path / "operator directory"
    cwd.mkdir()
    selected = config_file(cwd / "settings/config.json", vision_runtime_dir="configured models", vision_server="custom/server")
    monkeypatch.chdir(cwd)
    config = pf.resolve_config(Path("settings/config.json"), root=root)
    assert config.runtime_dir == root / "configured models"
    assert config.vision_server == root / "custom/server"
    calls = []
    monkeypatch.setattr(vision, "setup", lambda runtime_only=False: calls.append(vision.RUNTIME))
    args = ["--config", selected.relative_to(cwd), "--runtime-dir", "CLI models", "--data-dir", "CLI data"]
    assert call_main(monkeypatch, launcher, ["model-setup", *args]) == 0
    assert call_main(monkeypatch, vision, ["setup", *args]) == 0
    config = pf.resolve_config(Path("settings/config.json"), root=root, data_dir=Path("CLI data"), runtime_dir=Path("CLI models"))
    assert calls == [cwd / "CLI models"] * 2
    assert config.runtime_dir == cwd / "CLI models"
    assert config.data_dir == cwd / "CLI data"


def test_cli_home_expansion_uses_native_path_without_reading_the_real_home(workspace, tmp_path, monkeypatch):
    root, _ = workspace
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "synthetic home"))
    # expanduser uses the OS-specific path function, separate from Path.home.
    monkeypatch.setattr(pf.os.path, "expanduser", lambda path: str(tmp_path / "synthetic home") + str(path)[1:] if str(path).startswith("~") else str(path))
    config = pf.resolve_config(root=root, data_dir=Path("~/private data"), runtime_dir=Path("~/private models"))
    assert config.data_dir == tmp_path / "synthetic home/private data"
    assert config.runtime_dir == tmp_path / "synthetic home/private models"


@pytest.mark.parametrize("bad", ["missing", "malformed", "directory", "unknown-field", "invalid-runtime", "too-large"])
@pytest.mark.parametrize("entry", ["bundle", "direct", "start"])
def test_invalid_explicit_configuration_never_falls_back_or_starts_setup(workspace, tmp_path, monkeypatch, bad, entry):
    root, vision = workspace
    selected = tmp_path / "selected.json"
    if bad == "malformed":
        selected.write_text("{PRIVATE_INVALID", encoding="utf-8")
    elif bad == "directory":
        selected.mkdir()
    elif bad == "unknown-field":
        config_file(selected, private_secret="NEVER_PRINT")
    elif bad == "invalid-runtime":
        config_file(selected, vision_runtime_dir="\nNEVER_PRINT")
    elif bad == "too-large":
        selected.write_bytes(b" " * 16_385)
    calls = []
    monkeypatch.setattr(vision, "setup", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(launcher, "preflight", lambda *_: pytest.fail("Invalid config reached startup"))
    args = ["--config", selected, "--runtime-dir", tmp_path / "override"]
    module, command = (vision, ["setup"]) if entry == "direct" else (launcher, ["model-setup"] if entry == "bundle" else ["--check"])
    assert call_main(monkeypatch, module, [*command, *args]) == 1
    assert calls == []
    assert not (tmp_path / "override").exists()
    assert not (root / ".local").exists()


def test_invalid_selected_data_config_is_not_ignored(workspace, tmp_path, monkeypatch):
    root, vision = workspace
    data = tmp_path / "selected data"
    config_file(data / "config.json", schema_version=999)
    config_file(root / ".local/pilot/config.json", vision_runtime_dir="must not use")
    monkeypatch.setattr(vision, "setup", lambda **_: pytest.fail("Invalid selected config invoked setup"))
    assert call_main(monkeypatch, launcher, ["model-setup", "--data-dir", data]) == 1
    assert not (data / "vision-runtime").exists()
    assert not (root / "must not use").exists()


@pytest.mark.parametrize("script,args", [
    ("run_pilot.py", []), ("run_pilot.py", ["model-setup"]),
    ("local-vision.py", ["setup"]), ("local-vision.py", ["run"]), ("pilot_preflight.py", []),
])
def test_actual_entry_point_help_documents_matching_storage_flags(tmp_path, script, args):
    result = subprocess.run([sys.executable, "-E", "-s", str(SOURCE / "scripts" / script), *args, "--help"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    for flag in ("--config", "--data-dir", "--runtime-dir"):
        assert flag in result.stdout


@pytest.mark.parametrize("args", [["setup", "--server", "unused"], ["setup", "--port", "11439"], ["run", "--runtime-only"]])
def test_ignored_cross_command_flags_now_explain_their_scope(workspace, monkeypatch, args):
    _, vision = workspace
    with pytest.raises(SystemExit) as caught:
        call_main(monkeypatch, vision, args)
    assert caught.value.code == 2


def test_runtime_only_is_forwarded_without_starting_a_model(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    calls = []
    monkeypatch.setattr(vision, "setup", lambda runtime_only=False: calls.append((vision.RUNTIME, runtime_only)))
    runtime = tmp_path / "runtime only"
    for module, prefix in ((launcher, ["model-setup"]), (vision, ["setup"])):
        assert call_main(monkeypatch, module, [*prefix, "--runtime-dir", runtime, "--runtime-only"]) == 0
    assert calls == [(runtime, True)] * 2


def test_setup_prechecks_all_existing_weights_before_any_download(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    runtime = tmp_path / "models"
    (runtime / "models").mkdir(parents=True)
    unrelated = runtime / "models/second.gguf"
    unrelated.write_bytes(b"unrelated existing model")
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    monkeypatch.setattr(vision, "WEIGHTS", {"first.gguf": "0" * 64, "second.gguf": "1" * 64})
    with pytest.raises(ValueError, match="preserved"):
        vision.setup()
    assert unrelated.read_bytes() == b"unrelated existing model"
    assert list((runtime / "models").iterdir()) == [unrelated]
    assert not (runtime / "manifest.json").exists()


def test_download_publishes_verified_bytes_and_preserves_an_old_partial_file(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    content = b"small synthetic model"
    target = tmp_path / "new.gguf"
    old_partial = tmp_path / "new.gguf.part"
    old_partial.write_bytes(b"old operator-owned partial download")
    calls = []
    monkeypatch.setattr(vision.urllib.request, "urlopen", lambda *_a, **_kw: calls.append(1) or io.BytesIO(content))
    expected = hashlib.sha256(content).hexdigest()
    vision.download("https://synthetic.invalid/model", target, expected)
    vision.download("https://synthetic.invalid/model", target, expected)
    assert calls == [1]
    assert target.read_bytes() == content
    assert old_partial.read_bytes() == b"old operator-owned partial download"
    assert not list(tmp_path.glob(".*.download-*"))


def test_download_race_never_replaces_a_file_created_during_transfer(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    target = tmp_path / "model.gguf"
    content = b"synthetic requested model"

    def response(*_args, **_kwargs):
        target.write_bytes(b"another installation owns this file")
        return io.BytesIO(content)

    monkeypatch.setattr(vision.urllib.request, "urlopen", response)
    with pytest.raises(FileExistsError):
        vision.download("https://synthetic.invalid/model", target, hashlib.sha256(content).hexdigest())
    assert target.read_bytes() == b"another installation owns this file"
    assert not list(tmp_path.glob(".*.download-*"))


def test_bad_download_checksum_leaves_no_installed_or_partial_file(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    monkeypatch.setattr(vision.urllib.request, "urlopen", lambda *_a, **_kw: io.BytesIO(b"wrong bytes"))
    with pytest.raises(ValueError, match="Checksum"):
        vision.download("https://synthetic.invalid/model", tmp_path / "model.gguf", "0" * 64)
    assert list(tmp_path.iterdir()) == [workspace[0]]


def test_native_setup_verifies_repeat_install_and_preserves_changed_runtime(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    runtime = tmp_path / "synthetic runtime"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("lib/ollama/llama-server.exe", b"synthetic native server")
        archive.writestr("lib/ollama/cpu/library.dll", b"synthetic native dependency")
    data = archive_bytes.getvalue()
    spec = {"url": "https://synthetic.invalid/runtime.zip", "archive": "runtime.zip", "sha256": hashlib.sha256(data).hexdigest(),
            "directory": "native", "server": "lib/ollama/llama-server.exe"}
    monkeypatch.setattr(vision, "runtime_spec", lambda: spec)
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    calls = []
    monkeypatch.setattr(vision.urllib.request, "urlopen", lambda *_a, **_kw: calls.append(1) or io.BytesIO(data))
    vision.setup(runtime_only=True)
    token = runtime / "api-token"
    token.write_bytes(b"existing private token unchanged")
    vision.setup(runtime_only=True)
    assert calls == [1]
    assert vision.default_server().read_bytes() == b"synthetic native server"
    assert token.read_bytes() == b"existing private token unchanged"
    assert not (runtime / "models").exists()
    vision.default_server().write_bytes(b"separately prepared replacement")
    with pytest.raises(ValueError, match="preserved"):
        vision.setup(runtime_only=True)
    assert vision.default_server().read_bytes() == b"separately prepared replacement"
    assert calls == [1]
    assert not list(runtime.glob(".runtime-setup-*"))


def test_native_bundle_missing_its_expected_server_is_not_promoted(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    runtime = tmp_path / "incomplete runtime"
    runtime.mkdir()
    archive = runtime / "synthetic.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("unrelated.txt", b"synthetic incomplete bundle")
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    with pytest.raises(ValueError, match="not installed"):
        vision.install_runtime(archive, runtime / "native", "lib/ollama/llama-server.exe")
    assert not (runtime / "native").exists()
    assert not list(runtime.glob(".runtime-setup-*"))


@pytest.mark.parametrize("manifest", [{"model": "unrelated", "revision": "other"}, [], "unrelated"])
def test_foreign_manifest_is_not_overwritten(workspace, tmp_path, monkeypatch, manifest):
    _, vision = workspace
    runtime = tmp_path / "foreign runtime"
    runtime.mkdir()
    target = runtime / "manifest.json"
    content = json.dumps(manifest).encode()
    target.write_bytes(content)
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    with pytest.raises(ValueError, match="preserved"):
        vision.setup()
    assert target.read_bytes() == content
    assert list(runtime.iterdir()) == [target]


@pytest.mark.skipif(os.name == "nt", reason="Creating synthetic symlinks requires Windows privileges")
def test_symlinked_model_target_is_not_followed_or_downloaded(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    runtime = tmp_path / "selected runtime"
    (runtime / "models").mkdir(parents=True)
    other = tmp_path / "other model"
    other.write_bytes(b"untouched")
    (runtime / "models" / next(iter(vision.WEIGHTS))).symlink_to(other)
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    with pytest.raises(ValueError, match="symbolic"):
        vision.setup()
    assert other.read_bytes() == b"untouched"


@pytest.fixture
def synthetic_run(workspace, tmp_path, monkeypatch):
    """Use tiny verified bytes and forbid any real native process."""
    _, vision = workspace
    runtime = tmp_path / "direct runtime"
    models = runtime / "models"
    models.mkdir(parents=True)
    weights = {"model.gguf": b"synthetic weights", "projector.gguf": b"synthetic projector"}
    for name, content in weights.items():
        (models / name).write_bytes(content)
    server = tmp_path / "synthetic server"
    server.write_bytes(b"not an executable")
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    monkeypatch.setattr(vision, "WEIGHTS", {name: hashlib.sha256(content).hexdigest() for name, content in weights.items()})
    monkeypatch.setattr(vision.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Invalid setup reached native process creation"))
    return vision, runtime, server


@pytest.mark.parametrize("content", [b"", b"short", b"x" * 257, b"x" * 1025, b"x" * 40 + b"\x00", b"\xff" * 48])
def test_direct_run_refuses_invalid_token_before_process_creation_and_preserves_file(synthetic_run, monkeypatch, content):
    vision, runtime, server = synthetic_run
    token = runtime / "api-token"
    token.write_bytes(content)
    if os.name != "nt":
        token.chmod(0o640)
    mode = token.stat().st_mode
    assert call_main(monkeypatch, vision, ["run", "--runtime-dir", runtime, "--server", server]) == 1
    assert token.read_bytes() == content
    assert token.stat().st_mode == mode


@pytest.mark.parametrize("existing", [False, True])
def test_direct_run_uses_a_valid_private_token_without_exposing_or_replacing_it(synthetic_run, monkeypatch, capsys, existing):
    vision, runtime, server = synthetic_run
    runtime_mode = runtime.stat().st_mode
    target = runtime / "api-token"
    original = b"synthetic-existing-token-" + b"x" * 32 + b"\n"
    if existing:
        target.write_bytes(original)
    calls = []
    monkeypatch.setattr(vision.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(wait=lambda: 0))
    assert vision.run(str(server), 11439) == 0
    assert len(calls) == 1
    token_bytes = target.read_bytes()
    if existing:
        assert token_bytes == original
    else:
        assert 32 <= len(token_bytes.strip()) <= 256
    assert token_bytes.decode().strip() not in str(calls)
    assert token_bytes.decode().strip() not in capsys.readouterr().out
    assert calls[0][0][calls[0][0].index("--api-key-file") + 1] == str(target)
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
        assert runtime.stat().st_mode == runtime_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and synthetic symlink race; Windows ACL acceptance remains separate")
def test_token_parent_swap_cannot_chmod_an_unrelated_directory(synthetic_run, tmp_path, monkeypatch):
    vision, runtime, _ = synthetic_run
    token = runtime / "api-token"
    content = b"synthetic-existing-token-" + b"x" * 40
    token.write_bytes(content)
    unrelated = tmp_path / "unrelated directory"
    unrelated.mkdir(mode=0o755)
    unrelated.chmod(0o755)
    unrelated_token = unrelated / "api-token"
    unrelated_token.write_bytes(b"synthetic unrelated leaf remains unchanged")
    unrelated_token.chmod(0o640)
    retained = tmp_path / "retained original runtime"
    original_fchmod = os.fchmod

    def swap_parent(descriptor, mode):
        original_fchmod(descriptor, mode)
        runtime.rename(retained)
        runtime.symlink_to(unrelated, target_is_directory=True)

    monkeypatch.setattr(vision.os, "fchmod", swap_parent)
    vision.token_file()
    assert unrelated.stat().st_mode & 0o777 == 0o755
    assert unrelated_token.stat().st_mode & 0o777 == 0o640
    assert unrelated_token.read_bytes() == b"synthetic unrelated leaf remains unchanged"
    assert (retained / "api-token").read_bytes() == content
    assert (retained / "api-token").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory mode; Windows ACL acceptance remains separate")
def test_token_creation_requests_private_mode_for_a_new_runtime(workspace, tmp_path, monkeypatch):
    _, vision = workspace
    runtime = tmp_path / "new token runtime"
    monkeypatch.setattr(vision, "RUNTIME", runtime)
    target = vision.token_file()
    assert runtime.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Synthetic symlink creation requires Windows privileges; junction is tested separately")
@pytest.mark.parametrize("location", ["token", "model", "model-directory"])
def test_direct_run_refuses_linked_token_or_model_without_touching_its_target(synthetic_run, tmp_path, location):
    vision, runtime, server = synthetic_run
    if location == "model-directory":
        original = runtime / "models"
        outside = tmp_path / "outside models"
        original.rename(outside)
        original.symlink_to(outside, target_is_directory=True)
        preserved = {path: path.read_bytes() for path in outside.iterdir()}
        with pytest.raises(ValueError):
            vision.run(str(server), 11439)
        assert {path: path.read_bytes() for path in outside.iterdir()} == preserved
    else:
        outside = tmp_path / "outside private file"
        path = runtime / "api-token" if location == "token" else runtime / "models/model.gguf"
        data = b"synthetic-valid-token-" + b"x" * 40 if location == "token" else path.read_bytes()
        outside.write_bytes(data)
        outside.chmod(0o640)
        path.unlink(missing_ok=True)
        path.symlink_to(outside)
        with pytest.raises(ValueError):
            vision.run(str(server), 11439)
        assert outside.read_bytes() == data
        assert outside.stat().st_mode & 0o777 == 0o640
    if location != "token":
        assert not (runtime / "api-token").exists()


def test_direct_run_refuses_hard_linked_token_without_changing_shared_file(synthetic_run, tmp_path):
    vision, runtime, server = synthetic_run
    outside = tmp_path / "existing private file"
    content = b"synthetic-linked-token-" + b"x" * 40
    outside.write_bytes(content)
    os.link(outside, runtime / "api-token")
    mode = outside.stat().st_mode
    with pytest.raises(ValueError, match="regular file"):
        vision.run(str(server), 11439)
    assert outside.read_bytes() == content
    assert outside.stat().st_mode == mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner validation; Windows ACL acceptance remains separate")
def test_direct_run_refuses_token_not_owned_by_current_user(synthetic_run, monkeypatch):
    vision, runtime, server = synthetic_run
    token = runtime / "api-token"
    content = b"synthetic-token-" + b"x" * 40
    token.write_bytes(content)
    token.chmod(0o640)
    original_fstat = os.fstat

    def foreign_token(descriptor):
        metadata = original_fstat(descriptor)
        return SimpleNamespace(st_mode=metadata.st_mode, st_nlink=metadata.st_nlink, st_uid=os.geteuid() + 10000)

    monkeypatch.setattr(vision.os, "fstat", foreign_token)
    with pytest.raises(ValueError, match="current user"):
        vision.run(str(server), 11439)
    assert token.read_bytes() == content
    assert token.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO check; Windows does not use filesystem FIFOs")
def test_direct_run_rejects_nonregular_token_without_waiting_for_a_writer(synthetic_run):
    vision, runtime, server = synthetic_run
    os.mkfifo(runtime / "api-token", 0o600)
    with pytest.raises(ValueError, match="regular file"):
        vision.run(str(server), 11439)


@pytest.mark.parametrize("location", ["directory", "ancestor"])
def test_shared_private_path_check_rejects_windows_reparse_attributes(tmp_path, monkeypatch, location):
    import stat
    selected = tmp_path / "private directory"
    checked = selected if location == "directory" else selected / "runtime/api-token"
    original = Path.lstat

    def metadata(path):
        if path == selected:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
        return original(path)

    monkeypatch.setattr(Path, "lstat", metadata)
    assert pf.private_path_safe(checked) is False


@pytest.mark.skipif(os.name != "nt", reason="Native Windows directory junction creation is only exercised on Windows CI")
def test_native_windows_junction_is_rejected_by_setup_token_and_shared_check(synthetic_run, tmp_path, monkeypatch):
    vision, runtime, _ = synthetic_run
    selected = tmp_path / "junction runtime"
    # The synthetic model fixture forbids Popen. Permit only this OS junction
    # utility while retaining the native-model guard for setup and token checks.
    with monkeypatch.context() as junction:
        junction.setattr(subprocess, "Popen", SUBPROCESS_POPEN)
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(selected), str(runtime)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    try:
        monkeypatch.setattr(vision, "RUNTIME", selected)
        assert pf.private_path_safe(selected) is False
        assert pf.private_path_safe(selected / "api-token") is False
        with pytest.raises(ValueError, match="symbolic links"):
            vision.setup(runtime_only=True)
        with pytest.raises(ValueError, match="reparse"):
            vision.token_file()
        assert not (runtime / "api-token").exists()
        assert sorted(path.name for path in runtime.iterdir()) == ["models"]
    finally:
        selected.rmdir()


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory ownership/modes; Windows ACL verification is separate")
@pytest.mark.parametrize("mode,expected", [(0o777, False), (0o775, False), (0o1777, True), (0o1775, True), (0o700, True), (0o755, True)])
def test_shared_private_path_rejects_unsafe_posix_ancestors_but_allows_sticky_temp(tmp_path, mode, expected):
    ancestor = tmp_path / "synthetic ancestor"
    ancestor.mkdir()
    ancestor.chmod(mode)
    nested = ancestor / "private runtime"
    nested.mkdir(mode=0o700)
    assert pf.private_path_safe(ancestor) is expected
    assert pf.private_path_safe(nested / "api-token") is expected
    assert ancestor.stat().st_mode & 0o7777 == mode


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership checks; Windows ACL verification is separate")
@pytest.mark.parametrize("owner,mode,expected", [(0, 0o1777, True), ("current", 0o1777, True), ("foreign", 0o755, False), ("foreign", 0o1777, False)])
def test_shared_private_path_checks_ancestor_owner_even_when_sticky(tmp_path, monkeypatch, owner, mode, expected):
    import stat
    ancestor = tmp_path / "synthetic owned directory"
    ancestor.mkdir()
    original_lstat = Path.lstat
    user_id = os.geteuid() if owner == "current" else os.geteuid() + 10000 if owner == "foreign" else 0

    def metadata(path):
        if path == ancestor:
            return SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=user_id)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", metadata)
    assert pf.private_path_safe(ancestor / "missing runtime/api-token") is expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory modes; Windows ACL verification is separate")
@pytest.mark.parametrize("entry", ["bundle", "direct"])
@pytest.mark.parametrize("mode", [0o755, 0o700])
def test_setup_entrypoints_preserve_existing_runtime_directory_mode(workspace, tmp_path, monkeypatch, entry, mode):
    _, vision = workspace
    runtime = tmp_path / "existing setup directory"
    runtime.mkdir()
    runtime.chmod(mode)
    # Exercise actual setup without downloading models or extracting a runtime.
    monkeypatch.setattr(vision, "WEIGHTS", {})
    monkeypatch.setattr(vision, "runtime_spec", lambda: None)
    module, command = (launcher, ["model-setup"]) if entry == "bundle" else (vision, ["setup"])
    assert call_main(monkeypatch, module, [*command, "--runtime-dir", runtime]) == 0
    assert runtime.stat().st_mode & 0o777 == mode
    assert (runtime / "manifest.json").is_file()


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory modes; Windows ACL verification is separate")
@pytest.mark.parametrize("entry", ["bundle", "direct"])
def test_setup_refuses_world_writable_ancestor_before_any_directory_or_download(workspace, tmp_path, monkeypatch, entry):
    _, vision = workspace
    ancestor = tmp_path / "unsafe shared directory"
    ancestor.mkdir()
    ancestor.chmod(0o777)
    runtime = ancestor / "not created"
    module, command = (launcher, ["model-setup"]) if entry == "bundle" else (vision, ["setup"])
    assert call_main(monkeypatch, module, [*command, "--runtime-dir", runtime]) == 1
    assert not runtime.exists()
    assert ancestor.stat().st_mode & 0o777 == 0o777
