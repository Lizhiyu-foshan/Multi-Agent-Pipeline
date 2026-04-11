"""
Platform Adapter - 平台适配器

职责：
1. 检测当前平台
2. 适配平台特定的接口
3. 提供统一的接口
"""

import logging
from typing import Dict, Any
from pathlib import Path
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class PlatformAdapter(ABC):
    """平台适配器基类"""

    @staticmethod
    @abstractmethod
    def detect_platform() -> str:
        """检测当前平台"""
        pass

    @staticmethod
    @abstractmethod
    def adapt(skill_config: Dict) -> Dict:
        """为平台适配配置"""
        pass

    @staticmethod
    @abstractmethod
    def load_config(skill_name: str) -> Dict:
        """加载 Skill 配置"""
        pass

    @staticmethod
    @abstractmethod
    def execute_skill(skill_name: str, context: Dict) -> Any:
        """执行 Skill"""
        pass


def detect_platform() -> str:
    """遍历所有平台适配器子类，检测当前平台"""
    for adapter_cls in [OpenCodeAdapter, ClaudeCodeAdapter, OpenClawAdapter]:
        result = adapter_cls.detect_platform()
        if result != "unknown":
            return result
    return "unknown"


class OpenCodeAdapter(PlatformAdapter):
    """OpenCode 平台适配器"""

    @staticmethod
    def detect_platform() -> str:
        """检测 OpenCode 平台"""
        skills_dir = Path.cwd() / ".skills"
        if skills_dir.exists():
            skill_md = Path.cwd() / "SKILL.md"
            if skill_md.exists():
                return "opencode"
        return "unknown"

    @staticmethod
    def adapt(skill_config: Dict) -> Dict:
        """为 OpenCode 适配配置"""
        skill_config["skill_path"] = f".skills/{skill_config['name']}"
        return skill_config

    @staticmethod
    def load_config(skill_name: str) -> Dict:
        """加载 Skill 配置"""
        config_path = Path.cwd() / ".skills" / skill_name / "config.yaml"
        if config_path.exists():
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    @staticmethod
    def execute_skill(skill_name: str, context: Dict) -> Any:
        """执行 Skill"""
        pass


class ClaudeCodeAdapter(PlatformAdapter):
    """Claude Code 平台适配器"""

    @staticmethod
    def detect_platform() -> str:
        """检测 Claude Code 平台"""
        skills_dir = Path.cwd() / ".skills"
        if skills_dir.exists():
            skill_md = Path.cwd() / "SKILL.md"
            if skill_md.exists():
                with open(skill_md, "r", encoding="utf-8") as f:
                    if "Claude" in f.read():
                        return "claude"

        claude_json = Path.cwd() / "claude.json"
        if claude_json.exists():
            return "claude"

        return "unknown"

    @staticmethod
    def adapt(skill_config: Dict) -> Dict:
        """为 Claude Code 适配配置"""
        skill_config["skill_path"] = f".skills/{skill_config['name']}"
        return skill_config

    @staticmethod
    def load_config(skill_name: str) -> Dict:
        """加载 Skill 配置"""
        config_path = Path.cwd() / ".skills" / skill_name / "config.yaml"
        if config_path.exists():
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    @staticmethod
    def execute_skill(skill_name: str, context: Dict) -> Any:
        """执行 Skill"""
        pass


class OpenClawAdapter(PlatformAdapter):
    """OpenClaw 平台适配器"""

    @staticmethod
    def detect_platform() -> str:
        """检测 OpenClaw 平台"""
        openclaw_dir = Path.cwd() / ".openclaw" / "skills"
        if openclaw_dir.exists():
            return "openclaw"
        return "unknown"

    @staticmethod
    def adapt(skill_config: Dict) -> Dict:
        """为 OpenClaw 适配配置"""
        skill_config["skill_path"] = f".openclaw/skills/{skill_config['name']}"
        return skill_config

    @staticmethod
    def load_config(skill_name: str) -> Dict:
        """加载 Skill 配置"""
        config_path = Path.cwd() / ".openclaw" / "skills" / skill_name / "config.yaml"
        if config_path.exists():
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    @staticmethod
    def execute_skill(skill_name: str, context: Dict) -> Any:
        """执行 Skill"""
        pass


class GenericAdapter(PlatformAdapter):
    """通用适配器（兜底）"""

    @staticmethod
    def detect_platform() -> str:
        """检测平台（返回 unknown）"""
        return "unknown"

    @staticmethod
    def adapt(skill_config: Dict) -> Dict:
        """通用适配（不修改配置）"""
        return skill_config

    @staticmethod
    def load_config(skill_name: str) -> Dict:
        """加载 Skill 配置"""
        return {}

    @staticmethod
    def execute_skill(skill_name: str, context: Dict) -> Any:
        """执行 Skill"""
        pass
