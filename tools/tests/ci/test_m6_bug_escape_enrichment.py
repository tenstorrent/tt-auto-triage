from __future__ import annotations

from tools.ci.m6_bug_escape_enrichment import classify_escape_type, infer_layer_from_text


def test_classify_escape_type_detects_lower_to_higher_escape() -> None:
    escape_type, confidence = classify_escape_type(failure_layer="models", fix_layer="llk")
    assert escape_type == "layer_escape_lower_to_higher"
    assert confidence == "high"


def test_infer_layer_from_text_prefers_specific_signals() -> None:
    assert infer_layer_from_text("TT_FATAL in tt_metal/llrt/something.cpp") == "metalium"
    assert infer_layer_from_text("flaky model pipeline error") == "models"
    assert infer_layer_from_text("something unknown") == "unknown"
