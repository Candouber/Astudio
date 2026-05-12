"""Agent Zero, the CEO-level router and final synthesizer."""
import json
import re
from typing import Any, Dict

from loguru import logger

from agents.context import ContextBuilder
from services.llm_service import llm_service
from storage.studio_store import DEFAULT_TEAM_ID, StudioStore
from storage.task_store import TaskStore
from utils.language import response_language_instruction


class AgentZero:
    def __init__(self):
        self.studio_store = StudioStore()
        self.task_store = TaskStore()

    async def route_task(self, question: str) -> Dict[str, Any]:
        """
        对用户问题进行团队匹配，路由到已有团队或分配给 0 号直接处理。
        返回必须是包含 "action" 的特定 JSON 对象。
        返回值额外带上 studio_scenario（若已能确定）方便上层无需再次查 DB。
        """
        routing_intent = self._extract_routing_intent(question)
        logger.info(f"[route_task] start, question={routing_intent[:60]}")
        if await self._is_system_management_task(routing_intent):
            logger.info("[route_task] -> system_management (studio_0)")
            return {
                "action": "route",
                "studio_id": "studio_0",
                "studio_scenario": "系统管理团队",
                "brief": f"Handle this platform-level change in the internal system team: {routing_intent}",
            }

        cards = await self.studio_store.get_all_cards()
        business_cards = [card for card in cards if card.get("id") != "studio_0"]
        cards_json = json.dumps(business_cards, ensure_ascii=False, indent=2) if business_cards else "[]"
        # id -> scenario 映射，复用给上层 /ask 展示用，避免再次查 DB
        id_to_scenario: Dict[str, str] = {
            str(c.get("id")): str(c.get("scenario") or "") for c in cards
        }

        system_prompt = ContextBuilder.build_agent_zero(
            studio_cards_json=cards_json,
            language_instruction=response_language_instruction(routing_intent),
        )

        response_str = await llm_service.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": routing_intent}
            ],
            role="agent_zero",
            stream=False,
            temperature=0.0,
        )

        try:
            text = str(response_str).strip()
            if text.startswith("```json"):
                text = text[7:-3].strip()
            elif text.startswith("```"):
                text = text[3:-3].strip()

            result = json.loads(text)

            # 安全检查
            action = result.get("action")
            if action not in ["route", "solve"]:
                logger.warning(f"0号输出未知action: {action}，回退为业务路由")
                return self._fallback_business_route(routing_intent, business_cards, "unknown_action")

            if action == "route":
                studio_id = result.get("studio_id")
                valid_business_ids = {card.get("id") for card in business_cards}
                if studio_id == "studio_0":
                    logger.warning("非系统任务被模型路由到 studio_0，已拦截并改走业务路由")
                    return self._fallback_business_route(routing_intent, business_cards, "blocked_studio_0")
                if studio_id not in valid_business_ids:
                    logger.warning(f"模型路由到不存在或不可用工作室: {studio_id}，已改走业务路由")
                    return self._fallback_business_route(routing_intent, business_cards, "invalid_studio")
                # 把该工作室的 scenario 直接回填给上层
                result.setdefault("studio_scenario", id_to_scenario.get(str(studio_id), ""))

            if action == "solve" and not self._can_answer_directly(routing_intent):
                logger.warning("模型试图直接回答复杂业务任务，已改走业务路由")
                return self._fallback_business_route(routing_intent, business_cards, "blocked_solve")

            logger.info(f"[route_task] done, action={action}, studio={result.get('studio_id')}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Routing failed to parse JSON: {e}\nResponse: {response_str}")
            return self._fallback_business_route(routing_intent, business_cards, "json_parse_error")

    @staticmethod
    def _extract_routing_intent(question: str) -> str:
        """Generated iteration prompts include guardrails mentioning AStudio/system/sandbox.
        Route by the user's actual request section so those guardrails do not trigger system routing.
        """
        text = question or ""
        markers = (
            "## User Iteration Request",
            "## New User Request",
            "## Original Request from User / CEO",
            "## User Request",
        )
        for marker in markers:
            idx = text.find(marker)
            if idx == -1:
                continue
            section = text[idx + len(marker):]
            section = section.lstrip(" \t\r\n:：")
            next_header = section.find("\n## ")
            if next_header != -1:
                section = section[:next_header]
            section = section.strip()
            if section:
                return section
        return text.strip()

    @staticmethod
    def _score_card_for_question(card: dict, question: str) -> float:
        text_parts = [
            card.get("scenario", ""),
            card.get("description", ""),
            card.get("category", ""),
            " ".join(card.get("core_capabilities") or []),
            " ".join(card.get("recent_topics") or []),
        ]
        haystack = " ".join(str(part) for part in text_parts if part)
        score = 0.0
        for item in (card.get("core_capabilities") or []) + (card.get("recent_topics") or []):
            if item and str(item) in question:
                score += 3.0
        for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", haystack):
            if token in question:
                score += 1.0
        score += min(float(card.get("task_count") or 0), 10.0) * 0.05
        return score

    def _fallback_business_route(self, question: str, cards: list[dict], reason: str) -> Dict[str, Any]:
        """非系统任务禁止落到 0 号，模型输出异常时用保守业务路由兜底。"""
        scored = [
            (self._score_card_for_question(card, question), card)
            for card in cards
            if card.get("id") != "studio_0"
        ]
        if scored:
            best_score, best_card = max(scored, key=lambda item: item[0])
            if best_score >= 3.0:
                return {
                    "action": "route",
                    "studio_id": best_card["id"],
                    "brief": (
                        f"Fallback corrected the model route ({reason}); reuse "
                        f"'{best_card.get('scenario', 'Existing Studio')}' to handle: {question}"
                    ),
                }

        default = next((card for card in cards if card.get("id") == DEFAULT_TEAM_ID), None)
        return {
            "action": "route",
            "studio_id": (default or {}).get("id") or DEFAULT_TEAM_ID,
            "studio_scenario": str((default or {}).get("scenario") or "默认团队"),
            "brief": f"Fallback corrected the model route ({reason}); use the default team to handle: {question}",
        }

    @staticmethod
    def _can_answer_directly(question: str) -> bool:
        text = question.strip()
        if not text or "\n" in text or len(text) > 80:
            return False
        execution_pattern = (
            r"(帮我|请你|请帮|我想|需要|方案|计划|规划|分析|调研|评估|测评|写|实现|开发|创建|生成|设计|"
            r"整理|总结|搜索|查找|比较|推荐|安装|配置|代码|文件|执行|处理|复习|准备|"
            r"research|write|build|create|generate|design|plan|implement|develop|code|search|compare|recommend)"
        )
        return not re.search(execution_pattern, text.lower())

    async def _is_system_management_task(self, question: str) -> bool:
        """
        平台级变更必须留在 0 号工作室。

        这里不能只靠正则做最终判断：正则只用于少数高置信请求的快速命中，
        其余交给 LLM 分类器。分类失败时保守返回 False，避免业务任务误入 studio_0。
        """
        if self._is_high_confidence_system_management_task(question):
            return True

        try:
            response = await llm_service.chat(
                messages=[
                    {"role": "system", "content": ContextBuilder.build_system_task_classifier()},
                    {"role": "user", "content": question},
                ],
                role="agent_zero",
                stream=False,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result = self._parse_json_response(response)
            confidence = float(result.get("confidence") or 0)
            is_system = self._parse_bool(result.get("is_system_management"))
            if is_system and confidence >= 0.7:
                logger.info(
                    f"系统管理分类命中 confidence={confidence:.2f}: "
                    f"{result.get('reason', '')}"
                )
                return True
            return False
        except Exception as e:
            logger.warning(f"系统管理分类失败，按非系统任务处理: {e}")
            return False

    @staticmethod
    def _parse_json_response(response: Any) -> dict:
        text = str(response).strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
        return json.loads(text)

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "是"}
        return False

    @staticmethod
    def _is_high_confidence_system_management_task(question: str) -> bool:
        """只处理极明确的平台级请求，避免把业务任务里的泛词误判成系统管理。"""
        text = AgentZero._extract_routing_intent(question).lower()

        schedule_pattern = (
            r"(每天|每日|每周|每月|每年|每小时|每隔|定时任务|定时|定期|提醒我|到点|"
            r"schedule|scheduled|scheduler|cron|remind|reminder|daily|weekly|monthly)"
        )
        schedule_action_pattern = r"(帮我|提醒|执行|总结|检查|发送|运行|汇报|report|check|send|run)"
        if re.search(schedule_pattern, text) and re.search(schedule_action_pattern, text):
            return True

        platform_ref = r"(astudio|antit|本系统|这个系统|当前系统|系统本身|平台配置|平台设置|0号|agent zero)"
        platform_action = (
            r"(安装|卸载|启用|禁用|配置|设置|修改|更新|新增|添加|删除|移除|创建|注册|接入|管理|"
            r"install|uninstall|enable|disable|configure|update|add|delete|remove|create|register|setup)"
        )
        platform_target = (
            r"(skill|skills|mcp|插件|plugin|工具配置|模型供应商|模型配置|供应商|provider|oauth|"
            r"员工|成员|member|sub[-_ ]?agent|工作室|studio|沙箱|sandbox|agent\.md|soul|记忆)"
        )

        return bool(
            re.search(platform_action, text)
            and re.search(platform_target, text)
            and re.search(platform_ref, text)
        )

    async def synthesize_results(
        self,
        question: str,
        sub_agent_findings: str,
        extra_instruction: str = "",
    ) -> str:
        """
        全盘接收某个 Leader 交回的成果精华组合，并打包为对用户的最终结论交付语。
        """
        system_prompt = ContextBuilder.build_synthesis(
            user_question=question,
            sub_agent_findings=sub_agent_findings,
            language_instruction=response_language_instruction(question, subject="the final answer"),
        )

        user_prompt = (
            "Based on the materials and constraints above, provide the final conclusion. "
            "Strictly follow the Response Language Policy."
        )
        if extra_instruction:
            user_prompt = f"{user_prompt}\n\n{extra_instruction}"

        response_str = await llm_service.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            role="agent_zero",
            stream=False,
            temperature=0.4,
        )
        return str(response_str)

agent_zero = AgentZero()
