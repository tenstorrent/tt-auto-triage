"""Tests for folding new errors into the cluster state.

Similarity is stubbed throughout: what matters here is what happens to the
state once a match has or has not been found, not how the match was scored.
"""

import json
import time
from datetime import datetime

import pytest

import sync_new_errors

NOW = 1_767_000_000.0
TOKEN = "test-token"


@pytest.fixture(autouse=True)
def stub_github(monkeypatch):
    """Commit hashes come back from the API; keep that off the network."""
    monkeypatch.setattr(sync_new_errors, "get_commit_hash_from_github", lambda url, token: "abc1234")


@pytest.fixture
def no_match(monkeypatch):
    monkeypatch.setattr(
        sync_new_errors,
        "find_best_matching_centroid",
        lambda *args, **kwargs: (None, {"rapidfuzz": 0.0, "semantic": 0.0}),
    )


@pytest.fixture
def always_match(monkeypatch):
    """Match the first cluster, or nothing at all when there are none yet."""

    def match(error_message, centroids, **kwargs):
        if not centroids:
            return None, {"rapidfuzz": 0.0, "semantic": 0.0}
        return 0, {"rapidfuzz": 99.0, "semantic": 99.0}

    monkeypatch.setattr(sync_new_errors, "find_best_matching_centroid", match)


def error_entry(message="TT_THROW @ device.cpp: init failed", url="https://x/job/1", unix=NOW):
    return [
        message,
        url,
        "January 9th, 8:59am, 58.95 seconds",
        "build-artifact",
        "All post-commit tests",
        False,
        "https://x/runs/999",
        unix,
    ]


class TestNewClusters:
    def test_unmatched_error_starts_a_cluster(self, no_match):
        clusters = []

        changed, created = sync_new_errors.process_new_error(error_entry(), clusters, {}, TOKEN)

        assert (changed, created) == (True, True)
        assert len(clusters) == 1
        assert clusters[0]["centroid_error"] == "TT_THROW @ device.cpp: init failed"
        assert clusters[0]["failing_runs"] == ["https://x/job/1"]

    def test_new_cluster_gets_centroid_metadata(self, no_match):
        clusters = []

        sync_new_errors.process_new_error(error_entry(), clusters, {}, TOKEN)

        assert clusters[0]["centroid_metadata"] == {
            "url": "https://x/job/1",
            "commit_hash": "abc1234",
            "timestamp": "January 9th, 8:59am, 58.95 seconds",
            "unix_timestamp": NOW,
        }

    def test_run_metadata_carries_the_unix_timestamp(self, no_match):
        clusters = []

        sync_new_errors.process_new_error(error_entry(), clusters, {}, TOKEN)

        assert clusters[0]["run_metadata"]["https://x/job/1"]["unix_timestamp"] == NOW


class TestExistingClusters:
    def test_matched_error_joins_the_cluster(self, always_match):
        clusters = []
        sync_new_errors.process_new_error(error_entry(url="https://x/job/1"), clusters, {}, TOKEN)

        changed, created = sync_new_errors.process_new_error(
            error_entry(message="TT_THROW @ device.cpp: init failed (retry)", url="https://x/job/2"),
            clusters,
            {},
            TOKEN,
        )

        assert (changed, created) == (True, False)
        assert len(clusters) == 1
        assert clusters[0]["failing_runs"] == ["https://x/job/1", "https://x/job/2"]

    def test_centroid_text_does_not_drift(self, always_match):
        clusters = []
        sync_new_errors.process_new_error(error_entry(message="original"), clusters, {}, TOKEN)

        sync_new_errors.process_new_error(error_entry(message="different text", url="https://x/job/2"), clusters, {}, TOKEN)

        assert clusters[0]["centroid_error"] == "original"

    def test_repeated_url_is_ignored(self, always_match):
        clusters = []
        sync_new_errors.process_new_error(error_entry(), clusters, {}, TOKEN)

        changed, created = sync_new_errors.process_new_error(error_entry(), clusters, {}, TOKEN)

        assert (changed, created) == (False, False)
        assert clusters[0]["failing_runs"] == ["https://x/job/1"]


class TestValidation:
    def test_error_without_a_job_name_is_skipped(self, no_match):
        entry = error_entry()
        entry[3] = ""
        clusters = []

        changed, created = sync_new_errors.process_new_error(entry, clusters, {}, TOKEN)

        assert (changed, created) == (False, False)
        assert clusters == []

    def test_error_without_a_commit_hash_is_skipped(self, no_match, monkeypatch):
        monkeypatch.setattr(sync_new_errors, "get_commit_hash_from_github", lambda url, token: None)
        clusters = []

        changed, _ = sync_new_errors.process_new_error(error_entry(), clusters, {}, TOKEN)

        assert changed is False

    def test_placeholder_timestamp_is_rejected(self, no_match):
        entry = error_entry()
        entry[2] = "Link"
        clusters = []

        changed, _ = sync_new_errors.process_new_error(entry, clusters, {}, TOKEN)

        assert changed is False


