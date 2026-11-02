from stock_assistant.modules.ingestion import QualityInputs, quality_score
from stock_assistant.platform.config import get_quality_weights


def test_perfect_source_scores_one() -> None:
    assert (
        quality_score(
            QualityInputs(
                authority=1,
                relevance=1,
                freshness=1,
                corroboration=1,
                completeness=1,
            ),
            weights=get_quality_weights(),
        )
        == 1.0
    )


def test_conflict_penalty_is_bounded() -> None:
    score = quality_score(
        QualityInputs(
            authority=0.15,
            relevance=0.2,
            freshness=0.2,
            corroboration=0,
            completeness=0.2,
            conflict_penalty=1,
        ),
        weights=get_quality_weights(),
    )
    assert score == 0.0


def test_scoring_weights_come_from_versioned_config() -> None:
    weights = get_quality_weights()
    assert weights.authority == 0.35
    assert weights.conflict_penalty == -0.20
