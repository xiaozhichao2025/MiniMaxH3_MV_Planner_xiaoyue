# H3 输出协议中文索引

本文件只负责中文路由。固定格式和细节以同目录的 MiniMax 官方英文原文为准：

- 基础模式：`official-base-en.txt`
- Ref2VA：`official-ref-en.txt`

## 基础模式

适用于 T2VA、I2VA、FL2VA、L2VA。最终 H3 rewrite 的字段内容使用简体中文，并保留以下官方英文结构：

- `integrated_multimodal_description`
- `overall_soundscape`
- `non_diegetic_music`
- `[Shot 1]`
- 后续切镜：`[Shot N] At MM:SS.mmm, ...`
- 图片锚点：`<Picture N>`
- 说话者：`(S1)`、`(S2)`
- 对白或歌词：`<d>[Language] ...</d>`
- 跨镜语音：`<scenetrans>`
- 结尾截断：`<cutoff>`

I2VA 从首帧图片发展；FL2VA 描述首尾帧之间的连续路径；L2VA 反推开场并准确落到尾帧。图片的对齐说明、镜头归属和时间表达必须遵循 `official-base-en.txt`，固定句式不得翻译；固定句式后的画面与声音描述使用中文。

## Ref2VA

固定六段英文段名，顺序不能改变；每段的解释性内容使用中文：

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

参考标签保持英文：

- `<Subject N>`
- `<Picture N>`
- `<Video N>`
- `<Audio N>`

不同标签类型独立编号。`<Subject N>` 表示目标视频中实际复用或修改的可见内容，不等于素材文件；`<Picture N>` 只在图片本身承担首帧、关键帧、尾帧或构图锚点时单独定义；`<Video N>` 表示整段视频关系；`<Audio N>` 表示被复制或参考的音频信号。

可见内容关系值保持英文：

- `fully_preserved`
- `partially_preserved`
- `attribute_transfer`
- `weak_reference`

音频关系值保持英文：

- `fully_copy`
- `partially_copy`
- `reference`
- `weak_reference`

任务类型保持英文，例如：

- `keyframe completion`
- `reference generation`
- `video editing`
- `video continuation`
- `audio reuse`
- `audio reference`

只在实际关系成立时组合任务类型，使用 ` + ` 连接，不得因为输入中“有视频”或“有音频”就自动声明编辑、续写或复用。

## 本项目的无字幕约束

官方协议允许描述可见文字，但本项目默认不生成任何字幕或歌词排版。`<d>` 只表示真实可听对白或歌词，不表示画面文字。歌词时间戳只控制表演、动作、呼吸、切镜和口型。
