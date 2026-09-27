# 宝可梦中文化 · 工具链

本文件说明中文构建怎么把译文塞进 ROM、以及新增一个文件要动哪几处。
用词与标点规范见 `docs/TRANSLATION_STYLE.md`。

## 两条通路

同一个引擎里，文本文件的"归属"不同，替换方式也必须不同：

| 通路 | 适用文件 | 替换方式 | 产物 |
|---|---|---|---|
| **对象替换** | `data/text/*.asm` | 每个文件编成独立的 `.o`，中文构建直接换掉这个 `.o` | `data/text/zh/<stem>.asm` |
| **条件 INCLUDE** | `tools/zh/include_sources.txt` 里列的 | 文件被别的 `.asm` INCLUDE 进同一个目标文件（`engine/*.asm` 进 `main.o`，`data/options/*.asm` 进 `options_menu.o`），汇编器把它们当作宿主文件的一部分，没法单独换 `.o`，只能在 INCLUDE 点换 | `data/text/zh_include/<stem>.asm` |
| **剧本清单** | `tools/zh/map_sources.txt` 里列的地图脚本 | `maps/*.asm` **没有自己的目标文件**：654 个文件全被 `data/maps/scripts.asm` 收进同一个 `data/maps/map_data.o`，既不能换 `.o`、也不该改 654 处 INCLUDE 点。于是把 `scripts.asm` 本身变成生成物：逐行复制原文件，只把收录地图的 INCLUDE 换成中文副本 | `data/text/zh_maps/<stem>.asm` + `data/text/zh_maps/scripts.asm` |

条件 INCLUDE 的写法（以 `main.asm` 为例）：

```asm
IF DEF(LANG_ZH)
INCLUDE "data/text/zh_include/main_menu.asm"
ELSE
INCLUDE "engine/menus/main_menu.asm"
ENDC
```

英文构建走 `ELSE` 分支，逐字节仍是原文件。

## 目录

```
tools/zh/
  zh.py                      工具入口（text-extract / text-encode / text-validate / text-decode /
                             map-scripts ...）
  text_ir.py                 文本块的抽取与重新编码
  check_draft.py             译文草案预校验（draft.json / --all 回溯全部已译条目）
  verify_engine.py           PyBoy 引擎运行时核对（--scenario demo / newgame）
  verify_menus.py            上机菜单场景：初始选项菜单 -> START 菜单 -> 存档对话/背包/选项，
                             逐屏截图 + 引擎核对（截图在 tools/zh/verify_out/menus-*.png）
  include_sources.txt        需要「条件 INCLUDE」换入的源文件清单
  map_sources.txt            收录的地图脚本（第三类通路，逐行同上不能有注释）
  glossary/glossary.tsv      术语表（同时决定字库要收哪些字）
  translations/text/*.json    对象替换那批的翻译 IR
  translations/include/*.json 条件 INCLUDE 那批的翻译 IR
  translations/maps/*.json    地图剧本的翻译 IR
  translations/*.json         演示/示例文本（zh demo ROM 用）
data/text/zh/*.asm           对象替换的产物
data/text/zh_include/*.asm   条件 INCLUDE 的产物
data/text/zh_maps/*.asm      地图副本；其中 scripts.asm 是换入清单（Makefile 自动重生成）
data/font/zh_glyphs.asm      字库（gen-font 生成，字形集来自 glossary + 全部 IR）
```

## 可译条目的两种形态

`text_ir.py` 把源文件里的文本拆成**条目**，键唯一：

- **宏块**（`kind = "block"`）：`text` / `text_far` 包起来的整段文本，键就是标签名，
  例如 `ElmText1`。一个条目的文本里可能夹着 `{{0}}` 这样的占位命令，
  它代表一段不可翻译的汇编（通常是"这段文本在别处"），必须原样保留。
- **字符串表条目**（`kind = "unit"`）：菜单/界面里 `db "..."` 形式的短字符串，
  键是 `作用域#序号`，例如 `MainMenu.Strings#0`。作用域是
  `全局标签.局部标签`（局部标签只在同一个全局标签内唯一）。

`{{0}}` 这种整块只有一个占位命令的条目不用翻译：真正的文本由那条命令指到
`data/text/*.asm`，翻那边即可。留空表示未翻译，构建时逐行回退原文。

## 日常流程

