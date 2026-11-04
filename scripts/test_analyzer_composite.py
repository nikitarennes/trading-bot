"""Tests for the public-analyzer-only stock score composer."""
from pathlib import Path
import sys

import pytest

pytest.importorskip("pandas_ta")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def analyzer(score, confidence=0.8, **extra):
    return {"score": score, "confidence": confidence, **extra}


def test_all_analyzers_use_proportionally_scaled_baseline_weights():
    result = main.compose_score(
        analyzer(0.8), analyzer(0.6),
        analyzer(0.4, num_articles=3),
    )

    assert result["score"] == 65.0
    assert result["recommendation"] == "Buy"
    assert result["components"]["technical"]["weight"] == 0.4375
    assert result["components"]["trend"]["weight"] == 0.375
    assert result["components"]["sentiment"]["weight"] == 0.1875
    assert sum(c["weight"] for c in result["components"].values()) == 1


def test_unavailable_sentiment_is_omitted_and_remaining_weights_renormalize():
    result = main.compose_score(
        analyzer(0.8), analyzer(0.6),
        analyzer(0.5, confidence=0, num_articles=0),
    )

    assert result["score"] == pytest.approx(70.8)
    assert result["components"]["technical"]["weight"] == 0.5385
    assert result["components"]["trend"]["weight"] == 0.4615
    assert "sentiment" not in result["components"]


@pytest.mark.parametrize(
    ("score", "grade", "recommendation"),
    [
        (80, "A", "Strong Buy"),
        (70, "B+", "Buy"),
        (60, "B", "Buy"),
        (50, "C+", "Hold"),
        (40, "C", "Reduce"),
        (30, "D", "Reduce"),
        (29.9, "F", "Avoid"),
    ],
)
def test_documented_rating_thresholds(score, grade, recommendation):
    result = main.compose_score(analyzer(score / 100), None)

    assert result["score"] == score
    assert result["grade"] == grade
    assert result["recommendation"] == recommendation


def test_invalid_analyzer_scores_are_rejected():
    with pytest.raises(ValueError, match="between 0 and 1"):
        main.compose_score(analyzer(1.01), analyzer(0.5))


def test_legacy_alpha_argument_does_not_change_score(monkeypatch):
    technical = analyzer(0.8, components={"trend": {}})
    trend = analyzer(0.6, components={"regime": {"signals": {"regime": "bull"}}})
    monkeypatch.setattr(main.technical_analyzer, "analyze", lambda *a, **k: technical)
    monkeypatch.setattr(main.trend_analyzer, "analyze", lambda *a, **k: trend)
    monkeypatch.setattr(
        main.sentiment_analyzer, "analyze_sentiment",
        lambda *a, **k: analyzer(0.4, num_articles=1),
    )

    no_alpha, _ = main.analyze_stock("TEST", None, None, news_data=[{"title": "headline"}])
    legacy_alpha, _ = main.analyze_stock(
        "TEST", None, None, news_data=[{"title": "headline"}], alpha_score=1
    )

    assert no_alpha["score"] == legacy_alpha["score"] == 65.0
    assert no_alpha["score_version"] == "analyzer-composite-v1"
