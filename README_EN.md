# MiniMax H3 MV Workbench

[简体中文](README.md) · [Changelog](CHANGELOG.md) · [Acknowledgements](#acknowledgements)

**Align MiniMax H3's `17N+5` generation-frame rule with a music video's 24 fps edit points.** That is why this workbench exists. An MV needs cuts placed precisely against beats and lyrics, while H3 requires valid generation lengths. Calculating both by hand can shift a shot off its intended cut. Guide frames and extra generated frames (“bleed”) also make the true in/out points hard to find later in full-length source clips.

**MiniMax H3 MV Workbench** starts with the original song and its waveform. Place cuts on a visible, absolute-frame 24 fps timeline; the workbench calculates H3-compatible generation windows and records guide, bleed, and final delivery in/out points separately. Lyrics, assets, two-stage AI prompts, and every generated take stay attached to the same shot plan rather than relying on filenames or handwritten edit notes.

For long takes, **continuation shots** derive guide frames from a selected parent take and retain the parent chain and each segment's frame points. The final review desk lets you compare takes, pre-edit, and preview the complete MV. This provides a reliable basis for joining generated segments; visual continuity still depends on the actual model output, so you remain free to choose the best take.

The workbench **plans, organizes, and drives** each shot through visible output ports. You still choose and wire the downstream model, sampler, LoRAs, upscaler, and save node. This repository contains the plugin, two bundled Skills, and one editable MiniMax H3 example workflow; it contains **no models, songs, or generated footage**.

Current plugin version: **v0.1.0 private preview**. The `v1.0` in the example workflow filename is that template's revision, not the plugin version.

## Where to find the example workflow

The repository contains **one editable example workflow**: [MiniMax H3 MV Workbench JSON](workflows/MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json). After installation, it is at `ComfyUI/custom_nodes/MiniMaxH3_MV_Planner_xiaoyue/workflows/MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json`. Drag the JSON onto the ComfyUI canvas or open it with “Load Workflow.” If ComfyUI reports missing nodes, install them from the dependency list below and restart. The workbench plans and drives the project; the downstream H3 graph is a replaceable wiring example.

## Three work areas

These are screenshots from an actual project; the music and visual assets shown are not included in this repository.

### ① Project setup

![Project setup with aspect ratio, pass sizes, Skills, audio and assets](assets/screenshots/01-project-setup.png)

### ② MV workbench

![MV workbench with absolute-frame timeline, waveform and shot versions](assets/screenshots/02-mv-workbench.png)

### ③ Final review desk

![Final review desk with preview, subtitles and multitrack timeline](assets/screenshots/03-preview-desk.png)

- **Project setup** stores aspect ratio, first/second-pass MP targets, the original track, optional time-aligned vocals, lyrics/subtitles, and shared assets. Project data lives under ComfyUI's `input/xiaoyue_mv_projects/`, outside this repository.
- **MV workbench** plans shots around the soundtrack and lets you adjust hard cuts, continuations, and dissolves on a 24 fps absolute-frame timeline while calculating `17N+5` generation windows and bleed. Correct lyrics, bind per-shot assets, generate the overall visual outline and individual H3 prompts, then generate selected shots in order. Each successful take keeps its own frame points, seed, and continuation-parent relationship.
- **Final review desk** selects a take for each shot and previews the assembled MV using the original soundtrack; individual generated clips are muted. Export either a caption-free movie plus SRT or an FCP7 XML package with full source clips, relative media paths, and edit markers. The XML targets Premiere's FCP7 XML importer. Jianying/CapCut compatibility depends on its version and import path; the XML is not a native Jianying draft.

## From soundtrack to deliverable

1. **Set up the project:** import the original song, lyrics/subtitles, and character or scene assets; choose aspect ratio and pass sizes.
2. **Plan the shots:** place cuts and connections on an absolute-frame, 24 fps timeline. The workbench calculates `17N+5` generation lengths plus guide/bleed frames; the overall outline and per-shot prompts use the same plan.
3. **Generate and select:** run shots through your own graph. Successful takes retain their seed, original frame points, and continuation-parent take; failures do not masquerade as usable versions.
4. **Review and deliver:** select takes on a multitrack timeline, preview them with the original song and subtitles, then export a caption-free movie plus SRT or an FCP7 XML package with full source clips and edit markers.

## Installation and dependencies

1. Clone this repository into `ComfyUI/custom_nodes/MiniMaxH3_MV_Planner_xiaoyue`. Cloning the private repository requires GitHub authentication.
2. Install the parts you intend to run using the [custom-node](#custom-nodes-used-by-the-example-workflow), [model](#models-selected-in-the-example-workflow), and [LoRA](#loras-selected-in-the-example-workflow) lists below. The planner and the sample H3 generation graph are separate layers: planning alone does not require every model used by the sample graph.
3. Restart ComfyUI and load the [example workflow](workflows/MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json). In the main node, select **① New/open project**, import the original track and save. Run the workflow once to register the current project's planning UI, then use **② MV workbench → ③ Final review desk**.

Install only what you need: **project planning** does not require the H3 generation models; **AI outline/prompt generation** needs a working QwenTE inference chain and its models; **running the example video graph** additionally needs the video extensions, H3 models, and LoRAs actually enabled in that graph; **automatic subtitle recognition** alone needs optional local ASR.

**You can plan and generate video without a language model.** Leave the main node's `reasoning_provider` input disconnected. You may edit the overall and per-shot outlines yourself and write text in each target shot's “H3 prompt” box. Save the plan, then generate the shot; if the timeline still needs compilation, the workbench saves and compiles it first. Video generation reads that shot's H3 prompt directly and does not call QwenTE. A shot with no H3 prompt cannot be generated. QwenTE Provider is needed only for the “AI generate outline / H3 prompt” actions. The QwenTE nodes shown in the example workflow are an optional inference branch: remove them when not used, and leave `reasoning_provider` unwired.

### Custom nodes used by the example workflow

This list matches the **single published example workflow**. It is not a universal requirement for every planner feature. Install extensions under `ComfyUI/custom_nodes/`, follow each repository's instructions for Python dependencies, and restart ComfyUI. The MV workbench nodes themselves come from this plugin.

| Extension | Role in the example | Installation |
| --- | --- | --- |
| QwenTE | Optional AI outline/prompt inference; not needed for handwritten prompts | [comfyUI-llama-TE](https://github.com/tl2012tl/comfyUI-llama-TE) |
| T8 H3 Audio | Semantic bridge / audio-related nodes | [comfyui-minimax-h3-audio-T8](https://github.com/T8mars/comfyui-minimax-h3-audio-T8) |
| Swan Bits | Audio driving / latent upscaling | [ComfyUI_Swan_Bits](https://github.com/swan7-py/ComfyUI_Swan_Bits) |
| KJNodes | H3 memory optimization / preview | [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) |
| VideoHelperSuite | Video loading | [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) |
| Easy-Use | Index switching / cache clearing | [ComfyUI-Easy-Use](https://github.com/yolain/ComfyUI-Easy-Use) |
| Comfyroll | Prompt text | [ComfyUI_Comfyroll_CustomNodes](https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes) |
| utils-nodes | Text concatenation | [ComfyUI-utils-nodes](https://github.com/zhangp365/ComfyUI-utils-nodes) |
| ComfyLiterals | Float node | [ComfyLiterals](https://github.com/M1kep/ComfyLiterals) |
| ReservedVRAM | VRAM reservation | [ComfyUI-ReservedVRAM](https://github.com/Windecay/ComfyUI-ReservedVRAM) |

The example treats `UNETLoader`, `VAELoader`, `CLIPLoader`, `MiniMaxH3ReferenceToVideo`, samplers, and `SaveVideo` as nodes supplied by the current ComfyUI. If yours lacks them, check your ComfyUI version first.

### Models selected in the example workflow

All paths below are relative to `ComfyUI/models/`, and the **filenames match the published example workflow**. Place downloads at the listed paths. Reuse matching models you already have; model weights are not included in this repository.

| Purpose | Destination relative to `ComfyUI/models/` | Download |
| --- | --- | --- |
| H3 Hybrid diffusion model | `diffusion_models/minimaxh/minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors` | [Hugging Face](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models/resolve/main/minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors) |
| Qwen3-VL text encoder | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | [Hugging Face](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| H3 audio VAE | `vae/minimax_h3_audio_vae_fp32.safetensors` | [Hugging Face](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) |
| H3 video VAE (int8) | `vae/minimax_h3_video_vae_int8_convrot.safetensors` | [Hugging Face](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) |
| T8 semantic bridge | `semantic_bridge/t8_compat/BUNNY_H3_ActionLogic_Bridge_V1_T8_Compat.safetensors` | [Hugging Face](https://huggingface.co/t8star/Semantic-Bridge-Comfy/resolve/main/models/semantic_bridge/t8_compat/BUNNY_H3_ActionLogic_Bridge_V1_T8_Compat.safetensors) |
| H3 latent upscaler | `latent_upscale_models/h3_upscaler_lms_v0.1.safetensors` | [Hugging Face](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI/resolve/main/latent_upscaler/h3_upscaler_lms_v0.1.safetensors) |
| QwenTE main model | `LLM/Q38-27B-G-UD/Qwen3.8-27B-UD-IQ3_XXS.gguf` | [Hugging Face](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-IQ3_XXS.gguf) |
| QwenTE multimodal projector | `LLM/Q38-27B-G-UD/mmproj-Qwen3.8-27B-BF16.gguf` | [Hugging Face](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/mmproj-BF16.gguf); downloaded as `mmproj-BF16.gguf`, so rename it to match the destination |

### LoRAs selected in the example workflow

Place these under `ComfyUI/models/loras/minimaxh3/`. These are filenames selected in the example; exact sources have not all been verified, so no potentially misleading alternative links are supplied. If you use your own LoRAs, update the relevant nodes in the workflow.

- `GunFu.safetensors`
- `H3_Combat_V2.safetensors`
- `H3_speed_slider_1.1.safetensors`
- `Minimax H3真实电影质感V0.1.safetensors`
- `minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors`
- `minimax_h3_lms_v1.0_r64_r2.safetensors`
- `minimax_h3_taomate_3step_lora_avg_rank_19_bf16.safetensors`
- `minimax_h3_turbo_4step_10ErosMax_test4_pruned_curveproj1025_exp_v001-T8.safetensors`
- `minimax_h3_turbo_v4_step600_ema_DasiwaREF2VAHybridV1_0_curveproj1025_compat_v001.safetensors`
- `Motion_Repair.safetensors`

### Optional local ASR / Whisper

For audio-to-subtitle recognition, install [Easy Media](https://github.com/yolain/ComfyUI-Easy-Media) and `openai-whisper` in the Python environment used by ComfyUI. Place [whisper_large_v3_fp16.safetensors](https://huggingface.co/Comfy-Org/HuMo_ComfyUI/resolve/main/split_files/audio_encoders/whisper_large_v3_fp16.safetensors) in `ComfyUI/models/audio_encoders/`. If you already have timestamped lyrics, import them directly; **ASR is optional**. The workbench does not separate vocals; prepare stems in another workflow if needed. The author's private LAN ASR provider is not in the published workflow.

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

## License and third-party content

Original plugin code and documentation in this project are under the [MIT License](LICENSE). You may use, modify, and redistribute them while retaining the copyright and license notice. The example workflow builds on community-shared techniques and calls independently distributed extensions and models. **MIT does not replace the respective licenses of those upstream projects, model weights, LoRAs, or user-supplied media.** Check the source terms before downloading, using, or redistributing third-party content.

## Acknowledgements

This MV workbench owes a great deal to the ComfyUI community's shared knowledge and tools. Special thanks to:

- **黑鹤001** · [Bilibili](https://space.bilibili.com/515231056): the project author learned ComfyUI through his videos from the beginning and still uses a ComfyUI distribution he shared.
- **T8** · [Bilibili](https://space.bilibili.com/385085361) · [GitHub](https://github.com/T8mars?tab=repositories): his shared approach formed the basis of the example two-pass workflow, and his semantic-bridge work also helped this project.
- **Swan鹄仙** · [Bilibili](https://space.bilibili.com/592988861) · [Swan Bits GitHub](https://github.com/swan7-py/ComfyUI_Swan_Bits): the SelfLift split made it possible to connect audio driving as an independent part without installing another large node package solely for that feature.

Thanks also to everyone in the ComfyUI community who shares knowledge, workflows, and open-source work.
