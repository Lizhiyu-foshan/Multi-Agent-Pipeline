"""
Report Generator - 报告生成器

职责：
1. 生成执行报告
2. 生成任务清单
3. 生成时间估算
4. 生成建议
"""

import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ReportGenerator:
    """
    报告生成器

    职责：
    1. 生成执行报告
    2. 生成任务清单
    3. 生成时间估算
    4. 生成建议
    """

    def generate_report(
        self,
        task_description: str,
        path_type: str,
        execution_results: Dict[str, Any],
        duration: float,
    ) -> str:
        """
        生成执行报告

        Args:
            task_description: 任务描述
            path_type: 执行路径类型
            execution_results: 执行结果
            duration: 执行时间（秒）

        Returns:
            str: 报告内容
        """
        report_lines = []

        report_lines.append("# 执行报告")
        report_lines.append("")

        report_lines.append("## 执行摘要")
        report_lines.append(f"- **任务**: {task_description}")
        report_lines.append(f"- **执行路径**: {path_type}")
        report_lines.append(f"- **执行时间**: {duration:.1f}秒")
        report_lines.append("")

        report_lines.append("## 执行结果")
        for skill_name, result in execution_results.items():
            status = "[OK]" if result.get("success") else "[FAIL]"
            report_lines.append(f"- **{skill_name}**: {status}")
            if result.get("error"):
                report_lines.append(f"  - 错误: {result['error']}")
            if result.get("artifacts"):
                report_lines.append(f"  - 工件: {list(result['artifacts'].keys())}")
        report_lines.append("")

        successful_skills = sum(
            1 for r in execution_results.values() if r.get("success")
        )
        total_skills = len(execution_results)

        report_lines.append("## 统计信息")
        report_lines.append(f"- **总 Skill 数**: {total_skills}")
        report_lines.append(f"- **成功数**: {successful_skills}")
        report_lines.append(f"- **失败数**: {total_skills - successful_skills}")
        report_lines.append("")

        report_lines.append("## 建议")
        if successful_skills == total_skills:
            report_lines.append("- 所有 Skill 执行成功，可以进入下一步")
        elif successful_skills > total_skills * 0.5:
            report_lines.append("- 大部分 Skill 执行成功，检查失败的 Skill")
        else:
            report_lines.append("- 多个 Skill 执行失败，建议检查配置")
        report_lines.append("")

        report_lines.append("---")
        report_lines.append(f"*生成时间: {datetime.now().isoformat()}*")

        return "\n".join(report_lines)

    def generate_task_list(self, execution_results: Dict[str, Any]) -> str:
        """
        生成任务清单

        Args:
            execution_results: 执行结果

        Returns:
            str: 任务清单
        """
        task_list = []
        task_list.append("# 任务清单")
        task_list.append("")

        for skill_name, result in execution_results.items():
            if result.get("success") and result.get("artifacts"):
                task_list.append(f"## {skill_name}")
                for artifact_name, artifact_content in result["artifacts"].items():
                    task_list.append(f"- [ ] {artifact_name}")
                task_list.append("")

        return "\n".join(task_list)

    def generate_time_estimate(self, path_type: str, complexity: int = None) -> str:
        """
        生成时间估算

        Args:
            path_type: 路径类型
            complexity: 复杂度

        Returns:
            str: 时间估算
        """
        from .path_selector import PathSelector

        path_info = PathSelector.EXECUTION_PATHS.get(path_type, {})
        estimated_time = path_info.get("estimated_time", "未知")

        return estimated_time
