"""
Path Selector - 路径选择器

职责：
1. 选择执行路径
2. 基于复杂度推荐路径
3. 支持用户自定义路径
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class PathSelector:
    """
    路径选择器

    职责：
    1. 选择执行路径
    2. 基于复杂度推荐路径
    3. 支持用户自定义路径
    """

    EXECUTION_PATHS = {
        "simple": {
            "description": "简单任务路径",
            "skills": ["spec-kit", "superpowers"],
            "estimated_time": "1-4小时",
            "complexity_threshold": [1, 2, 3, 4, 5, 6],
        },
        "complex": {
            "description": "复杂任务路径",
            "skills": ["bmad-evo", "multi-agent-pipeline", "spec-kit", "superpowers"],
            "estimated_time": "2-5天",
            "complexity_threshold": [7, 8, 9, 10],
        },
        "auto": {
            "description": "自动选择路径",
            "skills": "dynamic",
            "estimated_time": "dynamic",
            "complexity_threshold": "dynamic",
        },
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化路径选择器

        Args:
            config: 配置字典
        """
        self.config = config
        self.custom_rules = config.get("routing_rules", {})
        logger.info("PathSelector initialized")

    def select_path(self, path_type: str, complexity: int = None) -> List[str]:
        """
        选择执行路径

        Args:
            path_type: 路径类型
            complexity: 复杂度（用于 auto 模式）

        Returns:
            List: Skill 名称列表
        """
        if path_type == "custom":
            return self._select_custom_path()

        if path_type == "auto":
            if complexity is not None:
                resolved = "complex" if complexity >= 7 else "simple"
            else:
                resolved = "simple"
            return self._resolve_skills(resolved)

        return self._resolve_skills(path_type)

    def _resolve_skills(self, path_type: str) -> List[str]:
        """根据路径类型解析 Skill 列表，优先使用配置中的 routing_rules"""
        if path_type in self.custom_rules:
            skills = self.custom_rules[path_type]
            if isinstance(skills, list):
                return skills

        path_info = self.EXECUTION_PATHS.get(path_type, self.EXECUTION_PATHS["simple"])
        skills = path_info["skills"]
        if isinstance(skills, list):
            return skills
        return self.EXECUTION_PATHS["simple"]["skills"]

    def _select_custom_path(self) -> List[str]:
        """选择自定义路径"""
        custom_rules = self.custom_rules.get("custom", [])
        if custom_rules:
            return custom_rules
        return self.EXECUTION_PATHS["simple"]["skills"]

    def recommend_path(self, complexity: int) -> str:
        """
        推荐执行路径

        Args:
            complexity: 复杂度 1-10

        Returns:
            str: 推荐的路径类型
        """
        if complexity >= 7:
            return "complex"
        else:
            return "simple"

    def get_path_info(self, path_type: str) -> Dict[str, Any]:
        """
        获取路径信息

        Args:
            path_type: 路径类型

        Returns:
            Dict: 路径信息
        """
        return self.EXECUTION_PATHS.get(path_type, {})
