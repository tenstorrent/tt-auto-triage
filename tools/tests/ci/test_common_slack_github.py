"""Tests for tools.ci.common.slack and tools.ci.common.github retry logic."""

from __future__ import annotations

import io
import json
from http.client import HTTPResponse
from unittest.mock import MagicMock, patch

import pytest

from tools.ci.common import slack, github


class TestSlackApiGetRetry:
    def _make_http_error(self, code: int, retry_after: str = "0"):
        import urllib.error
        headers = MagicMock()
        headers.get.return_value = retry_after
        return urllib.error.HTTPError(
            url="https://slack.com/api/test",
            code=code,
            msg="rate limited",
            hdrs=headers,
            fp=io.BytesIO(b""),
        )

    @patch("tools.ci.common.slack.urllib.request.urlopen")
    def test_retries_on_429(self, mock_urlopen):
        err = self._make_http_error(429, "0")
        ok_body = json.dumps({"ok": True, "data": 1}).encode()
        ok_resp = MagicMock()
        ok_resp.read.return_value = ok_body
        ok_resp.__enter__ = lambda s: s
        ok_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [err, ok_resp]

        result = slack.slack_api_get("tok", "conversations.history", {"channel": "C1"})
        assert result == {"ok": True, "data": 1}
        assert mock_urlopen.call_count == 2

    @patch("tools.ci.common.slack.urllib.request.urlopen")
    def test_caps_retry_after_at_max(self, mock_urlopen):
        err = self._make_http_error(429, "9999")
        ok_body = json.dumps({"ok": True}).encode()
        ok_resp = MagicMock()
        ok_resp.read.return_value = ok_body
        ok_resp.__enter__ = lambda s: s
        ok_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [err, ok_resp]

        with patch("tools.ci.common.slack.time.sleep") as mock_sleep:
            slack.slack_api_get("tok", "test", {})
            mock_sleep.assert_called_once_with(slack._MAX_RETRY_AFTER)

    @patch("tools.ci.common.slack.urllib.request.urlopen")
    def test_retries_on_ratelimited_error(self, mock_urlopen):
        limited_body = json.dumps({"ok": False, "error": "ratelimited"}).encode()
        limited_resp = MagicMock()
        limited_resp.read.return_value = limited_body
        limited_resp.__enter__ = lambda s: s
        limited_resp.__exit__ = MagicMock(return_value=False)

        ok_body = json.dumps({"ok": True}).encode()
        ok_resp = MagicMock()
        ok_resp.read.return_value = ok_body
        ok_resp.__enter__ = lambda s: s
        ok_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [limited_resp, ok_resp]

        with patch("tools.ci.common.slack.time.sleep"):
            result = slack.slack_api_get("tok", "test", {})
        assert result["ok"] is True

    @patch("tools.ci.common.slack.urllib.request.urlopen")
    def test_raises_after_max_retries(self, mock_urlopen):
        err = self._make_http_error(429, "0")
        mock_urlopen.side_effect = [err] * 6

        with patch("tools.ci.common.slack.time.sleep"):
            with pytest.raises(RuntimeError, match="HTTP error"):
                slack.slack_api_get("tok", "test", {}, max_retries=5)


class TestGithubApiGet:
    @patch("tools.ci.common.github.urllib.request.urlopen")
    def test_returns_list_for_collection(self, mock_urlopen):
        body = json.dumps([{"id": 1}, {"id": 2}]).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = github.github_api_get("tok", "/repos/org/repo/issues")
        assert isinstance(result, list)
        assert len(result) == 2

    @patch("tools.ci.common.github.urllib.request.urlopen")
    def test_returns_dict_for_object(self, mock_urlopen):
        body = json.dumps({"login": "user1"}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = github.github_api_get("tok", "/users/user1")
        assert isinstance(result, dict)
        assert result["login"] == "user1"
