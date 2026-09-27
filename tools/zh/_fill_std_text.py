"""一次性脚本：填 std_text.json（宝可梦中心护士/场景物件/捕虫大赛/游戏角等 65 条）。

写入前做三项校验：控制码+占位符多重集一致、终止符齐全、每行 tile 宽 <= 18。
校验不过就不落盘。用完即删。
"""
import collections
import json
import pathlib
import re
import unicodedata

P = pathlib.Path(__file__).resolve().parent / "translations" / "text" / "std_text.json"

T = {
    # --- 宝可梦中心护士 ---
    "NurseMornText": "早上好！<LINE>欢迎来到<CONT>宝可梦中心。<DONE>",
    "NurseDayText": "你好！<LINE>欢迎来到<CONT>宝可梦中心。<DONE>",
    "NurseEveText": "晚上好！<LINE>欢迎来到<CONT>宝可梦中心。<DONE>",
    "NurseNiteText": "晚上好！<LINE>这么晚还在外面。<PARA>欢迎来到<LINE>宝可梦中心。<DONE>",
    "PokeComNurseMornText": "早上好！<PARA>这里是宝可梦<LINE>通信中心，<PARA>大家简称它<LINE>通信中心。<DONE>",
    "PokeComNurseDayText": "你好！<PARA>这里是宝可梦<LINE>通信中心，<PARA>大家简称它<LINE>通信中心。<DONE>",
    "PokeComNurseEveText": "晚上好！<PARA>这里是宝可梦<LINE>通信中心，<PARA>大家简称它<LINE>通信中心。<DONE>",
    "PokeComNurseNiteText": "这么晚还在<LINE>努力呀，真好。<PARA>这里是宝可梦<LINE>通信中心，<PARA>大家简称它<LINE>通信中心。<DONE>",
    "NurseAskHealText": "我们可以把你的<LINE>宝可梦<CONT>完全治愈。<PARA>要治疗你的<LINE>宝可梦吗？<DONE>",
    "NurseTrainerStarText": "我们可以把你的<LINE>宝可梦<CONT>完全治愈。<PARA>要治疗吗？<PARA>那、那张<LINE>训练家卡片！<PARA>多么漂亮的<LINE>配色！还有那<CONT>闪亮的星星！<PARA>你真是位<LINE>出色的训练家。<PARA><PLAYER>，<LINE>让我来治疗<CONT>你的宝可梦吧？<DONE>",
    "NurseTheUsualText": "又见到你了，<LINE><PLAYER>！<PARA>还是要<LINE>老样子，对吧？<DONE>",
    "NurseTakePokemonText": "好的，请把你的<LINE>宝可梦交给我。<DONE>",
    "NurseReturnPokemonText": "感谢你的<LINE>等待。<PARA>你的宝可梦<LINE>都恢复健康了。<DONE>",
    "NurseGoodbyeText": "欢迎<LINE>下次再来。<DONE>",
    "NursePokerusText": "你的宝可梦<LINE>似乎<PARA>感染了微小的<LINE>生命体。<PARA>不过它们很<LINE>健康，看起来<CONT>没有问题。<PARA>但更多的事<LINE>我们无法<PARA>在宝可梦<LINE>中心告诉你。<DONE>",
    "PokeComNursePokerusText": "你的宝可梦<LINE>似乎<PARA>感染了微小的<LINE>生命体。<PARA>不过它们很<LINE>健康，看起来<CONT>没有问题。<PARA>更多的事<LINE>我们无法告诉你。<DONE>",
    # --- 场景可互动物件 ---
    "DifficultBookshelfText": "里面全是<LINE>很难的书。<DONE>",
    "PictureBookshelfText": "收藏了一整套<LINE>宝可梦<CONT>绘本！<DONE>",
    "MagazineBookshelfText": "宝可梦杂志…<LINE>宝可梦之友、<PARA>宝可梦手册、<LINE>宝可梦画报…<DONE>",
    "TeamRocketOathText": "火箭队誓言<PARA>为了利益<LINE>偷走宝可梦！<PARA>为了利益<LINE>利用宝可梦！<PARA>一切宝可梦<LINE>都是为了<CONT>火箭队的荣光！<DONE>",
    "IncenseBurnerText": "这是一个<LINE>香炉！<DONE>",
    "MerchandiseShelfText": "好多宝可梦<LINE>周边商品！<DONE>",
    "TownMapText": "这是城镇地图。<DONE>",
    "DiplomaText": "这是奖状。<DONE>",
    "WindowText": "我的倒影！<LINE>看起来不错！<DONE>",
    "TVText": "这是一台电视。<DONE>",
    "WrongSideText": "哎呀，搞错边了。<DONE>",
    "RadioOffAirText": "收音机只是<LINE>发出杂音…<DONE>",
    "RefrigeratorText": "这是冰箱。<DONE>",
    "SinkText": "这是水槽。<DONE>",
    "StoveText": "这是炉灶。<DONE>",
    "TrashCanText": "里面<LINE>什么都没有…<DONE>",
    "PokeCenterSignText": "治愈你的宝可梦！<LINE>宝可梦中心<DONE>",
    "MartSignText": "满足你所有<LINE>宝可梦需求<PARA>宝可梦商店<DONE>",
    # --- 捕虫大赛结果 ---
    "ContestResults_ReadyToJudgeText": "现在就来评审<LINE>你抓到的<CONT>宝可梦。<PARA>……<LINE>……<PARA>已经选出<LINE>获胜者！<PARA>准备好了<LINE>吗？<DONE>",
    "ContestResults_PlayerWonAPrizeText": "<PLAYER>获得了<LINE>第{{0}}名，<CONT>奖品是{{1}}！<DONE>",
    "ContestResults_JoinUsNextTimeText": "下届捕虫大赛<LINE>也请来参加！<DONE>",
    "ContestResults_ConsolationPrizeText": "其他人可以<LINE>获得{{0}}作为<CONT>安慰奖！<DONE>",
    "ContestResults_DidNotWinText": "希望你下次<LINE>能有好成绩！<DONE>",
    "ContestResults_ReturnPartyText": "我们会把<LINE>代为保管的<PARA>宝可梦还给你。<LINE>来，拿着！<DONE>",
    "ContestResults_PartyFullText": "你的队伍满了，<LINE>所以宝可梦被<CONT>送到正辉的电脑。<DONE>",
    # --- 道馆雕像 ---
    "GymStatue_CityGymText": "{{0}}<LINE>宝可梦道馆<PARA>馆主：{{1}}<DONE>",
    "GymStatue_WinningTrainersText": "获胜的训练家：<LINE><RIVAL><DONE>",
    "GymStatue_TwoWinningTrainersText": "获胜的训练家：<LINE><RIVAL><CONT><PLAYER><DONE>",
    "GymStatue_ThreeWinningTrainersText": "获胜的训练家：<LINE><RIVAL><CONT><PLAYER><CONT>琴音<DONE>",
    # --- 游戏角买代币 ---
    "CoinVendor_WelcomeText": "欢迎来到<LINE>游戏中心。<DONE>",
    "CoinVendor_NoCoinCaseText": "你需要<LINE>代币吗？<PARA>哎呀，你没有<LINE>装代币的<CONT>金币盒呢。<DONE>",
    "CoinVendor_IntroText": "你需要<LINE>代币吗？<PARA>¥1000可以买<LINE>50枚代币。<CONT>要买吗？<DONE>",
    "CoinVendor_Buy50CoinsText": "谢谢！<LINE>这是50枚代币。<DONE>",
    "CoinVendor_Buy500CoinsText": "谢谢！这是<LINE>500枚代币。<DONE>",
    "CoinVendor_NotEnoughMoneyText": "你的钱<LINE>不够呢。<DONE>",
    "CoinVendor_CoinCaseFullText": "哎呀！你的<LINE>金币盒满了。<DONE>",
    "CoinVendor_CancelText": "不买代币吗？<LINE>欢迎再来！<DONE>",
    # --- 其他 ---
    "BugContestPrizeNoRoomText": "哦？你的<LINE>背包满了。<PARA>这个先由我们<LINE>替你保管，<PARA>等你有空位了<LINE>再来拿吧。<DONE>",
    "HappinessText3": "哇！你和你的<LINE>宝可梦<CONT>真亲密！<DONE>",
    "HappinessText2": "只要多花时间<LINE>陪伴，宝可梦<PARA>就会变得<LINE>更亲近哦。<DONE>",
    "HappinessText1": "你还没<LINE>驯服宝可梦呢。<PARA>对它不好，<LINE>它就会闹别扭。<DONE>",
    "RegisteredNumber1Text": "<PLAYER>记下了<LINE>{{0}}的电话号码。<DONE>",
    "RegisteredNumber2Text": "<PLAYER>记下了<LINE>{{0}}的电话号码。<DONE>",
    "VendingMachineText": "自动售货机！<LINE>这是菜单。<DONE>",
    "VendingMachineClangText": "哐当！<PARA>{{0}}<LINE>掉了出来。<DONE>",
    "VendingMachineScoreText": "赚到了！多出<LINE>{{0}}<CONT>也掉了出来。<DONE>",
    "VendingMachineNoMoneyText": "哎呀，钱<LINE>不够…<DONE>",
    "VendingMachineNoSpaceText": "已经装不下<LINE>更多东西了…<DONE>",
    "HiddenGrottoText": "看！你发现了<LINE>一条窄路！<PARA>要沿着它<LINE>走过去吗？<DONE>",
}


