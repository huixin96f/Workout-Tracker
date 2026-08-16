# 训练历史追踪器

本项目的完整指引在 **[`CLAUDE.md`](CLAUDE.md)**（与 harness 无关，任何 agent 都读它）。

先读那个文件，它会让你接着读 `WorkoutTracker_Rules.md`。

要点先给三条，免得漏：

1. **唯一真源是 `workout-history.html`**，`.build/` 全是生成物，绝不手改。
2. **`WorkoutTracker_Rules.md` 第 11 节的六条回复规则是强制的**，漏掉会让用户手机上那份悄悄变旧。
3. **发布必须复用 `.build/publish.json` 里的 `url`**，换会话不传 url 会生成新网址、作废用户书签。