class TestExistingStateCleanup:
    def build_cluster(self, urls, commit_hash="abc1234", job_name="build"):
        return {
            "centroid_error": "boom",
            "failing_runs": list(urls),
            "run_metadata": {
                url: {
                    "job_name": job_name,
                    "workflow_name": "wf",
                    "is_nd": False,
                    "commit_hash": commit_hash,
                    "error_message": "boom",
                    "timestamp": "January 9th, 8:59am, 58.95 seconds",
                    "unix_timestamp": NOW,
                }
                for url in urls
            },
        }

    def test_url_in_two_clusters_survives_only_once(self):
        clusters = [self.build_cluster(["https://x/job/1"]), self.build_cluster(["https://x/job/1", "https://x/job/2"])]

        surviving, invalid, duplicate, dropped = sync_new_errors.validate_existing_clusters(clusters, {}, TOKEN)

        assert duplicate == 1
        assert surviving[0]["failing_runs"] == ["https://x/job/1"]
        assert surviving[1]["failing_runs"] == ["https://x/job/2"]

    def test_runs_missing_a_job_name_are_removed(self):
        clusters = [self.build_cluster(["https://x/job/1"], job_name="")]

        surviving, invalid, _, dropped = sync_new_errors.validate_existing_clusters(clusters, {}, "")

        assert invalid == 1 and dropped == 1
        assert surviving == []

    def test_valid_state_passes_through_untouched(self):
        clusters = [self.build_cluster(["https://x/job/1", "https://x/job/2"])]

        surviving, invalid, duplicate, dropped = sync_new_errors.validate_existing_clusters(clusters, {}, TOKEN)

        assert (invalid, duplicate, dropped) == (0, 0, 0)
        assert surviving[0]["failing_runs"] == ["https://x/job/1", "https://x/job/2"]
        assert surviving[0]["centroid_metadata"]["url"] == "https://x/job/1"


class TestSelectingNewErrors:
    def test_untracked_recent_error_is_selected(self):
        selected, counts = sync_new_errors.select_new_errors([error_entry()], set(), now_unix=NOW)

        assert len(selected) == 1
        assert counts == {"no_url": 0, "already_tracked": 0, "too_old": 0, "outside_date_range": 0}

    def test_tracked_url_is_skipped(self):
        selected, counts = sync_new_errors.select_new_errors(
            [error_entry(url="https://x/job/1")], {"https://x/job/1"}, now_unix=NOW
        )

        assert selected == [] and counts["already_tracked"] == 1

    def test_error_without_a_url_is_skipped(self):
        entry = error_entry()
        entry[1] = None

        selected, counts = sync_new_errors.select_new_errors([entry], set(), now_unix=NOW)

        assert selected == [] and counts["no_url"] == 1

    def test_error_past_the_retention_window_is_skipped(self):
        """Pruning has to be final, or a wide Slack window would undo it."""
        old = error_entry(unix=NOW - 45 * 86400)

        selected, counts = sync_new_errors.select_new_errors([old], set(), now_unix=NOW)

        assert selected == [] and counts["too_old"] == 1

    def test_error_just_inside_the_window_is_kept(self):
        recent = error_entry(unix=NOW - 29 * 86400)

        selected, counts = sync_new_errors.select_new_errors([recent], set(), now_unix=NOW)

        assert len(selected) == 1 and counts["too_old"] == 0

    def test_date_range_start_excludes_earlier_errors(self):
        entry = error_entry(unix=NOW - 10 * 86400)
        start = datetime.fromtimestamp(NOW - 5 * 86400)

        selected, counts = sync_new_errors.select_new_errors([entry], set(), date_range_start=start, now_unix=NOW)

        assert selected == [] and counts["outside_date_range"] == 1

    def test_date_range_end_excludes_later_errors(self):
        entry = error_entry(unix=NOW - 1 * 86400)
        end = datetime.fromtimestamp(NOW - 5 * 86400)

        selected, counts = sync_new_errors.select_new_errors([entry], set(), date_range_end=end, now_unix=NOW)

        assert selected == [] and counts["outside_date_range"] == 1

    def test_undatable_error_is_kept(self):
        entry = error_entry()
        entry[2] = ""
        entry[7] = None

        selected, _ = sync_new_errors.select_new_errors([entry], set(), now_unix=NOW)

        assert len(selected) == 1


