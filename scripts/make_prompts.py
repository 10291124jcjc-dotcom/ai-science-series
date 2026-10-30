# -*- coding: utf-8 -*-
"""AI 科普短剧：读 series.json + epNN/episode.json，拼出设定图、分镜图、视频提示词。

用法：
  python make_prompts.py SERIES sheets                   角色和大本营的设定图提示词 → SERIES/设定图提示词.md
  python make_prompts.py SERIES epNN                     这一集全部提示词 → SERIES/epNN/提示词.md（一份文件，按段排好）
  python make_prompts.py SERIES epNN script              剧本给用户看 → SERIES/epNN/剧本.md
  python make_prompts.py SERIES epNN panel 6 --seg 3     只重出第 3 段分镜的第 6 格
  python make_prompts.py SERIES epNN patch 5-6 --seg 3   只补拍第 3 段的镜头 5–6

每段一张写实分镜图（不带序号，每格标出人物和人数）+ 一份 Seedance 全能参考视频提示词。
脚本会检查：每段秒数加起来等于段长、每个镜头至少 2 秒、台词说得完、钻进去的转场不跨段、参考图不超上限。
环境变量 JIMENG_MAX_REFS 改参考图上限（默认 9）。
"""
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

MAX_REFS = int(os.environ.get("JIMENG_MAX_REFS", 9))
MAX_SEG = 30          # 即梦 Seedance 一次最多 30 秒
GRIDS = {4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3), 10: (2, 5), 12: (3, 4)}
GRID_NAMES = {4: "四宫格", 6: "六宫格", 8: "八宫格", 9: "九宫格", 10: "十宫格", 12: "十二宫格"}
CN = "零一二三四五六七八九十"
CPS_WARN = 6.0        # 每秒超过 6 个字就说不完
PUNCT = r"[\s，。！？、…：；“”「」‘’（）,.!?:;()\"'—-]"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    print("写好了：" + p)


def fmt(x):
    return f"{x:g}"


def cn(n):
    return CN[n] if n <= 10 else "十" + CN[n - 10]


def cells(idx):
    """[1,2,3,7,8] → 第一到第三、第七、第八格"""
    runs, cur = [], [idx[0]]
    for i in idx[1:]:
        if i == cur[-1] + 1:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    parts = []
    for r in runs:
        if len(r) >= 3:
            parts.append(f"第{cn(r[0])}到第{cn(r[-1])}")
        else:
            parts.extend(f"第{cn(i)}" for i in r)
    return "、".join(parts) + "格"


def shot_nums(idx):
    return "、".join(str(i) for i in idx)


def spoken(text):
    return len(re.sub(PUNCT, "", text))


