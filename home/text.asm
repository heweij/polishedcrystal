ClearSpeechBox::
IF DEF(LANG_ZH)
	; 汉字字形缓存在 VRAM 瓦片里，"哪个槽位放着哪个字" 的表必须与 VRAM 同步失效。
	; 这里刚要把内框清成空白（旧汉字不再可见），正是重置缓存的时机；
	; 否则整个存档期内槽位只增不减，ZH_SLOT_COUNT 个槽位用完后汉字会变成空白格。
	; 不能无脑全清（Zh_ResetSlots）：菜单这类界面的字和对话框共用同一批瓦片，
	; 更新描述框时菜单字还挂在屏幕上。改按屏幕现状智能释放：已经看不到的槽位
	; 释放并把占用的瓦片恢复成字库（否则下一段不翻译的英文会把残留字形当
	; ASCII 用，画出乱码），还看得见的槽位原样保留。
	farcall Zh_ReleaseHiddenSlots
ENDC
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY
IF DEF(LANG_ZH)
	; 中文正文顶边上抬 1 行后要铺满整个内框（行 13-16），清除范围必须跟着变成 INNERH 行；
	; 仍按 INNERH-1 清会漏掉最下面一行，翻页时残留上一段的字。
	lb bc, TEXTBOX_INNERH, TEXTBOX_INNERW
ELSE
	lb bc, TEXTBOX_INNERH - 1, TEXTBOX_INNERW
ENDC
ClearBox::
; Fill a c*b box at hl with blank tiles.
	ld a, ' '
FillBoxWithByte::
.row
	push bc
	push hl
.col
	ld [hli], a
	dec c
	jr nz, .col
	pop hl
	ld bc, SCREEN_WIDTH
	add hl, bc
	pop bc
	dec b
	jr nz, .row
	ret

ClearScreen::
	ld a, PAL_BG_TEXT
	hlcoord 0, 0, wAttrmap
	ld bc, SCREEN_AREA
	rst ByteFill
ClearTileMap::
; Fill wTilemap with blank tiles.
	ld a, ' '
FillTileMap::
	hlcoord 0, 0
	ld bc, wTilemapEnd - wTilemap
	rst ByteFill
	; Update the BG Map.
	ldh a, [rLCDC]
	bit B_LCDC_ENABLE, a
	ret z
	jr ApplyTilemapInVBlank

BlackOutScreen::
	xor a
	assert NO_BG_MAP_TRANSFER == 0
	ldh [hBGMapMode], a
	hlcoord 0, 0
	ld bc, SCREEN_AREA
	ld a, '<BLACK>'
	rst ByteFill
	ld a, TRANSFER_TILEMAP
	ldh [hBGMapMode], a
	ret

SpeechTextbox::
; Standard textbox.
	hlcoord TEXTBOX_X, TEXTBOX_Y
	lb bc, TEXTBOX_INNERH, TEXTBOX_INNERW
Textbox::
; Draw a text box at hl with room for b lines of c characters each.
; Places a border around the textbox, then switches the palette to 7.
	push bc
	push hl
	call TextboxBorder
	pop hl
	pop bc
	ld de, wAttrmap - wTilemap
	add hl, de
	inc b
	inc b
	inc c
	inc c
	ld a, PAL_BG_TEXT
	jr FillBoxWithByte

TextBoxCharacters:
	rawchar "┌─┐" ; top
	rawchar "│ ┃" ; middle
	rawchar "└━┘" ; bottom

TextboxBorder::
	ld de, TextBoxCharacters
	; fallthrough
CreateBoxBorders::
	ld a, SCREEN_WIDTH

	; Top
	call .PlaceRow
	jr .row

.row_loop
	dec de
	dec de
	dec de
.row
	call .PlaceRow
	dec b
	jr nz, .row_loop

	; Bottom row (fallthrough)

.PlaceRow:
	push af
	push hl
	ld a, [de]
	inc de
	ld [hli], a
	ld a, [de]
	inc de
	call .PlaceChars
	ld a, [de]
	inc de
	ld [hl], a
	pop hl
	pop af
	push bc
	ld b, 0
	ld c, a
	add hl, bc
	pop bc
	ret

