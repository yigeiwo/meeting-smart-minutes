# -*- coding: utf-8 -*-
import json
import re
import time
import logging
from typing import Dict, Any, List, Optional
from openai import OpenAI

logger = logging.getLogger("ai_summarizer")


SCENARIO_PROMPTS = {
    "general": """你是一位极其专业、高效的资深会议秘书与速记专家。请根据提供的会议转录文本，提炼出条理清晰、重点突出、权责分明的会议智能纪要。
重点输出：
1. 会议核心背景与整体结论
2. 达成一致的核心决策（Decisions）
3. 各议题的重点讨论（按主题提炼，避免流水账）
4. 明确的行动项（Action Items & Todo，必须包含责任人、截止时间和具体交付内容）
5. 遗留问题与待确认事项
""",
    "tech": """你是一位资深技术架构师与研发项目经理。请分析研发技术评审/架构会/敏捷站会记录。
重点输出：
1. 技术方案选型与架构变更结论
2. 关键设计决策与技术妥协点
3. 风险预警与技术债
4. 明确的开发/联调/测试任务 Todo（责任人、排期、接口交付物）
5. 待验证的性能/安全/兼容性问题
""",
    "prd": """你是一位资深高级产品总监（CPO）。请分析产品需求（PRD）评审或用户体验评审记录。
重点输出：
1. 需求背景、业务目标与核心价值
2. 确认上线的功能特性与被砍掉/延后的需求
3. 业务逻辑争议点的最终裁决
4. 产品/设计/研发行动项（带明确优先级 P0/P1/P2、Owner 和上线排期）
5. 数据埋点与灰度测试跟进项
""",
    "business": """你是一位敏锐的商务总监与大客户销售专家。请分析商务合作、客户谈判或供应商沟通记录。
重点输出：
1. 客户/合作方的核心诉求与关键顾虑
2. 商务条款、报价、结算方式或交付周期的谈判共识
3. 合作卡点与潜在商业风险
4. 商务推进行动清单（跟进人、合同/报价单提交时间）
5. 下一次沟通时间与议题
""",
    "brainstorm": """你是一位创新思维与战略咨询专家。请分析战略研讨会或头脑风暴记录。
重点输出：
1. 核心挑战与发散出的创新想法矩阵
2. 各方案的可行性、收益与投入评估
3. 最终选定的 2~3 个重点突破方向
4. 探索性实验/MVP 验证行动项（责任人、验收标准）
5. 待调研的市场/竞品信息
""",
}

SYSTEM_TEMPLATE = """{scenario_prompt}

请务必输出严格合法的 JSON 对象，格式规范如下：
```json
{{
  "title": "简明扼要的会议标题",
  "date": "{current_date}",
  "duration": "预计会议时长或转录时长",
  "participants": "主要发言人/参会人姓名或角色列表",
  "summary_overview": "100字左右的高管概括性总结",
  "decisions": [
    "明确达成的决策项1",
    "明确达成的决策项2"
  ],
  "topics": [
    {{
      "name": "议题名称",
      "speaker": "主要发言人",
      "summary": "该议题的核心讨论要点与论据"
    }}
  ],
  "todos": [
    {{
      "task": "具体可执行的任务描述",
      "owner": "责任人姓名",
      "due": "明确截止时间或阶段",
      "priority": "P0/P1/P2"
    }}
  ],
  "follow_ups": [
    "需要进一步跟进确认的问题1"
  ]
}}
```
注意：只返回上述 JSON，不得添加任何前置或后置解释。
"""


