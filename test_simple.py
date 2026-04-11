"""
Multi-Agent-Pipeline 简单测试脚本
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.complexity_evaluator import ComplexityEvaluator


def test_complexity_evaluator():
    """测试复杂度评估器"""
    print("=" * 80)
    print("测试复杂度评估器")
    print("=" * 80)

    evaluator = ComplexityEvaluator()

    test_tasks = [
        "开发一个简单的登录功能",
        "开发一个完整的电商系统",
        "分析复杂系统的架构设计",
    ]

    for task in test_tasks:
        print(f"\n任务: {task}")

        evaluation = evaluator.evaluate(task)

        print(f"  复杂度: {evaluation.overall_score}/10")
        print(f"  推荐路径: {evaluation.recommended_path}")
        print(f"  项目规模: {evaluation.scale}")
        print(f"  特征: {', '.join(evaluation.features)}")


if __name__ == "__main__":
    test_complexity_evaluator()
    print("\n测试完成！")
