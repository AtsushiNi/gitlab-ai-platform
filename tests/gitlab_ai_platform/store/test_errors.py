from gitlab_ai_platform.store import DuplicateReviewError, RecordNotFoundError, StateStoreError


def test_duplicate_review_error_is_a_state_store_error():
    assert issubclass(DuplicateReviewError, StateStoreError)


def test_record_not_found_error_is_a_state_store_error():
    assert issubclass(RecordNotFoundError, StateStoreError)
