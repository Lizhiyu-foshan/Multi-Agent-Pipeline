"""
Skill Loader - Skill 加载器

职责：
1. 检测当前平台
2. 动态加载 Skill
3. 解析 Skill 配置
4. 管理 Skill 依赖
"""

import logging
from typing import Dict, Any, List
from pathlib import Path
import importlib.util
import inspect

logger = logging.getLogger(__name__)


class SkillLoader:
    """
    Skill 加载器

    职责：
    1. 检测当前平台
    2. 动态加载 Skill
    3. 解析 Skill 配置
    4. 管理 Skill 依赖
    """

    def __init__(self, project_path: Path):
        """
        初始化 Skill 加载器

        Args:
            project_path: 项目路径
        """
        self.project_path = project_path
        self.skills_dir = project_path / ".skills"
        self.loaded_skills = {}
        logger.info(f"SkillLoader initialized, skills_dir: {self.skills_dir}")

    def load_skills(self, skill_names: List[str]) -> Dict[str, Any]:
        """
        动态加载 Skill

        Args:
            skill_names: Skill 名称列表

        Returns:
            Dict: 加载的 Skill 适配器字典
        """
        skills = {}

        for skill_name in skill_names:
            try:
                skill_adapter = self._load_skill(skill_name)
                if skill_adapter:
                    skills[skill_name] = skill_adapter
                    logger.info(f"Loaded skill: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load skill {skill_name}: {e}")
                continue

        return skills

    def _load_skill(self, skill_name: str) -> Any:
        """
        加载单个 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            Any: Skill 适配器实例
        """
        skill_path = self.skills_dir / skill_name
        if not skill_path.exists():
            logger.warning(f"Skill directory not found: {skill_path}")
            return None

        adapter_path = skill_path / "adapter.py"
        if adapter_path.exists():
            return self._load_adapter_from_file(skill_name, adapter_path)
        else:
            return self._create_mock_adapter(skill_name)

    def _load_adapter_from_file(self, skill_name: str, adapter_path: Path) -> Any:
        """从文件加载适配器"""
        spec = importlib.util.spec_from_file_location(
            f"{skill_name}_adapter", adapter_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name, obj in inspect.getmembers(module, inspect.isclass):
            if attr_name.endswith("Adapter") and obj.__module__ == module.__name__:
                return obj()

        return self._create_mock_adapter(skill_name)

    def _create_mock_adapter(self, skill_name: str) -> Any:
        """创建模拟适配器"""

        class MockAdapter:
            def __init__(self, name: str):
                self.name = name

            def execute(
                self, task_description: str, context: Dict[str, Any]
            ) -> Dict[str, Any]:
                return {
                    "success": True,
                    "artifacts": {"output": f"Mock output from {self.name}"},
                }

        return MockAdapter(skill_name)

    def resolve_dependencies(self, skill_name: str) -> List[str]:
        """
        解析 Skill 依赖

        Args:
            skill_name: Skill 名称

        Returns:
            List: 依赖的 Skill 名称列表
        """
        config_path = self.skills_dir / skill_name / "config.yaml"
        if config_path.exists():
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                return config.get("dependencies", [])

        return []

    def get_skill_config(self, skill_name: str) -> Dict[str, Any]:
        """
        获取 Skill 配置

        Args:
            skill_name: Skill 名称

        Returns:
            Dict: Skill 配置
        """
        config_path = self.skills_dir / skill_name / "config.yaml"
        if config_path.exists():
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)

        return {}