```bash
# 1. 抽取/更新 IR（保留已有译文，漏译不会导致编译失败）
python tools/zh/zh.py text-extract --only <stem> ...
# 不传 --only 就是全量。

# 2. 编辑 tools/zh/translations/<通路>/<stem>.json，把 zh 填上

# 3. 补字库：新译文用到的新字要先变成字形，否则第 4 步会报一堆「无法编码」
python tools/zh/zh.py gen-font

# 4. 校验（控制码完整性、行宽、可编码性）
python tools/zh/zh.py text-validate --only <stem>

# 5. 构建
make zh            # 中文（MBC5，产物带 -zh 后缀）
make crystal       # 英文，用于确认零回归
```

**验收门**是第 6 步反向核对：把生成的 `.asm` 字节流解回文字，与 IR 里的译文逐条对照：

```bash
python tools/zh/zh.py text-decode          # 期望「0 处问题」
python tools/zh/zh.py text-decode --only <stem> --show
```

它顺带报「引用多少个字形；单条最多几个（`ZH_SLOT_COUNT=23`）」——单条译文用到的
**不同**汉字不能超过 23 个（文本引擎一次只能装这么多槽位）。
（注：引擎在每个 `<PARA>` 换页时 `call ClearSpeechBox` → `Zh_ResetSlots`，槽位是**按页**
重置的，所以实际限制是每页 ≤ 24 槽。`text-decode` 已按页统计：第二批译文按整条算会报
86 处「槽位不足」，按页算单页最多 20 个字形，全部安全。译文写长对话时只需盯住单页。）

`gen-font` 的字形集默认 = **译文里真正用到的字**（三个 IR 目录：`translations/maps/`、
`translations/text/`、`translations/include/`）。术语表里的字**默认不刻**——每字 32 字节，
术语表一扩充就白占 ROM；要刻得显式加 `--with-glossary`。铺开一批新文件后必须重跑，
否则字库里没有那些字。

## 译文的两个硬要求

- **终止符不能丢**：`db "Fight@"` 的 `@` 是原文的一部分，译文同样要以
  `@` / `<DONE>` / `<PROMPT>` 收尾。漏了会报「缺少终止符」，构建时该条被**静默跳过**
  （回退英文）——批量写译文最容易踩这个坑。
- **首尾空格有意义**：菜单串靠空格对齐（`db " Money@"` 的开头空格、定宽值串的尾部补白），
  所以 `text_ir._clean_zh` 只做 `rstrip()`，仅在译文里出现换行/制表符时才整体 strip。
  译文占用的 tile 宽度建议**与原文字节数一致**（汉字 2 tile、半角 1 tile），
  定宽选项值靠尾部空格补齐到原宽度，切换值重绘时才不会留下残影。

## 新增一个文件

以 `engine/menus/foo.asm` 为例：

1. 把路径写进 `tools/zh/include_sources.txt`（**纯路径清单，每行一个，不能写注释**
   —— Makefile 用 `$(file <)` 直接读它）。
2. 在它的 INCLUDE 点加 `IF DEF(LANG_ZH)` / `INCLUDE "data/text/zh_include/foo.asm"` /
   `ELSE` / `ENDC`。
3. `python tools/zh/zh.py text-extract --only foo` 生成 IR。
4. 填译文 → `text-validate` → `make zh`。

对象替换那批不需要第 1、2 步：往 `data/text/` 放文件、跑一次 `text-extract` 就会自动纳入。

**stem 必须全仓库唯一**（取文件名去掉 `.asm`）：它同时决定 IR 文件名、产物文件名和
`--only` 参数，撞名时 `zh.py` 会直接报错。

## 地图剧本（第三类通路）

`maps/*.asm` 是地图脚本：里面既有 `applymovement` / `warpto` / `opentext` 这类指令，
也有 `text` 宏包起来的对白。
它们**没有自己的目标文件**——`data/maps/scripts.asm` 把 654 个地图 INCLUDE 进单一的
`data/maps/map_data.o`，所以前两条通路都用不上。做法是把 `scripts.asm` 本身变成生成物：
逐行复制原文件，只把收录地图的 INCLUDE 换成中文副本。

流程（第 1、2 步只在**新收录**一个地图时手工做）：

