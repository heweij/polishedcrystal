; 简体中文化：汉字 12x12 字形的 tilemap 2x2 渲染核心。
;
; 编码：lead($0a-$4c) + trail($80-$ff)
;   字形序号 i = (lead - ZH_LEAD_START) * 128 + (trail & $7f)
;   尾字节限定在 $80-$ff 的原因见 constants/charmap.asm：这样它就落在原字库的
;   "字面字形" 区，永远不会被 DoTextUntilTerminator / CheckTerminatorChar /
;   DecompressStringToRAM 等逐字节扫描器误当成命令或字符串终止符。
;   tile 顺序固定为 TL, TR, BL, BR，与 tools/zh/zh.py 的 _bitmap_to_tiles 一致。
;   屏幕占用：2x2 个 8x8 tile（16x16 单元），因此行距为 2 个 tile 行。
;
; 字形数据分 bank 存放（每块 ZH_GLYPHS_PER_BANK 个字形，ZH_GLYPH_SIZE = 32 字节/字形）：
;   chunk  = i >> ZH_GLYPH_BANK_SHIFT
;   基址   = ZhGlyphChunkPtrs[chunk]
;   bank   = ZhGlyphChunkBanks[chunk]
;   源地址 = 基址 + (i & (ZH_GLYPHS_PER_BANK - 1)) * ZH_GLYPH_SIZE
; 两张表必须与渲染代码同 bank（本文件被编入 main.asm 的 SECTION FRAGMENT "VWF"，
; 由 home/text.asm farcall 进入），所以它们就放在这里、由生成文件填内容。
;
; =========================== 瓦片池（本文件的核心）==========================
; 汉字不能占用 block 2（$9000-$97FF，tile $00-$7F）：那里是地图 tileset、菜单
; 图标、战斗用的宝可梦图，写进去会把地图涂花（旧实现就是这么坏的）。
; 唯一能安全借用的地方是**字库区**：LCDC 的这一档寻址下 tile $80-$FF 一律指向
; $8800-$8FFF，其中 $80-$F1 是当前字体的 114 个 ASCII 字形，可以随时用
; LoadStandardFont 从 ROM 整片加载回来；$F2-$F7（FontCommon）与 $F8-$FF
; （对话框边框）则固定不动。于是：
;
;   * 池 = tile $80-$F1（ZH_POOL_*）；
;   * 每个槽位占 4 个**连续** tile（一次 Get1bpp 就能传完，见下）；
;   * 借用前先把"现在没人用"的瓦片挑出来，判据有三张：
;       1) 屏幕上正显示着的字符：扫 wTilemap/wAttrmap 的 BG/Win 格子，外加
;          wShadowOAM —— 精灵的 tile $80-$FF 也指向字库区（Celebi 等过场就
;          把图标装进 $8840），借走会把精灵涂花；
;       2) look-ahead（wZhTilePinned）：本次文本串**剩余部分**还会打印的字面字符；
;       3) wZhTileLive：仍被存活槽位占着的瓦片。
;   * wZhTileOurs 记录"内容已经被我们改成字形"的瓦片。它有两个用处：
;       - 分配时优先复用（反正要整块覆写）；
;       - 每次分配前做冲突检测：(used & ours & ~live) != 0 说明屏幕上有格子
;         显示着被我们改坏的 ASCII（漏判的后果），此时逐格把字库 ROM 里对应的
;         那一格字形拷回来（见 ZhFixTile），只有冲突的那几格才拷贝。
;   * 游戏自己重新加载字库（engine/gfx/load_font.asm 里的钩子）会清掉我们的字形
;     内容，用 wZhFontDirty 记一笔，下次分配前按"4 个瓦片是否仍在屏幕上"重传或
;     释放槽位，汉字不会因为换字体、开菜单而消失。
; 结果：汉字只借用字库区，地图/菜单/精灵图不再受影响；万一判漏，下一次分配前就修好。
;
; 字形上传走 home 的 Get1bpp：LCD 关闭时它走 _Copy1bpp 即时拷贝路径，
; LCD 开着时走 Request1bpp 的 HBlank 阻塞拷贝，两种上下文都能用。

IF DEF(LANG_ZH)

; 分块寻址表（内容由 tools/zh/zh.py gen-font 生成）。
ZhGlyphChunkPtrs::
	INCLUDE "data/font/zh_chunk_ptrs.asm"
ZhGlyphChunkPtrsEnd::
ZhGlyphChunkBanks::
	INCLUDE "data/font/zh_chunk_banks.asm"
ZhGlyphChunkBanksEnd::

ASSERT BANK(ZhGlyphChunkPtrs) == BANK(ZhGlyphChunkBanks)

; 位掩码表：bit n 的掩码。
ZhBitMasks::
	db 1, 2, 4, 8, 16, 32, 64, 128

; ===========================================================================
; 小工具
; ===========================================================================

; a = 槽位号 → hl = &wZhGlyphSlots[a*2]。破坏 a/de/hl，保护 bc。
ZhSlotEntryPtr::
	ld l, a
	ld h, 0
	add hl, hl
	ld de, wZhGlyphSlots
	add hl, de
	ret

; a = 槽位号 → hl = &wZhSlotTile[a]。破坏 a/de/hl，保护 bc。
ZhSlotTilePtr::
	ld l, a
	ld h, 0
	ld de, wZhSlotTile
	add hl, de
	ret

; ===========================================================================
; 位图操作（bit n = tile ZH_POOL_FIRST + n）
; 三张位图位序相同，位下标一律用 "tile - ZH_POOL_FIRST"。
; 三个例程都**保护 bc/de/hl** —— 它们要在带活跃循环变量的循环里被调用；
; 只有 a 会被破坏。
; ===========================================================================

