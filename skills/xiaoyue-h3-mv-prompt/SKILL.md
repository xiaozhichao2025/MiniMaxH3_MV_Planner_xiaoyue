---
name: xiaoyue-h3-mv-prompt
description: Create MiniMax H3 prompts for Xiaoyue MV Planner shots, using beat, lyric timing, editorial transitions, and shot-local ordered asset bindings while keeping visible subtitles and lyric typography out of the generated video.
---

# Xiaoyue H3 MV Prompt

Use this Skill to turn one Xiaoyue MV Planner project or shot into a MiniMax H3 generation prompt. The Chinese entrypoint `SKILL.cn.md` is the primary file used by the ComfyUI Skill loader. This English file is the fallback for other Skill runtimes.

## Non-negotiable Rules

- Keep all official H3 field names, labels, task types, and relation values in English exactly as documented.
- Write the final H3 rewrite in English. Preserve the original language only inside `<d>[Language] ...</d>` when exact audible words are required.
- Read the shot's aggregated, user-authored `subtitle_context.ai_dialogue_text` as untimed sung-word context covering the leading extension, formal middle, and trailing extension. Do not derive per-line or per-word shot timing from `subtitle_projections`.
- Treat lyrics and subtitle timestamps as hidden timing and performance cues. Do not create subtitles, captions, lyric typography, karaoke text, title cards, or other on-screen text unless the user explicitly requires diegetic text in that shot.
- Never invent missing lyrics and never turn lyrics into visible text.
- Use only the current shot's ordered `asset_bindings`. Project-library order is never a generation reference order. Rebuild reference labels from 1 for each shot and each official label category.
- Treat Planner frame boundaries and `generation_windows` as authoritative. Do not recalculate or silently move cuts, handles, overlaps, or `17N+5` lengths.
- The original master audio is the final edit source. Do not invent conflicting vocals, dialogue, or music.
- Treat the final `<Audio N>` in shot-local audio input order as the original MV song audio attachment for this shot. User-declared audio attachments, if any, precede it. Describe the song's beat, vocals, exact supplied lyrics, and performance role without adding workflow binding status to the prompt.

## Required Routing

1. Read `references/planner-data-contract.md` when a Planner JSON or shot record is provided.
2. Read `references/mv-shot-language.md` when writing performance, camera, rhythm, hard-cut, continuation, or crossfade behavior.
3. For T2VA, I2VA, FL2VA, or L2VA, read `references/official-base-en.txt` before producing the final prompt.
4. For Ref2VA, read `references/official-ref-en.txt` before producing the final prompt.
5. Use `references/h3-output-protocol.md` as the Chinese routing summary. The bundled English official references remain authoritative when wording differs.

When several references are required for one request, ask the ComfyUI Skill executor to load all of them in the same `load_references` list instead of loading them one by one.

## Workflow

Confirm the active shot, generation mode, exact generation window, transition role, project brief, project-wide prompt and exclusions, the first-pass `creative_plan.overview` and current `shot.creative_outline`, current shot prompt, current shot asset bindings, and relevant lyric cues. The creative overview and shot outline are non-H3 planning inputs; this Skill performs the second-pass shot rewrite. Ask only for information that changes the result.

Build a shot-local reference map, then write the performance and camera plan against the shot's own relative timeline. A hard cut is an independent shot. A long-take boundary continues the preceding tail state. A crossfade is an editorial overlap and should normally be supported by compatible head/tail action rather than by asking H3 to render an opacity dissolve.

Return a short Chinese check summary when useful, followed by one clean English H3 prompt block. Do not leak internal asset IDs into the H3 prompt unless the surrounding workflow requires them for routing.

Before delivery, verify that the official structure is complete, no official identifier was translated, all reference numbering is shot-local, lyrics were not invented, and no visible subtitle or lyric typography instruction remains.

## Sources

Read `references/official-sources.md` for the pinned MiniMax official GitHub source and the scope of this project adaptation.