```bash
# 1. 把路径写进 tools/zh/map_sources.txt（纯路径清单，不能有注释）
# 2. 抽 IR（--only 用文件名去掉 .asm）
python tools/zh/zh.py text-extract --only NewBarkTown
# 3. 填译文 -> text-validate -> 生成/更新换入清单
python tools/zh/zh.py gen-font
python tools/zh/zh.py text-encode --only NewBarkTown
python tools/zh/zh.py map-scripts      # make zh 会自动跑到这一步
# 4. 构建
make zh
```

**验收必须回到字节层**——这类通路最坑的失败模式是"IR 里有译文、构建也成功、ROM 里还是英文"。
2026-09-16 实测（NewBarkTown，19 条 IR）：

- 一条未翻时，`data/text/zh_maps/NewBarkTown.asm` 与源文件**逐字节一致**（只多三行注释头）；
- 因此装了这套系统但零翻译时，中英两个 ROM 的 SHA-256 **都不变**（英文 `b28ef43a…`、
  中文 `631a0339…`）；
- 译一条 `Text_WaitPlayer` → `等一下，<PLAYER>！<DONE>` 后，`text-decode --only NewBarkTown
  --show` 还原出这句，且这 12 个字节能在 ROM 里找到（偏移 672306）。

踩过的坑：

- **生成清单里的 INCLUDE 必须写仓库根相对路径**。`tools/scan_includes` 不做"相对当前文件"
  的解析，写成同级相对名会让 `map_data.o` 依赖一个不存在的裸文件名，实测直接
  `No rule to make target 'NewBarkTown.asm'`。rgbasm 会依次试"相对当前文件"和"相对 cwd"，
  写成根相对两种情况下都能找到。
- **地图用单行冒号标签**。`data/text/*.asm` 的文本标签写作 `Foo::`，地图脚本写作 `Foo:`；
  反向核对用的 `LABEL_ONLY_RE` 原本只认双冒号，会把整份地图副本读成"没有条目"
  （症状：`text-decode --only <map>` 报「引用 0 个字形」，明明已经译了）。现放宽成 `::?`。
- **含内部 INCLUDE 的地图暂时不换**：只有一个文件里写了 `INCLUDE`
  （指向 `data/events/trainer_house_opponents.asm`）。副本换了目录后路径基准不一致，
  `map-scripts` 会跳过它并打印提示，宁可这条地图保持英文也不生成编不过的文件。

## 构建隔离（英文构建零回归）

中文产物只出现在两处，都受 `LANG_ZH` 保护：

- `data/text/zh/*.asm`：靠 `zh_real_stems` 把对应 `.o` 从英文对象列表里换掉。
- `data/text/zh_include/*.asm`：靠 INCLUDE 点的条件汇编。

但 Makefile 的 `DEP` 用 `tools/scan_includes` 算依赖，它**不认条件汇编**，会把
`IF` 两个分支的路径都当成依赖，于是英文构建也会被拉去生成中文产物。所以
`DEP`/`VCDEP` 里加了过滤：

```make
zh_include_filter := $(if $(filter zh,$(MAKECMDGOALS)),,data/text/zh_include/% data/text/zh_maps/%)
```

地图剧本的换入点（`data/text/zh_maps/scripts.asm`）同理被过滤；它在 zh 构建里由
`data/maps/map_data.o` 那条额外依赖补回来。

英文构建把这些路径从依赖里剔除，从而完全不依赖 Python、字库和 `tools/zh/`。

零回归的硬证据（已验）：

1. `git diff` 在宿主文件里只有 `IF DEF(LANG_ZH)` / `INCLUDE "data/text/zh_include/…"` /
   `ELSE` / `ENDC` 四类新增行，原 `INCLUDE "engine/…"` 行一字未动
   （地图那处同理：原 `INCLUDE "data/maps/scripts.asm"` 行保留在 `ELSE` 分支里）；
2. 补全部包装前后，英文 ROM 的 SHA-256 完全相同（实测 `b28ef43a…`）——逐字节零回归。

想自己看"这个对象到底 INCLUDE 了哪些文件"，别用 `rgbasm -E`：RGBDS 里 `-E` 是
export-all-labels，**不是**输出预处理源码（踩过，会得出错误结论）。正确工具是
`rgbasm -M <depfile>`，或 Makefile 用的 `tools/scan_includes`。

**`make -B` 的例外**：正常 `make` / `make zh` 都干净，但实测 `make -B`（强制重建全部目标）
会让英文构建也跑一次 `text-extract`（原因未深究，估计强制重建把 IR 规则也拉了进来）。
要验证"英文构建不碰 Python"，用 `touch main.asm && make` 这种正常增量路径，别用 `-B`。