.PlaceChars:
; Place char a c times.
	push bc
.loop
	ld [hli], a
	dec c
	jr nz, .loop
	pop bc
	ret

MenuTextbox::
	push hl
	call LoadMenuTextbox
	pop hl
	; fallthrough

PrintText::
; input: hl = string, bc = coords
; output: hl = advanced string, bc = advanced coords
; Clobbers de
	call SetUpTextbox
PrintTextNoBox::
	push hl
	call ClearSpeechBox
	pop hl
PrintTextboxText::
	bccoord TEXTBOX_INNERX, TEXTBOX_INNERY
PlaceWholeStringInBoxAtOnce::
	ld a, [wTextboxFlags]
	push af
	set 1, a
	ld [wTextboxFlags], a
	call DoTextUntilTerminator
	pop af
	ld [wTextboxFlags], a
	ret

SetUpTextbox::
	push hl
	call SpeechTextbox
	call UpdateSprites
	call ApplyTilemap
	pop hl
	ret

PlaceSubstring:
; input: de = string, hl = current coords, bc = starting coords
; output: de = advanced string, hl = starting coords advanced by "<NEXT>"/"<LNBRK>", bc = advanced coords
	push bc
	jr PlaceNextChar

_PlaceString::
; input: de = string, hl = coords
; output: de = advanced string, hl = starting coords advanced by "<NEXT>"/"<LNBRK>", bc = advanced coords
	push hl
	jr PlaceNextChar

SpaceChar::
	ld a, ' '
_PlaceLiteralChar:
	ld [hli], a
	call PrintLetterDelay
NextChar::
	inc de
PlaceNextChar::
	; charmap order: commands, then ngrams, then specials, then literals
	ld a, [de]
IF DEF(LANG_ZH)
	; 汉字是双字节：lead($0a-$4c) + trail，占 wTilemap 的 2x2 单元（16x16 px）。
	; 实际处理放在 ROMX 的 cjk_text.asm：这里只放最小的判定栅栏，
	; 因为 NextChar 到 LineChar 之间已经贴着 `jr` 的 -128 字节上限。
	cp ZH_LEAD_START
	jr c, .not_zh
	cp ZH_LEAD_END + 1
	jr nc, .not_zh
	; 先把文本所在的 ROM bank 记下来。
	; 下面这条 farcall 会把 CPU 切到 ROMX 的引擎 bank 上，而文本本身多半在别的
	; bank（ROM 里的地图/系统文本）；切过去之后 [de] 读到的会是引擎 bank 的
	; 同址数据 —— 那是代码或图形数据，被当成 lead/trail 解会算出乱七八糟的
	; 字形序号，表现就是片头偶尔冒出几个不相干的汉字。
	; 所以银行进来时的 bank 要先存好，引擎侧一律按它取远端字节（GetFarByte）。
	ldh a, [hROMBank]
	ld [wZhTextBank], a
	; 这里用 farcall。FarCall 声明保护 af/bc/de/hl，被调用方负责推进：
	;   - cjk_text.asm 把 de 推进 2（指向下一个 lead）
	;   - cjk_text.asm 把 hl 推进 2（下一个 2x2 瓦片列）
	;   - 本处无需再 inc de/inc hl。
	farcall Zh_PlaceNextCharCJK
	jp PlaceNextChar
.not_zh
ENDC
	cp BATTLEEXTRA_GFX_START
	jr nc, _PlaceLiteralChar
	cp SPECIALS_START
	jr nc, _PlaceSpecialChar
	cp NGRAMS_START
	jr nc, _PlaceNgramChar
	dec de
	jmp FinishString


_PlaceNgramChar:
	sub NGRAMS_START
	push de
	push hl
	ld e, a
	ld d, 0
	ld hl, NgramStrings
	add hl, de
	ld e, [hl]
	add hl, de
	cp NGRAMS_VAR_START - NGRAMS_START
	jr c, .done
	; These are pointers to strings
	ld a, [hli]
	ld h, [hl]
	ld l, a
