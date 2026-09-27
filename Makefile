NAME := polishedcrystal
MODIFIERS :=
VERSION := 3.2.3
AUTHOR := RANGI42

ROM_NAME = $(NAME)$(MODIFIERS)-$(VERSION)
EXTENSION := gbc

TITLE := PKPCRYSTAL
MCODE := PKPC
ROMVERSION := 0x32

FILLER := 0xff

COPYRIGHT = @$(shell date '+%Y') $(AUTHOR) v$(VERSION)

ifneq ($(wildcard rgbds/.*),)
RGBDS ?= rgbds/
else
RGBDS ?=
endif
RGBASM  ?= $(RGBDS)rgbasm
RGBFIX  ?= $(RGBDS)rgbfix
RGBGFX  ?= $(RGBDS)rgbgfx
RGBLINK ?= $(RGBDS)rgblink

Q :=

.SECONDEXPANSION:

POCKET_LOGO = gfx/logo/pocket.bin

RGBASMFLAGS    = -Weverything -Wtruncation=1 -E -Q8 -P includes.asm
RGBASMVCFLAGS  = $(RGBASMFLAGS) -DVIRTUAL_CONSOLE
RGBLINKFLAGS   = -Weverything -Wtruncation=1 -M -n $(ROM_NAME).sym -m $(ROM_NAME).map -p $(FILLER)
RGBLINKVCFLAGS = -Weverything -M -n $(ROM_NAME)_vc.sym -m $(ROM_NAME)_vc.map -p $(FILLER)
RGBFIXFLAGS    = -Weverything -csjv -t $(TITLE) -i $(MCODE) -n $(ROMVERSION) -p $(FILLER) -k 01 -l 0x33 -m MBC3+TIMER+RAM+BATTERY -r 3
RGBGFXFLAGS    = -Weverything

ifeq ($(filter faithful,$(MAKECMDGOALS)),faithful)
MODIFIERS := $(MODIFIERS)-faithful
RGBASMFLAGS += -DFAITHFUL
COPYRIGHT += F
endif
ifeq ($(filter monochrome,$(MAKECMDGOALS)),monochrome)
MODIFIERS := $(MODIFIERS)-monochrome
RGBASMFLAGS += -DMONOCHROME
endif
ifeq ($(filter noir,$(MAKECMDGOALS)),noir)
MODIFIERS := $(MODIFIERS)-noir
RGBASMFLAGS += -DNOIR
endif
ifeq ($(filter hgss,$(MAKECMDGOALS)),hgss)
MODIFIERS := $(MODIFIERS)-hgss
RGBASMFLAGS += -DHGSS
endif
ifeq ($(filter debug,$(MAKECMDGOALS)),debug)
MODIFIERS := $(MODIFIERS)-debug
RGBASMFLAGS += -DDEBUG
COPYRIGHT += dbg
endif
ifeq ($(filter pocket,$(MAKECMDGOALS)),pocket)
MODIFIERS :=
NAME := pkpc
EXTENSION := pocket
RGBASMFLAGS += -DANALOGUE_POCKET -DNO_RTC
RGBFIXFLAGS = -Weverything -csjv -t $(TITLE) -i $(MCODE) -n $(ROMVERSION) -p $(FILLER) -k 01 -l 0x33 -m MBC5+RAM+BATTERY -r 3 -L $(POCKET_LOGO)
endif
ifeq ($(filter huffman,$(MAKECMDGOALS)),huffman)
# Context models require token output to remain in source order.
.NOTPARALLEL:
Q := @
RGBASMFLAGS += -DHUFFMAN
endif

rom_obj := \
	main.o \
	home.o \
	ram.o \
	audio.o \
	audio/music_player.o \
	data/pokemon/dex_entries.o \
	data/pokemon/egg_moves.o \
	data/pokemon/evos_attacks.o \
	data/maps/map_data.o \
	data/text/common.o \
	data/tilesets.o \
	engine/movie/credits.o \
	engine/overworld/events.o \
	gfx/minis_icons.o \
	gfx/pokemon.o \
	gfx/sprites.o \
	gfx/trainers.o \
	gfx/items.o \
	gfx/misc.o