**别绕过 `make zh` 直接手跑 `zh.py text-encode`**：汉字字形是**按引用顺序编号**的，
必须先 `gen-font` 定下索引。手跑 encode 会拿旧索引编码，于是 `text-decode` 报一大片
「还原不一致」（实测踩过：190 处，`这是一棵` 被还原成 `跑新一栏`）。
要么走 `make zh`（它保证先 gen-font 再全量重编码），要么手工先跑一次
`zh.py gen-font` 再 encode。

## 排版口径（控制码怎么换才算对）

这批规则是回溯全部已译条目时反推出来的，写译文前先看这里，能省掉返工：

- **`<LINE>` 与 `<CONT>` 可以互换** —— `<CONT>` 是「滚动一行」、`<LINE>` 是「移到下一行」，
  中文更紧凑时把 `LINE` 换成 `CONT` 是正常的排版适配，**不要求**与原文的控制码多重集一致。
  历史批次有大量这类差异，逐条比对只会噪声淹没真问题。
- **`<PARA>` 数量不能白减** —— `PARA` 是「等待按键后清屏」，少一个就少一次阅读停顿。
  但中文紧凑导致**行数同步减少**时的合并是合理的（`check_draft.py` 只在
  「行数没减、分段却变少」时才报错，即每屏被塞得更挤）。
- **占位符比的是「出现顺序」，不是多重集** —— `{{0}}` 必须排在 `{{1}}` 前面。
  中文自然语序常想写成「对{{1}}使用了{{0}}」，这会被 `text-validate` 判
  `[占位符不匹配]`。要改成「用{{0}}对{{1}}使出」这类保序写法。
  （`Text_EnemyUsedOn`、`_PokemonTookItemText` 都栽过。）
- **行宽按 tile 算，且要排除非渲染内容**：全角 2 / 半角 1，
  `<PLAYER>`/`<ENEMY>`/`<USER>`/`<TARGET>`/`<RIVAL>` 各按 7 tile 展开，
  `…` 与 `♪` 虽是 ambiguous 宽度但引擎按全角渲染（兜 2），
  `{d:CONST}` 是汇编期数值常量、结尾的 `@` 是终止符 —— 两者都不占显示宽度，
  按字面算会得出假超宽（如 `皮皮  {d:GOLDENRODGAMECORNER_...}` 曾被算成 50 tile）。
- **菜单项/数据表不适用对话框宽度限制**：键名含 `MenuData`/`MenuStrings`/`MenuHeader`
  的条目走菜单框，宽度更大，`check_draft.py` 会跳过它们的行宽检查。

`python tools/zh/check_draft.py draft.json` 在写进 IR 之前把上面这些一次查完；
`--all` 则回溯校验 IR 里全部已译条目。

### 有一类条目不要填译文

IR 里有约 48 条**没有自然语言内容**的条目，保持未译（英文原样）才是正确状态，
**不要**为了凑进度把它们设成 `zh = en`：

- 纯占位符：`{{0}}`、`<PLAYER>@`、`' @'`；
- 菜单数据结构：键名含 `MenuData`/`MenuHeader` 的条目，内容是坐标与格式串；
- 界面格式串与专有符号：`QWERTY`、`MICR`、`BP`、`100%`、`′??″`、`---`、`<BOLDP>/   %`。

踩过的坑：把它们照搬成「译文」会让 `make zh` 在生成阶段直接失败
（`data/text/zh_include/intro_menu.asm` Error 1）—— 因为这些内容含 `<LNBRK>`
等非文本控制码、或依赖固定宽度的 ASCII 对齐，编码时会炸。
判断方法：把控制码与占位符去掉后，剩下的可见内容为空或纯符号 -> 不译。

## 换入点清单（20 个，漏一个就白译）

`data/text/zh_include/<stem>.asm` 必须在宿主文件的 INCLUDE 点用 `IF DEF(LANG_ZH)` 换入，
否则文件生成了却**永远进不了 ROM**——症状最坑：`make zh` 成功、译文也在 IR 里，
但游戏里还是英文（`data/text/std_text.asm` 就踩过这个坑）。20 个换入点分两处：

- `main.asm`：16 处（`engine/menus/` 下 10 个 + `engine/items/pack` +
  `engine/pokemon/party_menu` + `engine/pokemon/mon_menu` + `engine/battle/menu` +
  `engine/pokedex/pokedex` + `data/text/std_text`）；