; hl = 位图基址，a = 位下标 → 置位。
ZhSetBit::
	push bc
	push de
	push hl
	ld e, a
	srl e
	srl e
	srl e ; e = 字节下标
	ld d, 0
	add hl, de
	and 7
	ld e, a
	push hl
	ld hl, ZhBitMasks
	add hl, de
	ld a, [hl]
	pop hl
	or [hl]
	ld [hl], a
	pop hl
	pop de
	pop bc
	ret

; hl = 位图基址，a = 位下标 → 清位。
ZhResBit::
	push bc
	push de
	push hl
	ld e, a
	srl e
	srl e
	srl e
	ld d, 0
	add hl, de
	and 7
	ld e, a
	push hl
	ld hl, ZhBitMasks
	add hl, de
	ld a, [hl]
	pop hl
	cpl
	and [hl]
	ld [hl], a
	pop hl
	pop de
	pop bc
	ret

; hl = 位图基址，a = 位下标 → 置位则 z = 0，未置位则 z = 1。
ZhTestBit::
	push bc
	push de
	push hl
	ld e, a
	srl e
	srl e
	srl e
	ld d, 0
	add hl, de
	and 7
	ld e, a
	push hl
	ld hl, ZhBitMasks
	add hl, de
	ld a, [hl]
	pop hl
	and [hl]
	pop hl
	pop de
	pop bc
	ret

; ===========================================================================
; 字形源地址
; ===========================================================================

; 算字形 hl 的源地址与 ROM bank，写进 wZhGlyphSrc / wZhGlyphBank。
; 输入：hl = 字形序号；输出：进位 = 1 表示序号越界。破坏 a/bc/de/hl。
ZhGlyphSrcAddr::
	push hl
	ld a, h
	srl a ; ZH_GLYPH_BANK_SHIFT == 9，块号只取决于高字节
	cp ZH_GLYPH_BANK_COUNT
	jr nc, .out_of_range
	ld e, a
	ld d, 0
	; bank = ZhGlyphChunkBanks[chunk]
	ld hl, ZhGlyphChunkBanks
	add hl, de
	ld a, [hl]
	ld [wZhGlyphBank], a
	; 基址 = ZhGlyphChunkPtrs[chunk]
	ld hl, ZhGlyphChunkPtrs
	add hl, de
	add hl, de
	ld a, [hli]
	ld h, [hl]
	ld l, a
	ld a, h
	ld [wZhGlyphSrc + 1], a
	ld a, l
	ld [wZhGlyphSrc], a
	; 源地址 = 基址 + (i mod ZH_GLYPHS_PER_BANK) * ZH_GLYPH_SIZE
	pop de
	ld a, d
	and ZH_GLYPHS_PER_BANK / 256 - 1 ; 只保留块内的高位（512 字形 -> bit 8）
	ld d, a
	ld h, d
	ld l, e
	add hl, hl ; *2
	add hl, hl ; *4
	add hl, hl ; *8
	add hl, hl ; *16
	add hl, hl ; *32
	ld a, [wZhGlyphSrc]
	add a, l
	ld l, a
	ld a, [wZhGlyphSrc + 1]
	adc a, h
	ld h, a
	ld a, h
	ld [wZhGlyphSrc + 1], a
	ld a, l
	ld [wZhGlyphSrc], a
	and a ; 清进位
	ret
.out_of_range
	pop hl
	scf
	ret

; ===========================================================================
; 三张判据位图
; ===========================================================================

; 判据 1：屏幕上已经显示的字符。把被引用的字库区瓦片记进 wZhTileUsed。
;   * BG/Win：扫 wTilemap / wAttrmap。
;   * OBJ：LCDC 的 OBJ 基址是 $8000，所以精灵的 tile $80-$FF 落在同一个字库区
;     （Celebi 等过场就把图标装进 $8840）。这些瓦片同样不能借，否则会把精灵涂花，
;     所以扫 wShadowOAM 的 tile 号。
; 两处都跳过属性位 3 = 1 的项：那读的是 VRAM bank 1，与字库区无关。
ZhScanScreenTiles::
	ld hl, wZhTileUsed
	ld bc, ZH_POOL_BITMAP_BYTES
	xor a
	rst ByteFill
	ld hl, wTilemap
	ld de, wAttrmap
	ld c, SCREEN_HEIGHT
.row
	ld b, SCREEN_WIDTH
.cell
	ld a, [de]
	and ZH_ATTR_VRAM_BANK
	jr nz, .next
	ld a, [hl]
	push hl
	call ZhMarkUsedTile ; 保护 bc/de，破坏 a/hl
	pop hl
.next
	inc hl
	inc de
	dec b
	jr nz, .cell
	dec c
	jr nz, .row
	ld hl, wShadowOAM + 2 ; tile 字节（每项 y, x, tile, 属性）
	ld c, OAM_COUNT
.oam
	ld a, [hli] ; a = tile 号，hl → 属性
	bit 3, [hl]
	jr nz, .oam_next ; bank 1 的精灵与字库区无关
	push hl
	call ZhMarkUsedTile
	pop hl
.oam_next
	inc hl
	inc hl
	inc hl ; 下一项的 tile 字节
	dec c
	jr nz, .oam
	ret

; a = tile 号 → 落在池范围内就记进 wZhTileUsed。
; 破坏 a/hl（调用方自己压栈），保护 bc/de（ZhSetBit 会保护）。
ZhMarkUsedTile::
	sub ZH_POOL_FIRST
	ret c
	cp ZH_POOL_SIZE
	ret nc
	ld hl, wZhTileUsed
	jp ZhSetBit

