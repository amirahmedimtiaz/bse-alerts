from scripts.merge_announcement_state import merge_states


def test_merge_states_unions_company_ids_and_preserves_all_keys():
    assert merge_states(
        {"1": ["a", "b"], "2": ["c"]},
        {"1": ["b", "d"], "3": ["e"]},
    ) == {
        "1": ["a", "b", "d"],
        "2": ["c"],
        "3": ["e"],
    }


def test_merge_states_supports_legacy_single_company_lists():
    assert merge_states(["old"], {"544524": ["new"]}) == {
        "544524": ["new", "old"]
    }
