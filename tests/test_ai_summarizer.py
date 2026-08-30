# -*- coding: utf-8 -*-
import unittest
from feishu_meeting_tool.ai_summarizer import AISummarizer


class TestAISummarizer(unittest.TestCase):
    def test_format_to_markdown(self):
        summarizer = AISummarizer(api_key="test", base_url="http://test", model="test")
        sample_data = {
            "title": "飞书会议智能总结测试",
            "date": "2026-08-30 10:00",
            "duration": "30分钟",
            "participants": "李总, 张工",
            "summary_overview": "全链路系统联调与架构评审",
            "decisions": ["完成多AI大模型中枢对接", "完成sillyGirl多机器人推送"],
            "todos": [{"task": "完成单元测试", "owner": "张工", "due": "本周", "priority": "P0"}],
            "topics": [{"topic": "架构设计", "details": "采用双轨高可用与多渠道广播"}],
            "follow_ups": ["安排下周上线发布会"],
        }
        md = summarizer.format_to_markdown(sample_data)
        self.assertIn("飞书会议智能总结测试", md)
        self.assertIn("核心决议", md)
        self.assertIn("行动清单", md)


if __name__ == "__main__":
    unittest.main()
