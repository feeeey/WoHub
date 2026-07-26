"""Agent 行为评测框架（区别于 tests/ 的管道测试：这里度量行为质量）。

三层评分：
  L1 工具选择 —— 该调的调了、不该调的没调（对金标用例的约束断言）
  L2 轨迹效率 —— 调用次数、无意义重复、预算纪律
  L3 答案质量 —— system prompt 里的承诺变成可测规则（结论先行、数值证据、
                 永不声称已下单、中文作答……）

两种运行模式：
  离线（默认）：对 chat_messages 里的存量真实轨迹打 L2/L3 分，按
      prompt_version × model 分桶——回答「线上流量的行为分布是什么」。
  --live：对金标用例真跑 agent（工具数据用 fixtures 固定，唯一变量是模型），
      打 L1/L2/L3 全量分——回答「当前 prompt+模型的行为是否达标/退化」。

用法（在 backend/ 目录下）：
  python -m evals                 # 离线报告
  python -m evals --live          # 金标实跑（用已配置的 LLM 渠道，产生真实调用费用）
  python -m evals extract         # 从存量轨迹提取金标用例骨架供人工审校
"""