def grid_of(n):
    if n in GRIDS:
        return GRIDS[n]
    c = -(-n // 2)
    return 2, c


# ---------- 读设定 ----------

class Show:
    def __init__(self, series_dir):
        self.dir = series_dir
        self.s = load(os.path.join(series_dir, "series.json"))
        self.chars = {c["id"]: c for c in self.s["characters"]}
        self.scenes = {sc["name"]: sc for sc in self.s.get("scenes", [])}

    def episode(self, ep):
        e = load(os.path.join(self.dir, ep, "episode.json"))
        scenes = dict(self.scenes)
        for sc in e.get("scenes", []):
            scenes[sc["name"]] = sc
        return e, scenes


def speakers(shot):
    return [ln["who"] for ln in shot.get("lines", [])]


def check(show, e, scenes):
    seg_len = e.get("seg_seconds", MAX_SEG)
    if seg_len > MAX_SEG:
        sys.exit(f"每段 {fmt(seg_len)} 秒，超过即梦一次最多 {MAX_SEG} 秒")
    ok = True
    for k, seg in enumerate(e["segments"], 1):
        shots = seg["shots"]
        total = sum(sh["dur"] for sh in shots)
        if abs(total - seg_len) > 0.01:
            sys.exit(f"第 {k} 段镜头加起来 {fmt(total)} 秒，应该是 {fmt(seg_len)} 秒")
        if shots[0].get("link"):
            sys.exit(f"第 {k} 段镜头 1 写了 link：钻进去的转场不能跨段，挪到上一段里面，或者让这一段从转场之后开始")
        if len(shots) not in GRIDS:
            print(f"提醒：第 {k} 段 {len(shots)} 个镜头排不成整齐的宫格，建议 8 个（2 行 4 列）")
        words = 0
        for i, sh in enumerate(shots, 1):
            if sh["dur"] < 2:
                sys.exit(f"第 {k} 段镜头 {i} 只有 {fmt(sh['dur'])} 秒，至少 2 秒")
            for cid in sh.get("cast", []) + speakers(sh):
                if cid not in show.chars:
                    sys.exit(f"第 {k} 段镜头 {i}：角色 {cid} 在 series.json 里找不到")
            n = sum(spoken(ln["text"]) for ln in sh.get("lines", []))
            words += n
            if n / sh["dur"] > CPS_WARN:
                print(f"提醒：第 {k} 段镜头 {i} 台词 {n} 字只有 {fmt(sh['dur'])} 秒，说不完，加时长或删字")
                ok = False
        if words > seg_len * 4.7:
            print(f"提醒：第 {k} 段台词一共 {words} 字，30 秒偏满，画面会被台词赶着走")
        refs = refs_for(show, seg, scenes)
        if len(refs) > MAX_REFS:
            sys.exit(f"第 {k} 段要传 {len(refs)} 张参考图，超过上限 {MAX_REFS}")
    return ok


def refs_for(show, seg, scenes):
    """参考图顺序：出场或说话的角色（按 series.json 顺序）→ 用到的有图场景（按出现顺序）→ 分镜（最后）。"""
    used = set()
    for sh in seg["shots"]:
        used.update(sh.get("cast", []))
        used.update(speakers(sh))
    refs = [{"kind": "char", "item": c, "name": c["name"]} for cid, c in show.chars.items() if cid in used]
    seen = []
    for sh in seg["shots"]:
        sc = scenes.get(sh.get("place", ""))
        if sc and sc.get("ref") and sc["name"] not in seen:
            seen.append(sc["name"])
            refs.append({"kind": "scene", "item": sc, "name": sc["name"]})
    refs.append({"kind": "board", "item": None, "name": "分镜"})
    for i, r in enumerate(refs, 1):
        r["n"] = i
    return refs


# ---------- 设定图 ----------

def sheet_char(show, c):
    exp = c.get("expressions") or ["得意", "惊讶", "委屈", "崩溃"]
    pos = c.get("poses") or ["双手叉腰", "边走边说话"]
    return (f"写实电影角色设定图，同一个角色：左边是全身三视图（正面、侧面、背面），中间上方是一张正脸大特写，"
            f"中间下方是 {len(exp)} 个说话时的表情（{'、'.join(exp)}），右边是 {len(pos)} 个动作（{'；'.join(pos)}）。"
            f"排版干净，纯浅灰背景，左上角写名字「{c['name']}」。\n"
            f"外形：{c['look']}。\n"
            f"{show.s.get('sheet_style', '')}，所有格子里是同一个角色，脸和衣服完全一致，不像任何明星。")


def sheet_scene(show, sc):
    views = sc.get("views") or ["朝一边拍", "朝另一边拍"]
    sides = ["左边", "右边", "第三格", "第四格"]
    v = "，".join(f"{sides[i]}{x}" for i, x in enumerate(views))
    return (f"场景设定图，没有任何人物，同一个地方的 {len(views)} 个画面，{'左右两格' if len(views) == 2 else '并排'}：{v}。\n"
            f"{sc['name']}：{sc['look']}。\n"
            f"{show.s.get('scene_style', '写实电影质感，35mm 广角，自然光')}，8K 高清，几格是同一个地方，东西的位置能对上。")


def cmd_sheets(show):
    out = ["# 设定图提示词", "", "在即梦画布里：图片生成 → 比例 16:9 → 出 4 张挑 1 张 → 下载存到下面写的位置，节点留在画布里，以后每集都框选它们。", ""]
    for c in show.chars.values():
        out += [f"## {c['name']}", f"存成 `{c['ref']}`。", "```", sheet_char(show, c), "```", ""]
    for sc in show.scenes.values():
        if sc.get("ref"):
            out += [f"## {sc['name']}", f"存成 `{sc['ref']}`。", "```", sheet_scene(show, sc), "```", ""]
    write(os.path.join(show.dir, "设定图提示词.md"), "\n".join(out))


# ---------- 分镜 ----------

def cast_tag(show, sh, names):
    if sh.get("cast_note"):
        return f"（这一格：{sh['cast_note']}）"
    here = [show.chars[c]["name"] for c in sh.get("cast", [])]
    if not here:
        return "（这一格：没有" + "，也没有".join(names) + "）"
    absent = [n for n in names if n not in here]
    s = "、".join(f"{n} 1 个" for n in here)
    if len(here) == 1:
        s = "只有" + s
    if absent:
        s += "，没有" + "、".join(absent)
    return f"（这一格：{s}）"


def board_rules(show, refs, rows, cols, n):
    names = [r["name"] for r in refs if r["kind"] == "char"]
    order = "，".join(f"第{cn(r + 1)}行从左到右是{cells(list(range(r * cols + 1, min(n, (r + 1) * cols) + 1)))[:-1]}格"
                     for r in range(rows))
    dup = ("参考图里的设定图把同一个角色画了好几个角度和表情，只是让你看清长相：每一格里"
           + "、".join(f"{x}最多出现一次" for x in names)
           + "，不能出现" + "或".join(f"两个{x}" for x in names) + "，这一格没写到的角色不要画。") if names else ""
    who = "，".join(
        f"图 {r['n']} 是{r['name']}" + (f"（{r['item']['short']}）" if r["kind"] == "char" and r["item"].get("short") else "")
        for r in refs if r["kind"] != "board")
    return (f"要求：每一格都像一张真实的电影剧照，真实摄影质感、彩色、自然光影，人物和场景都是写实的，"
            f"不要动漫、卡通、插画、素描风格；画面干净不杂乱，无多余画面；格子里不要任何文字、数字、序号，{order}。"
            f"{dup}角色外形参考：{who}。")


def places_line(shots):
    groups = {}
    for i, sh in enumerate(shots, 1):
        groups.setdefault(sh.get("place", "同一个地方"), []).append(i)
    return "；".join(f"{cells(ix)}在{p}" for p, ix in groups.items()) + "。"


def board_prompt(show, e, k, seg, refs):
    shots = seg["shots"]
    n = len(shots)
    rows, cols = grid_of(n)
    names = [r["name"] for r in refs if r["kind"] == "char"]
    lines = [f"生成 {show.s.get('ratio', '16:9')} {GRID_NAMES.get(n, f'{n}格')}（{rows} 行 {cols} 列）写实电影风格分镜图。"
             + board_rules(show, refs, rows, cols, n) + places_line(shots), ""]
    for i, sh in enumerate(shots, 1):
        lines.append(f"第{cn(i)}格：{(sh.get('panel') or sh['desc']).rstrip('。')}。{cast_tag(show, sh, names)}")
    return "\n".join(lines)


# ---------- 视频 ----------

def say(show, sh):
    out = []
    for ln in sh.get("lines", []):
        name = show.chars[ln["who"]]["name"]
        out.append(f"{name}的画外音：“{ln['text']}”" if ln.get("vo") else f"{name}说：“{ln['text']}”")
    return "，".join(out)


def video_prompt(show, e, seg, refs, shots, start=0.0, note=""):
    board = [r for r in refs if r["kind"] == "board"][0]
    lines = [f"根据故事板 @图{board['n']} 生成分镜视频，仅参考其镜头设计、动作节奏、景别和运镜，镜头顺序按故事板从左到右、从上到下，"
             f"禁止直接拼接故事板图片；故事板只是参考，每个镜头都要重新生成连续的动作，画面里不能出现宫格、分割线和序号。{note}", ""]
    for r in refs:
        if r["kind"] == "char":
            c = r["item"]
            lines.append(f"@图{r['n']} 是{c['name']}，外形必须和参考图完全一致：{c['look']}。声音：{c['voice']}。")
        elif r["kind"] == "scene":
            idx = [i for i, sh in enumerate(shots, 1) if sh.get("place") == r["name"]]
            where = f"镜头 {shot_nums(idx)} 在这里，" if idx else ""
            lines.append(f"@图{r['n']} 是{r['name']}，{where}布局和参考图一致。")
    chars = [r["item"] for r in refs if r["kind"] == "char"]
    lines += ["", f"风格：{show.s.get('style', '')}。{show.s.get('lang_note', '角色说普通话，口型和台词对上。')}只要台词和音效，不要背景音乐。", ""]
    cont = []
    if chars:
        cont.append("全片只有" + "、".join(f"一个{c['name']}" for c in chars) + "，长相和衣服始终和参考图一致")
    cont += seg.get("continuity", [])
    for i, sh in enumerate(shots, 1):
        if sh.get("link") and i > 1:
            cont.append(f"镜头 {i - 1} 到镜头 {i} 是一个连续镜头，{sh['link']}，中间不切黑")
    if cont:
        lines += ["连贯要求：" + "；".join(cont) + "。", ""]
    t = start
    for i, sh in enumerate(shots, 1):
        words = say(show, sh)
        body = sh["desc"].rstrip("。") + ("，" + words if words else "")
        lines.append(f"镜头 {i}｜{fmt(t)}-{fmt(t + sh['dur'])}s：{body}")
        lines.append(f"音效：{sh.get('sfx', '')}")
        lines.append("")
        t += sh["dur"]
    lines.append("画面里不要出现字幕、文字和商标" + (f"；{seg['text_note']}" if seg.get("text_note") else "") + "。")
    return "\n".join(lines)


def select_line(refs, board_name):
    return "、".join(r["name"] if r["kind"] != "board" else board_name for r in refs)


def cmd_episode(show, ep):
    e, scenes = show.episode(ep)
    check(show, e, scenes)
    seg_len = e.get("seg_seconds", MAX_SEG)
    ratio = show.s.get("ratio", "16:9")
    segs = e["segments"]
    out = [f"# {e['title']}｜全部提示词", "",
           f"全片 {fmt(seg_len * len(segs))} 秒，分 {len(segs)} 段，每段 {fmt(seg_len)} 秒。即梦一次最多 {MAX_SEG} 秒，"
           "所以一段一段生成，最后拼起来。每段都是「先出分镜图 → 再出视频」。代码框里的内容整段复制进即梦。", ""]
    ep_scenes = [sc for sc in e.get("scenes", []) if sc.get("ref")]
    out += ["## 一、先准备的图", "",
            "角色和大本营用系列里已经出好的图（`设定图提示词.md`）。"
            + ("这集还要先出下面几张，它们在好几段里都会出现：" if ep_scenes else "这集不用另外出场景图。"), ""]
    for sc in ep_scenes:
        out += [f"### {sc['name']}", f"图片生成，比例 {ratio}，出 4 张挑 1 张，存成 `{sc['ref']}`。", "```", sheet_scene(show, sc), "```", ""]
    out += ["## 二、每一段", ""]
    t = 0.0
    for k, seg in enumerate(segs, 1):
        refs = refs_for(show, seg, scenes)
        board_name = f"第{k}段分镜"
        others = [r["name"] for r in refs if r["kind"] != "board"]
        out += [f"### 第 {k} 段（{fmt(t)}–{fmt(t + seg_len)} 秒）：{seg.get('title', '')}", "",
                f"**分镜图**：在画布里框选 {'、'.join(others)} → 图片生成，比例 {ratio}，出 4 张挑 1 张，"
                f"存成 `{ep}/分镜_第{k}段.png`，拖回画布，节点名写「{board_name}」。",
                "```", board_prompt(show, e, k, seg, refs), "```", "",
                f"**视频**：按顺序框选 {select_line(refs, board_name)}（图 1 到图 {len(refs)}）→ 视频生成 → Seedance 2.5 → 全能参考，"
                f"{ratio}，{fmt(seg_len)} 秒，先 480p，满意再超清。下载存成 `{ep}/clips/第{k}段.mp4`。",
                "```", video_prompt(show, e, seg, refs, seg["shots"]), "```", ""]
        t += seg_len
    out += ["## 三、全部做完以后", "",
            f"1. `python scripts/review_clip.py SERIES {ep}`：抽帧检查每段（人物有没有重复、变样，是不是照分镜拍的）",
            f"2. `python scripts/compile.py concat {ep}/成片.mp4 " + " ".join(f"{ep}/clips/第{k}段.mp4" for k in range(1, len(segs) + 1)) + "`",
            "3. 剪映里铺背景音乐（压低到不盖台词）、识别字幕并对照剧本校对数字、按剧本加「第 N 天」字卡", ""]
    write(os.path.join(show.dir, ep, "提示词.md"), "\n".join(out))


# ---------- 剧本 ----------

def cmd_script(show, ep):
    e, scenes = show.episode(ep)
    check(show, e, scenes)
    seg_len = e.get("seg_seconds", MAX_SEG)
    out = [f"# {e['title']}", ""]
    for key, label in (("topic", "话题"), ("challenge", "挑战"), ("bet", "赌约")):
        if e.get(key):
            out.append(f"- **{label}**：{e[key]}")
    out += [f"- **时长**：{fmt(seg_len * len(e['segments']))} 秒，{len(e['segments'])} 段", ""]
    if e.get("hooks"):
        out += ["## 伏笔", ""] + [f"- {h}" for h in e["hooks"]] + [""]
    t = 0.0
    for k, seg in enumerate(e["segments"], 1):
        out += [f"## 第 {k} 段（{fmt(t)}–{fmt(t + seg_len)} 秒）：{seg.get('title', '')}", ""]
        st = t
        for i, sh in enumerate(seg["shots"], 1):
            words = "　".join(
                f"{show.chars[ln['who']]['name']}{'（画外音）' if ln.get('vo') else ''}：「{ln['text']}」"
                for ln in sh.get("lines", []))
            card = f"【字卡：{sh['card']}】" if sh.get("card") else ""
            link = "【连续镜头】" if sh.get("link") else ""
            out.append(f"{i}. `{fmt(st - t)}–{fmt(st - t + sh['dur'])}s`｜{sh.get('place', '')}｜{card}{link}{sh['desc'].rstrip('。')}"
                       + (f" ｜ {words}" if words else ""))
            st += sh["dur"]
        out.append("")
        t += seg_len
    if e.get("next_bet"):
        out += ["## 下期赌约", "", e["next_bet"], ""]
    if e.get("facts"):
        out += ["## 数字出处（只写核实过的）", "", "| 台词里的数字 | 出处 | 注意 |", "| --- | --- | --- |"]
        out += [f"| {f['claim']} | {f['source']} | {f.get('note', '')} |" for f in e["facts"]]
        out.append("")
    write(os.path.join(show.dir, ep, "剧本.md"), "\n".join(out))


# ---------- 改一格 / 补拍 ----------

def seg_arg(args):
    if "--seg" not in args:
        sys.exit("要加 --seg 第几段")
    return int(args[args.index("--seg") + 1])


def cmd_panel(show, ep, args):
    e, scenes = show.episode(ep)
    k = seg_arg(args)
    seg = e["segments"][k - 1]
    i = int(args[1])
    sh = seg["shots"][i - 1]
    refs = [r for r in refs_for(show, seg, scenes) if r["kind"] != "board"]
    names = [r["name"] for r in refs if r["kind"] == "char"]
    who = "，".join(f"图 {r['n']} 是{r['name']}" for r in refs)
    body = (f"生成一张 {show.s.get('ratio', '16:9')} 写实电影剧照，真实摄影质感、彩色、自然光影，不要动漫、卡通、插画、素描风格，"
            f"画面里不要任何文字和数字。参考图只是让你看清长相，画面里每个角色最多出现一次。角色外形参考：{who}。\n"
            f"地点：{sh.get('place', '')}。\n画面：{(sh.get('panel') or sh['desc']).rstrip('。')}。{cast_tag(show, sh, names)}")
    print(f"【即梦画布｜图片生成｜框选：{'、'.join(r['name'] for r in refs)}｜出 4 张挑 1 张】\n"
          f"出好后：python scripts/grid.py split {ep}/分镜_第{k}段.png 单格 --rows 2 --cols 4，换掉第 {i} 格，"
          f"再 python scripts/grid.py grid {ep}/分镜_第{k}段.png 单格/*.png --cols 4\n\n{body}")


def cmd_patch(show, ep, args):
    e, scenes = show.episode(ep)
    k = seg_arg(args)
    seg = e["segments"][k - 1]
    a, b = (int(x) for x in args[1].split("-"))
    shots = seg["shots"][a - 1:b]
    if shots and shots[0].get("link"):
        shots = [dict(shots[0], link="")] + shots[1:]
    refs = refs_for(show, seg, scenes)
    dur = sum(sh["dur"] for sh in shots)
    note = f"只拍故事板里第{cn(a)}到第{cn(b)}格对应的这 {len(shots)} 个镜头。"
    print(f"【即梦画布｜视频生成｜Seedance 2.5 全能参考｜框选：{select_line(refs, f'第{k}段分镜')}｜{fmt(dur)} 秒｜先 480p】\n"
          f"出好后：python scripts/compile.py replace {ep}/clips/第{k}段.mp4 "
          f"{fmt(sum(sh['dur'] for sh in seg['shots'][:a - 1]))} {fmt(sum(sh['dur'] for sh in seg['shots'][:b]))} 补拍.mp4 新的第{k}段.mp4\n\n"
          + video_prompt(show, e, seg, refs, shots, note=note))


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    show = Show(args[0])
    if args[1] == "sheets":
        cmd_sheets(show)
        return
    ep, rest = args[1], args[2:]
    if not rest:
        cmd_episode(show, ep)
    elif rest[0] == "script":
        cmd_script(show, ep)
    elif rest[0] == "panel":
        cmd_panel(show, ep, rest)
    elif rest[0] == "patch":
        cmd_patch(show, ep, rest)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