.done
	ld d, h
	ld e, l
	pop hl
	jmp PlaceCommandCharacter

_PlaceSpecialChar:
	sub SPECIALS_START
	push hl
	add a
	ld c, a
	ld b, 0
	ld hl, SpecialCharacters
	add hl, bc
	ld a, [hli]
	ld b, [hl]
	ld c, a
	pop hl
_bc_::
	push bc
	ret

NextLineChar::
	ld b, 0
	jr HandleLineBreak

LineBreak:
	ld b, 1 << NO_LINE_SPACING_F
	; fallthrough
HandleLineBreak:
	ld a, [wTextboxFlags]
	or b
	bit USE_BG_MAP_WIDTH_F, a
	ld bc, SCREEN_WIDTH
	jr z, .got_screen_width
	ld c, TILEMAP_WIDTH

.got_screen_width
IF DEF(LANG_ZH)
	; 汉字占 2 个 tile 行，行距必须恒为 2 行；NO_LINE_SPACING 也不能压到 1 行，
	; 否则下一行会压到上一行的下半截。
	sla c
ELSE
	bit NO_LINE_SPACING_F, a
	jr nz, .ok
	sla c
ENDC

.ok
	pop hl
	add hl, bc
	push hl
	jr NextChar

LineChar::
	pop hl
	; <LINE> 固定跳到第二行。中英文这里恰好都是 INNER Y + 2：
	; 英文正文在行 14/16，中文正文在行 13-14/15-16，两者第二行的起始行号相同。
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY + 2
	push hl
	jr NextChar

; 特殊字符跳转表。刻意放在 LineChar 之后：NextChar → LineChar 的距离已经贴近
; `jr` 的 -128 字节上限，把这 ~30 字节的表挪出中间才留得出余量（汉字分派栅栏也要占）。
SpecialCharacters:
	dw DoneText         ; "<DONE>"
	dw FinishString     ; "@"
	dw PromptText       ; "<PROMPT>"
	dw LineBreak        ; "<LNBRK>"
	dw NextLineChar     ; "<NEXT>"
	dw LineChar         ; "<LINE>"
	dw ContText         ; "<CONT>"
	dw Paragraph        ; "<PARA>"
	dw PlaceTargetsName ; "<TARGET>"
	dw PlaceUsersName   ; "<USER>"
	dw PlaceEnemysName  ; "<ENEMY>"
	dw DecompressString ; "<CTXT>"
	dw SpaceChar        ; "¯"

ContText::
	ld a, [wTextboxFlags]
	bit NO_TEXT_PAUSE_F, a
	jr nz, StopAtNonblockingTextPause
	ld a, [wLinkMode]
	or a
	call z, LoadBlinkingCursor
	call Text_WaitBGMap
	push de
	call ButtonSound
	ld a, [wLinkMode]
	or a
	call z, UnloadBlinkingCursor
	call TextScroll
	call TextScroll
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY + 2
	pop de
	jmp NextChar

StopAtNonblockingTextPause::
	ld a, TRUE
	ldh [hStopPrintingString], a
	pop hl
	dec de
	ret

Paragraph::
	ld a, [wTextboxFlags]
	bit NO_TEXT_PAUSE_F, a
	jr nz, StopAtNonblockingTextPause
	push de
	ld a, [wLinkMode]
	cp LINK_COLOSSEUM
	call nz, LoadBlinkingCursor
	call Text_WaitBGMap
	call ButtonSound
	call ClearSpeechBox
	call UnloadBlinkingCursor
	ld a, [wOptions1]
	and AUTOSCROLL_MASK
	cp AUTOSCROLL_AORB
	ld c, 20
	jr z, .got_delay
	bit NO_TEXT_SCROLL, a
	jr nz, .got_delay
	and %11
	jr z, .got_delay
	ld c, 5
.loop
	dec a
	jr z, .got_delay
	sla c
	jr .loop
.got_delay
	call DelayFrames
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY
	pop de
	jmp NextChar