class TestRunMetadata:
    def test_absent_trailing_fields_get_defaults(self):
        metadata = sync_new_errors.build_run_metadata(["message only"], None)

        assert metadata == {
            "job_name": "",
            "workflow_name": "",
            "is_nd": False,
            "commit_hash": "",
            "error_message": "message only",
            "timestamp": "",
            "unix_timestamp": None,
        }


class TestRefusalToRunBlind:
    """Guards against reporting success while silently storing nothing.

    A rejected token makes every commit hash lookup fail, so every error is
    dropped for missing metadata. Before these guards the run stored an empty
    state and exited zero, which looked identical to a quiet day.
    """

    @pytest.fixture
    def otherwise_healthy_run(self, monkeypatch, tmp_path, no_match):
        """Everything downstream works, so only the token guard can stop the run.

        Without this the run would exit on a missing all_errors.json and the
        tests would pass whether or not the guard exists.
        """
        import github_api_utils

        errors_file = tmp_path / "all_errors.json"
        errors_file.write_text(
            json.dumps([error_entry(url="https://x/job/1", unix=time.time() - 3600)]),
            encoding="utf-8",
        )
        monkeypatch.setattr(sync_new_errors, "ALL_ERRORS_FILE", str(errors_file))
        monkeypatch.setattr(sync_new_errors, "get_commit_hash_from_github", lambda url, token: "abc1234")
        monkeypatch.setattr(sync_new_errors, "load_cluster_state", lambda *a, **k: [])
        monkeypatch.setattr(sync_new_errors, "save_cluster_state", lambda *a, **k: None)
        monkeypatch.setattr(sync_new_errors, "log_rate_limit_status", lambda *a, **k: None)
        monkeypatch.setattr(github_api_utils, "load_commit_hash_cache", lambda *a, **k: None)
        monkeypatch.setattr(github_api_utils, "save_commit_hash_cache", lambda *a, **k: None)
        monkeypatch.setattr(github_api_utils, "get_commit_hash_cache_stats",
                            lambda: {"total_entries": 0, "found": 0, "not_found": 0})

    def test_the_guard_does_not_fire_on_a_healthy_run(self, monkeypatch, otherwise_healthy_run):
        """Establishes that the two tests below are actually testing the guard."""
        import github_api_utils

        monkeypatch.setattr(sync_new_errors, "load_secrets", lambda: {"GITHUB_TOKEN": "valid"})
        monkeypatch.setattr(github_api_utils, "github_token_is_valid", lambda token: True)

        sync_new_errors.main()

    def test_a_rejected_token_stops_the_run(self, monkeypatch, otherwise_healthy_run):
        import github_api_utils

        monkeypatch.setattr(sync_new_errors, "load_secrets", lambda: {"GITHUB_TOKEN": "expired"})
        monkeypatch.setattr(github_api_utils, "github_token_is_valid", lambda token: False)

        with pytest.raises(SystemExit) as exit_info:
            sync_new_errors.main()

        assert exit_info.value.code == 1

    def test_a_missing_token_stops_the_run(self, monkeypatch, otherwise_healthy_run):
        monkeypatch.setattr(sync_new_errors, "load_secrets", lambda: {"GITHUB_TOKEN": ""})

        with pytest.raises(SystemExit) as exit_info:
            sync_new_errors.main()

        assert exit_info.value.code == 1

    def test_dropping_every_new_error_stops_the_run(self, monkeypatch, tmp_path, no_match):
        """Reached when the token is accepted but the lookups still fail."""
        import github_api_utils

        recent = time.time() - 3600
        errors_file = tmp_path / "all_errors.json"
        errors_file.write_text(json.dumps([error_entry(url="https://x/job/1", unix=recent),
                                           error_entry(url="https://x/job/2", unix=recent)]), encoding="utf-8")
        state_file = tmp_path / "cluster_state.json"

        monkeypatch.setattr(sync_new_errors, "load_secrets", lambda: {"GITHUB_TOKEN": "valid"})
        monkeypatch.setattr(github_api_utils, "github_token_is_valid", lambda token: True)
        monkeypatch.setattr(github_api_utils, "load_commit_hash_cache", lambda *a, **k: None)
        monkeypatch.setattr(sync_new_errors, "log_rate_limit_status", lambda *a, **k: None)
        monkeypatch.setattr(sync_new_errors, "ALL_ERRORS_FILE", str(errors_file))
        monkeypatch.setattr(sync_new_errors, "get_commit_hash_from_github", lambda url, token: None)
        monkeypatch.setattr(sync_new_errors, "load_cluster_state", lambda *a, **k: [])
        monkeypatch.setattr(sync_new_errors, "save_cluster_state",
                            lambda *a, **k: state_file.write_text("saved", encoding="utf-8"))

        with pytest.raises(SystemExit) as exit_info:
            sync_new_errors.main()

        assert exit_info.value.code == 1
        assert not state_file.exists(), "the empty state must not overwrite what is already stored"
