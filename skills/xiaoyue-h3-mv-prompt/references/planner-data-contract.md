# Xiaoyue MV Planner 数据契约

适用于 `xiaoyue.mvplan/1.0`。本文件说明提示词阶段如何消费 Planner 数据，不重新定义 Planner 的剪辑算法。

## 时间线权威

- `audio`：完整母带与绝对采样时钟。最终成片遵循 `final_audio_policy: replace_with_original_master`。
- `shots`：用户理解和编辑的逻辑镜头。
- `transitions`：相邻镜头的 `hard_cut`、`long_take` 或 `crossfade`。
- `edit_points`：24 fps 母带绝对帧切点，是最终铺轨依据。
- `generation_windows`：实际送入 H3 的窗口，已经包含合法 `17N+5` 原始帧数、续接上下文、头尾安全区和最终交付区。

提示词编写不得用小数秒反推并覆盖绝对帧位。镜头创作内容只需落入对应窗口的相对时间；剪辑和裁边继续使用 Planner 给出的帧字段。

## 项目级信息

按下列优先级合成创作约束：

1. `project.description`：项目整体叙事与用途。
2. `project.overall_prompt`：全片统一风格、人物状态、摄影和世界观。
3. `project.negative_prompt`：全片禁用项；无字幕规则无论此字段是否填写都有效。
4. `creative_plan.overview`：第一轮推理形成的全片情绪曲线、叙事推进和镜头策略；它不是 H3 提示词。
5. `shot.creative_outline`：第一轮推理形成并经用户修改的当前镜头大体内容。
6. `shot.prompt`：第二轮生成并经用户修改的当前镜头 H3 提示词。
7. `shot.connection_requirement`：当前镜头和相邻镜头的连续性意图。

项目级信息不能把未绑定的项目资产自动加入当前镜头。

第一轮整片规划不得调用本 Skill 的 H3 输出协议，也不得提前分配参考编号。本 Skill 用于第二轮逐镜头提示词：读取已经确认的 `creative_plan.overview`、当前 `shot.creative_outline` 和当前镜头有序资产，再写入 `shot.prompt`。

## 上一镜上下文

第二轮输入中的 `previous_shot_context` 不属于资产编号：

- 每个非首镜都带上一镜 `creative_outline`，用于人物、剧情、环境和时间线连续性。
- `usage=narrative_context_only` 表示当前边界不是尾帧续写；不得因读取上一镜信息而把硬切或叠化误写成长镜头。
- `usage=seamless_visual_continuation` 表示当前镜头必须从上一镜交付尾态无缝继续，并会额外携带上一镜本批次最新成功的 `generated_h3_prompt`。
- 批量生成每成功一镜，就只在任务私有计划副本中更新该镜 `prompt`，供紧随其后的续写镜头读取；某镜失败只使该字段退回旧值或空值，不停止后续镜头。

上一镜的 H3 参考标签不得直接占用当前镜头资产槽位；当前镜头真实资产仍严格按自己的 `asset_bindings` 重新编号。

## 资产绑定

`assets[]` 只保存项目公用资产及稳定内部 ID。常用字段包括：

- `id`
- `name`
- `category`: `character`、`scene`、`prop`
- `declaration`
- `source`

真正的单镜头调用来自 `shots[].asset_bindings[]`：

- `asset_id`：回查项目资产。
- `order`：当前镜头的素材输入顺序。
- `usage`：当前镜头如何使用该资产。

必须执行以下顺序：

1. 只取当前镜头的 `asset_bindings`。
2. 按 `order` 排序并回查 `assets[].id`。
3. 合并资产统一 `declaration` 与本镜头 `usage`。
4. 按实际 H3 输入类型建立本镜头的图片、视频、音频输入槽。
5. 依官方协议为每一类标签从 1 编号；下一镜头重新开始。

禁止把 `assets[]` 的数组位置、`asset_id` 尾号或上一镜头编号当作当前镜头的 `<Picture N>`、`<Subject N>`、`<Video N>`、`<Audio N>`。禁止引用当前镜头没有绑定的资产。

### 连续链资产策略

普通镜头的参考编号只由本镜头绑定决定。`MiniMaxH3AddGuide` 只向子段已有的 conditioning 追加 22 帧等 Motion Context，不会自动继承父段的参考资产或 reference label。因此 `long_take` 必须显式保存 `continuity_asset_mode`，不得从“有 22 帧 Guide”推断“已继承资产”：

