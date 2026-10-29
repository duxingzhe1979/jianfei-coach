# AI 减重教练（jianfei-coach）

> 把减肥从「靠意志力」变成「靠系统」的 AI 教练技能。适用于 WorkBuddy / Claude Code 等支持 SKILL.md 规范的 AI Agent。

## 它解决什么问题

问 AI「怎么减肥」，得到的答案通常是「多吃蛋白质、少吃精制碳水、每周运动三次」——这类建议换成任何人都成立，所以你照做了也不一定有用。

这个技能的判断标准只有一条：**如果一个建议换成任何人都成立，那它就是废话，不是教练该说的话。**

所以它不做通用方案，只做「针对你当天数据的具体动作」：

| 普通 AI 的回答 | 这个技能的回答 |
|---|---|
| 多吃蛋白质 | 还差 67 g：蛋白粉 1 勺 × 3 + 鸡蛋 1 个 |
| 跑慢点 | 放慢配速，平均心率压到 120 左右 |
| 别吃精制碳水 | 主食换芋头 200 g，碳水少一半，GI 低 30 个点 |

## 核心方法

**四步循环**：记录 → 分析 → 调整 → 复盘。走完一轮再走下一轮，不是线性流程。

- **记录**：每天只记三样（空腹体重、每餐照片、运动截图），门槛压到 3 秒，记不下来等于没记
- **分析**：四种分析对应四种盲区——体重趋势（看不见进步）、饮食与反弹关联（看不见真实原因）、运动心率区间（看不见运动质量）、体成分趋势（看不见肌肉流失）
- **调整**：每次只改一个变量，观察三到五天。这是在给自己的身体做对照实验
- **复盘**：每周周报、每月月报，把感觉变成数据

**目标框架**采用冯雪《科学减肥 16 讲》的「三体目标」：体重、体脂、体型三维验收，健康重要性排序为 **体型 > 体脂率 > 体重**。腹型肥胖的危害远大于体重数字，所以腰围比体重更值得盯。

## 安装

### WorkBuddy

```bash
git clone https://github.com/duxingzhe1979/jianfei-coach.git ~/.workbuddy/skills/jianfei-coach
```

### Claude Code

```bash
git clone https://github.com/duxingzhe1979/jianfei-coach.git ~/.claude/skills/jianfei-coach
```

也可以下载 zip 解压后放进上面的 `skills/` 目录，重启客户端生效。

无第三方依赖，只需要 Python 3（标准库即可）。

## 用法

装好后直接用自然语言说话，不需要记命令：

- 「早上空腹 79.2 公斤，帮我记一下，今天吃什么、怎么练」
- 「今天早上好」→ 它会主动给出今日任务卡
- 「帮我看下这周为什么反弹了」→ 跑反弹关联分析
- 「生成本周复盘周报」→ 出周报骨架 + 下周建议
- 发食物照片、发运动截图，它自己识别并记录

### 今日任务卡（真实输出）

```markdown
### 今日任务卡（周四）

**进度**：当前 79.2 kg｜累计 +0.0 kg｜连续记录 1 天

**今日饮食目标**
- 热量：1500–1600 大卡（体重 × 25 kcal/kg，缺口 500–750）
- 蛋白质：127–143 g（1.6–1.8 g/kg）
- 今天已摄入 60 g，还差 67 g：蛋白粉1勺×3 + 鸡蛋1个

**今日运动安排**
- 今天已运动 40 分钟，平均心率 118（心率在减脂区间，保持）

**数据待办**
- 体成分从未记录（每周一次：早上空腹、排便后、同一台秤）
- 腰围/臀围从未记录（三体目标-体型维度，软尺 30 秒）
```

## 数据与隐私

**所有数据存在你自己电脑上，不联网、不上传。**

- 数据文件：`~/.workbuddy/jianfei-coach/weight-log.jsonl`（纯 JSONL，随时能看、能改、能导出）
- 个人参数：同目录 `config.json`（模板见 `assets/config.example.json`）
- 数据目录可用 `--data` 参数或 `JIANFEI_DATA` 环境变量覆盖
- 脚本只用 Python 3 标准库，无任何网络请求

## 目录结构

```
jianfei-coach/
├── SKILL.md                  技能定义：角色、四步流程、每日教练机制、沟通规范
├── scripts/
│   └── weight_log.py         数据记录与趋势分析（877 行，纯标准库）
├── references/
│   ├── fengxue.md            冯雪科学减肥方法论完整拆解
│   ├── diet.md               饮食参数、蛋白质来源优先级、食物判断四问
│   ├── exercise.md           有氧/力量/碎片化训练/NEAT
│   ├── principles.md         必须讲透的八个原理
│   └── scenarios.md          饭局喝酒、外食点菜、烧烤、假期反弹
└── assets/
    └── config.example.json   个人参数模板
```

`references/` 是按需加载的——讨论饮食时才读 `diet.md`，避免一次性塞满上下文。

## 命令行工具

```bash
python3 scripts/weight_log.py today                          # 今日任务卡
python3 scripts/weight_log.py log --weight 79.2 --protein-g 120 --exercise-min 45
python3 scripts/weight_log.py body --body-fat 22 --muscle 35 --visceral 8 --waist 85
python3 scripts/weight_log.py santi --height 170 --sex male   # 三体目标评估
python3 scripts/weight_log.py trend                           # 体重低点趋势
python3 scripts/weight_log.py rebound                         # 反弹关联分析
python3 scripts/weight_log.py report --week                   # 周报骨架
```

## 三条底线

1. **不承诺速度。** 每周 0.5–1 公斤是健康区间。承诺「一个月 20 斤」必然靠极低热量饮食，代谢损伤，反弹率极高。
2. **不推荐药物路线。** GLP-1 类药物有适应症，需医生判断，AI 不替代医生。
3. **不做医疗诊断。** 本技能是行为管理工具，不是医疗建议。有基础疾病、孕期、进食障碍史等情况请遵医嘱。

## License

MIT
