; 自动生成，请勿手改（由 tools/zh/zh.py gen-font 产出）
; 1628 个字形分 4 块存放，每块至多 512 个字形（512 x 32 = 16384 字节 = 0x4000）。
IF DEF(LANG_ZH)

SECTION "Chinese Glyph GFX 0", ROMX
ZhGlyphGFX0::
INCBIN "gfx/font/zh.0.1bpp"
ZhGlyphGFX0End::

SECTION "Chinese Glyph GFX 1", ROMX
ZhGlyphGFX1::
INCBIN "gfx/font/zh.1.1bpp"
ZhGlyphGFX1End::

SECTION "Chinese Glyph GFX 2", ROMX
ZhGlyphGFX2::
INCBIN "gfx/font/zh.2.1bpp"
ZhGlyphGFX2End::

SECTION "Chinese Glyph GFX 3", ROMX
ZhGlyphGFX3::
INCBIN "gfx/font/zh.3.1bpp"
ZhGlyphGFX3End::

ENDC