class AISummarizer:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.3,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.strip()
        self.model = model.strip()
        self.temperature = temperature
        self.client: Optional[OpenAI] = None

        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
            )

    @classmethod
    def test_connection(
        cls,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
    ) -> Dict[str, Any]:
        api_key = (api_key or "").strip()
        base_url = (base_url or "").strip()
        model = (model or "deepseek-chat").strip()

        if not api_key:
            return {"code": -1, "msg": "未填写 API Key，无法连接"}

        start_t = time.time()
        try:
            client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=12.0)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "你好，请仅回复: OK"}],
                max_tokens=10,
                temperature=0.1,
            )
            latency_ms = int((time.time() - start_t) * 1000)
            reply = resp.choices[0].message.content or "OK"
            return {
                "code": 0,
                "msg": f"连接成功！模型响应正常",
                "latency_ms": latency_ms,
                "reply": reply.strip(),
                "model": model,
            }
        except Exception as e:
            latency_ms = int((time.time() - start_t) * 1000)
            return {
                "code": -1,
                "msg": f"连接失败: {str(e)}",
                "latency_ms": latency_ms,
                "model": model,
            }

    @classmethod
    def fetch_models_from_gateway(
        cls,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
    ) -> Dict[str, Any]:
        api_key = (api_key or "").strip()
        base_url = (base_url or "").strip()

        if not api_key:
            return {"code": -1, "msg": "请先在上方输入 API Key", "data": []}

        try:
            client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=10.0)
            res = client.models.list()
            model_ids = [m.id for m in res.data]
            # Filter out non-chat models or sort nicely
            return {
                "code": 0,
                "msg": f"成功从网关获取到 {len(model_ids)} 款可用模型！",
                "data": model_ids,
            }
        except Exception as e:
            return {
                "code": -1,
                "msg": f"从网关拉取模型列表失败: {str(e)}",
                "data": [],
            }

    def summarize(
        self,
        transcript_text: str,
        scenario: str = "general",
        meeting_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not transcript_text or not transcript_text.strip():
            raise ValueError("会议转录文本为空，无法生成纪要")

        if not self.api_key or not self.client:
            raise ValueError("未配置大模型 API Key！请前往【多 AI 模型与系统配置】页面填写 API Key。")

        scenario_prompt = SCENARIO_PROMPTS.get(scenario, SCENARIO_PROMPTS["general"])
        current_date = time.strftime("%Y-%m-%d %H:%M")

        system_content = SYSTEM_TEMPLATE.format(
            scenario_prompt=scenario_prompt,
            current_date=current_date,
        )

        user_content = f"以下是会议的真实发言与讨论内容：\n\n{transcript_text.strip()}"
        if meeting_context:
            user_content = f"【会议上下文背景】\n{json.dumps(meeting_context, ensure_ascii=False)}\n\n" + user_content

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                temperature=self.temperature,
            )
            raw_result = response.choices[0].message.content or ""
            return self._parse_json_result(raw_result, transcript_text)
        except Exception as e:
            logger.error(f"大模型 ({self.model}) 接口调用失败: {e}", exc_info=True)
            raise RuntimeError(f"AI 模型 ({self.model}) 调用失败: {str(e)}")

    def _parse_json_result(self, raw_text: str, source_text: str) -> Dict[str, Any]:
        cleaned = raw_text.strip()
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
            if match:
                cleaned = match.group(1).strip()

        try:
            data = json.loads(cleaned)
            data.setdefault("title", "会议智能纪要")
            data.setdefault("date", time.strftime("%Y-%m-%d %H:%M"))
            data.setdefault("duration", "约30分钟")
            data.setdefault("participants", "全员")
            data.setdefault("decisions", [])
            data.setdefault("topics", [])
            data.setdefault("todos", [])
            data.setdefault("follow_ups", [])
            data.setdefault("summary_overview", "已完成会议内容智能梳理与行动项提取。")
            return data
        except Exception as e:
            logger.warning(f"JSON 解析失败: {e}")
            return {
                "title": "会议智能总结",
                "date": time.strftime("%Y-%m-%d %H:%M"),
                "duration": "未知",
                "participants": "与会人员",
                "summary_overview": raw_text[:300] + "...",
                "decisions": [],
                "topics": [{"name": "会议重点", "speaker": "全员", "summary": raw_text}],
                "todos": [],
                "follow_ups": [],
                "raw_text": raw_text,
            }

    def format_to_markdown(self, summary_data: Dict[str, Any]) -> str:
        lines = []
        lines.append(f"# 📋 {summary_data.get('title', '会议智能纪要')}\n")
        lines.append(f"> 📅 **会议时间**：{summary_data.get('date', '')}  |  ⏱️ **会议时长**：{summary_data.get('duration', '')}")
        lines.append(f"> 👥 **参会人员**：{summary_data.get('participants', '')}\n")
        
        overview = summary_data.get("summary_overview")
        if overview:
            lines.append("## 💡 会议概览")
            lines.append(f"{overview}\n")

        decisions = summary_data.get("decisions", [])
        if decisions:
            lines.append("## 🎯 核心决议")
            for d in decisions:
                lines.append(f"- **{d}**")
            lines.append("")

        topics = summary_data.get("topics", [])
        if topics:
            lines.append("## 📝 议题讨论")
            for t in topics:
                name = t.get("name", "议题")
                speaker = t.get("speaker", "")
                speaker_str = f"?发言人：{speaker}?" if speaker else ""
                lines.append(f"### {name} {speaker_str}")
                lines.append(f"{t.get('summary', '')}\n")

        todos = summary_data.get("todos", [])
        if todos:
            lines.append("## ⚡ 行动清单 (Action Items)")
            lines.append("| 任务内容 | 责任人 | 截止时间 | 优先级 |")
            lines.append("| :--- | :---: | :---: | :---: |")
            for item in todos:
                task = item.get("task", "")
                owner = item.get("owner", "未指定")
                due = item.get("due", "待定")
                prio = item.get("priority", "P1")
                lines.append(f"| {task} | **{owner}** | `{due}` | {prio} |")
            lines.append("")

        follow_ups = summary_data.get("follow_ups", [])
        if follow_ups:
            lines.append("## ❓ 遗留与待跟进事项")
            for f in follow_ups:
                lines.append(f"- ⚠️ {f}")
            lines.append("")

        return "\n".join(lines)