- `strict_continuity`：项目安全默认。应用 `continuity_asset_lock`，继承父段实际视觉绑定的 `asset_id`、revision/hash 和连续链 reference label。只在此模式下，槽位或资产变化才使下游失效并阻止生成。
- `context_only`：只使用父段尾部 Motion Context；子段按自己的 `asset_bindings` 建立 reference label。允许生成，但必须告警第 23 帧之后的身份、场景、服饰和风格漂移风险。
- `asset_transition`：用户显式指定资产进入、退出或替换。子段使用新的本镜头编号，提示词描述过渡，并标记人工审片。

声音资产不属于视觉连续策略，继续按当前镜头音频窗口处理。旧工作流前后两段确实复用了同一参考图，但没有换资产对照实验；不得把这一接线习惯记录为 H3 协议的必然限制。硬切与叠化后的新镜头继续按自己的 `asset_bindings` 从 1 编号。

## 字幕与歌词

字幕有三个职责，不能混用：

- `subtitles.master_cues[].corrected_text`：可编辑的 ASR／歌词参考母版。
- `shots[].subtitle_track.leading_text`、`text`、`trailing_text`：用户手工编辑的前扩、正式字幕、后扩；只有中间 `text` 是最终字幕输出的文字权威。
- `shots[].subtitle_context.ai_dialogue_text`：按实际 H3 音频窗口汇总的唱词上下文，依次由 `leading_extension`、`main`、`trailing_extension` 合并而成。

`leading_extension`、`main`和 `trailing_extension` 的帧范围与参考 cue 归属由生成窗口自动计算，但三段文字分别只读取 `subtitle_track.leading_text`、`text`和 `trailing_text`。系统不得从母版 cue 自动填词，也不得在移动切点、改连接方式、重跑 ASR 或修改母版时覆盖用户文字。

`shots[].singing_performance` 为逐镜演唱开关。未设值的旧计划按“有 `ai_dialogue_text` 则开启”处理；第一轮整片规划会给每镜布尔建议，用户可在第二轮前逐镜覆盖。开启且有词时提示词必须有可见歌手、稳定 `(S1)`、原词 `<d>` 和口型／呼吸描述；关闭时只把原曲人声当声音线索，不安排角色唱歌。

`shot_audio_slots` 是本镜 H3 音频附件映射：用户明确声明用途的自传音频先占 `<Audio 1>` 起的编号，原 MV 歌曲声音文件的本镜片段占其后一个编号；当前没有自传音频时，原曲就是 `<Audio 1>`。提示词直接按附件编号说明原曲的节奏、人声与唱词作用。最终成片仍替换为完整母带。

提示词阶段提交的 `shot.dialogue_or_lyrics` 由当前 `subtitle_track` 的前扩、正式字幕、后扩依次合并，作为完整音频窗口的唱词文字辅助；旧的 `subtitle_context.ai_dialogue_text` 只是派生缓存，不得覆盖三格原文。模型不需要处理前后扩的分段逻辑，只需理解整段唱了哪些词、人物应如何演唱和表演。其策略固定为 `text_only_no_shot_relative_timestamps`：不得把 `subtitle_projections` 的镜头内帧位继续传给模型，不得伪造逐字对齐，也不得要求人物逐字卡动作。最终字幕导出仍只读取中间的正式字幕，不包含前后扩。

- 改错字：更新母版 `corrected_text` 后，所有母版参考区读取新文字；镜头三格不自动变更，由用户决定是否复制替换。
- 跨切点：同一母版 cue 可以为相邻镜头提供上下文，但 AI 只收到每个镜头聚合后的单行唱词。
- 无时间戳纯歌词：可以作为语义参考，但不得伪造精确对齐。
- 所有歌词数据默认隐藏，不得生成画面字幕或歌词排版。

## 连接与命名

- `hard_cut`：新大序号，如 `SC01`、`SC02`。
- 同一连续链或同一逻辑镜头的多个生成窗口：`SC01_a`、`SC01_b`。
- 同一生成段重做：只增加 `_v1`、`_v2`，不改变镜头关系。
- 完整文件名：`{project_code}_SC{shot_number}[_{continuation_part}]_v{version}`。
- 连续链上游重做会使下游续接段失效；按 `continuity_chains[].downstream_regeneration_labels` 处理。

文件名用于生成资产与自动剪辑，不要把版本号或内部命名写进画面内容。
