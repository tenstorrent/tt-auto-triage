"""Tests for loading, saving, and pruning the cluster state."""

import json
import time

import pytest

import cluster_state

DAY = 86400
NOW = 1_767_000_000.0


def make_entry(centroid="TT_THROW @ device.cpp: init failed", runs=None):
    """Build a cluster with the given (url, age_in_days) runs."""
    runs = runs or [("https://github.com/o/r/actions/runs/1/job/1", 1)]
    entry = {
        "centroid_error": centroid,
        "failing_runs": [url for url, _ in runs],
        "run_metadata": {
            url: {
                "job_name": "build",
                "workflow_name": "All post-commit tests",
                "is_nd": False,
                "commit_hash": "abc1234",
                "error_message": centroid,
                "timestamp": "January 9th, 8:59am, 58.95 seconds",
                "unix_timestamp": NOW - age * DAY,
            }
            for url, age in runs
        },
    }
    cluster_state.set_centroid_metadata(entry, entry["failing_runs"][0])
    return entry


class TestLoadAndSave:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "cluster_state.json")
        entries = [make_entry()]

        cluster_state.save_cluster_state(entries, path)
        loaded = cluster_state.load_cluster_state(path)

        assert loaded == entries

    def test_missing_file_starts_empty(self, tmp_path):
        assert cluster_state.load_cluster_state(str(tmp_path / "absent.json")) == []

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "cluster_state.json"
        path.write_text("{not json", encoding="utf-8")

        assert cluster_state.load_cluster_state(str(path)) == []

    def test_wrong_shape_starts_empty(self, tmp_path):
        path = tmp_path / "cluster_state.json"
        path.write_text(json.dumps({"clusters": []}), encoding="utf-8")

        assert cluster_state.load_cluster_state(str(path)) == []

    def test_save_creates_missing_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "deeper" / "cluster_state.json")

        cluster_state.save_cluster_state([make_entry()], path)

        with open(path, encoding="utf-8") as f:
            assert len(json.load(f)) == 1

    def test_unicode_survives_the_round_trip(self, tmp_path):
        path = str(tmp_path / "cluster_state.json")
        entries = [make_entry(centroid="assertion failed: résumé ✗ timeout")]

        cluster_state.save_cluster_state(entries, path)

        assert cluster_state.load_cluster_state(path)[0]["centroid_error"] == "assertion failed: résumé ✗ timeout"


class TestPruning:
    def test_keeps_runs_inside_the_window(self):
        entries = [make_entry(runs=[("https://x/job/1", 5), ("https://x/job/2", 29)])]

        surviving, removed, dropped = cluster_state.prune_old_runs(entries, now_unix=NOW)

        assert removed == 0 and dropped == 0
        assert surviving[0]["failing_runs"] == ["https://x/job/1", "https://x/job/2"]

    def test_drops_runs_past_the_window(self):
        entries = [make_entry(runs=[("https://x/job/1", 3), ("https://x/job/2", 45)])]

        surviving, removed, dropped = cluster_state.prune_old_runs(entries, now_unix=NOW)

        assert removed == 1 and dropped == 0
        assert surviving[0]["failing_runs"] == ["https://x/job/1"]
        assert "https://x/job/2" not in surviving[0]["run_metadata"]

    def test_drops_clusters_with_nothing_left(self):
        entries = [make_entry(runs=[("https://x/job/1", 40), ("https://x/job/2", 90)])]

        surviving, removed, dropped = cluster_state.prune_old_runs(entries, now_unix=NOW)

        assert surviving == []
        assert removed == 2 and dropped == 1

    def test_keeps_runs_whose_age_is_unknown(self):
        entries = [make_entry(runs=[("https://x/job/1", 1)])]
        entries[0]["run_metadata"]["https://x/job/1"] = {"job_name": "build"}

        surviving, removed, _ = cluster_state.prune_old_runs(entries, now_unix=NOW)

        assert removed == 0
        assert surviving[0]["failing_runs"] == ["https://x/job/1"]

    def test_boundary_run_is_kept(self):
        entries = [make_entry(runs=[("https://x/job/1", 30)])]

        surviving, removed, _ = cluster_state.prune_old_runs(entries, max_age_days=30, now_unix=NOW)

        assert removed == 0 and len(surviving) == 1

    def test_defaults_to_now_when_no_clock_is_given(self):
        entries = [make_entry(runs=[("https://x/job/1", 0)])]
        entries[0]["run_metadata"]["https://x/job/1"]["unix_timestamp"] = time.time() - DAY

        surviving, removed, _ = cluster_state.prune_old_runs(entries)

        assert removed == 0 and len(surviving) == 1


class TestCentroidMetadata:
    def test_new_cluster_points_at_its_only_run(self):
        entry = make_entry(runs=[("https://x/job/1", 2)])

        assert entry["centroid_metadata"]["url"] == "https://x/job/1"
        assert entry["centroid_metadata"]["commit_hash"] == "abc1234"

    def test_surviving_centroid_is_left_alone(self):
        entry = make_entry(runs=[("https://x/job/1", 2), ("https://x/job/2", 1)])

        cluster_state.refresh_centroid_metadata(entry)

        assert entry["centroid_metadata"]["url"] == "https://x/job/1"

    def test_pruned_centroid_repoints_to_the_oldest_survivor(self):
        entry = make_entry(runs=[("https://x/job/old", 45), ("https://x/job/mid", 10), ("https://x/job/new", 1)])
        assert entry["centroid_metadata"]["url"] == "https://x/job/old"

        surviving, _, _ = cluster_state.prune_old_runs([entry], now_unix=NOW)

        assert surviving[0]["centroid_metadata"]["url"] == "https://x/job/mid"

    def test_centroid_text_is_frozen_when_the_centroid_run_is_pruned(self):
        entry = make_entry(centroid="original centroid", runs=[("https://x/job/old", 45), ("https://x/job/new", 1)])

        surviving, _, _ = cluster_state.prune_old_runs([entry], now_unix=NOW)

        assert surviving[0]["centroid_error"] == "original centroid"

    def test_commit_hash_fetched_later_reaches_centroid_metadata(self):
        entry = make_entry(runs=[("https://x/job/1", 2)])
        entry["centroid_metadata"]["commit_hash"] = ""
        entry["run_metadata"]["https://x/job/1"]["commit_hash"] = "def5678"

        cluster_state.refresh_centroid_metadata(entry)

        assert entry["centroid_metadata"]["commit_hash"] == "def5678"


class TestHelpers:
    def test_all_urls_spans_every_cluster(self):
        entries = [
            make_entry(centroid="a", runs=[("https://x/job/1", 1)]),
            make_entry(centroid="b", runs=[("https://x/job/2", 1), ("https://x/job/3", 1)]),
        ]

        assert cluster_state.all_urls(entries) == {"https://x/job/1", "https://x/job/2", "https://x/job/3"}

    def test_newest_run_unix_picks_the_latest(self):
        entry = make_entry(runs=[("https://x/job/1", 10), ("https://x/job/2", 2)])

        assert cluster_state.newest_run_unix(entry) == pytest.approx(NOW - 2 * DAY)

    def test_oldest_run_url_picks_the_earliest(self):
        entry = make_entry(runs=[("https://x/job/1", 10), ("https://x/job/2", 2)])

        assert cluster_state.oldest_run_url(entry) == "https://x/job/1"
