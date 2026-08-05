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
    # Stated rather than inherited: whether a 404 may be cached now depends on
    # this, so leaving it to the environment would let these tests pass for the
    # wrong reason. RUN_URL is inside this scope.
    monkeypatch.setenv("GITHUB_REPOSITORY", "tenstorrent/tt-metal")
    github_api_utils.reset_read_counters()


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


class TestA404OutsideTheTokenScope:
    """GitHub returns 404, not 403, for what a token cannot see.

    The workflow token only reaches its own repository, so a 404 from elsewhere
    is as likely to be that boundary as a deleted run. Remembering it would
    poison the cache through permissions instead of a bad token.
    """

    OTHER_REPO_URL = "https://github.com/tenstorrent/tt-forge/actions/runs/999/job/888"

    def test_it_is_not_cached_as_absent(self, monkeypatch):
        respond(monkeypatch, FakeResponse(404))

        assert github_api_utils.get_commit_hash_from_github(self.OTHER_REPO_URL, TOKEN) is None
        assert github_api_utils._commit_hash_cache == {}

    def test_it_is_tried_again_rather_than_answered_from_cache(self, monkeypatch):
        calls = respond(monkeypatch, FakeResponse(404))

        github_api_utils.get_commit_hash_from_github(self.OTHER_REPO_URL, TOKEN)
        github_api_utils.get_commit_hash_from_github(self.OTHER_REPO_URL, TOKEN)

        assert len(calls) == 2

    def test_a_job_name_follows_the_same_rule(self, monkeypatch):
        respond(monkeypatch, FakeResponse(404))

        assert github_api_utils.get_job_name_from_github(self.OTHER_REPO_URL, TOKEN) is None
        assert github_api_utils._job_name_cache == {}

    def test_it_counts_as_unverifiable_not_unreachable(self, monkeypatch):
        """Retrying cannot fix a permissions boundary, so it must not fail the run."""
        respond(monkeypatch, FakeResponse(404))

        github_api_utils.get_commit_hash_from_github(self.OTHER_REPO_URL, TOKEN)

        assert github_api_utils.read_counters() == {"unreachable": 0, "unverifiable": 1}

    def test_the_same_run_id_in_scope_is_still_cached(self, monkeypatch):
        """The scope check must not disable caching for the repository we can see."""
        respond(monkeypatch, FakeResponse(404))

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert list(github_api_utils._commit_hash_cache.values()) == [ABSENT]

    def test_without_a_declared_scope_the_api_is_believed(self, monkeypatch):
        """A personal access token may reach anywhere, so 404 means gone."""
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        respond(monkeypatch, FakeResponse(404))

        github_api_utils.get_commit_hash_from_github(self.OTHER_REPO_URL, TOKEN)

        assert list(github_api_utils._commit_hash_cache.values()) == [ABSENT]

    def test_scope_matching_ignores_case(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "Tenstorrent/TT-Metal")
        respond(monkeypatch, FakeResponse(404))

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert list(github_api_utils._commit_hash_cache.values()) == [ABSENT]


class TestReadCounters:
    def test_a_rejected_token_counts_as_unreachable(self, monkeypatch):
        respond(monkeypatch, FakeResponse(401))

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert github_api_utils.read_counters() == {"unreachable": 1, "unverifiable": 0}

    def test_a_settled_answer_counts_as_neither(self, monkeypatch):
        respond(monkeypatch, FakeResponse(404))

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert github_api_utils.read_counters() == {"unreachable": 0, "unverifiable": 0}

    def test_a_network_fault_counts_as_unreachable(self, monkeypatch):
        def explode(url, **kwargs):
            raise github_api_utils.requests.RequestException("connection reset")

        monkeypatch.setattr(github_api_utils.requests, "get", explode)

        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        assert github_api_utils.read_counters()["unreachable"] == 1

    def test_resetting_forgets_previous_counts(self, monkeypatch):
        respond(monkeypatch, FakeResponse(500))
        github_api_utils.get_commit_hash_from_github(RUN_URL, TOKEN)

        github_api_utils.reset_read_counters()

        assert github_api_utils.read_counters() == {"unreachable": 0, "unverifiable": 0}


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
