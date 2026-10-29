#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""减重数据记录与趋势分析（AI减重教练 skill 配套工具）

数据文件为 JSONL，每天一条记录：
  {"date":"2026-01-15","weight":80.0,"protein_g":120,"meals":"鸡胸肉蛋白粉红薯",
   "exercise_min":45,"hr_avg":118,"note":"...","body":{"body_fat":22.0,"muscle":35.0,"visceral":8,"bmr":1650}}

用法示例：
  python3 weight_log.py log --weight 80.0 --protein-g 120 --meals "鸡胸肉蛋白粉红薯" --exercise-min 45 --hr-avg 118
  python3 weight_log.py body --body-fat 22.0 --muscle 35.0 --visceral 8 --bmr 1650 --waist 85 --hip 95
  python3 weight_log.py list
  python3 weight_log.py trend
  python3 weight_log.py rebound
  python3 weight_log.py stats
  python3 weight_log.py today
  python3 weight_log.py santi --height 170 --sex male
  python3 weight_log.py report --week
  python3 weight_log.py report --month

个人参数（目标体重/日期、身高、性别口径）放在数据文件同目录的 config.json：
  {"goal_weight": 70.0, "goal_date": "2026-12-31", "height": 170.0, "sex": "male"}
优先级：命令行参数 > 环境变量（JIANFEI_GOAL_WEIGHT / JIANFEI_GOAL_DATE /
JIANFEI_HEIGHT / JIANFEI_SEX）> config.json；均未设置时不做目标预测。
数据文件路径可用 --data 或环境变量 JIANFEI_DATA 覆盖。
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

DEFAULT_DATA = os.environ.get("JIANFEI_DATA") or os.path.expanduser(
    "~/.workbuddy/jianfei-coach/weight-log.jsonl")

# 冯雪·科学减肥16讲「三体目标」标准（男性）
SANTI_MALE = {
    "bmi": (18.5, 24.0),
    "body_fat": (15.0, 18.0),
    "waist": (None, 85.0),   # cm，上限
    "whr": (None, 0.9),      # 腰臀比，上限
}
SANTI_FEMALE = {
    "bmi": (18.5, 23.0),
    "body_fat": (20.0, 25.0),
    "waist": (None, 80.0),
    "whr": (None, 0.8),
}

REBOUND_THRESHOLD = 0.5   # 日环比涨幅达到该值视为一次反弹
LOW_ZONE = 0.05           # 与当前低点的容差（kg）
LOW_RISE = 0.3            # 高出当前低点该值才算"反弹起来"
PROTEIN_PER_KG = (1.6, 1.8)

BANNED_KEYWORDS = [
    "白粥", "粥", "馒头", "面包", "粉条", "炒饭", "炒面", "米皮", "拉面", "刀削面",
    "盖饭", "火腿肠", "午餐肉", "腊肉", "红烧肉", "回锅肉", "糖醋", "干锅", "油炸",
    "可乐", "奶茶", "果汁", "运动饮料", "速溶", "调味奶", "方便面", "零食", "糕点",
    "胡辣汤", "腐竹", "榴莲", "苏打饼干",
]


# ---------- 数据读写 ----------

def _env_float(name):
    v = os.environ.get(name)
    try:
        return float(v) if v else None
    except ValueError:
        return None


