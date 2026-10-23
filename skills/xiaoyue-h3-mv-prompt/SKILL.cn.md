---
name: xiaoyue-h3-mv-prompt
description: 为小月 MV Planner 的单个镜头或生成窗口编写 MiniMax H3 提示词；使用音乐节奏、歌词时间戳、镜头连接方式和镜头级有序资产绑定，但默认禁止字幕、歌词排版和其他画面文字。
---

# 小月 H3 MV 无字幕提示词

把 `XIAOYUE_MV_PLAN` 中已经确定的项目、镜头、生成窗口、资产绑定和歌词时间信息，转换成可交给 MiniMax H3 的中文提示词。官方字段名、标签、关系值、任务类型、时间格式及图片对齐固定句保持英文原样；这些结构后的镜头、人物、环境、动作、运镜、光影、声音描述全部使用简体中文。

## 绝对规则

1. **不生成画面字幕。** 歌词、ASR 字幕和时间戳只用于理解歌曲语义、演唱动作、情绪与节奏。默认禁止 `subtitles`、`captions`、`lyric typography`、`karaoke text`、`title cards` 和 `on-screen text`。不得创建 `Typography:` 字段，不得把歌词安排成画面文字。只有用户明确要求镜头内真实存在的剧情文字时，才描述该文字。
2. **不编歌词。** 有歌词或校正字幕时只使用原文；听不清写 `[unclear]`，不得猜写、润色或补齐。歌词可以作为真实可听内容写入 `<d>[Language] ...</d>`，但这不代表歌词要显示在画面上。
3. **官方标识不翻译。** 必须原样保留 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`、`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`[Shot N]`、`<Picture N>`、`<Subject N>`、`<Video N>`、`<Audio N>`、`(S1)`、`<d>`、`<scenetrans>`、`<cutoff>` 以及官方英文任务类型和关系值。
4. **镜头资产局部编号。** 只读取当前镜头的 `asset_bindings`，按 `order` 排序。总资产库顺序没有生成意义；未绑定到当前镜头的资产不得进入提示词。每个镜头都重新从 1 建立自己的参考映射；不同官方标签类型按官方规则独立编号。
5. **Planner 时间线优先。** `shots`、`transitions`、`edit_points` 和 `generation_windows` 已经决定真实剪辑点、续接区、叠化区、裁边和合法 `17N+5` 原始帧长。不得在提示词阶段擅自重算、移动或合并这些点位。
6. **母带优先。** 最终成片使用完整原始母带。不得给镜头编造与母带冲突的新歌词、新对白、新主旋律或额外人声；只有镜头明确需要且不会破坏母带时，才补充环境声或动作声描述。
7. **原曲音频附件。** `shot_audio_slots` 按用户声明的音频顺序排列；最后一个 `<Audio N>` 是原 MV 歌曲声音文件的本镜片段。提示词须直接说明这个附件的原曲节奏、人声、唱词及对表演的作用；就单镜生成而言使用 `audio reference`／`reference` 关系，不宣称当前生成片段直接复制了音频。最终剪辑仍用完整原始母带。
8. **演唱开关优先。** `shot.singing_performance=true` 且有歌词时，画面可见角色确实唱歌：在主描述中用稳定 `(S1)`、`<d>[Language] 原词</d>`，写出随原曲人声变化的嘴型、呼吸、眼神和身体表演，但不许伪造逐字时间或保证精确同步。`false` 时原曲歌词只是 `<Audio N>` 的声音内容，不虚构画面歌手或口型；无歌词时不编唱词。

## 按需读取参考

- 收到 Planner JSON、镜头或生成窗口数据时，先读取 [`references/planner-data-contract.md`](references/planner-data-contract.md)。
- 需要写表演、运镜、节奏、硬切、连续长镜头或叠化时，读取 [`references/mv-shot-language.md`](references/mv-shot-language.md)。
- T2VA、I2VA、FL2VA、L2VA 最终出词前，必须读取根据官方逻辑整理的 [`references/official-base-cn.md`](references/official-base-cn.md)；英文原文只用于核对固定结构。
- Ref2VA 最终出词前，必须读取根据官方逻辑整理的 [`references/official-ref-cn.md`](references/official-ref-cn.md)；英文原文只用于核对固定结构。
- 模式判断和固定结构可先查 [`references/h3-output-protocol.md`](references/h3-output-protocol.md)；若中文摘要与官方英文原文存在差异，以官方英文原文为准。
- 官方来源、固定版本和改编边界见 [`references/official-sources.md`](references/official-sources.md)。

同一任务需要多份参考时，在 ComfyUI 执行器第一次返回的 `load_references` 中一次列全，不要逐份请求。

## 工作流程

### 1. 锁定输入

确认本次处理的是一个逻辑镜头还是一个 `generation_window`，并读取：

