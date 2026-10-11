"""Original unit tests using real NoteQueue objects and no external services."""
import unittest

from job_queue import NoteQueue


class NoteQueueTests(unittest.TestCase):
    def test_capacity_rejects_boolean_zero_float_and_accepts_one(self):
        for invalid in (True, False, 0, -1, 2.0, "2"):
            with self.subTest(capacity=invalid), self.assertRaises(ValueError):
                NoteQueue(invalid)
        self.assertEqual(NoteQueue(1).snapshot(), [])

    def test_normalized_duplicate_preserves_original_text(self):
        queue = NoteQueue(2)
        self.assertTrue(queue.enqueue(" A ", " First "))
        self.assertFalse(queue.enqueue("a", "Replacement"))
        self.assertEqual(queue.snapshot(), [{"key": "a", "text": "First"}])

    def test_full_duplicate_is_rejected_before_capacity_check(self):
        queue = NoteQueue(1)
        queue.enqueue("a", "First")
        self.assertFalse(queue.enqueue(" A ", "Second"))
        with self.assertRaises(OverflowError):
            queue.enqueue("b", "New")
        self.assertEqual(queue.snapshot(), [{"key": "a", "text": "First"}])

    def test_invalid_values_do_not_mutate_and_precede_duplicate_check(self):
        queue = NoteQueue(1)
        queue.enqueue("a", "First")
        for key, text in ((42, "Note"), (" ", "Note"), ("a", " "), ("b", None)):
            with self.subTest(key=key, text=text), self.assertRaises(ValueError):
                queue.enqueue(key, text)
        self.assertEqual(queue.snapshot(), [{"key": "a", "text": "First"}])

    def test_take_is_fifo_and_releases_key(self):
        queue = NoteQueue(2)
        queue.enqueue("b", "Second letter, first job")
        queue.enqueue("a", "First letter, second job")
        self.assertEqual(queue.take()["key"], "b")
        self.assertTrue(queue.enqueue("B", "New job"))
        self.assertEqual([job["key"] for job in queue.snapshot()], ["a", "b"])
        self.assertEqual(queue.take()["key"], "a")
        self.assertEqual(queue.take()["key"], "b")
        self.assertIsNone(queue.take())

    def test_snapshot_copies_container_and_mutable_elements(self):
        queue = NoteQueue(1)
        queue.enqueue("a", "First")
        view = queue.snapshot()
        view[0]["text"] = "Changed outside"
        view.clear()
        self.assertEqual(queue.snapshot(), [{"key": "a", "text": "First"}])

    def test_instances_have_separate_pending_state(self):
        left, right = NoteQueue(1), NoteQueue(1)
        left.enqueue("same", "Left")
        self.assertTrue(right.enqueue("same", "Right"))
        self.assertEqual(left.take()["text"], "Left")
        self.assertEqual(right.take()["text"], "Right")


if __name__ == "__main__":
    unittest.main()
