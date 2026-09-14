# workbuddy-token-dashboard

把 WorkBuddy 的真实 token 消耗变成一张**单文件离线看板**，并附带"固定入口 + 每日自动刷新"完整链路。作为一个 WorkBuddy Skill 分发：别人加载后说一句"看看我的 token 用量"，Agent 就会扫描本机会话日志，生成同款看板、在资料库建立固定入口并创建每日自动刷新任务。

## 功能

### 看板（单文件 HTML，离线可看）

- **KPI**：合计 Token（含环比）/ 日均 / 会话 / 轮次（含环比）/ 输入输出比 / 缓存命中率（含量化"为你省了多少"）/ **使用工具** / **AI 耗时** / **官方实际积分**（含环比）/ 消耗金额估算 / 同用量 API 直购价
- **环比趋势**：本期 vs 上一期同等长度窗口，随时间窗口联动
- **积分消耗 TOP 榜**：按模型 / 按工作空间两个维度
- 日活热力图（20 分钟粒度）、0–24 时模型山脊图、每日模型用量堆叠柱、单次请求大小分布
- 工作空间 → 会话 → 单次请求三级下钻；空日志引导页；深浅色主题、1/3/7/30 天窗口 + 自定义区间

### 计费口径（多级实时刷新）

- **官方实际积分（主口径，最准）**：直接汇总日志逐请求 `rawUsage.credit`，与 App 账单同源
- **模型倍率 / 积分单价 / 模型牌价**每次生成实时抓取官方页与 LiteLLM 社区价目表，失败逐级回退本地缓存与内置表，官方调价自动跟随
- 内置国内外主流模型牌价表，支持第三方自定义模型（经 LiteLLM 覆盖 3900+ 模型）

### 固定入口 + 每日自动刷新（skill 标准交付）

- 首次生成后自动把看板导入 WorkBuddy **资料库**为在线页面（「我的文档 → Token看板 <日期>」固定入口，点开即最新数据）
- 自动创建**每日 09:00 自动化**：重跑脚本 → 重导入覆盖同一节点（链接不变、标题日期更新）→ 打开并汇报关键数字
- 标题日期 = 最近一次成功刷新日，日期落后即当天任务未跑成，一眼可判断；全生命周期只保留一个节点，绝不堆积文件
- 说明：WorkBuddy 左侧导航栏为原生界面无法加自定义入口，以上"资料库固定入口 + 定时自动化"即等效方案

## 安装（作为 Skill）

把本包内容放到 WorkBuddy 用户级技能目录：

```
~/.workbuddy/skills/token-dashboard/
├── SKILL.md                  # Skill 定义（Agent 加载后按此执行）
├── README.md
└── scripts/
    ├── gen_dashboard.py      # 看板生成脚本（跨平台，仅标准库）
    ├── multipliers.json      # 模型倍率缓存（离线兜底）
    ├── prices.json           # 模型牌价缓存（离线兜底）
    └── membership.json       # 积分单价缓存（离线兜底）
```

- Windows：`C:\Users\<你>\.workbuddy\skills\token-dashboard\`
- macOS / Linux：`~/.workbuddy/skills/token-dashboard/`

解压到上述目录后重启 WorkBuddy 会话即可加载。之后直接说"看看我的 token 用量"即可触发；首次执行会一并建好资料库固定入口与每日自动刷新任务。

## 手动运行（不装 Skill 也行）

Python 3.8+，仅标准库，无需装任何包：

```bash
python scripts/gen_dashboard.py                  # 输出 ./token-dashboard.html
python scripts/gen_dashboard.py --out 看板.html   # 自定义输出路径
python scripts/gen_dashboard.py --projects <目录> # 自定义会话日志目录
```

Windows / macOS / Linux 通用。

## 数据说明

- 数据源：`~/.workbuddy/projects/**/*.jsonl` 中每条记录的 `providerData.rawUsage / usage` 字段（请求级真实 usage）；子代理日志自动归属父会话。
- 计费细节（官方 credit 口径、逐模型回归、价格三级来源等）见 SKILL.md「数据与口径说明」。
- 所有数据均来自本机日志，不外发、不上传；唯一联网动作是生成时抓取官方倍率/价格页（失败自动回退缓存/内置表，全程离线也可用）。

## 致谢

本项目最初受 [fuyi-git/token-dashboard](https://github.com/fuyi-git/token-dashboard) 启发，此后在计费口径（官方积分）、固定入口与自动刷新链路等方面已大幅重写演进。

## 许可证

[MIT](./LICENSE) © 2026 majiabin2020

