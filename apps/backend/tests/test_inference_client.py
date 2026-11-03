from app.clients.inference import EmbeddingClient


def test_embedding_request_batches_isolate_long_parent_chunks() -> None:
    texts = ["child-1", "child-2", "P" * 1801, "child-3", "child-4", "child-5"]

    batches = EmbeddingClient._request_batches(texts, batch_size=2, long_text_chars=1800)

    assert batches == [["child-1", "child-2"], ["P" * 1801], ["child-3", "child-4"], ["child-5"]]


def test_embedding_request_batches_preserve_short_text_order() -> None:
    texts = ["one", "two", "three"]

    batches = EmbeddingClient._request_batches(texts, batch_size=2, long_text_chars=1800)

    assert batches == [["one", "two"], ["three"]]
