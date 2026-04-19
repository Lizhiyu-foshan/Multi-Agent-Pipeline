# Project-Manage 设计稿 v4

## 1. 目标

- 新增 `project-manage` skill，统一多项目治理入口。
- 支持三种项目初始化模式：全新项目 / Clone 项目 / 本地已有项目。
- 同时支持两条交付路径：`Local Promotion` 与 `GitHub PR`。
- 两条路径统一经过门禁、审批、审计，避免本地直推导致生产风险。
- 支持项目全生命周期管理（init -> active -> paused -> completed -> abandoned -> archived）。
- 采用自举开发方式，用 MAP 框架开发自身。
- 支持持续多轮 Agent 连续时间开发模式。

## 2. 分层架构

### 2.1 薄 Skill（门面层）

- 路径：`.skills/project-manage/adapter.py`
- 职责：action 路由、参数校验、返回格式统一

### 2.2 厚核心模块（业务层）

- 路径：`src/project_manage/`
- 模块：
  - `models.py`：数据模型定义
  - `registry.py`：项目注册、生命周期管理
  - `packs.py`：约束包注册、激活、回滚（含 Python 可执行规则引擎）
  - `ingest.py`：外部变更回灌
  - `drift.py`：约束漂移检测
  - `gates.py`：统一门禁评估
  - `delivery.py`：本地与 GitHub 交付
  - `approval.py`：审批流
  - `audit.py`：审计日志
  - `metrics.py`：跨项目指标聚合
  - `project_init.py`：三模式项目初始化（A/B/C）

## 3. 项目生命周期

### 3.1 状态机

```
INIT -> ACTIVE -> PAUSED -> COMPLETED -> ARCHIVED
                 \-> ABANDONED -> ARCHIVED
```

| 状态 | 含义 | 可转换到 |
|------|------|---------|
| `init` | 刚注册，未开始开发 | active, archived |
| `active` | 正在开发中 | paused, completed, archived |
| `paused` | 暂停（资源让给其他项目） | active, archived |
| `completed` | 开发完成，已交付 | archived |
| `abandoned` | 已废弃 | archived |
| `archived` | 已归档（只读，保留历史数据） | — |

### 3.2 历史项目列表

- `project_list(status="active")`：查活跃项目
- `project_list(status="archived")`：查历史项目
- `project_list(status="completed")`：查已完成项目
- `project_list(status="abandoned")`：查已废弃项目
- `project_list(status="all")`：查全部项目

## 4. 项目初始化三模式

### 4.1 模式 A：全新项目

1. `project_init(mode="new")` 触发问答
2. 用户选择：前端框架（React/Vue/...）、后端框架（Flask/FastAPI/...）、数据库（SQLite/PostgreSQL/...）
3. 选择开发模式：纯本地 / GitHub
4. 选择约束框架（或使用默认）
5. D 盘创建目录 + 生成脚手架
6. 生成/绑定约束包
7. 注册到 MAP，状态设为 `active`

### 4.2 模式 B：Clone 项目

1. `project_init(mode="clone", repo_url="...")` 
2. 从 GitHub clone 到 D 盘本地目录
3. 模型扫描目录结构 + 代码 → 自动提取技术栈
4. 生成约束包草案 → 人工确认/调整
5. 创建 dev branch
6. 注册到 MAP，状态设为 `active`
7. 开发完成后走 GitHub PR 交付

### 4.3 模式 C：本地已有项目

1. `project_init(mode="local", target_path="D:\\xxx")` 
2. 模型扫描目录结构 + 代码 → 自动识别技术栈
3. 对比已有约束 → 生成合规报告
4. 生成约束包草案 → 人工确认/调整
5. 注册到 MAP，状态设为 `active`
6. 默认本地开发模式

## 5. 数据与目录

- 全局状态：
  - `state/global/projects.json`
  - `state/global/constraint_packs.json`
  - `state/global/delivery_audit.jsonl`
- 项目隔离状态：
  - `state/projects/<project_id>/pipelines/`
  - `state/projects/<project_id>/sessions/`
  - `state/projects/<project_id>/checkpoints/`
  - `state/projects/<project_id>/metrics/`
  - `state/projects/<project_id>/workspace/`
  - `state/projects/<project_id>/staging/`

## 6. 统一交付状态机

`DRAFT -> STAGED -> GATE_PASSED -> APPROVED -> PROMOTED -> VERIFIED`

- `DRAFT`：准备变更
- `STAGED`：生成变更包（本地暂存区或 Git 分支）
- `GATE_PASSED`：自动门禁通过
- `APPROVED`：人工审核通过
- `PROMOTED`：发布成功（本地或 GitHub）
- `VERIFIED`：发布后健康检查通过

