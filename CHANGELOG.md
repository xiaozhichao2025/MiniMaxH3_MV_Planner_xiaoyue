# 更新记录 / Changelog

插件版本使用语义化 `vMAJOR.MINOR.PATCH` Git 标签。样例工作流文件名的 `v1.0` 仅代表模板修订；项目中的 `v1/v2` 镜头版本是生成素材记录，三者互不混用。

Plugin versions use `vMAJOR.MINOR.PATCH` Git tags. The example workflow's `v1.0` is a template revision; per-shot take numbers are project data, not plugin releases.

## 未发布 / Unreleased

- 暂无。

- None.

## v0.1.3 · 2026-11-14 · CreateVideo 升级兼容 / CreateVideo upgrade compatibility

- 修复 ComfyUI 升级为 `CreateVideo` 新增 `chroma_subsampling` 后，旧画布的 `codec: none` 因控件位置变化被误读为色度采样值，造成镜头生成完成但保存链被验证器忽略的问题。
- 镜头 API 图转换现在优先读取具名控件值；升级新增的控件使用目标 ComfyUI 节点定义中的默认值。已从目标版本移除的普通下拉值也会安全回退到该版本默认值。
- 不改写工作流 JSON；有效的 `444`、`422`、`420` 用户选择会原样保留，旧值错位时使用 `auto`。

- Fixed old canvases whose `codec: none` value shifted into the newly added `CreateVideo.chroma_subsampling` widget after a ComfyUI upgrade, causing validation to skip the video-save chain after generation.
- Shot API conversion now prefers named widget values. Newly introduced widgets use the target ComfyUI node default, while removed simple-combo values safely fall back to that version's default.
- Workflow JSON remains untouched; valid user selections such as `444`, `422`, and `420` are preserved, while shifted legacy values use `auto`.

## v0.1.2 · 2026-11-14 · SaveVideo 兼容性 / SaveVideo compatibility

- 修复部分新版 ComfyUI 组合中 V3 `SaveVideo` 未被旧式执行端点扫描选中，导致任务显示成功却没有视频落盘的问题。
- MV 工作台现在只在提交的镜头 API 图中注入内部兼容端点，强制执行 `SaveVideo`；不会改写用户的画布工作流，也不要求用户手动添加兼容节点。
- 自动识别 `SaveVideo` 是否具有视频输出口；没有输出口的旧版保存节点继续按原生方式运行，不连接新版兼容端点。

- Fixed mixed ComfyUI revisions where V3 `SaveVideo` was not selected by the legacy output-endpoint scan, allowing a prompt to report success without writing a video.
- The MV Workbench now injects an internal compatibility endpoint only into the submitted shot API graph, forcing `SaveVideo` to execute without rewriting the user's canvas workflow or requiring a manually added node.
- SaveVideo revisions without a video output socket continue through their native legacy path and are never connected to the newer compatibility endpoint.

## v0.1.1 · 2026-11-07 · 项目清理与稳定性 / Project cleanup and stability

- 项目设定的最近项目列表加入永久删除入口；确认后只清理该项目在 ComfyUI `input`、`output` 和 `temp` 下的受控 UUID 目录。
- 删除前验证项目清单与目录归属，正在生成提示词、镜头或导出成片的项目拒绝删除；删除当前项目后同步清空工作流节点中的失效引用。
- 取消母带、干声、字幕、公用资产或单镜资产的本地文件选择时，不再把冒泡的 `cancel` 事件误判为关闭整个工作台。
- FCP7 XML 工程时间线按所选版本帧点预剪实切、续写与叠化；打包的源视频仍完整，可在 PR 中拉回裁剪余量。
- 同一工程 ZIP 加入原生 Premiere `.prproj` 与搬迁后素材重定位入口；合成素材测试工程在 PR、剪映中均能打开，正式 MV 全量包仍待验收。
- 中英文 README 补齐样例工作流扩展节点、模型与 LoRA 的目录和来源，以及可选 ASR、Skill 安装位置和按需安装说明。
- 为原创插件代码与文档准备 MIT 许可证，并明确第三方扩展、模型、LoRA 与用户素材不因此获得 MIT 授权。
- 使用教程与跨版本导入兼容性仍待继续验证。

- Added permanent deletion to the recent-project list. After confirmation, cleanup is restricted to that project's UUID directories under ComfyUI `input`, `output`, and `temp`.
- Project manifests and directory ownership are validated before deletion. Projects with active prompt, shot-generation, or delivery-export jobs are protected, and deleting the active project clears stale workflow-node references.
- Cancelling native file selection for master audio, vocals, subtitles, shared assets, or shot assets no longer bubbles into closing the entire workbench.
- The FCP7 XML timeline now contains the selected hard cuts, continuations, and dissolves while packaging full source media for reversible trims in Premiere.
- The same ZIP now includes a native Premiere `.prproj` and a media relinker for moved packages. Synthetic test projects opened in both Premiere and Jianying; the full MV package still needs acceptance testing.
- Completed dependency, model, LoRA, optional ASR, and Skill-installation guidance in both READMEs.
- Prepared MIT licensing for original plugin code and documentation, with third-party licensing boundaries stated explicitly.
- Tutorial and cross-version import checks remain ongoing.

## v0.1.0 · 2026-10-24 · 私有预览 / Private preview

- 单节点 MV 工作台：项目设定、24 fps 分镜时间轴、提示词、镜头生成和最终验收。
- 独立附件路由与可见本镜输出口，可接用户的单采或双采生成链。
- 两个内置 Skill 及插件目录内的自定义 Skill 选择；QwenTE 只负责推理。
- 成功镜头的独立版本、续写父版本溯源、帧点记录和批量顺序生成。
- 最终验收选版、原曲预览、无字成片＋SRT 与 FCP7 XML 完整素材包。
- 中英文 README、三张实测工作台截图和社区致谢。

发布前仍需实测不同 ComfyUI、Premiere 和剪映版本的依赖及导入兼容性。/ Before public release, dependency and import compatibility still need testing across ComfyUI, Premiere, and Jianying versions.
