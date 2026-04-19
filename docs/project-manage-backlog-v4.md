# Project-Manage 开发 Backlog v4

## Phase 0（骨架，0.5 天）

- [ ] 新建 `.skills/project-manage/adapter.py`
  - [ ] action 路由框架（16 个 action）
  - [ ] 统一返回结构（success/action/artifacts/error）
- [ ] 新建 `src/project_manage/`
  - [ ] `__init__.py`
  - [ ] `models.py` — 全部数据模型
  - [ ] `registry.py` — 空壳
  - [ ] `packs.py` — 空壳
  - [ ] `ingest.py` — 空壳
  - [ ] `drift.py` — 空壳
  - [ ] `gates.py` — 空壳
  - [ ] `delivery.py` — 空壳
  - [ ] `approval.py` — 空壳
  - [ ] `audit.py` — 空壳
  - [ ] `metrics.py` — 空壳
  - [ ] `project_init.py` — 空壳
- [ ] 新建 `.skills/project-manage/SKILL.md`
- [ ] 新建 `tests/test_project_manage.py` — 测试骨架

## Phase 1（核心治理，3 天）

### 1.1 数据模型与持久化

- [ ] `models.py` 全部模型实现
  - [ ] ProjectRecord（含 6 状态字段）
  - [ ] ConstraintPack（含 Python 规则字段）
  - [ ] ExternalChangeEvent
  - [ ] DriftReport
  - [ ] GateReport
  - [ ] DeliveryRecord
  - [ ] ApprovalRecord
  - [ ] RollbackPoint
- [ ] `state/global/projects.json` 读写
- [ ] `state/global/constraint_packs.json` 读写
- [ ] `state/projects/<project_id>/` 目录隔离

### 1.2 项目注册与生命周期（registry.py）

- [ ] `project_init(mode="new")` — 全新项目初始化
  - [ ] D 盘创建目录
  - [ ] 问答确认技术栈
  - [ ] 生成脚手架
  - [ ] 绑定约束包
  - [ ] 注册到 MAP
- [ ] `project_init(mode="clone")` — Clone 项目初始化
  - [ ] git clone 到本地目录
  - [ ] 模型扫描目录 + 提取技术栈
  - [ ] 生成约束草案
  - [ ] 创建 dev branch
  - [ ] 注册到 MAP
- [ ] `project_init(mode="local")` — 本地已有项目
  - [ ] 模型扫描目录 + 识别技术栈
  - [ ] 生成合规报告
  - [ ] 生成约束草案
  - [ ] 注册到 MAP
- [ ] `project_get(project_id)`
- [ ] `project_list(status=None)` — 支持按状态筛选
- [ ] `project_update(project_id, **kwargs)`
- [ ] `project_pause(project_id)` — init/active -> paused
- [ ] `project_resume(project_id)` — paused -> active
- [ ] `project_archive(project_id)` — 任意状态 -> archived
- [ ] `project_delete(project_id, keep_files=True)`

### 1.3 约束包管理（packs.py）

- [ ] 约束包注册（name, version, rules）
- [ ] 静态规则（JSON）加载与校验
- [ ] Python 可执行规则引擎
  - [ ] 加载 .py 规则文件
  - [ ] 沙箱执行
  - [ ] 收集结果（pass/fail + issues）
- [ ] `pack_activate(project_id, pack_name, version)`
- [ ] 约束包回滚（切换历史版本）
- [ ] 约束包版本列表查询

### 1.4 外部变更回灌（ingest.py）

- [ ] `ingest_external_changes(project_id, source, commit_range|files)`
- [ ] 记录 ExternalChangeEvent
- [ ] 支持 source：opencode / git / manual

### 1.5 漂移检测（drift.py）

- [ ] `drift_check(project_id, change_event_id=None)`
- [ ] 对比激活约束包 vs 当前代码状态
- [ ] 输出 DriftReport（violations + severity）
- [ ] severity 分级：low / medium / high / critical
- [ ] 执行 Python 可执行规则

### 1.6 统一门禁（gates.py）

- [ ] `evaluate_gates(project_id, delivery_id=None, drift_report_id=None)`
- [ ] baseline 门禁（回归基线命令）
- [ ] drift 门禁（漂移检测结果）
- [ ] quality 门禁（CodeAnalyzer 分数）
- [ ] compat 门禁（关键接口保护）
- [ ] `critical` 阻断规则
- [ ] 综合决策：pass / blocked / needs_fix

### 1.7 Adapter 路由对接