## 7. 门禁与审核机制（本地/远程统一）

### 7.1 自动门禁（必须全部通过）

- 回归基线：`python scripts/run_regression_baseline.py`
- 漂移检测：`drift_check`
- 质量检查：CodeAnalyzer + 测试结果
- 兼容性检查：关键接口/目录保护规则

### 7.2 人工审核（本地也强制）

- 本地交付不允许直接覆盖生产目录。
- 审批人机制：第一版用字符串标识（如 `approver="admin"`），后续迭代支持白名单 + Git 身份自动识别。
- 至少 1 名审批人；高风险变更可配置 2 名。
- 审批结果落审计日志（审批人、时间、意见、风险等级）。

### 7.3 发布安全

- 原子发布（避免半发布）
- 发布前备份
- 发布后 smoke test
- 失败自动回滚

## 8. OpenCode 外部改动回灌与漂移治理

### 8.1 回灌动作

- `ingest_external_changes(project_id, commit_range | files)`
- 记录来源：`opencode | git | manual`

### 8.2 漂移检测

- `drift_check(project_id, change_event_id?)`
- 输出 `drift_report`，按 `low/medium/high/critical` 分级

### 8.3 策略

- `critical`：阻断发布
- `high`：需审批或必须附修复计划
- `low/medium`：允许放行但自动建修复任务

## 9. 约束包系统

### 9.1 约束包格式

支持两种规则类型：
- **静态规则**（JSON）：结构约束、命名规范、目录规范、质量门槛
- **可执行规则**（Python）：自定义检查逻辑，返回 pass/fail + 详情

### 9.2 可执行规则示例

```python
def check(project_path, context):
    """自定义约束检查规则"""
    issues = []
    src_dir = os.path.join(project_path, "src")
    if not os.path.exists(src_dir):
        issues.append({"rule": "must_have_src", "severity": "high", "message": "Missing src/ directory"})
    return {"pass": len(issues) == 0, "issues": issues}
```

### 9.3 约束包生命周期

- 注册（含版本号）
- 激活（绑定到项目）
- 回滚（切换到历史版本）
- 升级（版本递增）

## 10. Dashboard v1 指标

| 指标 | 含义 | 数据来源 |
|------|------|---------|
| 进度评估 | pipeline 完成百分比、任务完成/总数 | pipeline metrics |
| 平均连续开发时长 | pipeline 从创建到完成的平均耗时 | pipeline timestamps |
| 质量分 | CodeAnalyzer 平均分 | code_analyzer |
| 模型调用失败率 | model_request 失败数 / 总调用数 | session_manager |
| 重试率 | retry 次数 / 总任务数 | task_queue |

## 11. Skill Actions（16 个）

| # | Action | Phase | 说明 |
|---|--------|-------|------|
| 1 | `project_init` | 1 | 统一初始化入口（A/B/C 三模式） |
| 2 | `project_get` | 1 | 查询单个项目详情 |
| 3 | `project_list` | 1 | 列出项目（支持状态筛选） |
| 4 | `project_update` | 1 | 更新项目元数据 |
| 5 | `project_pause` | 1 | 暂停项目（停止调度） |
| 6 | `project_resume` | 1 | 恢复暂停项目 |
| 7 | `project_archive` | 1 | 归档项目（只读保留） |
| 8 | `project_delete` | 1 | 删除项目（清理状态，可选删目录） |
| 9 | `pack_activate` | 1 | 激活约束包（含 Python 规则引擎） |
| 10 | `ingest_external_changes` | 1 | 回灌外部变更 |
| 11 | `drift_check` | 1 | 漂移检测 |
| 12 | `evaluate_gates` | 1 | 统一门禁评估 |
| 13 | `deliver_local` | 2 | 本地交付（stage/gate/approve/promote/rollback） |
| 14 | `dashboard_summary` | 2 | 跨项目指标聚合 |
| 15 | `deliver_github` | 3 | GitHub PR 交付 |
| 16 | 约束包 Python 规则引擎 | 1 | 加载/执行 .py 规则文件 |

## 12. API 草案

### 项目管理

- `POST /projects/init` — 三模式初始化
- `GET /projects/{id}`
- `GET /projects?status=active`
- `PATCH /projects/{id}`
- `POST /projects/{id}/pause`
- `POST /projects/{id}/resume`
- `POST /projects/{id}/archive`
- `DELETE /projects/{id}?keep_files=true`

### 约束包