; 判据 2：本次文本串剩余部分还会打印的字面字符。
; 从 wZhTextPtr 起向后扫：
;   <DONE>($52) / "@"($53) 结束；
;   lead($0a-$4c) 后跟 1 个尾字节，整对跳过（汉字自己会占新瓦片）；
;   其余落在 $80-$F1 的字节是字面字符（tile 号就是字节值），钉进 wZhTilePinned；
;   遇到会展开成运行时字符串的控制码，就把整套字母数字钉住（见 ZhPinNameChars）。
; 破坏 a/bc/de/hl —— 调用方要自己压栈保住需要的东西。
ZhScanTextPins::
	ld hl, wZhTilePinned
	ld bc, ZH_POOL_BITMAP_BYTES
	xor a
	rst ByteFill
	ld hl, wZhTextPtr
	ld a, [hli]
	ld e, a
	ld a, [hl]
	ld d, a
	ld c, 200 ; 防御：坏串最多看 200 字节
.loop
	call ZhGetTextByte ; a = 该字节（按源 bank 取，不能直接用 [de]）
	inc de
	cp ZH_CHAR_DONE
	ret z
	cp ZH_CHAR_TERMINATOR
	ret z
	cp ZH_LEAD_START
	jr c, .control
	cp ZH_LEAD_END + 1
	jr nc, .literal
	inc de ; 汉字：连尾字节一起跳过
	jr .next
.control
	cp ZH_CHAR_RAM
	jr z, .pin_names
	cp ZH_CMD_ASM
	jr z, .pin_names
	cp ZH_CMD_NUM
	jr z, .pin_names
	cp ZH_CMD_DAY
	jr z, .pin_names
	cp ZH_CMD_FAR
	jr z, .pin_names
	cp ZH_CMD_PLURAL
	jr z, .pin_names
	cp ZH_NGRAM_HASH
	jr z, .pin_hash
	cp ZH_NGRAM_MON
	jr z, .pin_hash
	cp ZH_NGRAM_PLAYER
	jr z, .pin_names
	cp ZH_NGRAM_RIVAL
	jr z, .pin_names
	cp ZH_NGRAM_TRENDY
	jr z, .pin_names
	cp ZH_CHAR_TARGET
	jr z, .pin_names
	cp ZH_CHAR_USER
	jr z, .pin_names
	cp ZH_CHAR_ENEMY
	jr z, .pin_names
.literal
	cp ZH_POOL_FIRST
	jr c, .next
	cp ZH_POOL_LAST + 1
	jr nc, .next
	sub ZH_POOL_FIRST
	ld hl, wZhTilePinned
	call ZhSetBit ; 保护 bc/de/hl，只有 a 被破坏
	jr .next
.pin_hash
	; "#" 与 "#mon" 展开成 POKéMON 一类的固定串（可见字符不在字节流里）
	ld hl, .HashChars
	push bc ; c 是下面的扫描预算，钉字符的例程会用到 c
	call ZhPinChars
	pop bc
	jr .next
.pin_names
	push bc
	call ZhPinNameChars
	pop bc
.next
	dec c
	jr nz, .loop
	ret

.HashChars:
	db "P", "O", "K", "é", "M", "N"
	db 0

; hl = 以 0 结尾的字符表 → 把这些瓦片钉住。破坏 a/hl，保护 bc/de。
ZhPinChars::
.loop
	ld a, [hli]
	and a
	ret z
	push hl
	sub ZH_POOL_FIRST
	ld hl, wZhTilePinned
	call ZhSetBit
	pop hl
	jr .loop

; 把整套字母、数字与常见标点钉住：名字/星期/数字这类串运行时才拼出来，
; look-ahead 看不见。破坏 a/hl，保护 bc/de。
ZhPinNameChars::
	ld a, $80 ; 'A'（tile 号就是 charmap 里的字面字形，见 constants/charmap.asm）
	ld c, 26  ; A-Z
	call ZhPinRange
	ld a, $a0 ; 'a'
	ld c, 26  ; a-z
	call ZhPinRange
	ld a, $e0 ; '0'
	ld c, 10  ; 0-9
	call ZhPinRange
	ld hl, .PunctChars
	jp ZhPinChars
.PunctChars:
	db ".", "-", "!", "?"
	db 0

; 从 tile a 开始连续钉 c 个。破坏 a，保护 bc/de/hl（c 是输入的个数，循环里压栈恢复）。
ZhPinRange::
.loop
	push af
	push bc
	sub ZH_POOL_FIRST
	ld hl, wZhTilePinned
	call ZhSetBit
	pop bc
	pop af
	inc a
	dec c
	jr nz, .loop
	ret

; 判据 3：仍被存活槽位占用的瓦片。由 wZhSlotTile 重建 wZhTileLive。
ZhBuildLive::
	ld hl, wZhTileLive
	ld bc, ZH_POOL_BITMAP_BYTES
	xor a
	rst ByteFill
	ld hl, wZhSlotTile
	ld c, ZH_SLOT_COUNT
.slot
	ld a, [hli]
	cp $ff
	jr z, .next
	push hl
	ld e, a
	ld d, ZH_GLYPH_TILES
	ld hl, wZhTileLive
.tile
	ld a, e
	sub ZH_POOL_FIRST
	call ZhSetBit ; 保护 bc/de/hl
	inc e
	dec d
	jr nz, .tile
	pop hl
.next
	dec c
	jr nz, .slot
	ret

; ===========================================================================
; 重新同步
; ===========================================================================

; 重建槽位：字库区刚被整片恢复成 ASCII，所有槽位的字形内容都没了。
; 对每个已占用的槽位：
;   * 4 个瓦片仍然全部被屏幕引用 → 字形还完整显示着 → 重新上传；
;   * 否则 → 屏幕上已经看不到它了 → 释放槽位（那些瓦片刚变回干净的 ASCII，
;     所以顺手把 wZhTileOurs 的标记清掉）。
ZhRebuildSlots::
	ld hl, wZhTileOurs
	ld bc, ZH_POOL_BITMAP_BYTES
	xor a
	rst ByteFill
	ld c, 0 ; c = 槽位号