PromptText::
	ld a, [wTextboxFlags]
	bit NO_TEXT_PAUSE_F, a
	jr nz, DoneText
	push de
	ld a, [wLinkMode]
	cp LINK_COLOSSEUM
	call nz, LoadBlinkingCursor
	call Text_WaitBGMap
	call ButtonSound
	ld a, [wLinkMode]
	cp LINK_COLOSSEUM
	call nz, UnloadBlinkingCursor
	pop de
	; fallthrough

DoneText::
	pop hl
	dec de
	ret

PlaceTargetsName::
	ldh a, [hBattleTurn]
	xor 1
	jr _PlaceBattleNickname

PlaceUsersName::
	ldh a, [hBattleTurn]
	; fallthrough

_PlaceBattleNickname:
	push de
	ld de, wBattleMonNickname
	and a
	jr z, PlaceCommandCharacter
	ld de, .EnemyText
	rst PlaceString
	ld h, b
	ld l, c
	ld a, [wBattleType]
	cp BATTLETYPE_GHOST
	ld de, wEnemyMonNickname
	jr nz, PlaceCommandCharacter
	ld de, GhostNicknameText
	jr PlaceCommandCharacter

.EnemyText:
	db "Foe" ; fallthrough, no " @"
SpaceText::
	db " " ; fallthrough, no "@"
EmptyString::
	db "@"

GhostNicknameText:
	db "Ghost@"

PlaceEnemysName::
	push de
	ld de, wOTClassName
	ld a, [wLinkMode]
	and a
	jr nz, PlaceCommandCharacter
	rst PlaceString
	ld h, b
	ld l, c
	ld a, [wTextboxFlags]
	bit NEWLINE_ENEMY_F, a
	ld de, SpaceText
	jr z, .no_wordwrap
	ld de, ContChar
.no_wordwrap
	rst PlaceString
	push bc
	farcall Battle_GetTrainerName
	pop hl
	ld de, wStringBuffer1
	; fallthrough

PlaceCommandCharacter::
	rst PlaceString
	ld h, b
	ld l, c
	pop de
	jmp NextChar

TextCommand_PLURAL:
; Pluralize the last word. Might perform edits on it (Candy -> Candies).
	; If wItemQuantityChangeBuffer is 1, do nothing.
	ld a, [wItemQuantityChangeBuffer]
	dec a
	ret z

	; Try to pattern match the previous string with the plural table below.
	push hl
	push bc

	ld hl, PluralTable

.check_match_loop
	; Iterate until the pattern no longer matches our string.
	dec bc
	ld a, [bc]

	; If we find a terminator in the input string, we must have gone past it
	; into other data. Handle this separately. The reason for this is that
	; otherwise, if the plural table is also at a terminator, we'll misalign the
	; parser into reading output as input and vice versa.
	cp '@'
	jr nz, .not_at_start
	cp [hl]
	jr nz, .no_match
	inc hl
	jr .match

.not_at_start
	cp [hl]
	ld a, [hli] ; To check if we found the terminator.
	jr z, .check_match_loop

	; Did we hit the terminator?
	cp '@'
	jr nz, .no_match

.match
	; We have a match. Print out the adjusted string.
	inc bc
	ld d, h
	ld e, l
	ld h, b
	ld l, c
	pop bc
	rst PlaceString
	pop hl
	ret

.no_match
	ld b, 2
.no_match_loop
	ld a, [hli]
	cp '@'
	jr nz, .no_match_loop
	dec b
	jr nz, .no_match_loop
	pop bc
	push bc
	jr .check_match_loop

INCLUDE "data/text/plural_table.asm"

TextScroll::
IF DEF(LANG_ZH)
	; 中文正文顶边是 INNER Y（比英文高 1 行），滚动窗口必须整段留在内框里：
	; 源行 14-16 → 目标行 13-15，末尾清行 16。
	; 绝不能沿用英文的 "目标 = INNER Y - 1"，那一行是文本框上边框，会被抹掉。
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY + 1
	decoord TEXTBOX_INNERX, TEXTBOX_INNERY
ELSE
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY
	decoord TEXTBOX_INNERX, TEXTBOX_INNERY - 1
ENDC
	ld a, TEXTBOX_INNERH - 1