- `POST /projects/{id}/constraint-pack/activate`

### 变更与漂移

- `POST /projects/{id}/changes/ingest`
- `POST /projects/{id}/drift/check`
- `POST /projects/{id}/gates/evaluate`

### 交付

- `POST /projects/{id}/deliveries/stage/local`
- `POST /projects/{id}/deliveries/{delivery_id}/approval/request`
- `POST /projects/{id}/deliveries/{delivery_id}/approval/submit`
- `POST /projects/{id}/deliveries/{delivery_id}/promote/local`
- `POST /projects/{id}/deliveries/{delivery_id}/verify`
- `POST /projects/{id}/deliveries/{delivery_id}/rollback`
- `POST /projects/{id}/deliveries/stage/github`（Phase 3）
- `POST /projects/{id}/deliveries/{delivery_id}/promote/github`（Phase 3）

### 看板

- `GET /dashboard/summary`
- `GET /dashboard/projects/{id}/metrics`

## 13. 数据模型

### ProjectRecord
- `project_id`, `name`, `status`(init/active/paused/completed/abandoned/archived)
- `target_path`, `repo_url`, `default_branch`, `tech_stack`
- `active_pack`(name + version), `init_mode`(new/clone/local)
- `created_at`, `updated_at`, `archived_at`

### ExternalChangeEvent
- `event_id`, `project_id`, `source`(opencode/git/manual)
- `commit_range`, `files[]`, `timestamp`

### DriftReport
- `report_id`, `project_id`, `pack_version`
- `violations[]`(rule, severity, message, file?), `severity`
- `status`(open/accepted/fixed)

### GateReport
- `gate_id`, `project_id`, `delivery_id`
- `baseline_pass`, `drift_pass`, `quality_pass`, `compat_pass`
- `details`, `decision`(pass/blocked/needs_fix)

### DeliveryRecord
- `delivery_id`, `project_id`, `pipeline_id`
- `target`(local/github), `status`, `risk_level`
- `staged_at`, `promoted_at`, `verified_at`

### ApprovalRecord
- `approval_id`, `delivery_id`
- `approver`(string), `decision`(approved/rejected)
- `comment`, `timestamp`

### RollbackPoint
- `rollback_id`, `delivery_id`, `snapshot_path`
- `created_at`, `restored`(bool)

### ConstraintPack
- `pack_id`, `name`, `version`, `description`
- `rules[]`(type: static/executable, content/file_path)
- `quality_gates`, `risk_policy`

## 14. 工期计划

### Phase 0（0.5 天）

- 建立 `project-manage` skill 和 `src/project_manage/` 骨架
- 数据模型 `models.py` 落地
- SKILL.md 编写

### Phase 1（3 天）

- `registry.py` — 项目注册 + 生命周期管理（6 状态流转）
- `project_init.py` — 三模式初始化（A/B/C）
- `packs.py` — 约束包管理 + Python 可执行规则引擎
- `ingest.py` — 外部变更回灌
- `drift.py` — 漂移检测 + severity 分级
- `gates.py` — 统一门禁（baseline/drift/quality/compat）
- `adapter.py` — 16 个 action 路由对接
- 单元测试

### Phase 2（2 天）

- `delivery.py` — 本地交付类 PR 机制（stage/gate/approve/promote/rollback）
- `approval.py` — 审批流（字符串标识）
- `audit.py` — 审计日志
- `metrics.py` — Dashboard v1（5 指标）
- 集成测试

### Phase 3（1.5 天）

- `deliver_github` — GitHub PR 通道
- 跨项目 dashboard 完善
- E2E 测试

总计：约 7 天（MVP 可生产）

## 15. 自举开发方式

- 用 MAP 框架开发 `project-manage` skill 自身
- 将 MAP 注册为 `project_id=map-core` 项目
- 绑定 `map-self-dev` 约束包
- 每个功能点作为 pipeline task 推进
- 持续多轮 Agent 连续时间开发：一次 session 完成多个 Phase 1 任务，保持上下文连续

## 16. 验收标准

- 多项目状态隔离可用，数据不串
- 三模式项目初始化均可跑通（A/B/C）
- 项目生命周期 6 状态流转正确
- 历史项目列表可按状态筛选
- OpenCode 直接改动可回灌并被 drift 检测识别
- `critical` 漂移可阻断发布
- 本地交付不可绕过门禁与审批
- 可一键回滚并有审计记录
- Dashboard 5 指标可查询
- 任意交付可追溯到 `project_id + pipeline_id + pack_version + delivery_id`
- 约束包支持 Python 可执行规则
- 回归基线稳定通过
