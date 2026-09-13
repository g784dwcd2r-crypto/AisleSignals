"""Casework-only startup must not attach to a present or later local model."""

import pytest

from services.api.interaction_vision import VisionError, VisionProvider


@pytest.fixture(autouse=True)
def clean_vision_environment(monkeypatch):
    for name in ("DISABLED", "TOKEN", "TOKEN_FILE", "URL", "BACKEND", "MODEL"):
        monkeypatch.delenv("AISLESIGNALS_VISION_" + name, raising=False)

    def forbidden_network(*args, **kwargs):
        pytest.fail("Disabled vision must not construct an HTTP client")

    monkeypatch.setattr("services.api.interaction_vision.httpx.AsyncClient", forbidden_network)


def test_disabled_provider_reports_unavailable_without_reading_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AISLESIGNALS_VISION_DISABLED", "1")
    monkeypatch.setenv("AISLESIGNALS_VISION_TOKEN_FILE", str(tmp_path / "not-installed-token"))
    provider = VisionProvider()
    assert provider.status()["ready"] is False
    assert provider.status()["mode"] == "disabled"
    # Frame decoding and network are both forbidden: the explicit interlock wins.
    with pytest.raises(VisionError, match="disabled"):
        provider.analyze([])
    with pytest.raises(VisionError, match="disabled"):
        provider._request("GET", "/api/tags")


def test_disabled_instance_does_not_attach_after_environment_flag_removed(monkeypatch):
    monkeypatch.setenv("AISLESIGNALS_VISION_DISABLED", "1")
    provider = VisionProvider()
    monkeypatch.delenv("AISLESIGNALS_VISION_DISABLED")
    assert provider.status()["ready"] is False
    with pytest.raises(VisionError, match="disabled"):
        provider.analyze([])


def test_environment_interlock_applies_to_preexisting_provider(monkeypatch):
    provider = VisionProvider()
    monkeypatch.setenv("AISLESIGNALS_VISION_DISABLED", "1")
    assert provider.status()["mode"] == "disabled"
    with pytest.raises(VisionError, match="disabled"):
        provider.analyze([])
    with pytest.raises(VisionError, match="disabled"):
        provider._request("POST", "/v1/chat/completions", payload={}, inference=True)


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_exact_one_enables_the_interlock(monkeypatch, value):
    monkeypatch.setenv("AISLESIGNALS_VISION_DISABLED", value)
    assert VisionProvider().is_disabled() is False
