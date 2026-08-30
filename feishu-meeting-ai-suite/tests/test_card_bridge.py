# -*- coding: utf-8 -*-
import unittest
from feishu_meeting_tool.card_bridge import CardBridge


class TestCardBridge(unittest.TestCase):
    def test_init(self):
        bridge = CardBridge(port=5566)
        self.assertEqual(bridge.port, 5566)


if __name__ == "__main__":
    unittest.main()