def codes(s):
    return collections.Counter(re.findall(r"<[A-Za-z0-9_]+>|\{\{\d\}\}", s))


def tile_width(s):
    """粗估这一行的 tile 宽：控制码与占位符不占宽，CJK/全角算 2。"""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\{\{\d\}\}", "", s)
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def main():
    data = json.loads(P.read_text(encoding="utf-8"))
    b = data["blocks"]
    bad = []
    for k, zh in T.items():
        if k not in b:
            bad.append((k, "键不存在"))
            continue
        ce, cz = codes(b[k]["en"]), codes(zh)
        if ce != cz:
            bad.append((k, "控制码/占位符不一致 缺%s 多%s"
                        % (sorted((ce - cz).items()), sorted((cz - ce).items()))))
        if not re.search(r"<(DONE|PROMPT|WAIT)>|@$", zh):
            bad.append((k, "缺终止符"))
        for ln in re.split(r"<(?:LINE|PARA|CONT|NEXT)>", zh):
            w = tile_width(ln)
            if w > 18:
                bad.append((k, "行长超限 %d>18: %r" % (w, ln)))
    print("待译 %d 条 / 共 %d 条，本次提交 %d 条"
          % (sum(1 for v in b.values() if not (v.get("zh") or "").strip()), len(b), len(T)))
    if bad:
        print("校验失败：")
        for k, m in bad:
            print("  %s: %s" % (k, m))
        return 1
    for k, zh in T.items():
        b[k]["zh"] = zh
    P.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("校验通过，已写入", P)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
