"""
成本估算（粗粒度）。

litellm 的 usage 只给 total_tokens（输入+输出），无法精确区分 in/out。
这里做一个 **平均价** 的近似：以各家厂商官方公布价为参考，
取"prompt 70% + completion 30%"的加权平均作为 per-1k-tokens 估价。

这样算出来的 cost_usd 是量级正确、便于横向对比的数字，
不适合用于账单核对（那得走厂商官方对账单）。

FX_USD_CNY 是 USD → CNY 的保守汇率，前端可以基于 cost_usd × FX 得到人民币估算。
"""

from __future__ import annotations

# USD per 1k tokens (70% prompt + 30% completion 加权平均)
# 数据来源：各厂商 2025-2026 年公开报价，取常见档位。
_AVG_PRICE_PER_1K_USD: dict[str, float] = {
    # OpenAI
    "gpt-4o":            0.0038,   # in 0.0025 / out 0.01
    "gpt-4o-mini":       0.00023,  # in 0.00015 / out 0.0006
    "gpt-4.1":           0.0038,
    "gpt-4.1-mini":      0.00036,
    "gpt-4-turbo":       0.020,
    "gpt-3.5-turbo":     0.0009,
    "o1-preview":        0.030,
    "o1-mini":           0.008,

    # Anthropic
    "claude-3-5-sonnet-20241022": 0.00825,  # in 0.003 / out 0.015
    "claude-3-5-sonnet":          0.00825,
    "claude-3-5-haiku":           0.0022,
    "claude-3-opus":              0.0375,
    "claude-sonnet-4":            0.00825,
    "claude-opus-4":              0.0375,

    # DeepSeek
    "deepseek-chat":   0.00098,
    "deepseek-coder":  0.00098,
    "deepseek-reasoner": 0.00216,

    # Moonshot
    "moonshot-v1-8k":   0.00168,
    "moonshot-v1-32k":  0.00336,
    "moonshot-v1-128k": 0.00840,

    # Alibaba Qwen
    "qwen-max":    0.00560,
    "qwen-plus":   0.00112,
    "qwen-turbo":  0.00042,

    # Google Gemini
    "gemini-1.5-pro":   0.00525,
    "gemini-1.5-flash": 0.000315,
    "gemini-2.0-flash": 0.000315,

    # 智谱 GLM
    "glm-4":       0.00140,
    "glm-4-plus":  0.00700,
    "glm-4-air":   0.000140,
}

# 未命中时的兜底估价（按国内中档模型取中位数）
_DEFAULT_PRICE_PER_1K_USD = 0.002

# 汇率
FX_USD_CNY = 7.2


def _normalize_model(model: str) -> str:
    """把 provider 前缀剥掉并小写："openai/gpt-4o" → "gpt-4o"。"""
    if not model:
        return ""
    m = model.strip().lower()
    if "/" in m:
        m = m.split("/", 1)[1]
    return m


def estimate_cost_usd(total_tokens: int, model_name: str | None) -> float:
    """粗略估算一次 LLM 调用的成本（USD）。

    精度提示：这里用的是单一平均价，prompt/completion 比例不准时
    可能高估或低估，但对趋势判断足够用。
    """
    if not total_tokens or total_tokens <= 0:
        return 0.0
    key = _normalize_model(model_name or "")
    price = _AVG_PRICE_PER_1K_USD.get(key)
    if price is None:
        # 前缀匹配兜底：gpt-4o-2024-11-20 → gpt-4o
        for k, v in _AVG_PRICE_PER_1K_USD.items():
            if key.startswith(k):
                price = v
                break
    if price is None:
        price = _DEFAULT_PRICE_PER_1K_USD
    return round(total_tokens / 1000.0 * price, 6)


def usd_to_cny(cost_usd: float) -> float:
    """USD → CNY 粗略换算。"""
    return round((cost_usd or 0) * FX_USD_CNY, 4)