.slot
	push bc
	ld a, c
	call ZhSlotEntryPtr
	ld a, [hli]
	ld d, a
	ld a, [hl]
	or d
	inc a ; $ffff → 0
	pop bc
	jr z, .next
	; 该槽位占用的基址
	push bc
	ld a, c
	call ZhSlotTilePtr
	ld a, [hl]
	pop bc
	cp $ff
	jr z, .free
	ld e, a ; e = 基址
	ld d, ZH_GLYPH_TILES
.check
	ld a, e
	sub ZH_POOL_FIRST
	ld hl, wZhTileUsed
	call ZhTestBit ; 保护 bc/de/hl
	jr z, .free    ; 有一个瓦片没被引用 → 字形不在屏幕上
	inc e
	dec d
	jr nz, .check
	; 仍然完整显示 → 重新上传，并把它记回 wZhTileOurs
	push bc
	ld a, c
	call ZhSlotEntryPtr
	ld a, [hli]
	ld h, [hl]
	ld l, a ; hl = 字形序号
	ld a, c
	call ZhUploadGlyphToSlot
	pop bc
	ld a, c
	call ZhSlotTilePtr
	ld a, [hl]
	ld e, a
	ld d, ZH_GLYPH_TILES
	ld hl, wZhTileOurs
.mark
	ld a, e
	sub ZH_POOL_FIRST
	call ZhSetBit
	inc e
	dec d
	jr nz, .mark
	jr .next
.free
	ld a, c
	call ZhSlotTilePtr
	ld a, [hl]
	cp $ff
	; "槽位有字形却没记基址"也会跳到这里。此时绝不能拿 $ff 当基址去清位图：
	; $ff - $80 = 127 已经越出池子的 114 格，会把位图后面的内存写坏。
	jr z, .free_slot
	ld e, a
	ld d, ZH_GLYPH_TILES
	ld hl, wZhTileOurs
.unmark
	ld a, e
	sub ZH_POOL_FIRST
	call ZhResBit
	inc e
	dec d
	jr nz, .unmark
.free_slot
	ld a, c
	call ZhFreeSlot
.next
	inc c
	ld a, c
	cp ZH_SLOT_COUNT
	jp c, .slot
	ret

; 释放槽位 a（只改记账，不动 VRAM）。
; 说明：不碰 wZhTileOurs。被释放的瓦片内容此刻仍可能是我们的字形（调用点
; Zh_ResetSlots 就是这样），留着标记，冲突检测才能在有人要用回那段 ASCII 时
; 发现并修复；真到了字库被重载的场合，ZhRebuildSlots 会负责清标记。
; wZhTileLive 则要跟着清 —— 它的含义就是"被存活槽位占着"，槽位没了它必须没，
; 否则会留下孤儿：ZhAllocSlotTiles 以为那些瓦片不可借，池子被无谓占住。
; （分配那一侧在 Zh_GetOrLoadSlot 里同步标 live，两边对称。）
ZhFreeSlot::
	push af
	push bc
	call ZhSlotEntryPtr
	ld a, $ff
	ld [hli], a
	ld [hl], a
	pop bc
	pop af
	push af
	push bc
	push de
	call ZhSlotTilePtr
	ld a, [hl]
	cp $ff
	jr z, .no_live ; 基址都没记录过，自然没有 live 要清
	ld e, a
	ld d, ZH_GLYPH_TILES
	ld hl, wZhTileLive
.unmark
	ld a, e
	sub ZH_POOL_FIRST
	call ZhResBit ; 保护 bc/de/hl
	inc e
	dec d
	jr nz, .unmark
.no_live
	pop de
	pop bc
	pop af
	push af
	push bc
	call ZhSlotTilePtr
	ld [hl], $ff
	pop bc
	pop af
	ret

; 冲突修复。两类需要处理的情况：
;   1) (wZhTileUsed & wZhTileOurs & ~wZhTileLive) != 0
;      屏幕上有格子正显示着被我们改坏的 ASCII（典型来源：上一段对话借过这些瓦片，
;      这一段要把它们当普通字符用）。这些瓦片已经不属于任何槽位，逐格把字库 ROM 里
;      对应那一格 1bpp 字形拷回来、清掉 ours 就修好了 —— 只拷冲突的那几格，
;      比整片重载字库（114 格）便宜得多，也不会让屏幕上其他汉字闪一下。
;   2) wZhFontDirty（游戏自己重载过字库）：我们的字形内容全没了，交给
;      ZhRebuildSlots 按"4 个瓦片是否仍在屏幕上"重传或释放槽位。
ZhCheckResync::
	ld c, ZH_POOL_BITMAP_BYTES
.chk
	dec c
	ld b, 0
	ld hl, wZhTileOurs
	add hl, bc
	ld a, [hl]
	and a
	jr z, .next ; 这一段没有我们改过的瓦片
	ld d, a
	ld hl, wZhTileUsed
	add hl, bc
	ld a, [hl]
	and d
	jr z, .next
	ld d, a
	ld hl, wZhTileLive
	add hl, bc
	ld a, [hl]
	cpl
	and d ; a = 需要修复的瓦片位掩码
	jr z, .next
	ld e, a ; e = 掩码（ZhFixTile 保护 de）
	ld d, 0 ; d = 位下标
.fix
	srl e
	jr nc, .fix_next
	ld a, c
	add a, a
	add a, a
	add a, a ; c * 8
	add a, d ; + 位下标 = tile - ZH_POOL_FIRST
	; 位图是按字节存的，最后一段字节的高位会算出超出池子的下标（池子不是 8 的
	; 整数倍）。拿越界下标去调 ZhFixTile，它会算出池外的 tile 号并写坏别的内存。
	cp ZH_POOL_SIZE
	jr nc, .fix_next
	call ZhFixTile
