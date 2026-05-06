"""Agent Zero, the CEO-level router and final synthesizer."""
import json
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from loguru import logger

from agents.context import ContextBuilder
from services.llm_service import llm_service
from storage.studio_store import StudioStore
from storage.task_store import TaskStore
from utils.language import is_chinese, response_language_instruction


class AgentZero:
    def __init__(self):
        self.studio_store = StudioStore()
        self.task_store = TaskStore()

    async def route_task(self, question: str) -> Dict[str, Any]:
        """
        对用户问题进行场景匹配，路由到已有的工作室、分配给0号自己，或决定创建新工作室。
        返回必须是包含 "action" 的特定 JSON 对象。
        返回值额外带上 studio_scenario（若已能确定）方便上层无需再次查 DB。
        """
        logger.info(f"[route_task] start, question={question[:60]}")
        if await self._is_system_management_task(question):
            logger.info("[route_task] -> system_management (studio_0)")
            return {
                "action": "route",
                "studio_id": "studio_0",
                "studio_scenario": "Studio 0 (System Management)",
                "brief": f"Handle this platform-level change in Studio 0 System Management: {question}",
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
            language_instruction=response_language_instruction(question),
        )

        response_str = await llm_service.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
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
            if action not in ["route", "solve", "create_studio"]:
                logger.warning(f"0号输出未知action: {action}，回退为业务路由")
                return self._fallback_business_route(question, business_cards, "unknown_action")

            if action == "route":
                studio_id = result.get("studio_id")
                valid_business_ids = {card.get("id") for card in business_cards}
                if studio_id == "studio_0":
                    logger.warning("非系统任务被模型路由到 studio_0，已拦截并改走业务路由")
                    return self._fallback_business_route(question, business_cards, "blocked_studio_0")
                if studio_id not in valid_business_ids:
                    logger.warning(f"模型路由到不存在或不可用工作室: {studio_id}，已改走业务路由")
                    return self._fallback_business_route(question, business_cards, "invalid_studio")
                # 把该工作室的 scenario 直接回填给上层
                result.setdefault("studio_scenario", id_to_scenario.get(str(studio_id), ""))

            if action == "solve" and not self._can_answer_directly(question):
                logger.warning("模型试图直接回答复杂业务任务，已改走业务路由")
                return self._fallback_business_route(question, business_cards, "blocked_solve")

            if action == "create_studio":
                reusable = self._find_reusable_studio_by_category(result, business_cards, question)
                if reusable:
                    logger.info(
                        f"0号拟创建同类工作室 [{result.get('studio_name')}]，"
                        f"已复用 [{reusable.get('scenario')}] ({reusable.get('id')})"
                    )
                    return {
                        "action": "route",
                        "studio_id": reusable["id"],
                        "studio_scenario": str(reusable.get("scenario") or ""),
                        "brief": f"Reuse existing studio '{reusable.get('scenario', 'Existing Studio')}' to handle: {question}",
                    }

            logger.info(f"[route_task] done, action={action}, studio={result.get('studio_id')}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Routing failed to parse JSON: {e}\nResponse: {response_str}")
            return self._fallback_business_route(question, business_cards, "json_parse_error")

    @staticmethod
    def _normalize_category(value: Optional[str]) -> str:
        if not value:
            return ""
        return re.sub(r"[\s\-_/｜|:：,，;；。.!！?？]+", "", value).strip().lower()

    def _category_matches(self, new_category: str, existing_category: str) -> bool:
        new_norm = self._normalize_category(new_category)
        existing_norm = self._normalize_category(existing_category)
        if not new_norm or not existing_norm:
            return False
        if new_norm == existing_norm:
            return True
        if new_norm in existing_norm or existing_norm in new_norm:
            return True
        return SequenceMatcher(None, new_norm, existing_norm).ratio() >= 0.78

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

    def _find_reusable_studio_by_category(self, result: dict, cards: list[dict], question: str) -> Optional[dict]:
        """当 0 号想创建新工作室时，用类别做一层硬兜底，避免同类工作室膨胀。"""
        new_category = str(result.get("category") or "").strip()
        generic_categories = {
            "通用", "综合", "其他", "一般", "默认", "未分类",
            "general", "misc", "other", "default", "uncategorized",
        }
        if self._normalize_category(new_category) in {self._normalize_category(c) for c in generic_categories}:
            return None

        candidates = [
            card for card in cards
            if card.get("id") != "studio_0"
            and self._category_matches(new_category, str(card.get("category") or ""))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda card: self._score_card_for_question(card, question))

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

        return self._build_business_studio_create(question)

    @staticmethod
    def _build_business_studio_create(question: str) -> Dict[str, Any]:
        text = question.lower()
        chinese = is_chinese(question)
        if re.search(r"(代码|开发|程序|前端|后端|api|bug|软件|app|网站|网页|python|java|go|typescript|react)", text):
            return {
                "action": "create_studio",
                "studio_name": "软件开发工作室" if chinese else "Software Development Studio",
                "leader_role": "技术负责人" if chinese else "Technical Lead",
                "category": "软件开发" if chinese else "Software Development",
            }
        if re.search(r"(数据|分析|算法|poi|aoi|路网|卫星|影像|gis|地图|地理|模型|评估|测评)", text):
            return {
                "action": "create_studio",
                "studio_name": "数据分析与方案工作室" if chinese else "Data Analysis and Solution Studio",
                "leader_role": "数据方案负责人" if chinese else "Data Solutions Lead",
                "category": "数据分析与算法方案" if chinese else "Data Analysis and Algorithmic Solutions",
            }
        if re.search(r"(旅行|旅游|行程|酒店|机票|包车|攻略)", text):
            return {
                "action": "create_studio",
                "studio_name": "旅行规划工作室" if chinese else "Travel Planning Studio",
                "leader_role": "旅行规划负责人" if chinese else "Travel Planning Lead",
                "category": "旅行规划" if chinese else "Travel Planning",
            }
        if re.search(r"(面试|求职|简历|jd|岗位|职业)", text):
            return {
                "action": "create_studio",
                "studio_name": "职业发展工作室" if chinese else "Career Development Studio",
                "leader_role": "职业辅导负责人" if chinese else "Career Coaching Lead",
                "category": "职业发展" if chinese else "Career Development",
            }
        if re.search(r"(文案|文章|报告|内容|写作|脚本)", text):
            return {
                "action": "create_studio",
                "studio_name": "内容创作工作室" if chinese else "Content Creation Studio",
                "leader_role": "内容策略负责人" if chinese else "Content Strategy Lead",
                "category": "内容创作" if chinese else "Content Creation",
            }
        return {
            "action": "create_studio",
            "studio_name": "通用方案工作室" if chinese else "General Solutions Studio",
            "leader_role": "任务规划负责人" if chinese else "Task Planning Lead",
            "category": "通用业务规划" if chinese else "General Business Planning",
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
        text = question.lower()

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