.col
	push af
	ld c, TEXTBOX_INNERW
.row
	ld a, [hli]
	ld [de], a
	inc de
	dec c
	jr nz, .row
	inc de
	inc de
	inc hl
	inc hl
	pop af
	dec a
	jr nz, .col
IF DEF(LANG_ZH)
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY + 3
ELSE
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY + 2
ENDC
	ld a, ' '
	ld bc, TEXTBOX_INNERW
	rst ByteFill
	ld c, 5
	jmp DelayFrames

Text_WaitBGMap::
	push bc
	ldh a, [hOAMUpdate]
	push af
	ld a, 1
	ldh [hOAMUpdate], a
	call ApplyTilemapInVBlank
	pop af
	ldh [hOAMUpdate], a
	pop bc
	ret

LoadBlinkingCursor::
	ld a, '▼'
	ldcoord_a 18, 17
	ret

UnloadBlinkingCursor::
	lda_coord 17, 17
	ldcoord_a 18, 17
	ret

FarString::
	ld b, a
	ldh a, [hROMBank]
	push af
	ld a, b
	rst Bankswitch
	rst PlaceString
	pop af
	rst Bankswitch
	ret

DoTextUntilTerminator::
	xor a
	ldh [hStopPrintingString], a
.loop
	ldh a, [hStopPrintingString]
	and a
	ret nz
	ld a, [hli]
	call CheckTerminatorChar
	ret z
	call .TextCommand
	jr .loop

.TextCommand:
	cp NUM_TEXT_COMMANDS
	jr nc, _ImplicitlyStartedText
	push hl
	ld e, a
	ld d, 0
	ld hl, TextCommands
	add hl, de
	add hl, de
	ld a, [hli]
	ld d, [hl]
	ld e, a
	pop hl
	push de
	ret

TextCommands::
	table_width 2
	dw TextCommand_START         ; $00 <START>
	dw TextCommand_RAM           ; $01 <RAM>
	dw TextCommand_PROMPT_BUTTON ; $02 <WAIT>
	dw TextCommand_ASM           ; $03 <ASM>
	dw TextCommand_DECIMAL       ; $04 <NUM>
	dw TextCommand_PAUSE         ; $05 <PAUSE>
	dw TextCommand_SOUND         ; $06 <SOUND>
	dw TextCommand_DAY           ; $07 <DAY>
	dw TextCommand_FAR           ; $08 <FAR>
	dw TextCommand_PLURAL        ; $09 <PLURAL>
	assert_table_length NUM_TEXT_COMMANDS

_ImplicitlyStartedText:
	dec hl
TextCommand_START::
; write text until "@"
	ld d, h
	ld e, l
	ld h, b
	ld l, c
	rst PlaceString
	ld h, d
	ld l, e
	inc hl
	ret

TextCommand_RAM::
; write text from a ram address
	ld a, [hli]
	ld e, a
	ld a, [hli]
	ld d, a
	push hl
	ld h, b
	ld l, c
	rst PlaceString
	pop hl
	ret

TextCommand_FAR::
; write text from a different bank
	bit 7, [hl]
	jr z, .not_farjp

	; The only way to reach TextCommand_FAR is via the TextCommands jumptable
	; used by DoTextUntilTerminator. So this pops the address of DoTextUntilTerminator,
	; and returning from TextCommand_FAR will go to whatever called DoTextUntilTerminator.
	pop de

.not_farjp
	ldh a, [hROMBank]
	push af

	ld a, [hli]
	ld d, a
	res 7, d
	ld a, [hli]
	ld e, a
	ld a, [hli]
	rst Bankswitch

	push hl
	ld h, d
	ld l, e
	call DoTextUntilTerminator
	pop hl

	pop af
	rst Bankswitch
	ret

TextCommand_PROMPT_BUTTON::
; wait for button press; show arrow
	push hl
	ld a, [wLinkMode]
	cp LINK_COLOSSEUM
	call nz, LoadBlinkingCursor
	push bc
	call ButtonSound
	pop bc
	ld a, [wLinkMode]
	cp LINK_COLOSSEUM
	call nz, UnloadBlinkingCursor
	pop hl
	ret