crystal_obj    := $(rom_obj:.o=.o)
crystal_vc_obj := $(rom_obj:.o=_vc.o)

# 简体中文化构建（make zh）：
#   - 定义 LANG_ZH，只在该构建下编入中文字库/中文文本；
#   - 改用 MBC5（最大 4MB），因为 MBC3 的 2MB 已被原版占满（仅余约 30KB）；
#   - ROM 名带 -zh 后缀，不覆盖英文版产物。
ifeq ($(filter zh,$(MAKECMDGOALS)),zh)
MODIFIERS := -zh
RGBASMFLAGS += -DLANG_ZH
RGBFIXFLAGS := $(subst MBC3+TIMER+RAM+BATTERY,MBC5+RAM+BATTERY,$(RGBFIXFLAGS))

# make zh ZH_DEMO=1：开机直接进中文演示画面（标准对话框 + 两行汉字）。
# 用来在模拟器里一眼判定渲染链路是否通（字形加载 -> 双字节分派 -> 12x12 绘制），
# 排除 "跑错 ROM" 或 "看到的本来就是还没翻译的文本" 这类歧义。
# 演示画面会停在那里无法继续游玩，故用独立的 -zh-demo 文件名，不覆盖正常中文版。
ifneq ($(ZH_DEMO),)
RGBASMFLAGS += -DZH_DEMO
MODIFIERS := -zh-demo
endif
zh_text_obj := $(patsubst tools/zh/translations/%.json,data/text/zh/%.o,$(wildcard tools/zh/translations/*.json))

# 真实游戏文本：把英文文本对象换成同名 section/label 的中文版。
# 只有存在翻译 IR（tools/zh/translations/text/<stem>.json）的文件才替换；
# text-encode 对未翻译的块原样保留原文，所以即使一条都没翻，产物也与英文构建等价。
#
# 但 IR 会覆盖到**不是独立对象**的文件：data/text/ 下有些 .asm 是在别的文件里被
# SECTION 内 INCLUDE 的片段（例如 ngrams.asm），单独汇编会报
# "Cannot define label outside of a SECTION"。所以再和 rom_obj 里真实存在的
# data/text/*.o 取交集，只有能整对象替换的才纳入替换名单。
zh_ir_stems   := $(patsubst tools/zh/translations/text/%.json,%,$(wildcard tools/zh/translations/text/*.json))
zh_real_stems := $(patsubst data/text/%.o,%,$(filter $(addprefix data/text/,$(addsuffix .o,$(zh_ir_stems))),$(filter data/text/%.o,$(rom_obj))))
zh_real_obj   := $(addprefix data/text/zh/,$(addsuffix .o,$(zh_real_stems)))
zh_swap_obj   := $(addprefix data/text/,$(addsuffix .o,$(zh_real_stems)))

# 地图剧本没有独立对象：maps/*.asm 全被 data/maps/scripts.asm 收进 map_data.o，
# 换入点因此是脚本清单本身（data/text/zh_maps/scripts.asm）。英文构建那条依赖会被
# zh_include_filter 剔掉，这里补回来，保证中文剧本先生成再汇编 map_data.o。
data/maps/map_data.o: data/text/zh_maps/scripts.asm

rom_obj := $(filter-out $(zh_swap_obj),$(rom_obj)) $(zh_real_obj) data/font/zh.o $(zh_text_obj)
# crystal_obj/crystal_vc_obj 已在本行之上用 := 立即展开，故需单独追加。
crystal_obj := $(filter-out $(zh_swap_obj),$(crystal_obj)) $(zh_real_obj) data/font/zh.o $(zh_text_obj)
endif

# 变体切换：zh 与英文构建共用同一批 .o，但 RGBASMFLAGS 不同（-DLANG_ZH / -DZH_DEMO）。
# 一旦复用了另一变体编出的 .o，链接期就会报 "Undefined symbol ZhGlyphGFX0"：
# 不是中文符号缺失，而是英文链里混进了中文对象（反之亦然）。
#
# 这里刻意**不用**“空标记文件 + 把标记当 .o 的普通前置依赖”那套惯用法。
# 在本机 Windows 的 make 上实测它不可靠：切换变体后标记文件确实被删除并重建，
# 但 make 仍把同名 .o 判为最新而漏编 —— 实测 make crystal 之后 main.o 的时间戳
# 还是上一次 zh 构建留下的，直到链接期才暴露成未定义符号。
#
# 改成在**解析阶段**用 $(shell) 比较标记文件内容：变体一变就立刻删除整批对象，
# make 再也无从判断“最新”，只能重新汇编。代价是切换变体要付一次全量编译，
# 而这份开销本来就省不掉。
build_variant := $(if $(filter zh,$(MAKECMDGOALS)),zh$(if $(ZH_DEMO),-demo,),en)
variant_marker := .build-variant
ifeq (,$(filter clean tidy tools,$(MAKECMDGOALS)))
ifneq ($(build_variant),$(shell cat $(variant_marker) 2>/dev/null))
$(shell $(RM) $(crystal_obj) $(crystal_vc_obj) 2>/dev/null; echo $(build_variant) > $(variant_marker))
endif
endif

.SUFFIXES:
.PHONY: clean tidy crystal faithful pocket debug monochrome freespace tools bsp huffman vc zh
.PRECIOUS: %.2bpp %.1bpp
.SECONDARY:
.DEFAULT_GOAL: crystal

crystal: $$(ROM_NAME).$$(EXTENSION)
faithful: crystal
monochrome: crystal
noir: crystal
hgss: crystal
debug: crystal
pocket: crystal
zh: crystal
vc: $$(ROM_NAME).patch

tools:
	$(MAKE) -C tools/

clean: tidy
	find gfx maps data/tilesets -name '*.lzp' -delete
	find gfx \( -name '*.[12]bpp' -o -name '*.2bpp.vram[012]' -o -name '*.2bpp.vram[012]p' \) -delete
	find gfx/pokemon -mindepth 1 \( -name 'bitmask.asm' -o -name 'frames.asm' \
		-o -name 'front.animated.tilemap' -o -name 'front.dimensions' \) -delete
	find data/tilesets -name '*_collision.bin' -delete
	$(MAKE) clean -C tools/

tidy:
	$(RM) $(crystal_obj) $(crystal_vc_obj) $(wildcard $(NAME)-*.gbc) $(wildcard $(NAME)-*.pocket) $(wildcard $(NAME)-*.bsp) \
		$(wildcard $(NAME)-*.map) $(wildcard $(NAME)-*.sym) $(wildcard $(NAME)-*.patch) rgbdscheck.o \
		.build-variant $(wildcard .build-variant-*) $(wildcard gfx/title/version.2bpp*)

freespace: crystal tools/bankends
	tools/bankends $(ROM_NAME).map > bank_ends.txt

bsp: $(ROM_NAME).bsp

huffman: crystal


rgbdscheck.o: rgbdscheck.asm
	$Q$(RGBASM) -o $@ $<

ifeq (,$(filter clean tidy tools,$(MAKECMDGOALS)))
$(info $(shell $(MAKE) -C tools))
endif

preinclude_deps := includes.asm $(shell tools/scan_includes includes.asm)

# 中文化的 INCLUDE 点写了 IF DEF(LANG_ZH) / INCLUDE "data/text/zh_include/..."，
# 而 tools/scan_includes 不认条件汇编，会把中文路径也算成依赖。英文构建既不需要它，
# 也不该为此被拖进「必须先有 Python 和系统中文字体」的链条，所以只在 zh 构建里保留。
# 详见 docs/TRANSLATION_TOOLING.md。
# 地图剧本的换入点同理（data/text/zh_maps/scripts.asm），两处一起过滤。
zh_include_filter := $(if $(filter zh,$(MAKECMDGOALS)),,data/text/zh_include/% data/text/zh_maps/%)

define DEP
$1: $2 $$(filter-out $(zh_include_filter),$$(shell tools/scan_includes $2)) $(preinclude_deps) | rgbdscheck.o
	$Q$$(RGBASM) $$(RGBASMFLAGS) -o $$@ $$<
endef

define VCDEP
$1: $2 $$(filter-out $(zh_include_filter),$$(shell tools/scan_includes $2)) $(preinclude_deps) | rgbdscheck.o
	$Q$$(RGBASM) $$(RGBASMVCFLAGS) -o $$@ $$<
endef

ifeq (,$(filter clean tidy tools,$(MAKECMDGOALS)))
$(foreach obj, $(crystal_obj), $(eval $(call DEP,$(obj),$(obj:.o=.asm))))
$(foreach obj, $(crystal_vc_obj), $(eval $(call VCDEP,$(obj),$(obj:_vc.o=.asm))))
endif

$(ROM_NAME).patch: $(ROM_NAME)_vc.gbc $(ROM_NAME).$(EXTENSION) vc.patch.template
	tools/make_patch $(ROM_NAME)_vc.sym $^ $@

.$(EXTENSION): tools/bankends
$(ROM_NAME).$(EXTENSION): $(crystal_obj) layout.link
	$Q$(RGBLINK) $(RGBLINKFLAGS) -l layout.link -o $@ $(filter %.o,$^)
	$Q$(RGBFIX) $(RGBFIXFLAGS) $@
# 空间报告/门禁：中文构建走 Python 版（bankends.exe 硬编码 128 banks，且本机无 C 编译器编不动它）；
# 英文构建保持原来的 C 版，不引入 Python 依赖。
ifeq ($(filter zh,$(MAKECMDGOALS)),zh)
	$Q$(ZH_PY) tools/zh/zh.py gate --map $(ROM_NAME).map --banks $(ZH_ROMX_BANKS) --min-free $(ZH_MIN_FREE)
else
	$Qtools/bankends -q $(ROM_NAME).map >&2
endif

$(ROM_NAME)_vc.gbc: $(crystal_vc_obj) layout.link
	$Q$(RGBLINK) $(RGBLINKVCFLAGS) -l layout.link -o $@ $(filter %.o,$^)
	$Q$(RGBFIX) $(RGBFIXFLAGS) $@
	$Qtools/bankends -q $(ROM_NAME)_vc.map >&2

.bsp: tools/bspcomp
%.bsp: $(wildcard bsp/*.txt)
	$Qcd bsp; ../tools/bspcomp patch.txt ../$@; cd ..

# 中文字库/索引/常量由 Python 工具生成（源：术语表 + 译文 JSON）。
# 强制 UTF-8 输出，否则 Windows 控制台会按 GBK 打印，中文提示变乱码。
ZH_PY := PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python

# 容量门禁（只在 zh 构建后跑，英文构建不引入 Python 依赖）。
#   ZH_ROMX_BANKS  可用 ROMX bank 数；与 tools/bankends.c 的 BANKS-1 必须一致。
#                  Bankswitch 只写 [rROMB] 低 8 位，故上限 $ff（255）。
#   ZH_MIN_FREE    余量下限（字节）；0 = 只报警不失败。想卡死就 make zh ZH_MIN_FREE=16384
ZH_ROMX_BANKS ?= 255
ZH_MIN_FREE ?= 0

zh_font_sources := tools/zh/zh.py tools/zh/glossary/glossary.tsv \
	$(wildcard tools/zh/translations/*.json) $(wildcard tools/zh/translations/text/*.json) \
	$(wildcard tools/zh/translations/include/*.json)
# gen-font 一次产出：分块字形（gfx/font/zh.<n>.1bpp）、分块表、常量与调试索引。
zh_font_out := data/font/zh_glyphs.asm data/font/zh_chunk_ptrs.asm data/font/zh_chunk_banks.asm \
	data/font/zh_index.asm constants/zh_font.asm
$(zh_font_out) &: $(zh_font_sources)
	$Q$(ZH_PY) tools/zh/zh.py gen-font

# 真实游戏文本：data/text/<stem>.asm -> 翻译 IR（保留已有译文，只补新增块）。
# 放在示例文本规则之前，这样 data/text/zh/<stem>.asm 优先按真实文本流程生成；
# 对没有对应 data/text/<stem>.asm 的示例文本，本规则不匹配，会落到下面的规则。
tools/zh/translations/text/%.json: data/text/%.asm tools/zh/text_ir.py tools/zh/zh.py
	$Q$(ZH_PY) tools/zh/zh.py text-extract --only $*

# 真实文本译文 .asm：未翻译的块原样保留原文，已翻译的块输出双字节 db。
# 依赖 data/font/zh_glyphs.asm 是为了保证 gen-font 先跑，这样字形集覆盖译文用字。
data/text/zh/%.asm: tools/zh/translations/text/%.json data/text/%.asm data/font/zh_glyphs.asm tools/zh/text_ir.py tools/zh/zh.py
	$Q$(ZH_PY) tools/zh/zh.py text-encode --only $*

# 示例译文 .asm 由译文 JSON 转码生成（双字节 db）。
data/text/zh/%.asm: tools/zh/translations/%.json data/font/zh_glyphs.asm
	$Q$(ZH_PY) tools/zh/zh.py encode

# --- 靠条件 INCLUDE 换入的文本 --------------------------------------------------
# 这些文件被别的 .asm INCLUDE 进同一个目标文件（engine/*.asm 进 main.o，
# data/options/*.asm 进 options_menu.o），没法像 data/text/*.asm 那样整对象替换；
# 中文构建改在 INCLUDE 点用 IF DEF(LANG_ZH) 换入 data/text/zh_include/<stem>.asm。
# 清单见 tools/zh/include_sources.txt（zh.py 读同一个文件，避免两处漂移），
# 必须是纯路径清单——Makefile 用 $(file <) 直接读，注释会被当成路径。
# 因为 stem 到源路径的映射由 zh.py 负责，这里让所有 IR 都依赖全部清单源；
# 改任何一个源都会重跑 text-extract（保留已有译文，成本很低）。
# 新增文件要先手工跑一次 text-extract 生成 IR，之后 make 就会自动维护。
zh_include_src := $(filter %.asm,$(file < tools/zh/include_sources.txt))

tools/zh/translations/include/%.json: $(zh_include_src) tools/zh/text_ir.py tools/zh/zh.py
	$Q$(ZH_PY) tools/zh/zh.py text-extract --only $*

# 依赖 data/font/zh_glyphs.asm 是为了保证 gen-font 先跑，字形集覆盖译文用字。
data/text/zh_include/%.asm: tools/zh/translations/include/%.json data/font/zh_glyphs.asm \
	tools/zh/text_ir.py tools/zh/zh.py
	$Q$(ZH_PY) tools/zh/zh.py text-encode --only $*

# --- 地图剧本（第三类通路）----------------------------------------------------
# maps/*.asm 全被 data/maps/scripts.asm 收进 data/maps/map_data.o，既没有自己的
# 目标文件、也不该改 654 处 INCLUDE 点，所以换入点是**脚本清单的生成物**
# data/text/zh_maps/scripts.asm：逐行复制原文，只把收录地图的 INCLUDE 换成中文副本。
# 收录清单见 tools/zh/map_sources.txt（同样是纯路径清单，Makefile 也读它）。
zh_map_src := $(filter %.asm,$(file < tools/zh/map_sources.txt))

# 换入清单必须**等到每张收录地图的中文副本都生成完**才有得换。少了这层依赖，
# make 只会重建 scripts.asm 本身，map-scripts 找不到副本就保留英文 INCLUDE——
# 译文静默丢失，而 ROM 照样构建成功，只能靠读 ROM 才看得出来。
zh_map_stems := $(basename $(notdir $(zh_map_src)))
zh_map_asms := $(addprefix data/text/zh_maps/,$(addsuffix .asm,$(zh_map_stems)))

# 换入清单必须先于逐文件规则成立：它不对应任何 maps/%.json，
# 放在这里可以让 make 先读到不适用即跳过的逻辑，避免匹配顺序出岔子。
data/text/zh_maps/scripts.asm: data/maps/scripts.asm $(zh_map_src) $(zh_map_asms) \
	data/font/zh_glyphs.asm tools/zh/zh.py tools/zh/text_ir.py
	$Q$(ZH_PY) tools/zh/zh.py map-scripts

tools/zh/translations/maps/%.json: $(zh_map_src) tools/zh/text_ir.py tools/zh/zh.py
	$Q$(ZH_PY) tools/zh/zh.py text-extract --only $*

# 依赖 data/font/zh_glyphs.asm 是为了保证 gen-font 先跑，字形集覆盖译文用字。
data/text/zh_maps/%.asm: tools/zh/translations/maps/%.json data/font/zh_glyphs.asm \
	tools/zh/text_ir.py tools/zh/zh.py
	$Q$(ZH_PY) tools/zh/zh.py text-encode --only $*

gfx/battle/lyra_back.2bpp: RGBGFXFLAGS += -Z
gfx/battle/substitute-back.2bpp: RGBGFXFLAGS += -Z
gfx/battle/substitute-front.2bpp: RGBGFXFLAGS += -Z
gfx/battle/ghost.2bpp: RGBGFXFLAGS += -Z
gfx/battle/hpexpbar.2bpp: tools/gfx += --trim-whitespace

gfx/battle_anims/angels.2bpp: tools/gfx += --trim-whitespace
gfx/battle_anims/beam.2bpp: tools/gfx += --remove-xflip --remove-yflip --remove-whitespace
gfx/battle_anims/bubble.2bpp: tools/gfx += --trim-whitespace
gfx/battle_anims/charge.2bpp: tools/gfx += --trim-whitespace
gfx/battle_anims/egg.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/explosion.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/hit.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/horn.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/lightning.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/misc.2bpp: tools/gfx += --remove-duplicates --remove-xflip
gfx/battle_anims/noise.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/objects.2bpp: tools/gfx += --remove-whitespace --remove-xflip
gfx/battle_anims/reflect.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/rocks.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/skyattack.2bpp: tools/gfx += --remove-whitespace
gfx/battle_anims/status.2bpp: tools/gfx += --remove-whitespace

gfx/card_flip/card_flip_1.2bpp: tools/gfx += --trim-whitespace
gfx/card_flip/card_flip_2.2bpp: tools/gfx += --remove-whitespace

gfx/evo/bubble.2bpp: tools/gfx += --trim-whitespace

gfx/font/%.1bpp: tools/gfx += --trim-whitespace
gfx/font/space.2bpp: tools/gfx =

gfx/mail/dragonite.1bpp: tools/gfx += --remove-whitespace
gfx/mail/flower_mail_border.1bpp: tools/gfx += --remove-whitespace
gfx/mail/large_note.1bpp: tools/gfx += --remove-whitespace
gfx/mail/litebluemail_border.1bpp: tools/gfx += --remove-whitespace
gfx/mail/surf_mail_border.1bpp: tools/gfx += --remove-whitespace

gfx/music_player/bg.2bpp: tools/gfx += --trim-whitespace
gfx/music_player/music_player.2bpp: gfx/music_player/bg.2bpp gfx/music_player/ob.2bpp ; $Qcat $^ > $@

gfx/new_game/shrink1.2bpp: RGBGFXFLAGS += -Z
gfx/new_game/shrink2.2bpp: RGBGFXFLAGS += -Z

gfx/overworld/overworld.2bpp: gfx/overworld/puddle_splash.2bpp gfx/overworld/cut_grass.2bpp gfx/overworld/cut_tree.2bpp gfx/overworld/heal_machine.2bpp gfx/overworld/fishing_rod.2bpp gfx/overworld/shadow.2bpp gfx/overworld/shaking_grass.2bpp gfx/overworld/boulder_dust.2bpp ; $Qcat $^ > $@

gfx/pack/pack_left.2bpp: tools/gfx += --trim-whitespace
gfx/pack/pack_top_left.2bpp: gfx/pack/pack_top.2bpp gfx/pack/pack_left.2bpp ; $Qcat $^ > $@

gfx/paintings/%.2bpp: RGBGFXFLAGS += -Z

gfx/player/chris_back.2bpp: RGBGFXFLAGS += -Z
gfx/player/kris_back.2bpp: RGBGFXFLAGS += -Z
gfx/player/crys_back.2bpp: RGBGFXFLAGS += -Z
gfx/player/beta_back.2bpp: RGBGFXFLAGS += -Z

gfx/pokedex/%.bin: gfx/pokedex/%.tilemap gfx/pokedex/%.attrmap ; $Qcat $^ > $@
gfx/pokedex/oam.2bpp: tools/gfx += --trim-whitespace
gfx/pokedex/pokedex.2bpp: gfx/pokedex/pokedex0.2bpp gfx/pokedex/pokedex1.2bpp gfx/pokedex/area.2bpp ; $Qcat $^ > $@
gfx/pokedex/question_mark.2bpp: RGBGFXFLAGS += -Z

gfx/pokegear/pokegear.2bpp: tools/gfx += --trim-whitespace
gfx/pokegear/pokegear_sprites.2bpp: tools/gfx += --trim-whitespace

gfx/pokemon/%/back.2bpp: RGBGFXFLAGS += -Z

gfx/pc/obj.2bpp: gfx/pc/modes.2bpp gfx/pc/bags.2bpp ; $Qcat $^ > $@

gfx/slots/slots_1.2bpp: tools/gfx += --trim-whitespace
gfx/slots/slots_2.2bpp: tools/gfx += --interleave --png=$<
gfx/slots/slots_3.2bpp: tools/gfx += --interleave --png=$< --remove-duplicates --keep-whitespace --remove-xflip

gfx/splash/copyright.2bpp: gfx/splash/copyright.txt tools/fine_print.c
	$Qtools/fine_print -c 013 -s 0 '$(shell cat $<)' $@

gfx/stats/%.bin: gfx/stats/%.tilemap gfx/stats/%.attrmap ; $Qcat $^ > $@
gfx/stats/judge.2bpp: tools/gfx += --trim-whitespace

gfx/title/crystal.2bpp: tools/gfx += --interleave --png=$<

gfx/title/version.2bpp: Makefile tools/fine_print.c
	$Qtools/fine_print -e 20 '$(COPYRIGHT)' $@

gfx/title/suicune_unowns.2bpp: RGBGFXFLAGS += --unique-tiles --nb-tiles 127,127 --base-tiles 0,128
gfx/title/suicune_unowns.tilemap: RGBGFXFLAGS += --unique-tiles --nb-tiles 127,127 --base-tiles 0,128
gfx/title/suicune_unowns.tilemap: gfx/title/suicune_unowns.png
	$Q$(RGBGFX) -c dmg $(RGBGFXFLAGS) -t $@ $<

gfx/town_map/town_map.2bpp: tools/gfx += --trim-whitespace

gfx/trade/game_boy.2bpp: tools/gfx += --remove-duplicates --remove-xflip --remove-yflip --remove-whitespace
gfx/trade/link_cable.2bpp: tools/gfx += --remove-duplicates --remove-whitespace
gfx/trade/poof_cable.2bpp: gfx/trade/poof.2bpp gfx/trade/cable.2bpp ; $Qcat $^ > $@
gfx/trade/game_boy_cable.2bpp: gfx/trade/game_boy.2bpp gfx/trade/link_cable.2bpp ; $Qcat $^ > $@
gfx/trade/trade_screen.2bpp: gfx/trade/border.2bpp gfx/trade/textbox.2bpp ; $Qcat $^ > $@

gfx/trainer_card/chris_card.2bpp: RGBGFXFLAGS += -Z
gfx/trainer_card/kris_card.2bpp: RGBGFXFLAGS += -Z
gfx/trainer_card/crys_card.2bpp: RGBGFXFLAGS += -Z
gfx/trainer_card/beta_card.2bpp: RGBGFXFLAGS += -Z

gfx/trainers/%.2bpp: RGBGFXFLAGS += -Z

gfx/type_chart/bg.2bpp: tools/gfx += --remove-duplicates --remove-xflip --remove-yflip
gfx/type_chart/bg0.2bpp: gfx/type_chart/bg.2bpp.vram1p gfx/type_chart/bg.2bpp.vram0p ; $Qcat $^ > $@
gfx/type_chart/ob.2bpp: tools/gfx += --interleave --png=$<


gfx/pokemon/%/front.animated.2bpp: gfx/pokemon/%/front.2bpp gfx/pokemon/%/front.dimensions
	$Qtools/pokemon_animation_graphics -o $@ $^
gfx/pokemon/%/front.animated.tilemap: gfx/pokemon/%/front.2bpp gfx/pokemon/%/front.dimensions
	$Qtools/pokemon_animation_graphics -t $@ $^
gfx/pokemon/%/bitmask.asm: gfx/pokemon/%/front.animated.tilemap gfx/pokemon/%/front.dimensions
	$Qtools/pokemon_animation -b $^ > $@
gfx/pokemon/%/frames.asm: gfx/pokemon/%/front.animated.tilemap gfx/pokemon/%/front.dimensions
	$Qtools/pokemon_animation -f $^ > $@



%.lzp: %
	$Qtools/lzpcompress -- $< $@

#%.4bpp: %.png
#	$Qsuperfamiconv tiles -R -i $@ -d $<

%.2bpp: %.png
	$Q$(RGBGFX) -c dmg $(RGBGFXFLAGS) -o $@ $<
	$(if $(tools/gfx),\
		$Qtools/gfx $(tools/gfx) -o $@ $@)

%.1bpp: %.png
	$(RGBGFX) -c dmg $(RGBGFXFLAGS) -d1 -o $@ $<
	$(if $(tools/gfx),\
		$Qtools/gfx $(tools/gfx) -d1 -o $@ $@)

%.2bpp.vram0: %.2bpp
	$Qtools/sub_2bpp.sh $< 128 > $@

%.2bpp.vram1: %.2bpp
	$Qtools/sub_2bpp.sh $< 128 128 > $@

%.2bpp.vram2: %.2bpp
	$Qtools/sub_2bpp.sh $< 256 128 > $@

%.2bpp.vram0p: %.2bpp
	$Qtools/sub_2bpp.sh $< 127 > $@

%.2bpp.vram1p: %.2bpp
	$Qtools/sub_2bpp.sh $< 127 128 > $@

%.2bpp.vram2p: %.2bpp
	$Qtools/sub_2bpp.sh $< 255 128 > $@

%.vwf.1bpp: %.2bpp
	$Qtools/vwf -o $@ $<

%.vwf.widths: %.2bpp
	$Qtools/vwf -w $@ $<

%.dimensions: %.png
	$Qtools/png_dimensions $< $@

data/tilesets/%_collision.bin: data/tilesets/%_collision.asm
	$QRGBASM=$(RGBASM) RGBLINK=$(RGBLINK) tools/collision_asm2bin.sh $< $@
