from arbfinder.trends.base import TrendSignal
from arbfinder.trends.normalize import lexicon_category, make_rules_classifier


def test_lexicon_maps_known_categories():
    assert lexicon_category("viral teeth whitening strips") == "teeth whitening strips"
    assert lexicon_category("Ninja Air Fryer AF100UK") == "air fryer"
    assert lexicon_category("random political hashtag") is None


def test_classifier_drops_unmapped_social_but_keeps_amazon_and_manual():
    classify = make_rules_classifier(frozenset({"amazon_movers", "manual"}))
    # social/unknown source with no lexicon hit -> dropped
    assert classify(TrendSignal(source="twitter", original_title="#SomeTrend")) is None
    # amazon real product with no lexicon hit -> kept via cleaned title
    kept = classify(TrendSignal(source="amazon_movers", original_title="Oral-B iO9 Toothbrush"))
    assert kept and "toothbrush" in kept
    # preset category always honoured
    assert classify(TrendSignal(source="seasonal", original_title="x",
                                product_category="bbq")) == "bbq"


def test_manual_term_maps_through_lexicon():
    classify = make_rules_classifier(frozenset({"manual"}))
    assert classify(TrendSignal(source="manual", original_title="teeth whitening strips")) \
        == "teeth whitening strips"