.fix_next
	inc d
	ld a, d
	cp 8
	jr c, .fix
.next
	ld a, c
	and a
	jr nz, .chk
	ld a, [wZhFontDirty]
	and a
	ret z
	call ZhRebuildSlots
	xor a
	ld [wZhFontDirty], a
	ret

; 定点修复一格：清掉 ours 标记，并把当前字库里同一格的 ASCII 字形拷回 VRAM。
; 输入：a = 位下标（tile - ZH_POOL_FIRST）。破坏 a/hl，保护 bc/de（与 ZhSetBit 同规矩）。
;
; 这里再守一道：只修"无主"的格子。仍被存活槽位占着的格子是正在显示的汉字，
; 把它当成"上一段对话改坏后遗留的 ASCII"修掉，等于把屏幕上的字直接抹成 ASCII
; —— 实测正是这么丢字的（tile $82 属于槽位 0，被修成了 ASCII 'C'）。
; 上面 ZhCheckResync 的掩码看着已经排除了 live，但位图与槽位表之间仍可能因为
; 时序出现空档，所以在真正动手前按 live 再确认一次。
ZhFixTile::
	ld [wZhTmp], a
	ld hl, wZhTileLive
	call ZhTestBit ; 保护 bc/de/hl；返回后 Z 反映这一位是否为 0
	jr nz, .owned ; live 置位 → 还有槽位占着它，不许动
	ld hl, wZhTileOurs
	ld a, [wZhTmp]
	call ZhResBit
	ld a, [wZhTmp]
	add a, ZH_POOL_FIRST
	; Zh_RestoreFontTile 会破坏 bc/de（lb bc + GetMaybeOpaque1bpp），而调用方
	; ZhCheckResync 的 c/d/e 要跨本次调用继续用，所以这里必须保住它们。
	; （FarCall 只保证"调度过程"不弄乱寄存器，被调方照样随便破坏。）
	push bc
	push de
	farcall Zh_RestoreFontTile ; 参数走 a
	pop de
	pop bc
.owned
	ret

; ===========================================================================
; 分配与上传
; ===========================================================================

; 找一个空槽位用的 4 个连续瓦片。
; 空闲 = 不在 wZhTileUsed / wZhTilePinned / wZhTileLive。
; 输出：a = 基址 tile；进位 = 1 表示池里已没有连续 4 个可借的瓦片。
; 破坏 a/bc/hl，保护 de（ZhTestBit 负责）。
ZhAllocSlotTiles::
	ld b, ZH_POOL_FIRST
.window
	xor a
	ld c, a ; c = 窗口内偏移
.tile
	ld a, b
	add a, c
	sub ZH_POOL_FIRST
	cp ZH_POOL_SIZE
	jr nc, .next
	ld hl, wZhTileUsed
	call ZhTestBit ; 保护 bc/de/hl
	jr nz, .next
	ld a, b
	add a, c
	sub ZH_POOL_FIRST
	ld hl, wZhTilePinned
	call ZhTestBit
	jr nz, .next
	ld a, b
	add a, c
	sub ZH_POOL_FIRST
	ld hl, wZhTileLive
	call ZhTestBit
	jr nz, .next
	inc c
	ld a, c
	cp ZH_GLYPH_TILES
	jr c, .tile
	ld a, b
	and a ; 清进位
	ret
.next
	inc b
	ld a, b
	cp ZH_POOL_LAST + 1 - ZH_GLYPH_TILES + 1
	jr c, .window
	scf
	ret

; 把字形 hl 上传到槽位 a 的 4 个连续瓦片。
; 输入：a = 槽位号，hl = 字形序号
; 输出：a = 槽位号；进位 = 1 表示字形序号越界
; 说明：4 个瓦片连续，所以只要一次 Get1bpp（count = 4）；LCD 开着时它走
;       Request1bpp 的 HBlank 阻塞拷贝，返回时字形已经在 VRAM 里。
ZhUploadGlyphToSlot::
	ld [wZhGlyphSlot], a
	call ZhGlyphSrcAddr
	ret c
	ld a, [wZhGlyphSlot]
	call ZhSlotTilePtr
	ld a, [hl] ; 基址 tile
	ld l, a
	ld h, 0
	add hl, hl
	add hl, hl
	add hl, hl
	add hl, hl ; *16
	ld de, vTiles0
	add hl, de ; hl = 目标
	ld a, [wZhGlyphSrc]
	ld e, a
	ld a, [wZhGlyphSrc + 1]
	ld d, a
	ld a, [wZhGlyphBank]
	ld b, a
	ld c, ZH_GLYPH_TILES
	call Get1bpp
	ld a, [wZhGlyphSlot]
	and a
	ret

; ===========================================================================
; 对外接口
; ===========================================================================

; 清空槽位表（不动 VRAM，不清 wZhTileOurs，原因见 ZhFreeSlot）。
; 调用点：清空对话框时（home/text.asm 的 ClearSpeechBox）、开机初始化。
Zh_ResetSlots::
	ld hl, wZhGlyphSlots
	ld bc, ZH_SLOT_COUNT * 2
	ld a, $ff
	rst ByteFill
	ld hl, wZhSlotTile
	ld bc, ZH_SLOT_COUNT
	ld a, $ff
	rst ByteFill
	; live 的含义是"当前有槽位占着的瓦片"，槽位表已全清，它必须跟着清，
	; 否则留下孤儿：ZhAllocSlotTiles 以为那些瓦片不可借，池子被无谓占住。
	ld hl, wZhTileLive
	ld bc, ZH_POOL_BITMAP_BYTES
	xor a
	rst ByteFill
	ret

