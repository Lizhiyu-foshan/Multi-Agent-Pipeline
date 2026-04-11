"""
Multi-Agent-Pipeline 演示脚本

演示如何使用四框架通用编排系统
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.core_orchestrator import CoreOrchestrator
from orchestrator.complexity_evaluator import ComplexityEvaluator


def demo_complexity_evaluation():
    """演示复杂度评估"""
    print("=" * 80)
    print("【演示 1】复杂度评估")
    print("=" * 80)

    evaluator = ComplexityEvaluator()

    print("\n评估示例任务：")
    print("-" * 80)

    tasks = [
        ("开发一个简单的登录页面", "simple task"),
        ("开发一个完整的电商系统", "complex task"),
        ("分析美以地缘政治对石油市场的影响", "analysis task"),
    ]

    for task, task_type in tasks:
        print(f"\ntask type: {task_type}")
        print(f"task: {task}")

        evaluation = evaluator.evaluate(task)

        print(f"  complexity: {evaluation.overall_score}/10")
        print(f"  scale: {evaluation.scale}")
        print(f"  recommended path: {evaluation.recommended_path}")
        print(f"  features: {', '.join(evaluation.features[:3])}...")
        print(f"  suggestion: {evaluation.suggestions[0]}")


def demo_simple_path():
    """演示简单任务路径"""
    print("\n" + "=" * 80)
    print("[Demo 2] simple path")
    print("=" * 80)

    orchestrator = CoreOrchestrator(
        config_path="configs/default.yaml", project_path=Path.cwd()
    )

    task = "开发一个简单的登录功能"

    print(f"\ntask: {task}")
    print(f"path: simple (Spec-Kit + Superpowers)")
    print()

    result = orchestrator.execute(
        task_description=task, path_type="simple", max_duration_hours=1.0
    )

    if result["success"]:
        print("[OK] execution succeeded")
        print(f"  skills used: {', '.join(result['skills_used'])}")
        print(f"  duration: {result['duration']:.2f}s")
        print("\nexecution report:")
        print("-" * 80)
        print(result["report"])


def demo_complex_path():
    """演示复杂任务路径"""
    print("\n" + "=" * 80)
    print("[Demo 3] complex path")
    print("=" * 80)

    orchestrator = CoreOrchestrator(
        config_path="configs/default.yaml", project_path=Path.cwd()
    )

    task = "开发一个完整的电商系统"

    print(f"\ntask: {task}")
    print(f"path: complex (BMAD-EVO + Multi-Agent-Pipeline + Spec-Kit + Superpowers)")
    print()

    result = orchestrator.execute(
        task_description=task, path_type="complex", max_duration_hours=1.0
    )

    if result["success"]:
        print("[OK] execution succeeded")
        print(f"  skills used: {', '.join(result['skills_used'])}")
        print(f"  duration: {result['duration']:.2f}s")
        print("\nexecution report:")
        print("-" * 80)
        print(result["report"])


def demo_auto_path():
    """演示自动选择路径"""
    print("\n" + "=" * 80)
    print("[Demo 4] auto path")
    print("=" * 80)

    orchestrator = CoreOrchestrator(
        config_path="configs/default.yaml", project_path=Path.cwd()
    )

    task = "分析复杂系统的架构设计"

    print(f"\ntask: {task}")
    print(f"path: auto (system auto-eval)")
    print()

    result = orchestrator.execute(
        task_description=task, path_type="auto", max_duration_hours=1.0
    )

    if result["success"]:
        print("[OK] execution succeeded")
        print(f"  auto-selected path: {result['path_type']}")
        print(f"  skills used: {', '.join(result['skills_used'])}")
        print(f"  duration: {result['duration']:.2f}s")
        print("\nexecution report:")
        print("-" * 80)
        print(result["report"])


def main():
    """主演示函数"""
    print("\n" + "=" * 80)
    print("Multi-Agent-Pipeline demo")
    print("=" * 80)
    print()
    print("integrated frameworks:")
    print("  1. BMAD-EVO - deep analysis & decision support")
    print("  2. Spec-Kit - specification & documentation")
    print("  3. Superpowers - engineering & testing")
    print("  4. Multi-Agent-Pipeline - task decomposition & multi-agent")
    print()
    print("supported paths:")
    print("  - simple: Spec-Kit + Superpowers")
    print("  - complex: BMAD-EVO + Multi-Agent-Pipeline + Spec-Kit + Superpowers")
    print("  - auto: system auto-evaluates and selects best path")
    print("=" * 80)

    try:
        demo_complexity_evaluation()
        demo_simple_path()
        demo_complex_path()
        demo_auto_path()

        print("\n" + "=" * 80)
        print("demo completed!")
        print("=" * 80)
        print()
        print("tips:")
        print("  1. run tests: python test_orchestrator.py")
        print("  2. read docs: README.md and SKILL.md")
        print("  3. customize config: edit configs/default.yaml")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n[ERROR] demo failed: {e}")
        print("\nplease ensure:")
        print("  1. dependencies installed: pip install -r requirements.txt")
        print("  2. working directory is project root")
        print("  3. Python >= 3.8")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
