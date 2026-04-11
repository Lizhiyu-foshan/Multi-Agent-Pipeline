"""
测试 Multi-Agent-Pipeline 编排系统
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.core_orchestrator import CoreOrchestrator
from orchestrator.complexity_evaluator import ComplexityEvaluator


def test_complexity_evaluator():
    """测试复杂度评估器"""
    print("=" * 60)
    print("测试复杂度评估器")
    print("=" * 60)

    evaluator = ComplexityEvaluator()

    test_tasks = [
        "开发一个简单的登录功能",
        "开发一个完整的电商系统",
        "分析美以地缘政治的影响",
    ]

    for task in test_tasks:
        evaluation = evaluator.evaluate(task)
        report = evaluator.generate_report(evaluation)

        print(f"\n任务: {task}")
        print(f"复杂度: {evaluation.overall_score}/10")
        print(f"推荐路径: {evaluation.recommended_path}")
        print(f"项目规模: {evaluation.scale}")
        print()


def test_core_orchestrator():
    """测试主编排器"""
    print("=" * 60)
    print("测试主编排器")
    print("=" * 60)

    orchestrator = CoreOrchestrator(
        config_path="configs/default.yaml", project_path=Path.cwd()
    )

    test_tasks = [
        ("开发一个简单的登录功能", "simple"),
        ("开发一个完整的电商系统", "complex"),
        ("分析复杂系统", "auto"),
    ]

    for task, path_type in test_tasks:
        print(f"\n任务: {task}")
        print(f"路径: {path_type}")

        result = orchestrator.execute(
            task_description=task, path_type=path_type, max_duration_hours=1.0
        )

        if result["success"]:
            print(f"[OK] execution succeeded")
            print(f"  skills used: {result['skills_used']}")
            print(f"  duration: {result['duration']:.2f}s")
            print("\nreport:")
            print(result["report"])
        else:
            print(f"[FAIL]: {result.get('error')}")
        print()


if __name__ == "__main__":
    print("Multi-Agent-Pipeline 测试套件")
    print("=" * 60)

    test_complexity_evaluator()
    test_core_orchestrator()

    print("=" * 60)
    print("所有测试完成！")
    print("=" * 60)