; 按屏幕现状释放已经看不到的槽位（ClearSpeechBox 调用）。
; 对话框关掉后，对话字不再显示，它们的槽位该释放、占的瓦片该恢复成字库 ——
; 否则下一段文本（尤其是没翻译、不走汉字分配的英文）会把残留字形当 ASCII
; 用，屏幕上就是乱码。
; 但不能无脑全清：菜单这类界面的字和对话框共用池子，对话框更新时菜单字还
; 在屏幕上 —— 4 个瓦片只要还有一个被引用，就说明这个字还看着，槽位必须
; 原样保留。判定前先实时重建 used 快照（ZhScanScreenTiles）：快照平时只在
; 分配时重建，此刻早已过期。
Zh_ReleaseHiddenSlots::
	call ZhScanScreenTiles
	ld c, ZH_SLOT_COUNT
	xor a
.slot
	ld [wZhTmp], a
	call ZhSlotTilePtr ; a = 槽位号，返回 hl 指基址字节（保护 af/bc/de）
	ld a, [hl]
	cp $ff
	jr z, .next
	push af ; 基址，release 段要用
	ld e, a
	ld d, ZH_GLYPH_TILES
.chk
	ld a, e
	sub ZH_POOL_FIRST
	push de
	ld hl, wZhTileUsed
	call ZhTestBit ; Z = 该位为 0，即屏幕没在引用
	pop de
	jr z, .release
	inc e
	dec d
	jr nz, .chk
	pop af ; 4 格全在屏幕上 → 字还看着，槽位保留
	jr .next
.release
	pop af ; a = 基址
	ld e, a
	ld d, ZH_GLYPH_TILES
.tile
	ld a, e
	push de
	push bc ; c 是外层槽位循环的计数器：Zh_RestoreFontTile 会破坏 bc，
	        ; 不保住的话本槽位释放后循环会在错误的 c 下继续跑，槽位号越界
	        ; （实测跑到 24：ZhSlotEntryPtr(24) 落在 wZhSlotTile[0..1] 上，
	        ; 把存活槽位的基址写成 $ff —— 屏幕上随即出现乱码字形）。
	farcall Zh_RestoreFontTile ; 残留字形换回字库
	pop bc
	pop de
	ld a, e
	sub ZH_POOL_FIRST
	push de
	ld hl, wZhTileOurs
	call ZhResBit ; 内容已是字库，"我们改过"不再成立
	pop de
	inc e
	dec d
	jr nz, .tile
	ld a, [wZhTmp]
	call ZhFreeSlot ; 清 entry/基址/live
.next
	ld a, [wZhTmp]
	inc a
	dec c
	jr nz, .slot
	ret

; 初始化/重置引擎状态。
; 两个调用点都必须有，因为这两处都会把 WRAM 填成 0：
;   * 开机：engine/init.asm 的 ClearWRAM 之后；
;   * 新游戏：engine/menus/intro_menu.asm 的 ResetWRAM —— 它清的
;     wMusicEnd..wOptions3 正好盖住本文件在 ram/wram0.asm 里的全部状态。
; 而 0 是合法字形序号，所以必须显式把槽位表清成 $ffff 并把四张位图清零；
; wZhFontDirty 置 1，让第一次分配前先按屏幕现状对齐一次（那时槽位表是空的，
; 实际只会把标志位清掉），保证记账与 VRAM 内容一致。
; 四张位图在 ram/wram0.asm 里是连续声明的，故可以一次循环清完。
Zh_InitState::
	call Zh_ResetSlots
	xor a
	ld hl, wZhTileUsed
	ld c, 4 ; wZhTileUsed / wZhTilePinned / wZhTileLive / wZhTileOurs
.clear
	push bc
	push hl
	ld b, 0
	ld c, ZH_POOL_BITMAP_BYTES
	rst ByteFill
	pop hl
	pop bc
	ld de, ZH_POOL_BITMAP_BYTES
	add hl, de
	dec c
	jr nz, .clear
	ld hl, wZhGlyphSlot
	ld bc, 9 ; Slot / Src(2) / Bank / TextPtr(2) / Tmp / FontOpaque / FontDirty
	xor a
	rst ByteFill
	ld a, 1
	ld [wZhFontDirty], a
	ret

; 取字形 hl 的槽位号；未载入则分配空槽位、借 4 个瓦片并把字形上传。
; 输入：hl = 字形序号（0 .. ZH_GLYPH_COUNT-1）、wZhTextPtr = 当前文本指针
; 输出：a = 槽位号；进位 = 1 表示序号越界 / 槽位或瓦片池已满（调用方画空白占位）
; 要求：VRAM 可写（LCD 关闭），或由 Get1bpp 自己等 HBlank。
Zh_GetOrLoadSlot::
; 越界保护
	ld a, h
	cp HIGH(ZH_GLYPH_COUNT)
	jr c, .in_range
	jp nz, .fail ; .fail 在函数末尾，够不到 jr 的 ±127
	ld a, l
	cp LOW(ZH_GLYPH_COUNT)
	jr c, .in_range
	jp .fail

.in_range
	ld b, h
	ld c, l
	push bc
	call ZhScanScreenTiles
	call ZhBuildLive
	call ZhCheckResync ; 可能修好被改坏的 ASCII 瓦片，或重传/释放槽位
	call ZhBuildLive   ; 槽位表可能被改过，重建
	pop bc

	; --- 1) 该字形是否已载入 ---
	ld hl, wZhGlyphSlots
	ld e, 0
.scan
	ld a, [hli]
	cp c
	jr nz, .scan_next
	ld a, [hl]
	cp b
	jr z, .found
.scan_next
	inc hl
	inc e
	ld a, e
	cp ZH_SLOT_COUNT
	jr c, .scan

	; --- 2) 找一个空槽位 ---
	ld hl, wZhGlyphSlots
	ld e, 0
.free
	ld a, [hli]
	inc a ; $ff + 1 == 0
	jr nz, .free_next
	ld a, [hl]
	inc a
	jr z, .have_slot
