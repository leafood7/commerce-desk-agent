import unittest
from unittest.mock import MagicMock, patch

from business.rag_retrieval import BM25Index, cached_bm25, reciprocal_rank_fusion, rerank, retrieve, tokenize


class RagRetrievalTests(unittest.TestCase):
    def test_chinese_bigrams_and_numbers(self):
        terms = tokenize("预售48小时")
        self.assertIn("预售", terms)
        self.assertIn("48", terms)
        self.assertIn("小时", terms)

    def test_bm25_discriminates_terms_and_ignores_no_overlap(self):
        index = BM25Index([{"id": "a", "content": "优惠券过期无法使用"}, {"id": "b", "content": "商品正常发货"}])
        self.assertEqual(index.search("优惠券过期", 2)[0]["id"], "a")
        self.assertEqual(index.search("xyz", 2), [])
        self.assertEqual(BM25Index([]).search("退款", 3), [])

    def test_cache_invalidates_when_same_id_content_changes(self):
        first = cached_bm25((("a", "优惠券", "a.txt"),))
        second = cached_bm25((("a", "发票", "a.txt"),))
        self.assertIsNot(first, second)
        self.assertEqual(second.search("优惠券", 1), [])

    def test_rrf_combines_rank_not_incomparable_scores(self):
        rows = reciprocal_rank_fusion([[{"id": "a", "distance": 0.1}, {"id": "b", "distance": 0.8}],
                                      [{"id": "b", "bm25_score": 900}, {"id": "c", "bm25_score": 1}]])
        self.assertEqual(rows[0]["id"], "b")
        self.assertAlmostEqual(rows[0]["rrf_score"], 1/62 + 1/61)
        self.assertEqual(len(rows), 3)

    def test_duplicate_within_one_ranking_does_not_double_count(self):
        result = reciprocal_rank_fusion([[{"id": "a"}, {"id": "a"}]])
        self.assertAlmostEqual(result[0]["rrf_score"], 1/61)

    def test_rerank_uses_question_document_pairs(self):
        model = MagicMock()
        model.predict.return_value = [-2.0, 4.0]
        with patch("business.rag_retrieval.reranker", return_value=model):
            rows = rerank("退款", [{"id": "a", "content": "发货"}, {"id": "b", "content": "退款"}])
        self.assertEqual(rows[0]["id"], "b")
        self.assertEqual(model.predict.call_args.args[0], [("退款", "发货"), ("退款", "退款")])

    def test_rerank_failure_is_not_silently_reported_as_success(self):
        with patch("business.rag_retrieval.reranker", side_effect=RuntimeError("missing model")):
            with self.assertRaises(RuntimeError):
                rerank("退款", [{"id": "a", "content": "退款"}])

    def test_empty_candidates_do_not_load_model(self):
        with patch("business.rag_retrieval.reranker") as model:
            self.assertEqual(rerank("退款", []), [])
        model.assert_not_called()

    def test_threshold_is_rerank_score_not_old_distance(self):
        collection = MagicMock()
        collection.get.return_value = {"ids": ["a"], "documents": ["退款"], "metadatas": [{"source": "a.txt"}]}
        candidate = {"id": "a", "content": "退款", "source": "a.txt", "distance": 0.9}
        with patch("business.rag_retrieval.vector_candidates", return_value=[candidate]), patch("business.rag_retrieval.rerank", return_value=[{**candidate, "rerank_score": 3}]):
            result = retrieve(collection, "退款", "hybrid_rerank", rerank_threshold=2)
            self.assertEqual(result["matches"][0]["id"], "a")
            self.assertEqual(retrieve(collection, "退款", "hybrid_rerank", rerank_threshold=4)["matches"], [])

    def test_retrieval_returns_same_source_evidence_without_promoting_it_to_anchor(self):
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["a-0", "a-1", "b-0"], "documents": ["适用条件", "限制条款", "其他政策"],
            "metadatas": [{"source": "a.txt"}, {"source": "a.txt"}, {"source": "b.txt"}]}
        anchor = {"id": "a-0", "content": "适用条件", "source": "a.txt", "rerank_score": 3.0}
        with patch("business.rag_retrieval.vector_candidates", return_value=[anchor]), patch(
                "business.rag_retrieval.rerank", return_value=[anchor]):
            result = retrieve(collection, "适用条件", "hybrid_rerank", rerank_threshold=2)
        self.assertEqual(result["anchor_matches"], [anchor])
        self.assertEqual(result["matches"][0]["context_chunks"], [
            {"id": "a-1", "source": "a.txt", "content": "限制条款"}])
        self.assertEqual(result["context"]["evidence_count"], 2)
        self.assertEqual(result["stages"]["dense_ids"], ["a-0"])

    def test_unknown_strategy_and_invalid_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            retrieve(MagicMock(), "退款", "unknown")
        with self.assertRaises(ValueError):
            retrieve(MagicMock(), "退款", "hybrid", top_k=0)


if __name__ == "__main__":
    unittest.main()
