# -*- coding: utf-8 -*-
import unittest
from feishu_meeting_tool.audio_pipeline import AudioPipeline


class TestAudioPipeline(unittest.TestCase):
    def test_init(self):
        pipeline = AudioPipeline()
        self.assertIsNotNone(pipeline)


if __name__ == "__main__":
    unittest.main()