.free_next
	inc hl
	inc e
	ld a, e
	cp ZH_SLOT_COUNT
	jr c, .free
	jr .fail

.have_slot
	dec hl
	ld [hl], c
	inc hl
	ld [hl], b
	push bc ; bc = 字形序号：下面两个例程都会把 bc 当循环变量用
	push de ; de = 槽位号：ZhScanTextPins 会把它当扫描指针用，必须自己保住
	call ZhScanTextPins ; look-ahead
	call ZhAllocSlotTiles
	pop de  ; 恢复槽位号（pop 不影响标志位）
	pop bc
	jr c, .release ; 上面 pop 不改标志位，进位仍然有效
	push de
	ld d, 0
	ld hl, wZhSlotTile
	add hl, de
	ld [hl], a ; 记录基址
	ld e, a
	ld d, ZH_GLYPH_TILES
.mark
	ld a, e
	sub ZH_POOL_FIRST
	ld hl, wZhTileOurs
	call ZhSetBit
	; live 必须在这里就跟着标上，不能留到下次 ZhBuildLive 去补。
	; 否则 live 会滞后于槽位表（刚分配的 4 格还不在 live 里），ZhCheckResync
	; 的判据 used & ours & ~live 就会把刚显示出来的汉字当成"上一段对话改坏后
	; 遗留的 ASCII"给定点修复掉 —— 屏幕上的字会被自己抹成 ASCII。
	ld a, e
	sub ZH_POOL_FIRST
	ld hl, wZhTileLive
	call ZhSetBit
	inc e
	dec d
	jr nz, .mark
	pop de
	; --- 3) 上传 ---
	push de
	ld h, b
	ld l, c
	ld a, e
	call ZhUploadGlyphToSlot
	pop de
	jr c, .release ; 理论上到不了（序号前面已校验），真出错就别留下半截状态
	ld a, e
	and a ; 清进位
	ret

.release
	; 池子里没有连续 4 个瓦片：把刚占上的槽位还回去
	push bc
	ld a, e
	call ZhFreeSlot
	pop bc

.fail
	scf
	ret

.found
	ld a, e
	and a ; 清进位
	ret

; 在 wTilemap（行宽 = SCREEN_WIDTH）上画出槽位 a 的汉字，并把 hl 右移 2 格。
; 输入：hl = wTilemap 坐标，a = 槽位号
; 输出：hl = 坐标 + 2，进位 = 0
Zh_DrawGlyphToTilemap::
	push bc
	push de
	push hl
	call ZhSlotTilePtr
	ld a, [hl]
	pop hl
	ld [hl], a ; 左上
	inc a
	inc hl
	ld [hl], a ; 右上
	inc a
	; hl 此刻指向右上格：退一列再加一整行才是左下格
	; （若直接 add SCREEN_WIDTH，会写到右边一列去，整个字形下半截右移一格）
	push hl
	ld de, SCREEN_WIDTH - 1
	add hl, de
	ld [hl], a ; 左下
	inc hl
	inc a
	ld [hl], a ; 右下
	pop hl ; hl = 右上格（坐标 + 1）
	inc hl ; hl = 坐标 + 2：一个汉字占 2 个 tile 列，与 tools/zh 的 CJK_TILES 一致
	pop de
	pop bc
	and a ; 清进位
	ret

; home/text.asm 的 PlaceNextChar 在遇到汉字时 farcall 到这里。
; 输入：de = 文本指针（指向 lead 字节），hl = wTilemap 坐标
; 输出：无返回值；推进靠改寄存器本身（farcall 返回时寄存器原样保留，见下）。
;
; 关于 farcall 的寄存器语义（实测确认，别被命名误导）：
;   FarCall 的 "Preserves af, bc, de, hl" 指的是**进入被调用方时**寄存器还是调用方
;   的原值（而不是被中间代码弄乱的），被调用方对这些寄存器的修改**不会**被还原 ——
;   这正是本文件能靠改 de/hl 把"文本指针 +1 个汉字、坐标右移 2 格"带回调用方的原因
;   （home/text.asm 那边 farcall 之后直接 jp PlaceNextChar，不再自己 inc de/inc hl）。
;   反过来，如果哪天真给 farcall 加了"返回后还原调用方寄存器"的包装，这里的推进就全丢了：
;   表现是同一个 lead 字节被反复读、文字卡死不前进。
; 唯一必须自己保住的是入口的 hl：算字形序号要借用 hl，坐标必须先入栈，
;   否则字形会被写进 "以字形序号当地址" 的地方（落在 ROM 区、写入无效），
;   表现是汉字完全不显示、只剩空白。
; 取文本串当前位置的一个字节：a = [de]，按 wZhTextBank 记录的源 bank。
; 本文件整体装在 ROMX 上，跑到这里时 CPU 正在引擎自己的 bank 里，直接 [de]
; 读到的会是该 bank 的同址数据（代码或图形数据）而不是文本 —— 那是片头冒汉字、
; 算出越界字形序号的根因。必须按 wZhTextBank 记的源 bank 取远端字节。
;
; 走 GetFarByte（home/farcall.asm）：它的取指代码在 ROM0，切换窗口内即使来中断
; 也不会取错指令，且 _ReturnFarCall 会自己切回 bank —— 比在这里手动 rst Bankswitch
; 稳妥（手动切会在 LCD 期间留下可被打断的窗口）。它只破坏 a，de/hl 都被保护。
;
; 返回值必须一路留在 a 里：de/hl 都要用 push/pop 归还给调用方，而 pop 会把暂存
; 它的寄存器一起覆盖掉 —— 曾经就是 "先 ld e,a 再 pop de" 而白读一场，传出来的
; 其实是 de 的低字节（表现为屏幕上按顺序冒出一串乱码汉字）。
ZhGetTextByte::
	push hl
	ld h, d
	ld l, e
	ld a, [wZhTextBank]
	call GetFarByte      ; a = [de]@源bank；只破坏 a
	pop hl
	ret

