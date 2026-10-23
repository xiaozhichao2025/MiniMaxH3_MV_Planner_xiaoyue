# 官方来源与改编边界

核对日期：2026-10-17。

本 Skill 的 H3 协议以 MiniMax 官方 GitHub 仓库为准：

- 官方仓库：https://github.com/MiniMax-AI/MiniMax-H3
- 固定提交：`d21241f0a4b3acbb34c97dae47fa417b7065e438`
- 提交页面：https://github.com/MiniMax-AI/MiniMax-H3/commit/d21241f0a4b3acbb34c97dae47fa417b7065e438
- 官方 H3 Skill：https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing
- 基础模式原文：https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt
- Ref2VA 原文：https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt
- 官方 MV 字幕 Skill：https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/music-video-subtitle-generator

同目录 `official-base-en.txt` 和 `official-ref-en.txt` 是上述提交中两份官方协议原文的本地副本，用于离线加载和防止英文标识被翻译。

`xiaoyue-h3-mv-prompt` 是项目专用改编，不冒充 MiniMax 官方翻译。它保留官方 H3 输出协议，并从官方 MV Skill 中吸收节奏、表演、镜头和续接方法；同时按本项目要求删除字幕上屏、歌词排版、Typography、自动创作歌词和只允许硬切等分支。
