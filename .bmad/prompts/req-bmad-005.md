# Model Request: bmad-005

**Caller**: ModelBridge
**Model**: glm-4.7
**Type**: model_inference
**Created**: 2026-04-20T22:43:55.104496

## Prompt

你是一个智能任务分析专家。请分析以下任务，并提供结构化的分析结果。

## 任务描述
开发一个完整的电商系统

## 分析要求
请从以下维度分析任务，并以 JSON 格式输出：

1. **task_type**: 任务类型（如：data_processing, web_development, api_design, automation, research等）
2. **complexity_score**: 复杂度评分（1-10，1最简单，10最复杂）
3. **recommended_roles_count**: 推荐角色数量（根据复杂度，简单任务1-2个，复杂任务3-7个）
4. **key_skills**: 关键技能列表（字符串数组，如 ["python", "data_analysis", "api_design"]）
5. **estimated_duration**: 预估完成时间（如："1小时", "1-2天", "1周"）
6. **risk_factors**: 风险因素列表
7. **success_criteria**: 成功标准列表

## 复杂度评估指南
- 1-3分: 简单任务（如：文件格式转换、简单数据处理、单函数实现）→ 1-2个角色
- 4-6分: 中等任务（如：小型API开发、多模块脚本、简单Web页面）→ 2-3个角色
- 7-8分: 复杂任务（如：完整系统开发、多服务架构、复杂算法）→ 3-5个角色
- 9-10分: 极复杂任务（如：大型平台开发、分布式系统、AI系统）→ 5-7个角色

## 输出格式
必须返回有效的 JSON，不要包含任何其他文字：

```json
{
  "task_type": "任务类型",
  "complexity_score": 5,
  "recommended_roles_count": 3,
  "key_skills": ["skill1", "skill2"],
  "estimated_duration": "1-2天",
  "risk_factors": ["风险1", "风险2"],
  "success_criteria": ["标准1", "标准2"]
}
```


## Instructions for Agent

1. Read and understand the prompt above
2. Execute model inference using your capabilities
3. Write the response to `.bmad/prompts/res-bmad-005.md`
4. Call the adapter again with `context.model_response = <your response>`
