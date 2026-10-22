import unittest
from copy import deepcopy
from business.policy_context import expand_policy_context, flatten_policy_matches


def chunk(identifier, source="a.txt", content="内容"):
    return {"id": identifier, "source": source, "content": content}


class PolicyContextTests(unittest.TestCase):
    def test_adds_only_same_source_and_preserves_original_anchor_score(self):
        anchor = {**chunk("a-1"), "rerank_score": 2.5}
        original = deepcopy(anchor)
        result = expand_policy_context([anchor], [chunk("a-0"), anchor, chunk("b-0", "b.txt")])
        self.assertEqual(result[0]["id"], "a-1")
        self.assertEqual(result[0]["content"], original["content"])
        self.assertEqual(result[0]["rerank_score"], 2.5)
        self.assertEqual([r["id"] for r in result[0]["context_chunks"]], ["a-0"])
        self.assertEqual(anchor, original)

    def test_global_budget_keeps_anchors_but_limits_extra_text(self):
        anchors = [chunk("a-1", content="AAA")]
        result = expand_policy_context(anchors, [chunk("a-0", content="BBBB"), *anchors], char_budget=6)
        self.assertEqual(result[0]["context_chunks"], [])
        result = expand_policy_context(anchors, [chunk("a-0", content="BBBB"), *anchors], char_budget=7)
        self.assertEqual(sum(len(r["content"]) for r in flatten_policy_matches(result)), 7)

    def test_multiple_anchors_from_same_source_do_not_duplicate_siblings(self):
        anchors = [chunk("a-0"), chunk("a-2")]
        result = expand_policy_context(anchors, [chunk("a-0"), chunk("a-1"), chunk("a-2")])
        self.assertEqual([r["id"] for r in result[0]["context_chunks"]], ["a-1"])
        self.assertEqual(result[1]["context_chunks"], [])
        self.assertEqual(len(flatten_policy_matches(result)), 3)

    def test_round_robin_gives_each_policy_an_opportunity(self):
        anchors = [chunk("a-0", content="A"), chunk("b-0", "b.txt", "B")]
        documents = [*anchors, chunk("a-1", content="C"), chunk("a-2", content="D"), chunk("b-1", "b.txt", "E")]
        result = expand_policy_context(anchors, documents, char_budget=4)
        self.assertEqual({r["id"] for r in flatten_policy_matches(result)}, {"a-0", "a-1", "b-0", "b-1"})

    def test_closest_paragraphs_are_considered_first(self):
        anchors = [chunk("a-2", content="A")]
        documents = [chunk(f"a-{i}", content=str(i)) for i in range(5)]
        result = expand_policy_context(anchors, documents, char_budget=3)
        self.assertEqual([r["id"] for r in result[0]["context_chunks"]], ["a-1", "a-3"])

    def test_flatten_rejects_context_from_a_different_source(self):
        result = flatten_policy_matches([{**chunk("a-0"), "context_chunks": [chunk("a-1"), chunk("b-0", "b.txt")]}])
        self.assertEqual([r["id"] for r in result], ["a-0", "a-1"])
        self.assertEqual(result[1]["context_of"], "a-0")

    def test_empty_negative_budget_and_non_numeric_ids(self):
        self.assertEqual(expand_policy_context([], []), [])
        with self.assertRaises(ValueError):
            expand_policy_context([], [], -1)
        result = expand_policy_context([chunk("opaque")], [chunk("opaque"), chunk("other")])
        self.assertEqual(result[0]["context_chunks"][0]["id"], "other")


if __name__ == "__main__":
    unittest.main()