- `project.description`、`project.overall_prompt`、`project.negative_prompt`
- `creative_plan.overview` 与当前 `shot.creative_outline`；它们来自第一轮整片规划，不是 H3 输出
- `previous_shot_context`：上一镜纲领始终作为剧情、人物、环境和时间线参考；仅当 `usage=seamless_visual_continuation` 时读取上一镜刚生成成功的 `generated_h3_prompt` 作为续写强约束
- 当前 `shot.prompt`、`shot.connection_requirement`
- 当前镜头的 `asset_bindings`
- 对应 `generation_window` 的原始帧长、交付区、头尾裁边和续接上下文
- 入边与出边的 `transition.type`
- 当前镜头 `subtitle_context.ai_dialogue_text` 汇总的用户校正前扩＋正式＋后扩唱词；只取文字，不取镜头内字幕帧位
- 实际提交给模型的 `shot.dialogue_or_lyrics` 已由系统按前扩、正式字幕、后扩合成完整唱词；它是原曲音频窗口的文字辅助，不是只取前扩，也不是上屏字幕。不要再自行读取三格、裁剪唱词或推断逐字时间
- BPM、重拍、保护点和用户已确认的镜头意图

Planner 没给出的关键创作信息可以询问；已经确定的帧位、连接方式、资产顺序和歌词校正结果不得自行改写。

### 2. 建立本镜头参考映射

从 `asset_bindings` 按 `order` 取出资产，并合并：项目资产的统一声明、该绑定的 `usage`、当前镜头提示词。先生成仅供检查的本镜头参考表，再按当前 H3 模式转换为 `<Picture N>`、`<Subject N>`、`<Video N>` 或 `<Audio N>`。

资产库内部 `asset_id` 只负责找到素材，不等于任何 H3 编号。一个镜头完成后，下一镜头重新编号；不得沿用上一镜头或总资产库的号码。

### 3. 写镜头行为

先确定镜头内的主动作、表演目标、景别、主体调度、镜头运动、光线变化和节奏落点，再沿生成窗口的相对时间展开。`shot.dialogue_or_lyrics` 是本镜完整 H3 音频窗口的唱词文本，用于原曲听辨辅助、演唱语义、口型、呼吸、情绪与大致表演连续性；不要继续读取 `subtitle_projections` 为歌词计算镜头内位置，不要逐字配一个动作，也不要把歌词内容具象成画面文字。

若 `singing_performance=true` 且有歌词，必须明确人物真实演唱且嘴部可见，按母带节奏描述口型、呼吸和表演强度；若开关关闭，即使原曲有人声，画面人物仍不发声，不要凭空分配 `(Sx)`。若歌词跨镜头持续，按官方规则使用 `<scenetrans>` 并说明音频连续，仍然不显示歌词。

### 4. 按连接方式处理

- `hard_cut`：前后是独立镜头；上一镜纲领只用于保持故事、身份、世界和时间线连贯，不继承上一镜尾帧，也不伪造视觉续写。
- `long_take`：当前镜头是“接着上一镜继续生成的新一段”，不是“上一镜如何结束”。必须读取上一镜本批次刚生成成功的 H3 提示词，再结合上一镜纲领，从上一生成段交付尾帧的主体身份、姿态、构图、运镜方向、光线、环境和动作相位继续；需要实际续写已有视频时使用官方 `video continuation` 关系。上一镜 H3 提示词为空或本批次生成失败时，本镜禁止生成；连续链的后续段依次跳过，直到下一个 `hard_cut` 或 `crossfade` 镜头再恢复。连续链超过三个生成窗口时提示劣化风险，不擅自拆链。
- `crossfade`：叠化由后期完成。提示词保证前镜尾部和后镜头部具有可混合的构图、亮度、运动方向或动作节奏；除非用户明确要模型内转场，不要求 H3 渲染透明度叠化。

### 5. 选择官方格式并输出

- T2VA、I2VA、FL2VA、L2VA：使用官方基础模式结构和三个核心字段。
- Ref2VA：严格按 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music` 六段顺序输出。

最终可复制给 H3 的提示词块以中文描述内容为主；官方字段名、标签、任务类型、关系值、时间格式和图片对齐固定句保持英文。`<d>` 内真实对白或歌词保留原语言。不要混入工作笔记、内部资产 ID、UI 提醒或后期剪辑说明。

## 交付前检查

- 官方字段、标签、任务类型和关系值均为英文原文且结构完整；字段内实际描述内容为简体中文。
- 没有 `Typography:`，没有字幕、歌词排版、卡拉 OK 字、标题卡或其他非剧情画面文字。
- 没有把歌词时间戳误当作必须上屏的文字。
- 只使用当前镜头绑定资产，编号从本镜头重新开始，绑定顺序正确。
- 动作、运镜和演唱表演落在当前生成窗口时间内。
- 长镜头正确承接上一段状态；叠化仅提供可混合把手；硬切保持镜头独立。
- 普通镜头已参考上一镜纲领但没有误写成续接；续写镜头已使用上一镜最新成功 H3 提示词，且明确当前镜是下一段。
- 没有重算 Planner 的 `17N+5`、裁边、重叠或绝对帧位。
- 没有编造歌词、对白、音乐段落或与母带冲突的声音。
- 原曲 `<Audio N>` 附件在用户自传音频之后，明确它是原 MV 歌曲本镜片段。演唱镜头有原词 `<d>`、稳定歌手 ID、随原曲变化的嘴型；非演唱镜头不做口型。
