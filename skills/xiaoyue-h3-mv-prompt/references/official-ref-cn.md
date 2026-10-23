# MiniMax H3 Ref2VA 中文执行规范

本文件按照 MiniMax 官方 `ref-en.txt` 的逻辑整理。六个字段名、参考标签、任务类型和关系值保持官方英文；字段内的定义、分析与镜头描述使用简体中文。

## 固定六段

严格按以下顺序输出，不增加或改名：

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

## `subject_definitions`

每个需要独立追踪的参考内容单独一行。标签含义不变：

- `<Subject N>`：目标视频中真正复用或修改的人物、动物、物体、场景、服装、道具、动作、姿势或视觉效果。
- `<Picture N>`：图片本身承担首帧、关键帧、尾帧或具体构图锚点时使用；只用于定义人物或场景时，应作为 `<Subject N>` 的来源，不重复建立图片项。
- `<Video N>`：整段视频被编辑、续写，或提供完整时序、运镜、剪辑结构时使用。
- `<Audio N>`：被复制或参考的独立音频信号。

定义句中的标签保持英文，解释内容使用中文，例如：

```text
subject_definitions:
<Subject 1> 是 <Picture 1> 中的短发女子，保持深蓝长外套、银色耳饰和冷白肤色。
<Video 1> 是当前续写镜头的上一段成片，提供尾帧状态和连续运镜。
```

同一参考在所有段落中保持相同标签。项目资产库 ID 不得写入提示词；每个镜头只按本镜绑定顺序重新编号。

## `summary`

以一个英文任务类型前缀开头，后接中文摘要。允许的任务类型保持英文：`keyframe completion`、`reference generation`、`video editing`、`video continuation`、`audio reuse`、`audio reference`。多种关系使用 ` + ` 连接，只在真实成立时声明。

```text
summary:
[video continuation + reference generation] 当前镜头从 <Video 1> 的交付尾帧继续，并保持 <Subject 1> 的身份和服装。
```

## `retention_analysis`

逐标签说明保留或引用关系。关系值必须保持英文：

- 可见内容：`fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`
- 音频：`fully_copy`、`partially_copy`、`reference`、`weak_reference`

英文关系值之后的原因使用中文：

```text
retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - 保持人物身份、短发、深蓝外套和银色耳饰。
<Video 1> (continuation starting state): fully_preserved - 本镜从上一段交付尾帧的姿态、构图和运镜相位继续。
```

## `detailed_description`

这是主要生成描述。整体风格可在 `[Shot 1]` 前用一至两句中文建立。首镜使用 `[Shot 1]`，后续镜头使用 `[Shot N] At MM:SS.mmm, ...`；固定标签和时间语法保持英文，实际描述使用中文。

每镜写清当前构图、主体外观与位置、环境与光线、动作与状态变化、运镜、同期声音，以及参考标签实际生效的时刻。重要 `<Subject N>` 首次出现时描述其可见特征；后续继续用相同标签，不重复定义。

续写已有视频时自然引用 `<Video N>`，并明确当前镜头是从上一段尾态继续的新生成段，不是描述上一段如何结束。主体身份、姿态、空间位置、构图、运镜方向与速度、光线、环境和动作相位必须连续。

发声人物同时保留 `<Subject N>` 和 `(Sx)`。对白或歌词只位于 `<d>[Language] 原文</d>`，原文不翻译；无法辨认写 `[unclear]`。直接复用的母带人声若没有画面内具体发声者，以 `<Audio N>` 作为声音来源，不虚构 `(Sx)`。

## 声音段

`overall_soundscape` 用中文概括环境声、动作声和非语言人声。`non_diegetic_music` 用中文描述角色听不到的配乐；无此层时写 `N/A`。参考音频的复制或参考关系只写在实际对应的声音层中。

## 完整中文结构示例

```text
subject_definitions:
<Subject 1> 是 <Picture 1> 中的短发女子，保持深蓝长外套和银色耳饰。

summary:
[reference generation] 目标视频表现 <Subject 1> 独自穿过雨夜站台。

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - 保持身份、服装、发型和面部特征。

detailed_description:
真人电影质感，冷蓝雨夜照明。[Shot 1] 中景构图中的 <Subject 1> 沿站台向前走，镜头以小幅度缓慢跟移；她在顶灯下停步回望，外套下摆仍沿原方向摆动。

overall_soundscape:
雨滴敲击站棚，远处列车声逐渐减弱，近处脚步与衣料摩擦保持清晰。

non_diegetic_music:
N/A
```