- [ ] `project_init` -> project_init.py
- [ ] `project_get` -> registry.py
- [ ] `project_list` -> registry.py
- [ ] `project_update` -> registry.py
- [ ] `project_pause` -> registry.py
- [ ] `project_resume` -> registry.py
- [ ] `project_archive` -> registry.py
- [ ] `project_delete` -> registry.py
- [ ] `pack_activate` -> packs.py
- [ ] `ingest_external_changes` -> ingest.py
- [ ] `drift_check` -> drift.py
- [ ] `evaluate_gates` -> gates.py

### 1.8 单元测试

- [ ] registry CRUD + 生命周期状态流转
- [ ] packs 注册/激活/回滚 + Python 规则执行
- [ ] ingest 回灌 + 事件记录
- [ ] drift 检测 + severity 分级
- [ ] gates 门禁 + critical 阻断
- [ ] adapter 合约测试（16 个 action）

## Phase 2（交付闭环，2 天）

### 2.1 本地交付（delivery.py）

- [ ] `stage_local(project_id, files)` — 暂存变更
- [ ] `evaluate_gates` 对接（自动门禁）
- [ ] `request_approval` — 请求审批
- [ ] `approve_delivery` — 审批通过
- [ ] `promote_local` — 原子发布
  - [ ] 发布前备份（创建 RollbackPoint）
  - [ ] 原子替换（tempfile + os.replace）
  - [ ] 发布后 smoke test
- [ ] `verify_delivery` — 发布后健康检查
- [ ] `rollback_delivery` — 回滚到备份点

### 2.2 审批流（approval.py）

- [ ] 审批请求创建
- [ ] 审批提交（approver 字符串标识）
- [ ] 高风险变更双审策略
- [ ] 审批记录落库

### 2.3 审计日志（audit.py）

- [ ] DeliveryRecord 记录
- [ ] ApprovalRecord 记录
- [ ] RollbackPoint 记录
- [ ] `state/global/delivery_audit.jsonl` 追加写入

### 2.4 Dashboard v1（metrics.py）

- [ ] 进度评估（pipeline 完成百分比 + 任务完成/总数）
- [ ] 平均连续开发时长（pipeline 创建到完成的平均耗时）
- [ ] 质量分（CodeAnalyzer 平均分）
- [ ] 模型调用失败率（model_request 失败/总调用）
- [ ] 重试率（retry 次数/总任务数）
- [ ] `dashboard_summary()` 聚合接口

### 2.5 集成测试

- [ ] 本地交付四段式（stage -> gate -> approve -> promote）
- [ ] 发布失败自动回滚
- [ ] OpenCode 外部改动回灌 + drift 阻断
- [ ] 审批拒绝阻断发布
- [ ] Dashboard 指标准确性

## Phase 3（GitHub + 完善，1.5 天）

- [ ] `deliver_github` — GitHub PR 通道
  - [ ] `stage_github` — 创建 feature branch
  - [ ] `promote_github` — 提交 + 创建 PR
  - [ ] PR URL 回写 delivery record
- [ ] 跨项目 dashboard 完善
- [ ] E2E 测试（三模式初始化 + 完整交付流程）
- [ ] 回归基线验证

## 测试 Checklist

- [ ] 单元测试：registry（CRUD + 生命周期 6 状态）
- [ ] 单元测试：packs（注册/激活/回滚/Python 规则执行）
- [ ] 单元测试：ingest（回灌 + 事件记录）
- [ ] 单元测试：drift（检测 + severity 分级）
- [ ] 单元测试：gates（门禁 + critical 阻断）
- [ ] 合约测试：adapter 16 个 action
- [ ] 集成测试：本地交付四段式
- [ ] 集成测试：发布失败自动回滚
- [ ] 集成测试：外部改动回灌 + drift 阻断
- [ ] 集成测试：Dashboard 5 指标准确性
- [ ] E2E：三模式初始化（new/clone/local）
- [ ] 回归：`python scripts/run_regression_baseline.py`

## DoD（完成定义）

- [ ] 多项目隔离正常，数据不串
- [ ] 三模式项目初始化均可跑通
- [ ] 项目生命周期 6 状态流转正确
- [ ] 历史项目列表可按状态筛选
- [ ] 外部改动可回灌并可检测漂移
- [ ] `critical` 漂移阻断交付
- [ ] 本地交付不可绕过审批
- [ ] 交付可追溯并支持回滚
- [ ] Dashboard 5 指标可查询
- [ ] 约束包支持 Python 可执行规则
- [ ] 回归基线稳定通过
