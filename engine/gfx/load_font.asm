INCLUDE "gfx/font.asm"

_LoadStandardOpaqueFont::
	ld a, TRUE
	call _LoadStandardMaybeOpaqueFont
	ld hl, vTiles2 tile ' '
	ld de, TextboxSpaceGFX
	jmp GetOpaque1bppFontTile

_LoadStandardFont::
	xor a
_LoadStandardMaybeOpaqueFont:
IF DEF(LANG_ZH)
	; 简体中文化：汉字字形是借字库区（$8800-$8F1F）的瓦片写的，整片重载会把它们
	; 覆盖掉。这里通知引擎"字库被换过了"，下次分配前会按屏幕现状把还该显示的
	; 汉字重传回去（否则换字体/开菜单后汉字会变成别的 ASCII 字符）。
	; a = 本次是不是不透明样式，原样传给钩子；FarCall 会保护 af/bc/de/hl。
	farcall Zh_OnFontReload
ENDC
	push af
	call LoadStandardFontPointer
	ld d, h
	ld e, l
	ld hl, vTiles0 tile 'A'
	lb bc, BANK(FontTiles), 114
	pop af
	ldh [hRequestOpaque1bpp], a
	push af
	call GetMaybeOpaque1bpp
	ld de, FontCommon
	ld hl, vTiles0 tile '↑'
	lb bc, BANK(FontCommon), 6
	pop af
	ldh [hRequestOpaque1bpp], a
	jmp GetMaybeOpaque1bpp

LoadStandardFontPointer::
	ld hl, .FontPointers
	ld a, [wOptions2]
	and FONT_MASK
	ld b, 0
	ld c, a
	add hl, bc
	add hl, bc
	ld a, [hli]
	ld h, [hl]
	ld l, a
	ret

.FontPointers:
	table_width 2
	dw FontNormal
	dw FontNarrow
	dw FontBold
	dw FontItalic
	dw FontSerif
	dw FontChicago
	dw FontMICR
	dw FontUnown
	assert_table_length NUM_FONTS

IF DEF(LANG_ZH)
; 简体中文化：把当前字库里 tile a（$80-$F1）那一格的 1bpp 字形拷回 VRAM 同一格。
; 汉字引擎借字库区的瓦片放汉字，当某一格要还给普通 ASCII 使用时，用这个做定点修复
; —— 只拷这一格，比整片 LoadStandardFont（114 格）便宜得多。
; 必须与本文件的字库数据/ LoadStandardFontPointer 同 bank，所以放在这里。
; 输入：a = 字库区 tile 号（调用方保证落在 $80-$F1 内），样式取 wZhFontOpaque。
; 破坏 a/bc/de/hl —— 调用方走 farcall，其寄存器由 FarCall 负责保护。
Zh_RestoreFontTile::
	push af
	call LoadStandardFontPointer ; hl = 当前字体的 1bpp 数据基址
	pop bc                       ; b = tile 号
	; 源 = 基址 + (tile - $80) * 8（字库区 tile $80 就是 'A' 的字形）
	; 1bpp 字形每格 8 字节，所以这里必须是 off*8 —— 曾经写成连加三次 off（=off*3），
	; 恢复出来的就是错位 3/8 的字形，看起来仍是 ASCII 却跟 ROM 里该格的差了十万八
	; 千里（VRAM 与字库核对时表现为若干格对不上）。
	ld a, b
	sub $80
	ld e, a
	ld d, 0
	ld a, 3 ; de <<= 3
.shift
	sla e
	rl d
	dec a
	jr nz, .shift
	add hl, de
	ld d, h
	ld e, l ; de = 源
	; 目标 = vTiles0 + tile * 16
	ld a, b
	ld l, a
	ld h, 0
	add hl, hl
	add hl, hl
	add hl, hl
	add hl, hl
	ld bc, vTiles0
	add hl, bc ; hl = 目标
	ld a, [wZhFontOpaque]
	ldh [hRequestOpaque1bpp], a
	lb bc, BANK(FontTiles), 1
	jmp GetMaybeOpaque1bpp
ENDC

_LoadFontsBattleExtra::
	ld de, wColoredMaleFemaleShinyTiles
	call CopyColoredMaleFemaleShinyTiles
	ld hl, vTiles2 tile BATTLEEXTRA_GFX_START
	ld de, wColoredMaleFemaleShinyTiles
	lb bc, BANK(@), 3
	call Get2bpp
	ld hl, BattleExtrasGFX
	ld de, vTiles2 tile (BATTLEEXTRA_GFX_START + 3)
	lb bc, BANK(BattleExtrasGFX), 29
	call DecompressRequest2bpp
	; fallthrough