def load_config(data_path):
    """读取数据文件同目录的 config.json（个人配置：目标/身高/性别，不属于 skill 本体）"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(data_path)), "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def load_records(path):
    records = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    records.sort(key=lambda r: r.get("date", ""))
    return records


def save_records(path, records):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def upsert(records, rec):
    """按日期合并写入，返回 (records, 动作)"""
    for i, r in enumerate(records):
        if r.get("date") == rec["date"]:
            merged = dict(r)
            for k, v in rec.items():
                if v is not None:
                    if k == "body" and isinstance(v, dict):
                        body = dict(merged.get("body") or {})
                        body.update(v)
                        merged["body"] = body
                    else:
                        merged[k] = v
            records[i] = merged
            return records, "updated"
    records.append(rec)
    records.sort(key=lambda r: r.get("date", ""))
    return records, "added"


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def weight_series(records):
    return [(parse_date(r["date"]), float(r["weight"]))
            for r in records if r.get("weight") is not None]


# ---------- 分析 ----------

def find_lows(series, window=2):
    """找出低点序列：窗口内的最小值（带容差），过近的低点保留更低的那个"""
    lows = []
    n = len(series)
    for i in range(n):
        d, w = series[i]
        lo, hi = max(0, i - window), min(n, i + window + 1)
        local_min = min(x[1] for x in series[lo:hi])
        if w <= local_min + LOW_ZONE:
            if lows and (d - lows[-1][0]).days <= window:
                if w < lows[-1][1]:
                    lows[-1] = (d, w)
                continue
            lows.append((d, w))
    return lows


def rebound_events(series, records_by_date, threshold=REBOUND_THRESHOLD):
    """日环比涨幅 >= threshold 记为一次反弹；返回 (日期, 涨幅, 前一天饮食, 前几天回落)"""
    events = []
    for i in range(1, len(series)):
        d, w = series[i]
        pd, pw = series[i - 1]
        delta = round(w - pw, 2)
        if delta >= threshold:
            prev = records_by_date.get(pd.isoformat(), {})
            meals = prev.get("meals") or "（无记录）"
            # 回落：反弹后几天内回到反弹前水平
            fall_days = None
            for j in range(i + 1, len(series)):
                if series[j][1] <= pw + LOW_ZONE:
                    fall_days = (series[j][0] - d).days
                    break
            events.append((d, delta, meals, fall_days))
    return events


def fmt_delta(x):
    return f"{x:+.1f}"


# ---------- 子命令 ----------

def cmd_log(args, records):
    rec = {"date": args.date}
    if args.weight is not None:
        rec["weight"] = args.weight
    if args.protein_g is not None:
        rec["protein_g"] = args.protein_g
    if args.meals is not None:
        rec["meals"] = args.meals
    if args.exercise_min is not None:
        rec["exercise_min"] = args.exercise_min
    if args.hr_avg is not None:
        rec["hr_avg"] = args.hr_avg
    if args.note is not None:
        rec["note"] = args.note
    if len(rec) == 1:
        print("没有任何字段可记录。至少给一个：--weight/--protein-g/--meals/--exercise-min/--hr-avg/--note")
        return 1
    records, action = upsert(records, rec)
    save_records(args.data, records)
    label = "更新" if action == "updated" else "新增"
    print(f"已{label} {args.date} 的记录：" + "，".join(
        f"{k}={v}" for k, v in rec.items() if k != "date"))
    return 0


def cmd_body(args, records):
    body = {}
    if args.body_fat is not None:
        body["body_fat"] = args.body_fat
    if args.muscle is not None:
        body["muscle"] = args.muscle
    if args.visceral is not None:
        body["visceral"] = args.visceral
    if args.bmr is not None:
        body["bmr"] = args.bmr
    if args.waist is not None:
        body["waist"] = args.waist
    if args.hip is not None:
        body["hip"] = args.hip
    if not body:
        print("没有任何体成分字段。至少给一个：--body-fat/--muscle/--visceral/--bmr/--waist/--hip")
        return 1
    rec = {"date": args.date, "body": body}
    records, action = upsert(records, rec)
    save_records(args.data, records)
    print(f"已记录 {args.date} 体成分：" + "，".join(f"{k}={v}" for k, v in body.items()))
    return 0


def cmd_list(args, records):
    recent = records[-args.days:] if args.days else records
    if not recent:
        print("还没有任何记录。")
        return 0
    print("| 日期 | 体重kg | 日变化 | 蛋白质g | 运动min | 心率 | 饮食 |")
    print("|------|--------|--------|---------|---------|------|------|")
    prev_w = None
    for r in recent:
        w = r.get("weight")
        delta = "—" if (w is None or prev_w is None) else fmt_delta(w - prev_w)
        if w is not None:
            prev_w = w
        meals = (r.get("meals") or "—")
        if len(meals) > 18:
            meals = meals[:18] + "…"
        print(f"| {r['date']} | {w if w is not None else '—'} | {delta} | "
              f"{r.get('protein_g', '—')} | {r.get('exercise_min', '—')} | "
              f"{r.get('hr_avg', '—')} | {meals} |")
    return 0


def cmd_trend(args, records):
    series = weight_series(records)
    if args.days:
        cutoff = date.today() - timedelta(days=args.days)
        series = [(d, w) for d, w in series if d >= cutoff]
    if not series:
        print("没有体重记录，无法分析趋势。")
        return 0
    lows = find_lows(series)
    print("### 体重低点趋势")
    print("")
    print("| 低点 | 日期 | 体重kg | 距上个低点 |")
    print("|------|------|--------|-----------|")
    for i, (d, w) in enumerate(lows):
        gap = "—" if i == 0 else fmt_delta(w - lows[i - 1][1])
        print(f"| {i + 1} | {d.isoformat()} | {w:.1f} | {gap} |")
    print("")
    if len(lows) >= 2:
        total = lows[-1][1] - lows[0][1]
        if total < -0.2:
            verdict = f"低点线向下（{fmt_delta(total)} kg），趋势没断，继续执行。"
        elif abs(total) <= 0.2:
            verdict = (f"低点线走平（{fmt_delta(total)} kg），进入平台期。"
                       "按调整原则换一个变量，不要同时改多个。")
        else:
            verdict = (f"低点线向上（{fmt_delta(total)} kg），趋势断了。"
                       "检查饮食记录和记录完整性，优先做饮食-反弹关联分析。")
        print(f"判断：{verdict}")
    else:
        print("判断：低点数量不足，继续记录。单日涨跌不要在意，看低点线。")
        print("")
        print("当前完整序列：")
        print("| 日期 | 体重kg | 日变化 |")
        print("|------|--------|--------|")
        for i, (d, w) in enumerate(series):
            delta = "—" if i == 0 else fmt_delta(w - series[i - 1][1])
            print(f"| {d.isoformat()} | {w:.1f} | {delta} |")
    return 0


def cmd_rebound(args, records):
    series = weight_series(records)
    if args.days:
        cutoff = date.today() - timedelta(days=args.days)
        series = [(d, w) for d, w in series if d >= cutoff]
    if len(series) < 2:
        print("记录不足，无法分析反弹。")
        return 0
    by_date = {r["date"]: r for r in records}
    events = rebound_events(series, by_date, args.threshold)
    if not events:
        print(f"窗口内没有日环比 ≥{args.threshold}kg 的反弹。")
        return 0
    print("### 反弹关联分析（反弹日前一天的饮食）")
    print("")
    print("| 反弹日 | 涨幅kg | 前一天饮食 | 回落 |")
    print("|--------|--------|-----------|------|")
    for d, delta, meals, fall in events:
        fall_txt = f"{fall} 天后回落" if fall is not None else "未回落（重点复盘）"
        if len(meals) > 30:
            meals = meals[:30] + "…"
        print(f"| {d.isoformat()} | +{delta:.1f} | {meals} | {fall_txt} |")
    print("")
    print("提示：反弹 2–4 kg 绝大部分是钠水和糖原，不是脂肪。2–3 天回到原位是水，"
          "一周回不去才是真长了。")
    return 0


def cmd_stats(args, records):
    series = weight_series(records)
    if not series:
        print("没有体重记录。")
        return 0
    first_d, first_w = series[0]
    last_d, last_w = series[-1]
    days = max((last_d - first_d).days, 1)
    total = last_w - first_w
    weekly = total / days * 7
    remaining = last_w - args.goal_weight if args.goal_weight is not None else None
    print("### 减重总览")
    print("")
    print(f"- 起始：{first_d.isoformat()} {first_w:.1f} kg")
    print(f"- 当前：{last_d.isoformat()} {last_w:.1f} kg")
    print(f"- 累计减重：{-total:.1f} kg（{days} 天，平均每星期 {-weekly:.2f} kg）")
    if args.goal_weight is not None:
        print(f"- 距目标 {args.goal_weight:.0f} kg：还剩 {remaining:.1f} kg")
    else:
        print("- 未设置目标体重（--goal-weight 或数据目录 config.json），以上为纯总览")
    if args.goal_date:
        try:
            goal_d = parse_date(args.goal_date)
            left_days = (goal_d - last_d).days
            if left_days > 0 and remaining is not None and remaining > 0:
                need = remaining / (left_days / 7)
                print(f"- 目标日期 {args.goal_date}：还剩 {left_days} 天，"
                      f"需要平均每星期减 {need:.2f} kg")
                print("- 口径：每星期 0.5–1 kg 是健康区间；超出该区间的速度不承诺。")
            elif remaining is not None and remaining <= 0:
                print(f"- 已达成 {args.goal_weight:.0f} kg 目标。")
            else:
                print("- 已过目标日期，转为保持阶段。")
        except ValueError:
            print("- 目标日期格式应为 YYYY-MM-DD")
    return 0


def cmd_santi(args, records):
    """冯雪三体目标评估：体重(BMI) / 体脂 / 体型(腰围·腰臀比) 对照标准"""
    series = weight_series(records)
    if not series:
        print("没有体重记录，无法评估三体目标。")
        return 0
    if args.height is None:
        print("需要身高才能计算 BMI：--height 175（cm），"
              "或设置环境变量 JIANFEI_HEIGHT / 数据目录 config.json")
        return 1
    if args.sex is None:
        print("需要性别口径：--sex male|female，"
              "或设置环境变量 JIANFEI_SEX / 数据目录 config.json")
        return 1
    last_d, last_w = series[-1]
    std = SANTI_MALE if args.sex == "male" else SANTI_FEMALE
    sex_txt = "男性" if args.sex == "male" else "女性"
    bmi = last_w / (args.height / 100.0) ** 2

    # 最近一次体成分
    latest_body = None
    for r in reversed(records):
        if r.get("body"):
            latest_body = (r["date"], r["body"])
            break

    print(f"### 三体目标评估（{sex_txt}口径 · 身高 {args.height:.0f}cm · 数据截至 {last_d.isoformat()}）")
    print("")
    print("| 维度 | 指标 | 当前值 | 标准 | 差距 | 判定 |")
    print("|------|------|--------|------|------|------|")

    # 1. 体重
    lo, hi = std["bmi"]
    gap = bmi - hi
    verdict = "达标" if bmi <= hi else ("临界" if gap <= 1.0 else "未达标")
    print(f"| 体重 | BMI | {bmi:.1f} | {lo:.1f}–{hi:.1f} | "
          f"{'—' if bmi <= hi else f'超 {gap:.1f}'} | {verdict} |")

    # 2. 体脂
    if latest_body and latest_body[1].get("body_fat") is not None:
        bf = latest_body[1]["body_fat"]
        bd = latest_body[0]
        lo, hi = std["body_fat"]
        gap = bf - hi
        verdict = "达标" if lo <= bf <= hi else ("偏低" if bf < lo else ("临界" if gap <= 3.0 else "未达标"))
        print(f"| 体脂 | 体脂率 | {bf:.1f}%（{bd}） | {lo:.0f}–{hi:.0f}% | "
              f"{'—' if lo <= bf <= hi else (f'高 {gap:.1f}pp' if bf > hi else f'低 {lo - bf:.1f}pp')} | {verdict} |")
    else:
        print(f"| 体脂 | 体脂率 | 无记录 | {std['body_fat'][0]:.0f}–{std['body_fat'][1]:.0f}% | — | 待补测 |")

    # 3. 体型
    if latest_body:
        b = latest_body[1]
        w_cm, h_cm = b.get("waist"), b.get("hip")
        if w_cm is not None:
            lo_w = std["waist"][1]
            gap = w_cm - lo_w
            verdict = "达标" if w_cm < lo_w else ("临界" if gap <= 5 else "未达标")
            print(f"| 体型 | 腰围 | {w_cm:.0f} cm | <{lo_w:.0f} cm | "
                  f"{'—' if w_cm < lo_w else f'超 {gap:.0f} cm'} | {verdict} |")
        else:
            print(f"| 体型 | 腰围 | 无记录 | <{std['waist'][1]:.0f} cm | — | 待补测 |")
        if w_cm is not None and h_cm is not None:
            whr = w_cm / h_cm
            hi_r = std["whr"][1]
            gap = whr - hi_r
            verdict = "达标" if whr < hi_r else ("临界" if gap <= 0.05 else "未达标")
            print(f"| 体型 | 腰臀比 | {whr:.2f} | <{hi_r} | "
                  f"{'—' if whr < hi_r else f'超 {gap:.2f}'} | {verdict} |")
        else:
            print(f"| 体型 | 腰臀比 | 无记录 | <{std['whr'][1]} | — | 待补测 |")
        if b.get("visceral") is not None:
            v = b["visceral"]
            print(f"| 体型 | 内脏脂肪等级 | {v:.0f} | ≤9（健康参考值） | "
                  f"{'—' if v <= 9 else f'高 {v - 9:.0f} 级'} | {'达标' if v <= 9 else '未达标'} |")
    else:
        print(f"| 体型 | 腰围/腰臀比 | 无记录 | <{std['waist'][1]:.0f}cm / <{std['whr'][1]} | — | 待补测 |")

    print("")
    # 达标体重推算
    target_bmi_w = (args.height / 100.0) ** 2 * std["bmi"][1]
    print(f"- 按 BMI 上限反推：达标体重约 {target_bmi_w:.1f} kg"
          f"（距当前 {last_w - target_bmi_w:+.1f} kg）")
    if latest_body and latest_body[1].get("body_fat") is not None:
        bf = latest_body[1]["body_fat"]
        lo, hi = std["body_fat"]
        if bf > hi:
            fat_kg = last_w * bf / 100.0
            lean = last_w - fat_kg
            fat_target = lean * hi / (100.0 - hi)
            w_target = lean + fat_target
            print(f"- 按体脂率上限 {hi:.0f}% 反推（瘦体重 {lean:.1f}kg 不变）：达标体重约 {w_target:.1f} kg"
                  f"（需减脂约 {fat_kg - fat_target:.1f} kg）")
    print("- 口径：体型 > 体脂率 > 体重（冯雪课程的健康重要性排序）。")
    print("- 体型维度每周测一次：早晨空腹，软尺量肚脐水平腰围和最凸处臀围。")
    return 0


# ---------- 每日任务卡 ----------

ENERGY_FLOOR = {"male": 1500, "female": 1200}   # 摄入安全下限（大卡）


def calc_energy(weight, bmr, sex):
    """摄入建议区间 = TDEE − 500~750（冯雪：热量缺口500–750大卡）；
    TDEE 有体成分 BMR 时用 BMR×1.375，否则用体重粗略估算"""
    if bmr:
        tdee = bmr * 1.375
        src = f"基础代谢 {bmr:.0f} × 活动系数 1.375"
    else:
        factor = 25.0 if sex != "female" else 23.0
        tdee = weight * factor
        src = f"体重 {weight:.1f} × {factor:.0f} kcal/kg（无体成分数据，粗略口径，测一次 BMR 更准）"
    floor = ENERGY_FLOOR.get(sex, 1500)
    lo = max(tdee - 750, floor)
    hi = max(tdee - 500, lo + 100)
    return lo, hi, src


def protein_suggestion(gap_g):
    """把蛋白质缺口折算成具体食物"""
    parts, remaining = [], gap_g
    for name, g in [("蛋白粉1勺", 20), ("鸡胸肉100g", 20), ("牛奶250ml", 8), ("鸡蛋1个", 6)]:
        if remaining >= g:
            n = int(remaining // g)
            parts.append(f"{name}×{n}" if n > 1 else name)
            remaining -= g * n
        if len(parts) >= 3:
            break
    if not parts and gap_g > 0:
        parts.append("鸡蛋1个")
    return " + ".join(parts)


def cmd_today(args, records):
    today = date.today()
    week_cn = "一二三四五六日"[today.weekday()]
    today_s = today.isoformat()
    yesterday_s = (today - timedelta(days=1)).isoformat()
    by_date = {r["date"]: r for r in records}
    series = weight_series(records)

    print(f"### 今日任务卡（{today_s} 周{week_cn}）")
    print("")

    if not series:
        print("还没有任何记录。教练要给出针对你个人的建议，先有数据——"
              "换成任何人都成立的建议就是废话。")
        print("")
        print("今天先做三件事：")
        print("1. 早上空腹、排便后称体重，数字发我")
        print("2. 每餐拍照或打字发我（如\"2鸡蛋2盒牛奶2块红薯\"）")
        print("3. 告诉我目标体重/日期和身高，或在数据目录建 config.json"
              "（模板见 skill 的 assets/config.example.json）")
        return 0

    last_d, last_w = series[-1]
    first_d, first_w = series[0]
    total = last_w - first_w
    recorded = set(by_date)
    streak, d = 0, today
    if today_s not in recorded:
        d -= timedelta(days=1)
    while d.isoformat() in recorded:
        streak += 1
        d -= timedelta(days=1)

    goal_txt = ""
    if args.goal_weight is not None:
        goal_txt = f"｜距目标 {args.goal_weight:.0f}kg 还差 {max(last_w - args.goal_weight, 0):.1f}kg"
    print(f"**进度**：当前 {last_w:.1f} kg（{last_d.isoformat()}）｜"
          f"累计 {total:+.1f} kg｜连续记录 {streak} 天{goal_txt}")
    print("")

    # ---- 今日必做 ----
    print("**今天必做**")
    todos = []
    if today_s not in recorded or by_date.get(today_s, {}).get("weight") is None:
        todos.append("早上空腹称重，数字发我（排便后、同一台秤，3 秒）")
    todos.append("每餐吃了什么，拍照或打字发我")
    if yesterday_s not in recorded:
        todos.append(f"补记昨天（{yesterday_s}）的饮食和运动，别断档")
    todos.append("运动完发截图（有氧看心率区间，不只看消耗）")
    for i, t in enumerate(todos, 1):
        print(f"{i}. {t}")
    print("")

    # ---- 数据待办 ----
    print("**数据待办**")
    pend = []
    body_dates = [r["date"] for r in records if r.get("body")]
    if body_dates:
        gap = (today - parse_date(body_dates[-1])).days
        if gap >= 7:
            pend.append(f"体成分已 {gap} 天没测（每周一次：早上空腹、排便后、同一台秤）")
    else:
        pend.append("体成分从未记录（每周一次：早上空腹、排便后、同一台秤；"
                    "有 BMR 才能算准每日热量目标）")
    latest_body = None
    for r in reversed(records):
        if r.get("body"):
            latest_body = (r["date"], r["body"])
            break
    if not latest_body or latest_body[1].get("waist") is None:
        pend.append("腰围/臀围从未记录（三体目标-体型维度，软尺 30 秒：肚脐水平腰围 + 最凸处臀围）")
    if today.weekday() == 0:
        pend.append("今天是周一：生成本周周报（report --week）")
    if today.day <= 3:
        pend.append("月初：生成本月月报（report --month + santi 三体评估）")
    if not pend:
        pend.append("暂无缺口，保持节奏")
    for t in pend:
        print(f"- {t}")
    print("")

    # ---- 今日饮食 ----
    lo_p, hi_p = PROTEIN_PER_KG[0] * last_w, PROTEIN_PER_KG[1] * last_w
    e_lo, e_hi, e_src = calc_energy(last_w, (latest_body or ({}, {}))[1].get("bmr")
                                    if latest_body else None, args.sex)
    print(f"**今日饮食目标**")
    print(f"- 热量：{e_lo:.0f}–{e_hi:.0f} 大卡（{e_src}，缺口 500–750）")
    print(f"- 蛋白质：{lo_p:.0f}–{hi_p:.0f} g（1.6–1.8 g/kg × {last_w:.1f} kg）")
    print("- 餐盘：211 法则——2 份蔬菜、1 份蛋白质、1 份主食；进食顺序：蔬菜 → 蛋白 → 主食")
    today_rec = by_date.get(today_s, {})
    eaten_p = today_rec.get("protein_g")
    if eaten_p is not None:
        gap_p = max(lo_p - eaten_p, 0)
        if gap_p > 0:
            print(f"- 今天已摄入 {eaten_p:.0f} g，还差 {gap_p:.0f} g：{protein_suggestion(gap_p)}")
        else:
            print(f"- 今天已摄入 {eaten_p:.0f} g，蛋白质已达标，盯住热量别超")
    else:
        print("- 参考组合：早餐 2鸡蛋+牛奶250ml；午餐 鸡胸肉150g+芋头200g+蔬菜；"
              "晚餐 清蒸鱼/虾+豆腐+蔬菜（具体见 references/diet.md）")
    print("")

    # ---- 今日运动 ----
    print("**今日运动安排**")
    ex_days = [(r["date"], r) for r in records[-7:] if r.get("exercise_min")]
    if ex_days:
        last_ex_d = parse_date(ex_days[-1][0])
        last_ex = ex_days[-1][1]
        gap_days = (today - last_ex_d).days
        mins, hr = last_ex.get("exercise_min"), last_ex.get("hr_avg")
        hr_txt = f"，平均心率 {hr:.0f}" if hr is not None else ""
        if gap_days == 0:
            state = "今天已运动" if last_ex_d == today else "今天还没记运动"
            print(f"- {state} {mins} 分钟{hr_txt}"
                  + ("（心率超 128，下次放慢配速）" if hr and hr > 128 else "（心率在减脂区间，保持）"))
        elif gap_days == 1 and mins >= 30:
            print(f"- 昨天已练 {mins} 分钟{hr_txt}：今天休息，或做一轮力量训练"
                  "（深蹲/俯卧撑/臀桥/平板支撑，动作见 references/exercise.md）")
        elif gap_days >= 2:
            print(f"- 已连续 {gap_days} 天没运动：今天补 40–45 分钟有氧"
                  "（快走/慢跑/骑行均可），平均心率压 105–128，超过 128 放慢")
        else:
            print(f"- 昨天练了 {mins} 分钟（偏短）：今天补到 40–45 分钟，心率 105–128")
        over = [r["date"] for r in records[-7:] if r.get("exercise_min") and r["exercise_min"] > 60]
        if over:
            print(f"- 注意：{'、'.join(over[-3:])} 单次超 60 分钟红线（皮质醇 30–40 分钟后飙升，拆成两次）")
    else:
        print("- 最近 7 天没有运动记录：今天从 40 分钟快走开始，心率压 105–128，"
              "动作和加量优先级见 references/exercise.md")
    print("")

    # ---- 异常提醒（扫最近 3 天）----
    print("**异常提醒**")
    warns = []
    for k in range(3, 0, -1):
        d_s = (today - timedelta(days=k)).isoformat()
        rec = by_date.get(d_s, {})
        text = (rec.get("meals") or "") + (rec.get("note") or "")
        if text:
            hits = [w for w in BANNED_KEYWORDS if w in text]
            hits = [w for w in hits if not any(w != h and w in h for h in hits)]
            if hits:
                warns.append(f"{d_s} 饮食命中禁止清单：{'、'.join(hits)}"
                             "（机器初筛需人工核对；今天饮食清淡回来，不用极端节食补救）")
    for i in range(1, min(len(series), 4)):
        d, w = series[-i]
        pd, pw = series[-i - 1]
        delta = w - pw
        if delta >= REBOUND_THRESHOLD:
            fall = None
            for j in range(len(series) - i + 1, len(series)):
                if series[j][1] <= pw + LOW_ZONE:
                    fall = (series[j][0] - d).days
                    break
            fall_txt = f"{fall} 天后回落" if fall is not None else "尚未回落，重点复盘"
            warns.append(f"{d.isoformat()} 反弹 +{delta:.1f} kg（{fall_txt}）："
                         "2–4kg 绝大部分是钠水和糖原，不是脂肪，跑 rebound 看前一天吃了什么")
            break
    if not warns:
        print("- 无异常，按计划执行")
    else:
        for w in warns:
            print(f"- {w}")
    return 0


def cmd_report(args, records):
    if not records:
        print("没有记录，无法生成报告。")
        return 0
    end = parse_date(args.date) if args.date else parse_date(records[-1]["date"])
    span = 7 if args.mode == "week" else 30
    start = end - timedelta(days=span - 1)
    window = [r for r in records if start <= parse_date(r["date"]) <= end]
    if not window:
        print(f"{start.isoformat()} ~ {end.isoformat()} 区间内没有记录。")
        return 0
    by_date = {r["date"]: r for r in window}
    series = [(parse_date(r["date"]), float(r["weight"]))
              for r in window if r.get("weight") is not None]
    mode_name = "周报" if args.mode == "week" else "月报"

    print(f"# 减重{mode_name}（{start.isoformat()} ~ {end.isoformat()}）")
    print("")

    # 1. 体重趋势
    print("## 1. 本周体重趋势" if args.mode == "week" else "## 1. 本月体重趋势")
    if series:
        w_first, w_last = series[0][1], series[-1][1]
        w_max = max(series, key=lambda x: x[1])
        w_min = min(series, key=lambda x: x[1])
        lows = find_lows(series)
        low_txt = "低点数量不足" if len(lows) < 2 else (
            f"低点线 {fmt_delta(lows[-1][1] - lows[0][1])} kg"
            f"（{'向下' if lows[-1][1] - lows[0][1] < -0.2 else '走平' if abs(lows[-1][1] - lows[0][1]) <= 0.2 else '向上'}）")
        print(f"- 起止体重：{w_first:.1f} → {w_last:.1f} kg（{fmt_delta(w_last - w_first)} kg）")
        print(f"- 最高点：{w_max[1]:.1f} kg（{w_max[0].isoformat()}）")
        print(f"- 最低点：{w_min[1]:.1f} kg（{w_min[0].isoformat()}）")
        print(f"- 变化方向：{low_txt}")
    else:
        print("- 窗口内没有体重记录")
    print("")

    # 2. 饮食达标率
    print("## 2. 饮食达标率")
    latest_w = series[-1][1] if series else None
    if latest_w:
        lo, hi = PROTEIN_PER_KG[0] * latest_w, PROTEIN_PER_KG[1] * latest_w
        print(f"- 蛋白质目标：{lo:.0f}–{hi:.0f} g/天（1.6–1.8 g/kg × {latest_w:.1f} kg）")
    protein_days = sum(1 for r in window if r.get("protein_g") is not None
                       and latest_w and r["protein_g"] >= PROTEIN_PER_KG[0] * latest_w)
    meal_days = sum(1 for r in window if r.get("meals"))
    print(f"- 蛋白质达标：{protein_days}/{len(window)} 天")
    print(f"- 餐食记录：{meal_days}/{len(window)} 天")
    banned_days = []
    for r in window:
        text = (r.get("meals") or "") + (r.get("note") or "")
        hits = [k for k in BANNED_KEYWORDS if k in text]
        # 长词命中时去掉被包含的短词（如命中"白粥"不再单列"粥"）
        hits = [k for k in hits if not any(k != h and k in h for h in hits)]
        if hits:
            banned_days.append((r["date"], "、".join(hits)))
    if banned_days:
        print(f"- 禁止清单命中 {len(banned_days)} 天（机器初筛，需人工核对）："
              + "；".join(f"{d}（{k}）" for d, k in banned_days))
    else:
        print("- 禁止清单命中：0 天")
    print("")

    # 3. 运动总结
    print("## 3. 运动总结")
    ex = [r for r in window if r.get("exercise_min")]
    if ex:
        hrs = [r["hr_avg"] for r in ex if r.get("hr_avg") is not None]
        print(f"- 次数：{len(ex)} 次；总时长：{sum(r['exercise_min'] for r in ex)} 分钟"
              + (f"；平均心率：{sum(hrs) / len(hrs):.0f} 次/分" if hrs else ""))
        over = [r["date"] for r in ex if r["exercise_min"] > 60]
        if over:
            print(f"- 超 60 分钟红线：{'、'.join(over)}（皮质醇 30–40 分钟后飙升，拆成两次）")
    else:
        print("- 窗口内没有运动记录")
    print("")

    # 4. 反弹分析
    print("## 4. 反弹分析")
    if len(series) >= 2:
        events = rebound_events(series, by_date)
        if events:
            for d, delta, meals, fall in events:
                fall_txt = f"{fall} 天后回落" if fall is not None else "未回落（重点复盘）"
                print(f"- {d.isoformat()} +{delta:.1f} kg，前一天饮食：{meals}，{fall_txt}")
        else:
            print("- 无反弹")
    else:
        print("- 体重记录不足")
    print("")

    # 5. 建议
    print("## 5. 下周建议" if args.mode == "week" else "## 5. 下月建议")
    print("【待教练填写：改什么、保持什么、启动什么。每次只改一个变量。】")

    # 月报：体成分
    if args.mode == "month":
        bodies = [(r["date"], r["body"]) for r in window if r.get("body")]
        print("")
        print("## 6. 体成分趋势")
        if bodies:
            print("| 日期 | 体脂率% | 骨骼肌kg | 内脏脂肪 | 基础代谢 | 腰围cm | 臀围cm |")
            print("|------|---------|----------|----------|----------|--------|--------|")
            for d, b in bodies:
                print(f"| {d} | {b.get('body_fat', '—')} | {b.get('muscle', '—')} | "
                      f"{b.get('visceral', '—')} | {b.get('bmr', '—')} | "
                      f"{b.get('waist', '—')} | {b.get('hip', '—')} |")
            first_b, last_b = bodies[0][1], bodies[-1][1]
            for key, name in [("body_fat", "体脂率"), ("muscle", "骨骼肌"),
                              ("visceral", "内脏脂肪"), ("bmr", "基础代谢"),
                              ("waist", "腰围"), ("hip", "臀围")]:
                if key in first_b and key in last_b:
                    print(f"- {name}：{first_b[key]} → {last_b[key]}"
                          f"（{fmt_delta(last_b[key] - first_b[key])}）")
            print("- 口径：体脂率下降=在减脂肪；骨骼肌维持或增长=没掉肌肉；"
                  "体重降、骨骼肌也降，立刻提示蛋白质不够或力量训练不足；基础代谢只能稳不能掉；"
                  "腰围/腰臀比是冯雪「三体目标」的体型维度，与内脏脂肪等级互相印证。")
        else:
            print("- 窗口内没有体成分记录（每周固定条件测一次：早上空腹、排便后、同一台秤；"
                  "加测腰围和臀围各一次）")
    print("")
    print("---")
    print(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
          f"｜数据文件：{args.data}")
    return 0


# ---------- 入口 ----------

def build_parser():
    p = argparse.ArgumentParser(description="减重数据记录与趋势分析")
    p.add_argument("--data", default=DEFAULT_DATA, help="JSONL 数据文件路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("log", help="记录一天")
    sp.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD，默认今天")
    sp.add_argument("--weight", type=float, help="早上空腹体重 kg")
    sp.add_argument("--protein-g", type=float, help="蛋白质摄入 g")
    sp.add_argument("--meals", help="吃了什么（文字或照片描述）")
    sp.add_argument("--exercise-min", type=int, help="运动时长 分钟")
    sp.add_argument("--hr-avg", type=float, help="运动平均心率")
    sp.add_argument("--note", help="备注")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("body", help="记录体成分（每周一次）")
    sp.add_argument("--date", default=date.today().isoformat())
    sp.add_argument("--body-fat", type=float, help="体脂率 %")
    sp.add_argument("--muscle", type=float, help="骨骼肌 kg")
    sp.add_argument("--visceral", type=float, help="内脏脂肪等级")
    sp.add_argument("--bmr", type=float, help="基础代谢 大卡")
    sp.add_argument("--waist", type=float, help="腰围 cm（三体目标-体型维度）")
    sp.add_argument("--hip", type=float, help="臀围 cm（三体目标-体型维度）")
    sp.set_defaults(func=cmd_body)

    sp = sub.add_parser("list", help="最近记录表格")
    sp.add_argument("--days", type=int, default=14, help="显示最近 N 天，默认 14")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("trend", help="体重低点趋势")
    sp.add_argument("--days", type=int, default=0, help="只看最近 N 天，默认全部")
    sp.set_defaults(func=cmd_trend)

    sp = sub.add_parser("rebound", help="反弹关联分析")
    sp.add_argument("--days", type=int, default=0)
    sp.add_argument("--threshold", type=float, default=REBOUND_THRESHOLD,
                    help=f"日环比涨幅阈值 kg，默认 {REBOUND_THRESHOLD}")
    sp.set_defaults(func=cmd_rebound)

    sp = sub.add_parser("stats", help="减重总览")
    sp.add_argument("--goal-weight", type=float, default=None,
                    help="目标体重 kg（也可用环境变量 JIANFEI_GOAL_WEIGHT 或数据目录 config.json）")
    sp.add_argument("--goal-date", default=None,
                    help="目标日期 YYYY-MM-DD（也可用环境变量 JIANFEI_GOAL_DATE 或 config.json）")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("santi", help="冯雪三体目标评估（BMI/体脂率/腰围腰臀比）")
    sp.add_argument("--height", type=float, default=None,
                    help="身高 cm（必填，或环境变量 JIANFEI_HEIGHT / config.json）")
    sp.add_argument("--sex", choices=["male", "female"], default=None,
                    help="标准口径：male|female（必填，或环境变量 JIANFEI_SEX / config.json）")
    sp.set_defaults(func=cmd_santi)

    sp = sub.add_parser("today", help="今日任务卡：待办/饮食目标/运动安排/异常提醒")
    sp.add_argument("--goal-weight", type=float, default=None,
                    help="目标体重 kg（也可用环境变量 JIANFEI_GOAL_WEIGHT 或 config.json）")
    sp.add_argument("--sex", choices=["male", "female"], default=None,
                    help="热量安全线口径：male|female（或环境变量 JIANFEI_SEX / config.json）")
    sp.set_defaults(func=cmd_today)

    sp = sub.add_parser("report", help="生成周报/月报骨架")
    sp.add_argument("--mode", choices=["week", "month"], default="week")
    sp.add_argument("--date", help="报告截止日期 YYYY-MM-DD，默认最后一条记录")
    sp.set_defaults(func=cmd_report)
    return p


def main():
    args = build_parser().parse_args()
    cfg = load_config(args.data)
    # 个人参数优先级：命令行 > 环境变量 > config.json（均未设置则为空）
    if getattr(args, "goal_weight", None) is None:
        args.goal_weight = _env_float("JIANFEI_GOAL_WEIGHT")
        if args.goal_weight is None:
            args.goal_weight = cfg.get("goal_weight")
    if getattr(args, "goal_date", None) is None:
        args.goal_date = os.environ.get("JIANFEI_GOAL_DATE") or cfg.get("goal_date")
    if getattr(args, "height", None) is None:
        args.height = _env_float("JIANFEI_HEIGHT")
        if args.height is None:
            args.height = cfg.get("height")
    if getattr(args, "sex", None) is None:
        args.sex = os.environ.get("JIANFEI_SEX") or cfg.get("sex")
    records = load_records(args.data)
    return args.func(args, records)


if __name__ == "__main__":
    sys.exit(main())
