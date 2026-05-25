from __future__ import annotations

from pathlib import Path

import plugins.video_gen.openai as openai_video


def test_openai_video_provider_creates_and_downloads_video(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VIDEO_BASE_URL", "https://api.openai.test/v1")
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    requests = []

    def fake_json_request(method, url, *, api_key, payload=None, timeout=60):
        requests.append((method, url, api_key, payload))
        if method == "POST":
            return {
                "id": "video_test",
                "status": "completed",
                "model": "sora-2",
                "seconds": "8",
                "size": "1280x720",
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    def fake_download(video_id, *, api_key, base_url):
        assert video_id == "video_test"
        assert api_key == "sk-test"
        assert base_url == "https://api.openai.test/v1"
        return b"fake mp4 bytes"

    monkeypatch.setattr(openai_video, "_json_request", fake_json_request)
    monkeypatch.setattr(openai_video, "_download_video", fake_download)

    result = openai_video.OpenAIVideoGenProvider().generate(
        "vertical UGC ad for a resume tailoring product",
        duration=8,
        aspect_ratio="16:9",
        resolution="720p",
    )

    assert result["success"] is True
    assert result["provider"] == "openai"
    assert result["model"] == "sora-2"
    assert result["video_id"] == "video_test"
    assert Path(result["video"]).read_bytes() == b"fake mp4 bytes"
    assert requests[0] == (
        "POST",
        "https://api.openai.test/v1/videos",
        "sk-test",
        {
            "model": "sora-2",
            "prompt": "vertical UGC ad for a resume tailoring product",
            "seconds": "8",
            "size": "1280x720",
        },
    )


def test_openai_video_provider_sends_single_image_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    payloads = []

    def fake_json_request(method, url, *, api_key, payload=None, timeout=60):
        payloads.append(payload)
        return {
            "id": "video_image",
            "status": "completed",
            "model": "sora-2-pro",
            "seconds": "4",
            "size": "720x1280",
        }

    monkeypatch.setattr(openai_video, "_json_request", fake_json_request)
    monkeypatch.setattr(openai_video, "_download_video", lambda *args, **kwargs: b"video")

    result = openai_video.OpenAIVideoGenProvider().generate(
        "animate this product screenshot into a short mobile ad",
        model="sora-2-pro",
        image_url="https://example.com/screenshot.png",
        aspect_ratio="9:16",
    )

    assert result["success"] is True
    assert result["modality"] == "image"
    assert payloads[0]["input_reference"] == {"image_url": "https://example.com/screenshot.png"}
    assert payloads[0]["size"] == "720x1280"
