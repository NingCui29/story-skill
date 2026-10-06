"""Reported retry budgets must include the size of their own numeric fields."""

import unittest

import test_story as base


class BudgetPacketTests(unittest.TestCase):
    def test_reported_minimum_can_return_the_complete_packet(self):
        for size in (1000, 10000):
            with self.subTest(size=size):
                packet = {"text": "雨" * size}
                with self.assertRaises(base.story.StoryError) as caught:
                    base.story.bounded_packet(packet, 256)
                self.assertEqual(caught.exception.code, "budget_exceeded")
                minimum = caught.exception.details["minimum_bytes"]
                complete = base.story.bounded_packet(packet, minimum)
                self.assertEqual(complete["text"], "雨" * size)
                self.assertEqual(complete["budget"]["used"], minimum)
                self.assertEqual(len(base.story.dumps(complete).encode("utf-8")), minimum)
                with self.assertRaises(base.story.StoryError) as below:
                    base.story.bounded_packet(packet, minimum - 1)
                self.assertEqual(below.exception.code, "budget_exceeded")
                self.assertEqual(below.exception.details["minimum_bytes"], minimum)


if __name__ == "__main__":
    unittest.main()
