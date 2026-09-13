# 阶段代码快照约定

## 目的

路线图中的每个 HARN 阶段都要保留一份完整代码，使学习者无需切换 Git commit，也能独立查看、运行和测试任意阶段。

```text
当前开发版本（仓库根目录）
          │
          ├── 完成 HARN-00 ──→ stages/HARN-00/
          │
          ├── 演进到 HARN-01 ─→ stages/HARN-01/
          │
          ├── 演进到 HARN-02 ─→ stages/HARN-02/
          │
          └── ...
```

## 目录职责

### 仓库根目录

根目录是下一阶段的开发副本。它会持续演进，因此只代表最新完成或正在构建的阶段。

### `stages/HARN-XX/`

每个目录是对应阶段完成时的只读历史快照。它包含完整源码、依赖声明、Demo、测试和阶段说明，不能依赖根目录中的 `harness/` 才能运行。

后续阶段只能新增新的快照目录，不应为了迁就新代码而回改旧快照。若旧阶段本身存在事实性错误，应单独说明原因并同时保留可追踪的修复记录。

## 每个阶段的操作顺序

1. 阅读路线图、当前根目录代码和上一阶段说明。
2. 只在根目录实现当前阶段要求的最小演进。
3. 运行当前阶段测试、Demo、编译和依赖检查。
4. 提交已经验证的根目录实现，使该阶段拥有明确的来源 commit。
5. 运行下面的命令，从该 commit 创建快照：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/create_stage_snapshot.ps1 HARN-XX
   ```

6. 进入新快照目录，再次运行其测试与 Demo，证明它可以独立工作。
7. 提交新的阶段快照，并把两个 commit 一起推送到 `main`。

## 快照最低内容

```text
stages/HARN-XX/
├── harness/
├── examples/
├── tests/
├── docs/
│   └── stage_notes/
├── README.md
└── pyproject.toml
```

`.git/`、`.venv/`、`__pycache__/`、构建产物以及外层 `stages/` 本身不得复制进快照。

`.gitattributes` 将根目录的 `stages/` 标记为 `export-ignore`，因此脚本使用 `git archive` 时不会把旧快照递归复制到新快照中。脚本还会拒绝覆盖已有快照或在工作区不干净时运行。

## 当前快照来源

| 快照 | 来源提交 | 说明 |
| --- | --- | --- |
| `stages/HARN-00/` | `74a6788` | HARN-00 完成状态 |
| `stages/HARN-01/` | `ea024ec` | HARN-01 完成状态 |
| `stages/HARN-02/` | `50ebe9d` | HARN-02 完成状态 |
| `stages/HARN-03/` | `5262b5a` | HARN-03 完成状态 |
| `stages/HARN-04/` | `8dee76c` | HARN-04 完成状态 |
| `stages/HARN-05/` | `4a0856c` | HARN-05 完成状态 |
| `stages/HARN-06/` | `5f3e997` | HARN-06 完成状态 |
| `stages/HARN-07/` | `642e57f` | HARN-07 完成状态 |
| `stages/HARN-08/` | `098555e` | HARN-08 完成状态 |
