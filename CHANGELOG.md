# 更新记录 / Changelog

插件版本使用语义化 `vMAJOR.MINOR.PATCH` Git 标签。样例工作流文件名的 `v1.0` 仅代表模板修订；项目中的 `v1/v2` 镜头版本是生成素材记录，三者互不混用。

Plugin versions use `vMAJOR.MINOR.PATCH` Git tags. The example workflow's `v1.0` is a template revision; per-shot take numbers are project data, not plugin releases.

## 未发布 / Unreleased

- 中英文 README 补齐样例工作流扩展节点、模型与 LoRA 的目录和来源，以及可选 ASR、Skill 安装位置和按需安装说明。
- 为原创插件代码与文档准备 MIT 许可证，并明确第三方扩展、模型、LoRA 与用户素材不因此获得 MIT 授权。
- 使用教程与跨版本导入兼容性仍待验证；本节不对应 Git 标签，也不表示仓库已经公开。

- Completed dependency, model, LoRA, optional ASR, and Skill-installation guidance in both READMEs.
- Prepared MIT licensing for original plugin code and documentation, with third-party licensing boundaries stated explicitly.
- Tutorial and cross-version import checks remain pending; this section is untagged and does not indicate public release.

## v0.1.0 · 2026-10-24 · 私有预览 / Private preview

- 单节点 MV 工作台：项目设定、24 fps 分镜时间轴、提示词、镜头生成和最终验收。
- 独立附件路由与可见本镜输出口，可接用户的单采或双采生成链。
- 两个内置 Skill 及插件目录内的自定义 Skill 选择；QwenTE 只负责推理。
- 成功镜头的独立版本、续写父版本溯源、帧点记录和批量顺序生成。
- 最终验收选版、原曲预览、无字成片＋SRT 与 FCP7 XML 完整素材包。
- 中英文 README、三张实测工作台截图和社区致谢。

发布前仍需实测不同 ComfyUI、Premiere 和剪映版本的依赖及导入兼容性。/ Before public release, dependency and import compatibility still need testing across ComfyUI, Premiere, and Jianying versions.
