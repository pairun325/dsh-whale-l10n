# 统一文本表（Universal Text Table）

汉化工具链的中间格式。**提取器**产出它，**翻译器**只读它，**注入器**消费它。
只要三方都遵守这个格式，任意引擎都能插进来。

## 文件形态

一行一条 JSON（JSONL，UTF-8，`\n` 分隔），扩展名建议 `.utl.jsonl`。

## 字段

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | ✅ | string | 全局唯一。建议 `<engine>:<file>:<key>`，注入器靠它定位 |
| `src` | ✅ | string | 原文 |
| `dst` | ✅ | string | 译文，空串表示未翻译 |
| `file` | ✅ | string | 相对游戏根目录的文件路径，不含绝对路径 |
| `loc` | | string | 在文件内的定位：`offset:1234` / `row:56` / `asset:level0/Path/To/Obj` |
| `kind` | | enum | `ui` / `dialog` / `item` / `name` / `title` / `other`，默认 `other` |
| `context` | | string | 给翻译模型看的上下文，比如「主菜单按钮」「装备描述」 |
| `limit` | | int | 译文长度上限（UI 框宽度撑不住的约束） |
| `status` | ✅ | enum | `new` / `translated` / `skip` / `review` |
| `note` | | string | 备注，人工审核用 |

`status=skip` 的条目注入器必须原样跳过（比如纯数字、型号、占位符）。

## 规则

1. **不翻译就留空**，不要用原文填充 `dst`。
2. **占位符必须原样保留**：`$VAR$`、`{0}`、`\n`、`[Root.GetName]`、`<color=#fff>`。
3. **长度上限**（`limit`）是硬约束，超出时翻译器要截断或改写，而不是让 UI 溢出。
4. **不要写绝对路径**，`file` 永远相对游戏根目录 —— 这样文本表可以换机器、进版本库。
5. 同一个 `id` 只能出现一次。

## 适配器契约

一个引擎适配器实现两个函数：

```python
def extract(game_dir: str, opts: dict) -> Iterable[dict]:  # 产出文本表条目
def pack(game_dir: str, entries: list[dict], opts: dict) -> dict:  # 写回，返回统计
```

`pack` 必须遵守：
- 默认**只读原文件**，产物写到独立位置（mod 目录 / 新文件），除非显式 `--in-place`
- 写之前把原文件备份成 `<name>.bak.<时间戳>`
- 失败要整体回滚，不留半成品

## 现有适配器

| 名称 | 引擎 | 注入方式 | 状态 |
|---|---|---|---|
| `paradox` | Clausewitz（HOI4/Stellaris/EU4/CK3） | 外挂 mod（localisation/simp_chinese） | ✅ 可用 |
| `unity-il2cpp` | Unity IL2CPP | BepInEx 运行时替换 | 🚧 计划中 |
| `unity-mono` | Unity Mono | 资产回写 | 🚧 计划中 |
| `generic-json` | 自定义 JSON 语言包（如 Limbus 的 Lang/） | 放文件 + 改 config | 🚧 计划中 |