Zh_PlaceNextCharCJK::
	push hl ; 先存坐标：下面 hl 要用来算字形序号
	; 字形序号 = (lead - ZH_LEAD_START) * 128 + (trail & $7f)
	; 注意必须先把 lead 差值放进低字节再左移 7 次，得到 delta * 128；
	; 若写成 `ld h,a / ld l,0`（delta << 8）再左移 7 次会得到 delta << 15，
	; 只有 delta = 0 的汉字能碰巧算对，其余全部越界。
	call ZhGetTextByte ; a = lead
	sub ZH_LEAD_START
	ld l, a
	ld h, 0
	add hl, hl ; *2
	add hl, hl ; *4
	add hl, hl ; *8
	add hl, hl ; *16
	add hl, hl ; *32
	add hl, hl ; *64
	add hl, hl ; *128
	inc de      ; 指向 trail
	call ZhGetTextByte ; a = trail
	and ZH_TRAIL_MASK
	add a, l    ; 低 7 位恒为 0，不会进位
	ld l, a     ; hl = 字形序号
	inc de      ; 指向下一个字节（本函数负责推进 de）
	; 当前文本指针要留给 look-ahead（ZhScanTextPins）：Zh_GetOrLoadSlot 会大量
	; 使用 de，所以推进好的指针先存进 WRAM，返回给调用方的 de 则靠压栈保命。
	push de
	ld a, e
	ld [wZhTextPtr], a
	ld a, d
	ld [wZhTextPtr + 1], a
	call Zh_GetOrLoadSlot
	pop de
	pop hl      ; hl = wTilemap 坐标（pop 不影响标志位，下面的 jr c 仍有效）
	jr c, .blank
	call Zh_DrawGlyphToTilemap ; a = 槽位号；hl 右移 2 格
.delay
	; 打字延迟在这边做：PrintLetterDelay 在 ROM0，跨 bank 直接 call 即可。
	call PrintLetterDelay
	ret
.blank
	; 字形越界 / 槽位或瓦片池已满：占位 2 格，保持后续文字对齐
	inc hl
	inc hl
	jr .delay

; 游戏自己重新加载了标准字库（engine/gfx/load_font.asm 里的钩子）。
; 整片字库区（含我们写的字形）刚被覆盖，标记需要重传；字库区现在又是干净的
; ASCII，所以把 wZhTileOurs 一并清空。真正的重传留到下次分配前统一做，
; 那时屏幕扫描的结果才知道哪些槽位还该救回来。
; 输入：a = 本次加载的是不是"不透明"样式（0 = 透明）。
Zh_OnFontReload::
	ld [wZhFontOpaque], a
	ld a, 1
	ld [wZhFontDirty], a
	ld hl, wZhTileOurs
	ld bc, ZH_POOL_BITMAP_BYTES
	xor a
	rst ByteFill
	ret

; --- 演示画面（阶段一验证脚手架）------------------------------------------
; 走真实文本引擎把示例中文串显示在标准对话框里：SpeechTextbox 打边框，
; PlaceString 走 PlaceNextChar -> Zh_PlaceNextCharCJK 渲染，最后 ApplyTilemap 上传。
; 正式接入主线后即可删除本段与 Makefile 里的 -DZH_DEMO。
IF DEF(ZH_DEMO)

Zh_DemoScreen::
	call DisableLCD
	xor a
	ldh [rVBK], a
	ldh [hBGMapMode], a
	call Zh_ResetSlots

	; 清空 wTilemap。汉字只借用字库区（$8800 起），与 block 2 的 tileset 无关，
	; 所以这里不再需要清 vTiles2 —— 清掉反而会抹掉地图资源。
	hlcoord 0, 0
	ld bc, wTilemapEnd - wTilemap
	ld a, ' '
	rst ByteFill

	; 打开 LCD：之后的字形加载走 HBlank 阻塞拷贝、tilemap 走 VBlank 上传，
	; 与真实游戏显示文本时完全同一条路径。
	call Zh_DemoPalette
	ld a, LCDC_DEFAULT
	ldh [rLCDC], a

	; 载入对话框边框（正常流程由 LoadFrame 完成，这里在 init 阶段手动补上，
	; 否则边框会是 ClearVRAM 后的空白图案）。
	call LoadFrame

	; 走真实文本引擎：PlaceString -> PlaceNextChar。这样 demo 校验的就是正式
	; 集成路径（含 <LINE>/<NEXT> 等控制码分派），而不是 demo 专用的简化打印器。
	xor a
	ld [wTextboxFlags], a ; 关掉打字延迟，demo 屏一次性铺完
	hlcoord 0, 0
	ld de, ZhText_Sample_Marker
	call PlaceString

	call SpeechTextbox
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY
	ld de, ZhText_Sample_Demo
	call PlaceString
	call ApplyTilemapInVBlank

; VBlank 处理程序可能把调色板重置为全白，故每帧重申一次；画面本身保持静态。
.loop
	halt
	call Zh_DemoPalette
	jr .loop

; 白底黑字：DMG 用 rBGP，CGB 用 BG 调色板 0。
Zh_DemoPalette::
	ld a, %11100100
	ldh [rBGP], a
	ld a, %10000000
	ldh [rBCPS], a
	ld hl, .colors
	ld c, 8
.load
	ld a, [hli]
	ldh [rBCPD], a
	dec c
	jr nz, .load
	ret

.colors:
	; BGR555，低字节在前
	dw $7fff ; 0 白（底色）
	dw $56b5 ; 1 浅灰
	dw $294a ; 2 深灰
	dw $0000 ; 3 黑（字形）

ENDC


ENDC
