# 训练历史追踪器 — 新会话请先读这里

> 其他 harness：`AGENTS.md` 和 `README.md` 都指向本文件，内容以本文件为准。

Huixin 维护一个**四日哑铃训练循环**，用聊天口头汇报每次训练的重量组数，由助手记进一个
单文件 HTML artifact，并发布成一个私有网页供手机查看。

## 第一件事

**完整读 [`WorkoutTracker_Rules.md`](WorkoutTracker_Rules.md)。** 它是这个项目的单一交接来源，
涵盖训练计划结构、记录约定、动作替换史、完整训练历史、构建发布管线，以及**强制的回复规则**。

**第 11 节是强制的，不是建议。** 漏掉会造成实际损失——手机上那份会悄悄变旧而两边都不知道。
六条规则的要点：

| 规则 | 触发条件 | 回复里必须有 |
|---|---|---|
| 11.1 | artifact 有任何改动 | 内联渲染的 artifact **和** 网址，两样齐全 |
| 11.2 | artifact 有任何改动 | 同一条回复里重新构建 + 重新发布 |
| 11.3 | — | 顺序：改源文件 → validate → build → 发布 → 展示 |
| 11.4 | 用户问"今天练什么" | 上次的重量组数 + 内联 artifact + 网址（三样）|
| 11.5 | — | **不主动预测、不主动建议加重**；只在两个条件下提醒 |
| 11.6 | 训练中逐个汇报动作 | 下一个动作 + 今天所有未做动作的重量组数 + 网址 |

其他容易踩的：**绝不编造数据**，措辞含糊先问再记；点评只给事实观察，不要鼓励鞭策语。

## 文件

| 文件 | 作用 |
|---|---|
| `workout-history.html` | **唯一真源**。所有改动改这里，绝不改 `.build/` 里的东西 |
| `WorkoutTracker_Rules.md` | 完整说明书（先读这个）|
| `tools.py` | 校验器 + 发布构建，合并在一个文件里 |
| `.build/` | 全部生成物，一次性产物，不要手改也不要当第二真源 |

## 常用命令

```bash
python3 tools.py validate   # 数据校验 + 真跑一遍 JS 渲染
python3 tools.py build      # 校验通过后生成 .build/workout-history.artifact.html
python3 tools.py streaks    # 规则 11.5 的判定依据，不要靠印象
python3 tools.py preview    # 本地起服务，手机宽度实测
```

只依赖 python3 标准库。JS 运行时检查会自动挑 `node` 或 macOS 的 `osascript`；两个都没有时
**跳过渲染检查并明确警告**，数据校验照跑。

## 发布

参数在 [`.build/publish.json`](.build/publish.json)，每次照抄，**尤其是 `url`**：

- 路径是**相对**的，相对于本文件夹解析
- **同一会话**重发同一路径 → 网址不变
- **换了会话**必须把 `url` 显式传进去，否则会生成新网址、作废用户的书签
- favicon 保持 🏋️ 不变（用户靠图标找标签页）

当前网址：https://claude.ai/code/artifact/a4d428a8-4f06-4fbd-b844-4cb0c7760ea1

## 每次新训练的流程

1. 用户逐个动作口头汇报 → 聊天里用表格回显确认（遵守规则 11.6）
2. 含糊处先问清楚再记
3. 用户说"更新 artifact" → 追加 session（日期升序）+ 更新 footer 日期区间
4. `validate` → `build` → 重新发布 → 按规则 11.1 展示两样
5. 新产生的长期决定回写进 `WorkoutTracker_Rules.md`，保持它是完整的交接来源
