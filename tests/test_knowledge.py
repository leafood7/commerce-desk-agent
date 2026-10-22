import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from business import knowledge


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        mode = patch.dict("os.environ", {"SUPPORTPILOT_RETRIEVAL_STRATEGY": "vector"})
        mode.start()
        self.addCleanup(mode.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_chunks_preserve_title_source_and_stable_ids(self):
        (self.root / "policy.txt").write_text("政策标题\n\n第一条\n第二条\n", encoding="utf-8")
        chunks = knowledge.build_chunks(self.root)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], {"id": "policy.txt-0", "source": "policy.txt",
                                     "content": "政策标题\n\n第一条"})
        self.assertEqual(chunks[1]["id"], "policy.txt-1")

    def test_empty_or_title_only_documents_have_no_chunks(self):
        (self.root / "policy.txt").write_text("只有标题\n", encoding="utf-8")
        self.assertEqual(knowledge.build_chunks(self.root), [])

    def test_empty_corpus_does_not_touch_existing_index(self):
        with patch.object(knowledge, "build_chunks", return_value=[]), \
             patch.object(knowledge.chromadb, "PersistentClient") as factory:
            with self.assertRaises(ValueError):
                knowledge.initialize_knowledge()
        factory.assert_not_called()

    def test_rebuild_updates_current_chunks_and_removes_stale_ids(self):
        collection = MagicMock()
        collection.get.return_value = {"ids": ["policy.txt-0", "removed.txt-0"]}
        collection.count.return_value = 1
        chunk = {"id": "policy.txt-0", "source": "policy.txt", "content": "新政策"}
        with patch.object(knowledge, "build_chunks", return_value=[chunk]), \
             patch.object(knowledge, "embedding_function"), \
             patch.object(knowledge.chromadb, "PersistentClient") as factory:
            factory.return_value.get_or_create_collection.return_value = collection
            result = knowledge.initialize_knowledge()
        collection.upsert.assert_called_once_with(ids=["policy.txt-0"], documents=["新政策"],
                                                   metadatas=[{"source": "policy.txt"}])
        collection.delete.assert_called_once_with(ids=["removed.txt-0"])
        self.assertEqual(result["chunks"], 1)

    def test_missing_index_reports_initialization_command(self):
        with patch.object(knowledge, "index_path", return_value=self.root):
            with self.assertRaisesRegex(FileNotFoundError, "init_knowledge"):
                knowledge.search_policies("退款")

    def test_distance_filter_and_small_collection(self):
        (self.root / "chroma.sqlite3").touch()
        collection = MagicMock()
        collection.count.return_value = 2
        collection.query.return_value = {
            "ids": [["p-0", "p-1"]], "documents": [["相关", "无关"]],
            "metadatas": [[{"source": "p.txt"}, {"source": "q.txt"}]],
            "distances": [[0.2, 0.9]],
        }
        with patch.object(knowledge, "index_path", return_value=self.root), \
             patch.object(knowledge, "PROJECT_ROOT", self.root), \
             patch.object(knowledge, "embedding_function"), \
             patch.object(knowledge.chromadb, "PersistentClient") as factory:
            factory.return_value.get_collection.return_value = collection
            matches = knowledge.search_policies("退款", top_k=3)
        collection.query.assert_called_once_with(query_texts=["退款"], n_results=2)
        self.assertEqual([item["source"] for item in matches], ["p.txt"])
        record = json.loads((self.root / "work/policy-retrieval.jsonl").read_text(encoding="utf-8"))
        self.assertFalse(record["candidates"][1]["accepted"])

    def test_blank_query_does_not_load_model(self):
        with patch.object(knowledge, "embedding_function") as embedding:
            self.assertEqual(knowledge.search_policies("  "), [])
        embedding.assert_not_called()

    def test_missing_index_is_not_ready(self):
        with patch.object(knowledge, "index_path", return_value=self.root):
            self.assertFalse(knowledge.is_index_ready())

    def test_ready_index_requires_records_and_matching_model(self):
        (self.root / "chroma.sqlite3").touch()
        collection = MagicMock()
        collection.count.return_value = 23
        collection.metadata = {"embedding_model": knowledge.EMBEDDING_MODEL}
        with patch.object(knowledge, "index_path", return_value=self.root), \
             patch.object(knowledge.chromadb, "PersistentClient") as factory:
            factory.return_value.get_collection.return_value = collection
            self.assertTrue(knowledge.is_index_ready())
            collection.metadata = {"embedding_model": "a-different-model"}
            self.assertFalse(knowledge.is_index_ready())


if __name__ == "__main__":
    unittest.main()
