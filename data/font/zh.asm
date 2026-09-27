; 简体中文化：12x12 汉字点阵字库。
;
; 字形数据按 bank 分块存放，内容与常量全部由 `python tools/zh/zh.py gen-font` 生成：
;   data/font/zh_glyphs.asm      分块字形数据（每块 <= 0x4000 字节，独立 ROMX 段）
;   data/font/zh_chunk_ptrs.asm  分块基址表   ┐ 由 engine/gfx/cjk_text.asm 引用
;   data/font/zh_chunk_banks.asm 分块 bank 表 ┘ （必须与渲染代码同 bank）
;   data/font/zh_index.asm       字形序号 -> 码点，仅供排查，不编入 ROM
;   constants/zh_font.asm        ZH_GLYPH_COUNT / ZH_GLYPHS_PER_BANK / ...
;
; 仅在 make zh（LANG_ZH）时编入 ROM，英文构建不受影响。
INCLUDE "data/font/zh_glyphs.asm"
