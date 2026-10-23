# MiniMax H3 MV Workbench · xiaoyue

[简体中文](README.md) · [Changelog](CHANGELOG.md) · [Acknowledgements](ACKNOWLEDGEMENTS.md)

A ComfyUI workbench for music-video projects. One main node manages project assets, the original soundtrack, lyrics, a 24 fps shot timeline, two-stage AI prompting, per-shot generation versions, and final review. The workbench organizes each shot's inputs and exposes them through **visible ports**; the downstream video-generation workflow remains yours to configure. This repository contains the plugin, two bundled Skills, and one editable MiniMax H3 example workflow. It does not contain models, songs, or generated footage.

Current plugin version: **v0.1.0 private preview**. The `v1.0` in the example workflow filename is that template's revision, not the plugin version.

## Three work areas

These are screenshots from an actual project; the music and visual assets shown are not included in this repository.

### ① Project setup

![Project setup with aspect ratio, pass sizes, Skills, audio and assets](assets/screenshots/01-project-setup.png)

### ② MV workbench

![MV workbench with absolute-frame timeline, waveform and shot versions](assets/screenshots/02-mv-workbench.png)

### ③ Final review desk

![Final review desk with preview, subtitles and multitrack timeline](assets/screenshots/03-preview-desk.png)

- **Project setup** stores aspect ratio, first/second-pass MP targets, the original track, optional time-aligned vocals, lyrics/subtitles, and shared assets. Project data lives under ComfyUI's `input/xiaoyue_mv_projects/`, outside this repository.
- **MV workbench** plans shots around the soundtrack and lets you adjust hard cuts, continuations, and dissolves on a 24 fps absolute-frame timeline. Correct lyrics, bind per-shot assets, generate the overall visual outline and individual H3 prompts, then generate selected shots in order. Each successful take keeps its own frame points, seed, and continuation-parent relationship.
- **Final review desk** selects a take for each shot and previews the assembled MV using the original soundtrack; individual generated clips are muted. Export either a caption-free movie plus SRT or an FCP7 XML package with full source clips, relative media paths, and edit markers. The XML targets Premiere's FCP7 XML importer. Jianying/CapCut compatibility depends on its version and import path; the XML is not a native Jianying draft.

## Install

1. Clone this repository into `ComfyUI/custom_nodes/MiniMaxH3_MV_Planner_xiaoyue`. Cloning the private repository requires GitHub authentication.
2. Open the [single published example workflow](workflows/MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json) and consult its “发布依赖｜扩展节点与模型目录” note for required custom nodes, model filenames, and download URLs. The planner and the sample H3 generation graph are separate layers: planning alone does not require every model used by the sample graph.
3. Restart ComfyUI and load the workflow. In the main node, select **① New/open project**, import the original track and save. Run the workflow once to register the current project's planning UI, then use **② MV workbench → ③ Final review desk**.

The example generation graph uses extensions including [QwenTE](https://github.com/tl2012tl/comfyUI-llama-TE), [T8 H3 Audio](https://github.com/T8mars/comfyui-minimax-h3-audio-T8), and [Swan Bits](https://github.com/swan7-py/ComfyUI_Swan_Bits). The complete list and model links are in the workflow note. Optional local ASR uses [Easy Media](https://github.com/yolain/ComfyUI-Easy-Media) and Whisper. If you already have timestamped lyrics, import them directly; neither ASR nor a source-separation model is required.

## Connect your own generation workflow

The main node exposes the per-shot prompt, soundtrack window, aspect ratio, first/second-pass MP targets, exact `raw_frames`, seed, continuation guide, asset manifest, and output path/name prefix. The separate “MV 本镜附件槽位” asset router maps images, videos, and audio to reference slots. Wire the relevant ports to your own single- or two-pass graph. The plugin does not secretly rewrite downstream node parameters. Hover over a port or read the example workflow's interface note for its exact meaning.

Each generation saves the full model window. Guide frames and tail bleed stay recorded with the take; the review desk trims only after a final take is chosen. Continuations must follow the actual parent-take chain. Editing the timeline does not silently rewrite old takes or their original frame points. The final preview/export uses the original track once rather than layering the generated clips' audio.

## Bundled Skills

- `skills/xiaoyue-mv-visual-outline/SKILL.cn.md`: overall visual shot outline.
- `skills/xiaoyue-h3-mv-prompt/SKILL.cn.md`: individual H3 shot prompt.

Both bundled and user-added Skills are read **only from this plugin's `skills/` directory**. Add a separate folder containing `SKILL.cn.md` (preferred) or `SKILL.md`, or add a standalone `.md`/`.txt` file; then click “刷新 Skill 列表” in the workbench, select it, and save the project. QwenTE provides inference only. The workbench does not scan QwenTE's Skill directory. Avoid reusing the bundled Skill folder names.

## Data, compatibility, and versioning

- Source audio, assets, plans, and shot versions remain in ComfyUI's project-data directory. Updating or cloning this plugin does **not** upload them. Before sharing a workflow, inspect node values, asset paths, and credentials yourself.
- The published template excludes the author's private LAN ASR provider and service configuration. Use the local Whisper provider, or leave ASR disconnected and edit/import lyrics manually.
- The editor currently uses a **24 fps** timeline. The example H3 graph uses `17N+5` generation windows. Other video models can be connected through visible ports, but the sample graph is not a universal adapter.
- Plugin releases use `vMAJOR.MINOR.PATCH` Git tags and the [changelog](CHANGELOG.md). A project's *shot versions* are separate generation records, not Git versions. `v0.1.0` is the first private preview; dependencies, import compatibility, and documentation will continue to be checked before public release.

Thanks to the ComfyUI community members who inspired and enabled this work; see [Acknowledgements](ACKNOWLEDGEMENTS.md).