- `engine/menus/options_menu.asm`（2 处）、`engine/menus/initial_options_menu.asm`（2 处）：
  `data/options/*.asm` 是被这两个文件 INCLUDE 的，**不是** main.asm。

注意 `data/text/` 下的文件是**两条通路的交叉区**：`data/text/*.asm` 默认走对象替换
（每个文件编成独立目标文件，中文构建换掉这个目标文件），但被别的 SECTION INCLUDE 的
文件（如 `std_text.asm`）没有自己的目标文件，必须改走条件 INCLUDE 通路。收录进
`include_sources.txt` 后，`text_source_paths()` 会自动把它从对象替换通路里排除——
否则会多出一份永不生效的 IR，`text-decode` 核对时报「还原不一致」（ROM 里是英文、IR 里是中文）。

`engine/menus/options_menu.asm` 里的包装会被 `text-encode` **原样复制**进生成的
`data/text/zh_include/options_menu.asm`，所以嵌套换入只在原始文件里写一次即可；
但改完必须重跑 `text-extract` + `text-encode`，让生成文件跟上（否则生成文件里没有包装，
嵌套的 `option_names` / `options_descriptions` 仍是英文）。

自检：把生成物和 ROM 对一遍，别只看"编译成功"。要**按 `;@ 键` 标记逐条**取字节，
不能用"连续 `db` 行"这种粗办法——后者会把紧随其后的未译 `db "..."` 行、以及
`db $68, $18, 0, 0, 0, 0` 这类非文本数据表拼进来，造出一堆假的"未命中"（实测 17 条假阴性）：

```bash
python - <<'PY'
import pathlib, re
rom = pathlib.Path('polishedcrystal-zh-3.2.3.gbc').read_bytes()
tot = hit = 0
for f in sorted(pathlib.Path('data/text/zh_include').glob('*.asm')):
    ent, cur, key = [], [], None
    for line in f.read_text(encoding='utf-8').splitlines():
        m = re.match(r';@ (\S+)', line)
        if m:
            if key and len(cur) >= 4: ent.append((key, bytes(cur)))
            key, cur = m.group(1), []
            continue
        if key is not None and re.match(r'\s*db\s', line):
            cur += [int(x,16) for x in re.findall(r'\$([0-9A-Fa-f]{2})', line)]
    if key and len(cur) >= 4: ent.append((key, bytes(cur)))
    hit += sum(1 for _, b in ent if b in rom); tot += len(ent)
    if ent: print(f'{f.name:30s} {sum(1 for _, b in ent if b in rom)}/{len(ent)}')
print(f'{hit}/{tot}')
PY
```

（`data/text/zh/*.asm` 是整对象替换，同样按标记核一遍即可。）最终判据还是
`text-decode` 的「0 处问题」+ 上机看画面。

## 已知缺口（还没解决，别当成新 bug 重新踩）

- **容量已实测并解锁（2026-09-18）**——原先「卡在 128 个 bank」的判断是错的：
  - `tools/bankends.c` 里 `#define BANKS 128` 是那个小工具自己硬编码的，它返回非 0 就让
    make 失败，所以 `unknown ROM bank $80` 报的其实是**空间超限**，不是非法 bank。
  - 引擎侧：`Bankswitch` 只写 `[rROMB]`（`$2000`，低 8 位），故 bank `$01`-`$FF`
    （255 个、约 4MB）都可用；**只有超过 `$FF` 才需要写 `$3000`（第 9 位）**，
    所以扩到 4MB 不用动引擎。
  - 实测：rgblink 可排到 bank #200；真实构建用 961 字形撑到 128 banks（ROM 4MB），
    `make zh` 通过，PyBoy 跑 `newgame` 场景汉字正常出屏（抓到「霹」）。
  - 中文构建因此改用 `zh.py gate` 做空间报告/门禁（`bankends.exe` 编不动：本机无 C 编译器），
    上限由 `ZH_ROMX_BANKS`（默认 255）控制；英文构建仍是原来的 C 版 `bankends`。
  - 预算看 `zh.py capacity`：全量待译 266KB、压缩比约 0.78、临界字数约 2676（32 字节/字），
    而字集外推 5000+，所以**全量翻译必定突破 2MB**——靠扩容兜住，不必裁字模。
  - 注意：ROM 一旦超 2MB 就会变成 4MB 的文件，分发补丁要跟着验（自定义 `.patch`，
    `tools/make_patch.c` 未见 2MB 假设，但没实测过 4MB）。

