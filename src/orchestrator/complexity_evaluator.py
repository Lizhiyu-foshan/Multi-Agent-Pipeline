"""
Complexity Evaluator - 复杂度评估器

职责：
1. 分析任务描述
2. 评估复杂度（1-10分）
3. 识别任务特征
4. 生成任务建议
"""

import logging
from typing import Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ComplexityEvaluation:
    """复杂度评估结果"""

    overall_score: int

    time_required: int
    collaboration: int
    analysis_depth: int
    multi_day: int
    architecture: int
    scale: str

    features: List[str]
    recommended_path: str
    suggestions: List[str]

    evaluation_time: str
    task_description: str


class ComplexityEvaluator:
    """复杂度评估器"""

    def __init__(self):
        self.complexity_keywords = {
            "simple": ["简单", "小", "单个", "独立", "基础", "入门", "演示", "示例"],
            "complex": [
                "复杂",
                "系统",
                "框架",
                "架构",
                "多",
                "集成",
                "分布式",
                "大规模",
            ],
        }
        self.time_keywords = {
            "short": ["小时", "hour", "短期", "quick", "fast"],
            "medium": ["天", "day", "周", "week"],
            "long": ["月", "month", "季度", "quarter", "长期", "long-term"],
        }
        self.collaboration_keywords = {
            "low": ["个人", "individual", "solo"],
            "high": ["团队", "协作", "collaboration", "team", "multi-agent"],
        }
        self.analysis_keywords = {
            "low": ["实现", "implement", "开发", "develop"],
            "high": [
                "分析",
                "analysis",
                "设计",
                "design",
                "规划",
                "planning",
                "研究",
                "research",
            ],
        }
        logger.info("ComplexityEvaluator initialized")

    def evaluate(self, task_description: str) -> ComplexityEvaluation:
        """评估任务复杂度"""
        logger.info(f"Evaluating complexity for: {task_description[:50]}...")
        description_lower = task_description.lower()

        time_score = self._evaluate_time_required(description_lower)
        collaboration_score = self._evaluate_collaboration(description_lower)
        analysis_depth_score = self._evaluate_analysis_depth(description_lower)
        multi_day_score = self._evaluate_multi_day(description_lower)
        architecture_score = self._evaluate_architecture(description_lower)

        overall_score = int(
            (
                time_score * 0.25
                + collaboration_score * 0.20
                + analysis_depth_score * 0.20
                + multi_day_score * 10 * 0.15
                + architecture_score * 0.20
            )
        )

        overall_score = max(1, min(10, overall_score))

        features = self._identify_features(description_lower)
        scale = self._determine_scale(overall_score, description_lower)
        recommended_path = "complex" if overall_score >= 7 else "simple"
        suggestions = self._generate_suggestions(
            overall_score, features, time_score, collaboration_score
        )

        evaluation = ComplexityEvaluation(
            overall_score=overall_score,
            time_required=time_score,
            collaboration=collaboration_score,
            analysis_depth=analysis_depth_score,
            multi_day=multi_day_score,
            architecture=architecture_score,
            scale=scale,
            features=features,
            recommended_path=recommended_path,
            suggestions=suggestions,
            evaluation_time=datetime.now().isoformat(),
            task_description=task_description,
        )

        logger.info(f"Complexity evaluation complete: {overall_score}/10")
        return evaluation

    def _evaluate_time_required(self, description: str) -> int:
        score = 5
        for keyword in self.time_keywords["short"]:
            if keyword in description:
                return 3
        for keyword in self.time_keywords["medium"]:
            if keyword in description:
                return 6
        for keyword in self.time_keywords["long"]:
            if keyword in description:
                return 9
        return score

    def _evaluate_collaboration(self, description: str) -> int:
        for keyword in self.collaboration_keywords["high"]:
            if keyword in description:
                return 8
        for keyword in self.collaboration_keywords["low"]:
            if keyword in description:
                return 3
        return 5

    def _evaluate_analysis_depth(self, description: str) -> int:
        for keyword in self.analysis_keywords["high"]:
            if keyword in description:
                return 8
        for keyword in self.analysis_keywords["low"]:
            if keyword in description:
                return 4
        return 5

    def _evaluate_multi_day(self, description: str) -> int:
        multi_day_indicators = [
            "周",
            "week",
            "月",
            "month",
            "长期",
            "long-term",
            "持续",
            "ongoing",
        ]
        for indicator in multi_day_indicators:
            if indicator in description:
                return 1
        return 0

    def _evaluate_architecture(self, description: str) -> int:
        complex_keywords = [
            "系统",
            "system",
            "框架",
            "framework",
            "架构",
            "architecture",
            "分布式",
            "distributed",
            "微服务",
            "microservice",
            "大规模",
            "large-scale",
        ]
        score = 4
        for keyword in complex_keywords:
            if keyword in description:
                score += 1
        return min(10, score)

    def _identify_features(self, description: str) -> List[str]:
        features = []
        feature_keywords = {
            "需要深度分析": [
                "分析",
                "analysis",
                "研究",
                "research",
                "规划",
                "planning",
            ],
            "需要多Agent协作": ["协作", "collaboration", "团队", "team", "multi-agent"],
            "需要架构设计": [
                "架构",
                "architecture",
                "系统",
                "system",
                "设计",
                "design",
            ],
            "需要多天完成": ["周", "week", "月", "month", "长期", "long-term"],
            "需要实现": ["实现", "implement", "开发", "develop", "构建", "build"],
            "需要测试": ["测试", "test", "验证", "verify", "检查", "check"],
            "需要文档": ["文档", "document", "规范", "spec", "说明", "guide"],
        }
        for feature, keywords in feature_keywords.items():
            for keyword in keywords:
                if keyword in description:
                    features.append(feature)
                    break
        return features

    def _determine_scale(self, overall_score: int, description: str) -> str:
        if overall_score <= 3:
            return "小型"
        elif overall_score <= 6:
            return "中型"
        else:
            return "大型"

    def _generate_suggestions(
        self,
        overall_score: int,
        features: List[str],
        time_score: int,
        collaboration_score: int,
    ) -> List[str]:
        suggestions = []
        if overall_score >= 7:
            suggestions.append("使用多Agent分析")
            suggestions.append("制定详细实施计划")
            suggestions.append("进行风险评估")
            suggestions.append("考虑分阶段实施")
        else:
            suggestions.append("使用标准开发流程")

        if "需要深度分析" in features:
            suggestions.append("使用 BMAD-EVO 进行深度分析")
        if "需要多Agent协作" in features:
            suggestions.append("使用 Multi-Agent-Pipeline 进行任务编排")
        if "需要实现" in features:
            suggestions.append("使用 Superpowers 进行工程实现")
        if "需要文档" in features:
            suggestions.append("使用 Spec-Kit 生成规范文档")

        if time_score >= 8:
            suggestions.append("制定详细的时间计划")
        if collaboration_score >= 7:
            suggestions.append("建立有效的团队协作机制")

        return suggestions

    def to_dict(self, evaluation: ComplexityEvaluation) -> Dict[str, Any]:
        return {
            "overall_score": evaluation.overall_score,
            "dimensions": {
                "time_required": evaluation.time_required,
                "collaboration": evaluation.collaboration,
                "analysis_depth": evaluation.analysis_depth,
                "multi_day": evaluation.multi_day,
                "architecture": evaluation.architecture,
            },
            "scale": evaluation.scale,
            "features": evaluation.features,
            "recommended_path": evaluation.recommended_path,
            "suggestions": evaluation.suggestions,
            "evaluation_time": evaluation.evaluation_time,
            "task_description": evaluation.task_description,
        }

    def generate_report(self, evaluation: ComplexityEvaluation) -> str:
        report_lines = []
        report_lines.append("# 复杂度评估报告")
        report_lines.append("")
        report_lines.append("## 评估摘要")
        report_lines.append(f"- **任务**: {evaluation.task_description}")
        report_lines.append(f"- **总体复杂度**: {evaluation.overall_score}/10")
        report_lines.append(f"- **项目规模**: {evaluation.scale}")
        report_lines.append(f"- **推荐路径**: {evaluation.recommended_path}")
        report_lines.append("")
        report_lines.append("## 维度得分")
        report_lines.append(f"- **时间需求**: {evaluation.time_required}/10")
        report_lines.append(f"- **协作需求**: {evaluation.collaboration}/10")
        report_lines.append(f"- **分析深度**: {evaluation.analysis_depth}/10")
        report_lines.append(f"- **架构复杂性**: {evaluation.architecture}/10")
        report_lines.append(f"- **需要多天**: {'是' if evaluation.multi_day else '否'}")
        report_lines.append("")
        report_lines.append("## 识别的特征")
        for feature in evaluation.features:
            report_lines.append(f"- {feature}")
        report_lines.append("")
        report_lines.append("## 建议")
        for suggestion in evaluation.suggestions:
            report_lines.append(f"- {suggestion}")
        report_lines.append("")
        report_lines.append("---")
        report_lines.append(f"*评估时间: {evaluation.evaluation_time}*")
        return "\n".join(report_lines)
