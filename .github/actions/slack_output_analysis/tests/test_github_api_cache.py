"""Tests for what the API caches are allowed to remember.

These caches persist between runs in the state artifact, so a remembered
failure is a permanent one. A single expired token once poisoned 141 entries
and silently dropped 19 errors on the following run.
"""

import json

import pytest

import github_api_utils
from github_api_utils import ABSENT

RUN_URL = "https://github.com/tenstorrent/tt-metal/actions/runs/12345/job/67890"
SHA = "a" * 40
TOKEN = "token"


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def empty_caches(monkeypatch):
    monkeypatch.setattr(github_api_utils, "_commit_hash_cache", {})
    monkeypatch.setattr(github_api_utils, "_job_name_cache", {})
    monkeypatch.setattr(github_api_utils, "_commit_cache_loaded", True)
    monkeypatch.setattr(github_api_utils, "_job_cache_loaded", True)


def respond(monkeypatch, *responses):
    """Queue responses and count how many requests were actually made."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(github_api_utils.requests, "get", fake_get)
    return calls


class TestTransientFailuresAreRetried:
    def test_an_unauthorized_response_is_not_remembered(self, monkeypatch):
        calls = respond(monkeypatch, FakeResponse(401))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) is None
        assert github_api_utils._commit_hash_cache == {}, "a rejected token must not be cached as a miss"

    def test_a_retried_lookup_succeeds_after_the_token_is_fixed(self, monkeypatch):
        respond(monkeypatch, FakeResponse(401))
        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        respond(monkeypatch, FakeResponse(200, {"head_sha": SHA}))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) == SHA

    def test_rate_limiting_is_not_remembered(self, monkeypatch):
        respond(monkeypatch, FakeResponse(429))

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert github_api_utils._commit_hash_cache == {}

    def test_a_server_error_is_not_remembered(self, monkeypatch):
        respond(monkeypatch, FakeResponse(500))

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert github_api_utils._commit_hash_cache == {}

    def test_a_network_fault_is_not_remembered(self, monkeypatch):
        def explode(url, **kwargs):
            raise github_api_utils.requests.RequestException("connection reset")

        monkeypatch.setattr(github_api_utils.requests, "get", explode)

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) is None
        assert github_api_utils._commit_hash_cache == {}


class TestDefinitiveAnswersAreRemembered:
    def test_a_found_hash_is_cached_and_not_refetched(self, monkeypatch):
        calls = respond(monkeypatch, FakeResponse(200, {"head_sha": SHA}))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) == SHA
        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) == SHA
        assert len(calls) == 1

    def test_a_deleted_run_is_cached_as_absent(self, monkeypatch):
        """A 404 is the API saying the run is gone, which will not change."""
        calls = respond(monkeypatch, FakeResponse(404))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) is None
        assert list(github_api_utils._commit_hash_cache.values()) == [ABSENT]

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) is None
        assert len(calls) == 1, "a confirmed absence should not be looked up again"

    def test_a_response_missing_the_field_is_cached_as_absent(self, monkeypatch):
        respond(monkeypatch, FakeResponse(200, {}))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) is None
        assert list(github_api_utils._commit_hash_cache.values()) == [ABSENT]

    def test_a_truncated_hash_is_rejected(self, monkeypatch):
        respond(monkeypatch, FakeResponse(200, {"head_sha": "abc123"}))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) is None


class TestJobNamesFollowTheSameRules:
    def test_an_unauthorized_response_is_not_remembered(self, monkeypatch):
        respond(monkeypatch, FakeResponse(401))

        assert github_api_utils.get_job_name_from_github(RUN_URL, TOKEN) is None
        assert github_api_utils._job_name_cache == {}

    def test_a_found_name_is_cached(self, monkeypatch):
        calls = respond(monkeypatch, FakeResponse(200, {"name": "build / device-tests"}))

        assert github_api_utils.get_job_name_from_github(RUN_URL, TOKEN) == "build / device-tests"
        assert github_api_utils.get_job_name_from_github(RUN_URL, TOKEN) == "build / device-tests"
        assert len(calls) == 1

    def test_a_deleted_job_is_cached_as_absent(self, monkeypatch):
        respond(monkeypatch, FakeResponse(404))

        assert github_api_utils.get_job_name_from_github(RUN_URL, TOKEN) is None
        assert list(github_api_utils._job_name_cache.values()) == [ABSENT]


class TestHealingAPoisonedCacheOnDisk:
    def test_legacy_null_misses_are_discarded(self):
        """Nulls predate ABSENT and could have come from any failure."""
        loaded = {"repo/1": None, "repo/2": SHA, "repo/3": ABSENT}

        kept = github_api_utils.drop_unexplained_misses(loaded, "commit hash")

        assert kept == {"repo/2": SHA, "repo/3": ABSENT}

    def test_a_poisoned_cache_file_is_healed_on_load(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "commit_hash_cache.json"
        cache_file.write_text(json.dumps({"repo/1": None, "repo/2": SHA}), encoding="utf-8")
        monkeypatch.setattr(github_api_utils, "COMMIT_HASH_CACHE_FILE", str(cache_file))
        monkeypatch.setattr(github_api_utils, "_commit_cache_loaded", False)
        monkeypatch.setattr(github_api_utils, "_commit_hash_cache", {})

        github_api_utils.load_commit_hash_cache()

        assert github_api_utils._commit_hash_cache == {"repo/2": SHA}

    def test_a_healed_entry_is_fetched_again(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "commit_hash_cache.json"
        cache_file.write_text(json.dumps({"tenstorrent/tt-metal/12345": None}), encoding="utf-8")
        monkeypatch.setattr(github_api_utils, "COMMIT_HASH_CACHE_FILE", str(cache_file))
        monkeypatch.setattr(github_api_utils, "_commit_cache_loaded", False)
        monkeypatch.setattr(github_api_utils, "_commit_hash_cache", {})
        github_api_utils.load_commit_hash_cache()
        respond(monkeypatch, FakeResponse(200, {"head_sha": SHA}))

        assert github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN) == SHA


class TestCacheStatistics:
    def test_absent_markers_count_as_misses_not_hits(self, monkeypatch):
        monkeypatch.setattr(github_api_utils, "_commit_hash_cache", {"a": SHA, "b": ABSENT})

        stats = github_api_utils.get_commit_hash_cache_stats()

        assert stats == {"total_entries": 2, "found": 1, "not_found": 1}