- **`db` + `next` 续行串（已解决，2026-09-26）**。源码里

  ```asm
  	db   "<PK><MN> are listed in"
  	next "regional order.@"
  ```

  这种"一条字符串拆两行"的写法曾导致抽取器只把 `db` 行当单位、`next` 行不进
  IR。现在 `extract_units` 会把紧随的 `next "..."` 并进同一单位（文本里记成
  `<NEXT>`），`build` 跳过被并掉的行，键仍取 `db` 行所以旧译文不错位。
  `engine/pokedex/pokedex.asm` 的 `_Pokedex_Mode.MenuDescriptions#0..3` 已按此
  译出并通过 `text-validate` / `text-decode`。
- **同行 `标签: db "..."` 形态抽不到**。抽取器只认「标签单独一行、`db` 在下一行」
  的块；`engine/menus/start_menu.asm` 的菜单项名写成 `.SaveString:  db "Save@"`，
  收录后实测抽出 0 条（已回退，`include_sources.txt` 未收录它）。所以 START 菜单
  的 Bag/Save/Options/Exit 等项名仍是英文。修法二选一：抽取器支持同行 `db`，
  或把源码改成标签独占一行（后者要动引擎源码排版，需另行评估）。
  `mom.asm` 的 `db "Save@"` 等同理。
- **START 菜单等 ASCII 界面与 CJK 槽位共享 `$80-$F1` 瓦片池**。对话框路径由
  `ClearSpeechBox -> Zh_ReleaseHiddenSlots` 在清屏时把不再可见的槽位换回字库；
  但 START 菜单这类不经过 `ClearSpeechBox` 的界面直接画 ASCII（如 `Save` 的
  `'S'=$80`、`'a'=$a0`），此时仍存活的 CJK 槽位其 2x2 单元会被 ASCII 单格瓦片
  打断。`verify_engine` 的「池内 tilemap 引用必成 2x2」检查在这种界面天然误报
  （渲染经截图确认正确，且后续汉字绘制正常 = 引擎会在下次分配时自恢复）。
  `tools/zh/verify_menus.py` 已把这类报错降级为「已知限制」警告；在
  `verify_engine` 里改判定前先想清楚，别把真乱码也放过去。
- **2×2 战斗菜单里的 `<PK><MN>` 保留原文**。`engine/battle/menu.asm` 的框是
  `menu_coords 8, 12, 19, 17`，第二列文字起点 x=15、右边框 x=19，只有 4 tile 可用；
  `<PK><MN>` 占 2 tile，而「宝可梦」要 6 tile（会压到边框）。要改就得同时把框往左挪
  （属版面改动，需上机确认），所以先保留。
- **`PokedexStr_Feet`（`′??″`）保留原文**：英制专用格式，中间的 `??` 是画完字符串后
  **原地替换**成数字的，任何宽度变化都会错位。公制是默认选项，影响很小。
- **`String_PowAcc`（`   <BOLDP>/   %`）保留原文**：同理，空格位置由另一处按固定偏移
  打印的数字填充。
- **6 条「行长偏长」告警是误报**：`text-validate` 对 `db` 单位用的上限是 16 tile（偏保守）。
  `party_menu` 那 6 条中文都比英文窄（英文 19~23 tile），判断标准应是「不宽于原文」。
- **`tools/zh/translations/engine/` 残留目录已删（2026-09-26）**：改名前的 15 个旧 IR，
  工具链早已不读，已整目录删除。
- **IR 会覆盖"不是独立对象"的文件**。全量 `text-extract` 会给每个 `data/text/*.asm`
  建 IR，但其中有些只是在别的文件里被 SECTION 内 INCLUDE 的片段（如 `ngrams.asm`），
  单独汇编会报 `Cannot define label 'NgramStrings' outside of a SECTION`。
  Makefile 的 `zh_real_stems` 因此和 `rom_obj` 里真实存在的 `data/text/*.o` 取了交集，
  这类文件的译文目前**不会生效**（它们要走 INCLUDE 通路才行了）。
