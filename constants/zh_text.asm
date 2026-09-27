; 简体中文化：汉字宽字形渲染相关常量。
; 与 constants/charmap.asm、engine/gfx/cjk_text.asm 配套，仅在 make zh（LANG_ZH）下生效。

IF DEF(LANG_ZH)

; 字库规模常量（由 tools/zh/zh.py gen-font 生成）：
;   ZH_GLYPH_COUNT / ZH_GLYPH_SIZE / ZH_GLYPHS_PER_BANK / ZH_GLYPH_BANK_COUNT / ZH_GLYPH_BANK_SHIFT
INCLUDE "constants/zh_font.asm"

; 字形序号必须能塞进 lead/trail 双字节编码（见 constants/charmap.asm）。
ASSERT ZH_GLYPH_COUNT <= ZH_LEAD_COUNT * ZH_GLYPHS_PER_PAGE
ASSERT ZH_TRAIL_START + ZH_TRAIL_MASK == ZH_TRAIL_END

; --- 特殊字符 ---------------------------------------------------------------
; 数值必须与 constants/charmap.asm 中的 charmap 定义保持一致。
DEF ZH_CHAR_DONE       EQU $52 ; "<DONE>"
DEF ZH_CHAR_TERMINATOR EQU $53 ; "@"
DEF ZH_CHAR_LINE       EQU $57 ; "<LINE>"

; --- look-ahead 需要认识的控制码 -------------------------------------------
; 这些码会展开成"运行时才拼出来的字符串"（玩家名字、星期、数字……），
; look-ahead 看不见里面的字符，只能保守地把整套字母数字钉住。
DEF ZH_CHAR_RAM     EQU $01 ; <RAM>
DEF ZH_CMD_ASM      EQU $03 ; <ASM>
DEF ZH_CMD_NUM      EQU $04 ; <NUM>
DEF ZH_CMD_DAY      EQU $07 ; <DAY>
DEF ZH_CMD_FAR      EQU $08 ; <FAR>
DEF ZH_CMD_PLURAL   EQU $09 ; <PLURAL>
DEF ZH_NGRAM_HASH   EQU $4d ; "#"（展开成 POKé 一类的固定串）
DEF ZH_NGRAM_MON    EQU $4e ; "#mon"
DEF ZH_NGRAM_PLAYER EQU $4f ; <PLAYER>
DEF ZH_NGRAM_RIVAL  EQU $50 ; <RIVAL>
DEF ZH_NGRAM_TRENDY EQU $51 ; <TRENDY>
DEF ZH_CHAR_TARGET  EQU $5a ; <TARGET>
DEF ZH_CHAR_USER    EQU $5b ; <USER>
DEF ZH_CHAR_ENEMY   EQU $5c ; <ENEMY>

; --- 字形缓存瓦片池 ---------------------------------------------------------
; 每个汉字 = 4 个**连续**的 8x8 tile（16x16 像素，顺序 TL,TR,BL,BR）。
; 池子只从字库区（tile $80-$F1 → VRAM $8800-$8F1F）里借"当前没人用"的一段：
;   $80-$F1 = 当前字体的 114 个 ASCII 字形（8 种字体共用同一套 tile 号）；
;   $F2-$F7 = FontCommon（光标/箭头），$F8-$FF = 对话框边框 —— 固定不动，不碰。
;
; 为什么不放在 block 2（$9000-$97FF，tile $00-$7F）：
;   那里是地图 tileset、菜单图标、战斗用的宝可梦图，写汉字进去会把地图涂花。
;   字库区的 ASCII 字形可以随时用 LoadStandardFont 从 ROM 整片加载回来，
;   是唯一能安全借用的地方。
;
; "没人用"由三张判据决定（实现见 engine/gfx/cjk_text.asm）：
;   1) 扫 wTilemap/wAttrmap 的 BG/Win 格子，外加 wShadowOAM —— 精灵的 tile
;      $80-$FF 也指向字库区（Celebi 等过场就把图标装进 $8840）；
;   2) look-ahead：当前文本串剩余部分还会打印的字面字符；
;   3) 运行时才生成的串（名字/数字）依赖的字符，保守钉住。
; 判漏了也不会画坏东西：槽位记录会指出"哪些瓦片被我们改过"，下一次分配前的
; 重扫一旦发现屏幕上真的有格子引用这些瓦片，就把冲突的那几格从字库 ROM 拷回来
; （见 ZhFixTile，只拷冲突的格子）；游戏自己重载字库时则按"仍然完整显示"与否
; 重传/释放槽位。
DEF ZH_POOL_FIRST EQU $80
DEF ZH_POOL_LAST  EQU $f1
DEF ZH_POOL_SIZE  EQU ZH_POOL_LAST - ZH_POOL_FIRST + 1
DEF ZH_POOL_BITMAP_BYTES EQU (ZH_POOL_SIZE + 7) / 8

; 属性位 3 = 1 的格子读的是 VRAM bank 1，与字库区无关，扫描时跳过。
DEF ZH_ATTR_VRAM_BANK EQU %1000

; 槽位上限：池子最多能切出 ZH_POOL_SIZE / ZH_GLYPH_TILES 个槽位，但屏幕上总有
; ASCII 要显示，故留余量取 24。槽位用满时汉字显示成空白（绝不破坏别的画面），
; 等这些汉字滚出屏幕，对应的瓦片会被回收再利用。
DEF ZH_SLOT_COUNT  EQU 24
DEF ZH_GLYPH_TILES EQU 4 ; 2x2，顺序 TL,TR,BL,BR

; 1bpp tile 为 8 字节，故 4 个 tile 共 32 字节，与生成器的 ZH_GLYPH_SIZE 一致。
ASSERT ZH_GLYPH_TILES * TILE_1BPP_SIZE == ZH_GLYPH_SIZE
ASSERT ZH_SLOT_COUNT * ZH_GLYPH_TILES <= ZH_POOL_SIZE

ENDC
