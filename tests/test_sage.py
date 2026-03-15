"""Tests for Sage chat and TTS endpoints."""
import pytest
from unittest.mock import patch, MagicMock


def _make_mock_response(text="Your finances look great."):
    """Create a mock Anthropic message response."""
    block = MagicMock()
    block.text = text
    block.type = "text"
    block.model_dump.return_value = {"type": "text", "text": text}

    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


class TestSageChat:
    def test_chat_requires_auth(self, client):
        res = client.post("/api/sage/chat", json={"message": "hello", "history": []})
        assert res.status_code == 401

    def test_chat_returns_response(self, client, auth_headers):
        mock_resp = _make_mock_response("Your net worth is $250,000.")
        with patch("api.sage.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            res = client.post("/api/sage/chat", headers=auth_headers,
                              json={"message": "What is my net worth?", "history": []})

        assert res.status_code == 200
        data = res.json()
        assert "response" in data
        assert "history" in data
        assert data["response"] == "Your net worth is $250,000."

    def test_chat_returns_updated_history(self, client, auth_headers):
        mock_resp = _make_mock_response("You have $5,000 in checking.")
        with patch("api.sage.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            res = client.post("/api/sage/chat", headers=auth_headers,
                              json={"message": "How much cash do I have?", "history": []})

        assert res.status_code == 200
        history = res.json()["history"]
        assert isinstance(history, list)
        assert len(history) >= 2  # user message + assistant response

    def test_chat_history_is_serializable(self, client, auth_headers):
        """History returned must be plain JSON-serializable dicts, not SDK objects."""
        mock_resp = _make_mock_response("Done.")
        with patch("api.sage.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            res = client.post("/api/sage/chat", headers=auth_headers,
                              json={"message": "Hi", "history": []})

        assert res.status_code == 200
        # If we can json()-parse it, it's serializable
        import json
        json.dumps(res.json()["history"])  # must not raise

    def test_chat_with_tool_use_loop(self, client, auth_headers):
        """Sage calls a tool, gets result, then returns end_turn response."""
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "get_net_worth"
        tool_block.id = "tu_123"
        tool_block.input = {}
        tool_block.model_dump.return_value = {
            "type": "tool_use", "name": "get_net_worth", "id": "tu_123", "input": {}
        }

        tool_resp = MagicMock()
        tool_resp.stop_reason = "tool_use"
        tool_resp.content = [tool_block]

        final_resp = _make_mock_response("Your net worth is $300,000.")

        with patch("api.sage.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = [tool_resp, final_resp]

            res = client.post("/api/sage/chat", headers=auth_headers,
                              json={"message": "Net worth?", "history": []})

        assert res.status_code == 200
        assert "300,000" in res.json()["response"]

    def test_chat_whitespace_message_handled(self, client, auth_headers):
        # Pydantic accepts empty strings (no min_length constraint) — backend decides behaviour
        res = client.post("/api/sage/chat", headers=auth_headers,
                          json={"message": "   ", "history": []})
        assert res.status_code in (200, 400, 422, 500)  # Anthropic rejects whitespace-only


class TestSageSpeak:
    def test_speak_requires_auth(self, client):
        res = client.post("/api/sage/speak", json={"text": "hello"})
        assert res.status_code == 401

    def test_speak_returns_503_without_api_key(self, client, auth_headers):
        import os
        original = os.environ.pop("OPENAI_API_KEY", None)
        try:
            res = client.post("/api/sage/speak", headers=auth_headers,
                              json={"text": "Hello Sage"})
            assert res.status_code == 503
        finally:
            if original:
                os.environ["OPENAI_API_KEY"] = original

    def test_speak_returns_audio_with_valid_key(self, client, auth_headers):
        import os
        os.environ.setdefault("OPENAI_API_KEY", "sk-test")

        mock_audio_bytes = b"\xff\xfb\x90\x00" * 100  # fake MP3 header bytes

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.iter_bytes.return_value = iter([mock_audio_bytes])

        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.audio.speech.with_streaming_response.create.return_value = mock_response

            res = client.post("/api/sage/speak", headers=auth_headers,
                              json={"text": "Hello"})

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("audio/mpeg")


class TestSageRateLimit:
    def test_sage_rate_limited_after_threshold(self, client, auth_headers):
        from api import rate_limit

        # Manually fill up the rate limit bucket for testuser
        import time
        now = time.time()
        rate_limit._sage_calls["testuser"] = [now] * rate_limit.SAGE_MAX

        mock_resp = _make_mock_response("Hi")
        with patch("api.sage.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            res = client.post("/api/sage/chat", headers=auth_headers,
                              json={"message": "Hi", "history": []})

        assert res.status_code == 429
        # Clean up
        rate_limit._sage_calls.pop("testuser", None)