_LoadFrame::
	ld a, [wTextboxFrame]
	ld bc, TEXTBOX_FRAME_TILES * TILE_1BPP_SIZE
	ld hl, Frames
	rst AddNTimes
	ld d, h
	ld e, l
	ld hl, vTiles0 tile '┌'
	lb bc, BANK(Frames), TEXTBOX_FRAME_TILES
	call Get1bpp
	ld hl, vTiles2 tile ' '
	ld de, TextboxSpaceGFX
	lb bc, BANK(TextboxSpaceGFX), 1
	jmp Get1bpp

LoadBattleFontsHPBar:
	call _LoadFontsBattleExtra
	; fallthrough

LoadSummaryStatusIcon:
	push de
	ld de, wTempMonStatus
	call GetStatusConditionOrFaintIndex
	ld hl, SummaryStatusIconGFX
	ld bc, 2 tiles
	rst AddNTimes
	ld d, h
	ld e, l
	ld hl, vTiles0 tile SUMMARY_TILE_OAM_STATUS
	lb bc, BANK(SummaryStatusIconGFX), 2
	call Request2bpp
	farcall LoadSummaryStatusIconPalette
	farcall ApplyOBPals
	pop de
	ret

LoadPlayerStatusIcon:
	push de
	ld de, wBattleMonStatus
	call GetStatusConditionIndex
	ld hl, StatusIconGFX
	ld bc, 2 tiles
	rst AddNTimes
	ld d, h
	ld e, l
	ld hl, vTiles2 tile $55
	lb bc, BANK(StatusIconGFX), 2
	call Request2bpp
	farcall LoadPlayerStatusIconPalette
	pop de
	ret

LoadStatusIcons:
	call LoadPlayerStatusIcon
	; fallthrough

LoadEnemyStatusIcon:
	push de
	ld de, wEnemyMonStatus
	call GetStatusConditionIndex
	ld hl, EnemyStatusIconGFX
	ld bc, 2 tiles
	rst AddNTimes
	ld d, h
	ld e, l
	ld hl, vTiles2 tile $57
	lb bc, BANK(EnemyStatusIconGFX), 2
	call Request2bpp
	farcall LoadEnemyStatusIconPalette
	pop de
	ret

LoadBoldPDoubled:
	; point hl to the '<BOLDP>' 1bpp tile in ROM
	call LoadStandardFontPointer
	ld de, ('<BOLDP>' - $80) * TILE_1BPP_SIZE
	add hl, de
	; copy the bold P, with halves swapped, to the middle char of wSummaryScreenPPTileBuffer
	ld de, wSummaryScreenPPTileBuffer + TILE_1BPP_SIZE
	push de
	ld c, TILE_1BPP_SIZE
.loop
	ld a, [hli]
	swap a
	ld [de], a
	inc de
	dec c
	jr nz, .loop
	pop hl
	; copy the left and right halves to the two other chars of wSummaryScreenPPTileBuffer
	ld c, TILE_1BPP_SIZE
.loop2
	push hl
	ld a, [hl]
	and $0f
	ld de, -TILE_1BPP_SIZE
	add hl, de
	ld [hl], a
	ld de, TILE_1BPP_SIZE
	add hl, de
	ld a, [hl]
	and $f0
	add hl, de
	ld [hl], a
	pop hl
	inc hl
	dec c
	jr nz, .loop2
	; copy the three tiles from wSummaryScreenPPTileBuffer to VRAM
	ld hl, vTiles2 tile SUMMARY_TILE_PP_START
	ld de, wSummaryScreenPPTileBuffer
	lb bc, BANK(@), 3
	jmp Get1bpp

CopyColoredMaleFemaleShinyTiles:
; Copy dark '♂', light '♀', and light '★' to de.
; Must be in VRAM bank 0.
	call LoadStandardFontPointer
	ld bc, ('♂' - $80) * TILE_1BPP_SIZE
	add hl, bc
	call Copy1bppTileAsDark
	assert '♂' + 1 == '♀'
	call Copy1bppTileAsLight
	ld h, d
	ld l, e
	ld de, ShinyIconGFX
	lb bc, BANK(ShinyIconGFX), 1
	jmp Get2bpp

Copy1bppTileAsLight:
; Copy one 1bpp tile from hl to de, with the
; high 2bpp bytes set to $00 to convert black to light.
	ld a, [hli]
	ld [de], a
	inc de
_Copy1bppTileAsColor:
rept 7
	xor a
	ld [de], a
	inc de
	ld a, [hli]
	ld [de], a
	inc de
endr
	xor a
	ld [de], a
	inc de
	ret

Copy1bppTileAsDark:
; Copy one 1bpp tile from hl to de, with the
; low 2bpp bytes set to $00 to convert black to dark.
	call _Copy1bppTileAsColor
	ld a, [hli]
	ld [de], a
	inc de
	ret