TextCommand_ASM::
	bit 7, h
	jr nz, .not_rom
	jp hl

.not_rom
	ld [hl], '@'
	ret

TextCommand_DECIMAL::
; print a decimal number
	ld a, [hli]
	ld e, a
	ld a, [hli]
	ld d, a
	ld a, [hli]
	push hl
	ld h, b
	ld l, c
	ld b, a
	and $f
	ld c, a
	ld a, b
	and $f0
	swap a
	or PRINTNUM_DELAY | PRINTNUM_LEFTALIGN
	ld b, a
	call PrintNum
FinishString:
	ld b, h
	ld c, l
	pop hl
	ret

TextCommand_PAUSE::
; wait for button press or 30 frames
	push hl
	push bc
	call GetJoypad
	ldh a, [hJoyDown]
	and PAD_A | PAD_B
	jr nz, .done
	ld c, 30
	call DelayFrames
.done
	pop bc
	pop hl
	ret

TextCommand_SOUND::
; play a sound effect
	ld a, [hli]
	push hl
	push de
	push bc
	ld e, a
	ld d, 0
	call PlaySFX
	call WaitSFX
	jmp PopBCDEHL

TextCommand_DAY::
; print the day of the week
	call GetWeekday
PrintDayOfWeek::
	push hl
	push bc
	ld c, a
	ld b, 0
	ld hl, .Days
	add hl, bc
	ld c, [hl]
	add hl, bc
	ld d, h
	ld e, l
	pop hl
	rst PlaceString
	ld h, b
	ld l, c
	ld de, .Day
	rst PlaceString
	pop hl
	ret

.Days:
	dr .Sun
	dr .Mon
	dr .Tues
	dr .Wednes
	dr .Thurs
	dr .Fri
	dr .Satur

.Sun:    db "Sun@"
.Mon:    db "Mon@"
.Tues:   db "Tues@"
.Wednes: db "Wednes@"
.Thurs:  db "Thurs@"
.Fri:    db "Fri@"
.Satur:  db "Satur@"
.Day:    db "day@"

DecompressString::
	; save starting coords
	pop bc
	push bc
	ld a, c
	ldh [hPlaceStringCoords], a
	ld a, b
	ldh [hPlaceStringCoords+1], a

	call SwapHLDE ; hl = string, de = current coords

	inc hl ; skip "<CTXT>"

	; start the contextual decoder at a word boundary
	ld a, ' '
	ldh [hCompressedTextBuffer], a

	; terminate buffer for printing each character
	ld a, '@'
	ldh [hCompressedTextBuffer+1], a

	ld b, 1 ; start with no bits to read a byte right away
.character_loop

	push de ; push current coords

	call ReadHuffmanChar

	; buffer character for printing
	ldh [hCompressedTextBuffer], a

	pop de ; pop current coords

	push hl ; push string position
	push bc ; push bit-reading state

	; hl = current coords
	ld h, d
	ld l, e

	; read saved starting coords
	ldh a, [hPlaceStringCoords]
	ld c, a
	ldh a, [hPlaceStringCoords+1]
	ld b, a

	ld de, hCompressedTextBuffer
	ld a, [de]
	push af

	; print string de at coord hl, having started from coord bc
	call PlaceSubstring

	; write updated starting coords
	ld a, l
	ldh [hPlaceStringCoords], a
	ld a, h
	ldh [hPlaceStringCoords+1], a

	; update current coords
	ld d, b
	ld e, c

	pop af

	pop bc ; pop bit-reading state
	pop hl ; pop string position

	; check for characters that signal end of compression
	; (same ones that finish PlaceString)
	sub '<DONE>'
	jr z, .done
	assert '<DONE>' + 1 == '@'
	dec a
	jr z, .end
	assert '@' + 1 == '<PROMPT>'
	dec a
	jr nz, .character_loop

.done
	inc a ; ld a, 1 since it's zero
	ldh [hStopPrintingString], a

