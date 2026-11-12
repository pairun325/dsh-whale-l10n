# UTL 汉化地基 v0.1

给「提取 → AI 翻译 → 注入」这套流程打的地基。**它不承诺支持任何具体游戏**，只提供三样东西：

1. 一种中间格式（统一文本表），让提取器和注入器可以互相替换
2. 一个翻译层（千问 + 缓存 + 占位符规则），谁都能用
3. 一个适配器接口，加一个引擎只要写一个类

## 快速试一下

需要 Python 3.10+（本机在 `E:\ds\resources\runtime\primary-runtime\dependencies\python\python.exe`），
以及一个能用的千问 key（放在 `keys-handover.json` 的 `ws_key_20261027.key`，脚本会自动往上找）。

```bat
cd helper

:: 1. 提取。examples/demo-game 是一个假游戏，用来演示流程
python utl.py extract --adapter generic-json --game ../examples/demo-game --out demo.utl.jsonl --src-lang en

:: 2. 翻译。只改表里的 dst，不动游戏文件
python utl.py translate --table demo.utl.jsonl

:: 3. 注入。产物写进新语言目录，原文件不碰
python utl.py pack --adapter generic-json --game ../examples/demo-game --table demo.utl.jsonl --lang zh_demo --src-lang en --switch-lang
```

跑完打开 `examples/demo-game/LimbusCompany_Data/Lang/zh_demo/story.json`，看到的是中文。
`Lang/config.json` 里的 `lang` 也会被改成 `zh_demo` —— 这就是「原生语言包」注入模式，删目录即还原。

## 命令

| 命令 | 作用 |
|---|---|
| `utl.py adapters` | 列出已注册的适配器 |
| `utl.py extract --adapter X --game <dir> --out t.jsonl` | 提取文本表 |
| `utl.py translate --table t.jsonl [--batch 40]` | 批量翻译，带本地缓存 |
| `utl.py pack --adapter X --game <dir> --table t.jsonl` | 写回 |
| `utl.py stats --table t.jsonl` | 看进度 |

老的 Paradox 一键流程还在：`l10n.py scan/build/remove/status`。

## 术语表：用官方中文反推

有官方简中的游戏（Paradox 全家桶），不用搜索、不用猜——把英文和官方中文按 key 对齐，
从 10 万句人工校对过的平行语料里挖译名：

```bat
:: 挖一份该游戏专用的术语表
python utl.py glossary --game "F:\SteamLibrary\steamapps\common\Hearts of Iron IV" ^
    --sample 900 --rounds 9 --out glossary-hoi4.json

:: 翻译时挂上它
python utl.py translate --table hoi4.utl.jsonl --glossary glossary-hoi4.json
```

术语表按游戏分开存（`glossary-<游戏>.json`），不要混用：HOI4 的「波兰」不该拿去翻 Limbus。
`--merge "<分组名>"` 可以把结果并进 `glossary.json` 的某个分组。

## 加一个引擎

在 `helper/adapters.py` 里写一个类：

```python
@register('my-engine')
class MyEngine(Adapter):
    def extract(self, game_dir, opts):
        yield {'id': '...', 'src': '原文', 'dst': '', 'file': '相对路径', 'status': 'new'}
    def pack(self, game_dir, entries, opts):
        ...  # 只写新文件，失败整体回滚
        return {'ok': True, 'files_written': 1}
```

`utl.py` 一行都不用改。格式与约定见 `docs/SCHEMA.md`。

## 已注册

- `paradox` —— HOI4/Stellaris/EU4/CK3 的 yml，外挂 mod 注入
- `generic-json` —— 游戏自带外挂语言包（如 Limbus 的 `LimbusCompany_Data/Lang/`）

## 明确不做的事

- 不打包任何游戏的原文或译文（版权）
- 不修改可执行文件
- 不支持的游戏，请自己写适配器 —— 地基只保证接口稳定