- **名字表（宝可梦名 / 招式名 / 道具名）不汉化 —— 是架构限制，不是漏翻**。
  `data/pokemon/names.asm`（固定宽 `table_width MON_NAME_LENGTH - 1` + `rawchar`）、
  `data/moves/names.asm`（`list_start` / `li`，256 条）、`data/items/names.asm`
  三张表都是**全局共享**的裸数据表，任何界面都能随时取用任意条目。
  而中文走的是 **per-block 动态槽位字库**：池子只有 `$80-$F1` 共 114 tile，
  24 槽 × 4 tile = 96 tile，理论上限 28 槽（受
  `ASSERT ZH_SLOT_COUNT * ZH_GLYPH_TILES <= ZH_POOL_SIZE` 约束）。
  列表界面一屏要同时显示 5-8 个名字（每个 3-6 汉字 = 15-48 个字形），**远超 28 槽**；
  而 `constants/zh_text.asm` 的注释写明「槽位用满时汉字显示成**空白**」——
  也就是说即使把名字翻成中文，列表里也是一片空白。
  **更硬的证据在引擎里**：`engine/gfx/cjk_text.asm` 的 `ZhPinNameChars` 注释写着
  「把整套字母、数字与常见标点钉住：名字/星期/数字这类串**运行时才拼出来，
  look-ahead 看不见**」。即渲染器预先扫描字节流来装字，而名字是运行时拼出来的，
  扫描不到内容 —— 现在只能靠"钉住整套 A-Z"（26 个 tile）兜住。换成汉字就要钉住
  整套字集（数千个），池子只有 114 tile，**根本装不下**。
  要汉化必须改引擎字库方案：可行方向是列表渲染时逐个名字动态装字 + 分页，
  属引擎级改动，需另立任务评估。当前**保持英文原名是正确状态**。

  **已探明的技术路线**（若日后立项，按这个走，别重复探查）：
  1. *编码*：汉字是 **2 字节索引** + `$53` 终止（见 `data/text/zh_include/*.asm`，
     如「性格」= `db $0e,$e9, $10,$d5, $53`）。名字表若沿用这个编码，变长表
     结构仍然成立（条目靠终止符切分）。
  2. *宏*：`macros/asserts.asm` 里 `li` 就是 `db \1, "@"`，且 `assert_list_length`
     靠 `li` 递增 `list_index` 计数 —— 所以不能直接用裸 `db` 替换，否则条目数断言
     失败。需要新增支持字节序列的 `li` 变体，或让生成器直接产出并保持计数。
  3. *装字*：`GetMoveName`/`GetItemName` 都汇到 `home/names.asm` 的 `GetName`，
     它把名字拷进 `wStringBuffer1`。拷贝后名字就在缓冲里、**可以扫描**，
     所以在这里扫描汉字索引 → 装载字形 → 钉住（中文版 `ZhPinNameChars`），
     是被回收问题唯一可行的切入点。
  4. *槽位*：一屏多个名字会争抢 24 槽，列表滚动时要重新装字；需逐界面上机调。

  **原型已实测的部分**（2026-09-27，做完后已回滚，不留未验证改动）：
  把 3 个初始宝可梦名按上述编码改成中文 10 字节条目 —— `make zh` **构建通过**，
  且从 ROM 里读回字节确认无误（idx155 = `11 cf 12 88 16 d0` = 火球鼠）。
  所以「改名字表」这一步**不是障碍**。编码换算：

  ```
  字形序号 i -> 字节：lead = i // 128 + $0a，trail = (i % 128) | $80
  反推：i = (lead - $0a) * 128 + (trail & $7f)      ; 序号表见 glyph_map()
  ```

  **运行时行为未上机验证，但已能从代码判定**（两次导航尝试都失败：连按 A 跑
  4000 帧、Start+A 跑 7000 帧，都没推进到显示宝可梦名的界面，故回滚收尾）。
  判定依据：名字显示后其瓦片出现在 tilemap 上，会被 `ZhScanScreenTiles` 标成
  live，所以**静态显示时不会被回收**；真正的风险是**槽位争抢** —— 列表界面
  一屏多个中文名（每个字 4 tile）加上同屏对话文字，24 槽会不够，
  后分配者挤掉先显示者的字形（汉字变空白）。这也就是 `ZhPinNameChars` 要
  「钉住整套 A-Z」的原因：名字运行时才拼出来，预扫描（look-ahead）看不见，
  只能靠钉住兜底；而汉字无法整套钉住（池子只有 114 tile）。
  要上机验证必须先解决导航（直接跳到选宝可梦界面，不要用按键硬跑）。