.end
	pop bc ; pop starting coords

	; update current coords
	ld b, d
	ld c, e

	; update string position
	ld d, h
	ld e, l
	dec de

	; restore starting coords
	ldh a, [hPlaceStringCoords]
	ld l, a
	ldh a, [hPlaceStringCoords+1]
	ld h, a
	ret

DecompressStringToRAM::
; input: hl = string, de = destination
.outer_loop
	ld a, [hl]
	cp '<CTXT>'
	jr nz, .copy_loop

	inc hl ; skip "<CTXT>"

.do_decompression
	; start the contextual decoder at a word boundary
	ld a, ' '
	ldh [hCompressedTextBuffer], a
	ld b, 1 ; start with no bits to read a byte right away
.decompress_loop

	push de
	call ReadHuffmanChar
	pop de
	ldh [hCompressedTextBuffer], a
	; check for characters that signal end of compression
	; (same ones that finish PlaceString)
	call CheckTerminatorChar
	jr z, .append_terminator

	; Store decompressed char to WRAM and advance
	ld [de], a
	inc de
	jr .decompress_loop

.copy_loop
	ld a, [hli]
	cp '<CTXT>'
	jr z, .do_decompression
	call CheckTerminatorChar
	jr z, .append_terminator
	ld [de], a
	inc de
	jr .copy_loop

.append_terminator
	; to maintain generic usage, this function will return on
	; any terminator encountered. it is up to the caller to decide
	; what to do with the given terminator returned in a
	ld [de], a
	ret

ReadHuffmanChar:
	; Keep the compressed source pointer in de, leaving hl available for the
	; context-specific tree base.
	ld d, h
	ld e, l

	; Select a tree from the previous decoded character.
	ldh a, [hCompressedTextBuffer]
	cp ' '
	jr z, .boundary
	cp '<LNBRK>'
	jr c, .check_vowel
	cp '<PARA>' + 1
	jr c, .boundary
.check_vowel
	and ~('A' ^ 'a') ; normalize lowercase letters to uppercase
	cp 'A'
	jr z, .vowel
	cp 'E'
	jr z, .vowel
	cp 'I'
	jr z, .vowel
	cp 'O'
	jr z, .vowel
	cp 'U'
	jr z, .vowel
	ld hl, TextCompressionHuffmanTreeOther
	jr .got_tree
.boundary
	ld hl, TextCompressionHuffmanTreeBoundary
	jr .got_tree
.vowel
	ld hl, TextCompressionHuffmanTreeVowel
.got_tree
	assert ROOT_NODE_ID == $00
	xor a
.tree_loop
	; "c = [de++]" when b reaches 0, then carry = next bit from c
	dec b
	jr nz, .no_reload
	push af
	ld a, [de]
	ld c, a
	inc de
	ld b, 8
	pop af
.no_reload
	sla c
	; Temporarily advance hl from the selected tree base to node a/branch carry.
	adc a
	push hl
	add l
	ld l, a
	adc h
	sub l
	ld h, a
	; keep traversing the tree until a leaf node
	ld a, [hl]
	pop hl
	cp FIRST_LEAF_NODE_ID
	jr c, .tree_loop

	cp CONTEXT_ESCAPE_NODE_ID
	jr z, .literal

	; shifted leaf node IDs correspond to lesser characters
	; (since node IDs below the first leaf node ID must be parent nodes)
	cp FIRST_SHIFTED_LEAF_NODE_ID
	jr c, .done
	sub FIRST_SHIFTED_LEAF_NODE_ID - FIRST_SHIFTED_LEAF_CHAR_ID
	jr .done

.literal
	; An escape leaf is followed by one uncompressed character byte.
	ld a, 1
.literal_loop
	dec b
	jr nz, .literal_no_reload
	push af
	ld a, [de]
	ld c, a
	inc de
	ld b, 8
	pop af
.literal_no_reload
	sla c
	rla
	jr nc, .literal_loop

.done
	; Return the advanced compressed source pointer in hl.
	ld h, d
	ld l, e
	ret

CheckTerminatorChar:
; check for a character that terminates `_dchr` Huffman compression
	cp '@'
	ret z
	cp '<DONE>'
	ret z
	cp '<PROMPT>'
	ret
