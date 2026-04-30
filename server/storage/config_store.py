"""
配置存储层 — YAML 文件
"""
import yaml

from models.config import AppConfig, normalize_config, parse_app_config
from storage.database import CONFIG_PATH


class ConfigStore:
    """配置数据存储"""

    async def load(self) -> AppConfig:
        """加载配置"""
        if not CONFIG_PATH.exists():
            # 创建默认配置
            default_config = AppConfig()
            await self.save(default_config)
            return default_config

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = parse_app_config(data)

        if config.model_dump() != data:
            await self.save(config)

        return config

    async def save(self, config: AppConfig):
        """保存配置"""
        config = normalize_config(config)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                config.model_dump(),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
