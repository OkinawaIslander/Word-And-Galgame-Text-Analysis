# TextLens

**把 Word 文档、galgame 脚本和杂乱文本变成可检索、可统计的本地语料库。**

TextLens 是一个纯 Python 的本地 CLI 工具，面向翻译校对、剧情考据、角色台词整理和写作素材检索。它不需要把文件上传到任何服务，索引就是一个 SQLite 文件。

## 能做什么

- **一次索引，多种格式统一检索**：`.docx`、`.txt`、`.md`、KiriKiri `.ks`、Ren'Py `.rpy`、NScripter `.scn`、TJS `.tjs`、`.json/.jsonl`、`.csv/.tsv`、`.srt/.ass`。
- **内置 YPF 解包**：直接读取 YU-RIS `.ypf`，自动恢复 YSTB 密钥，把编译脚本还原成可检索文本。
- **中文短词也能搜**：FTS5 trigram 处理 3 字以上片段；1–2 个字自动回退 `LIKE`，不会出现「搜两个字没结果」。
- **知道是谁说的**：尽力从 `诗音：「……」`、`[name text="诗音"]`、Ren'Py `Character("Eileen")` 等写法提取角色名，可按角色过滤。
- **直接看上下文**：`context` 显示命中行前后若干段，适合校对和考据。
- **语料分析**：字数、台词量、角色台词排行、高频词、语言分布、预计阅读时间，可导出 Markdown 报告。
- **批量导出**：合并成 JSONL / TXT / Markdown，方便喂给翻译工具、表格或后续 NLP 流程。
- **增量索引**：按路径、大小、mtime 和 SHA-1 跳过未变化文件；`--force` 可强制重解析。
- **本地优先**：无网络请求，无账号，无云端依赖。

## 快速开始（Windows PowerShell）

```powershell
cd "D:\Desk\个人\TextAnalysis"

# 建议使用虚拟环境
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 安装（核心无第三方依赖）
python -m pip install -e .

# 中文分词推荐安装 jieba；不装也能用
python -m pip install -e ".[zh]"
# 或者：python -m pip install jieba

textlens --help
```

如果网络受限、venv 里没有 setuptools，可以复用系统包后再离线安装：

```powershell
py -m venv --system-site-packages .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --no-build-isolation -e .
```

或者完全跳过安装，直接使用下面的 `run.cmd`。

如果 PowerShell 不允许执行激活脚本：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

不想安装也可以直接用仓库里的启动脚本：

```powershell
.\run.cmd --help
.\run.cmd index "D:\Games\MyGame"
```

`run.ps1` 功能相同；如果 PowerShell 拦截脚本执行，用 `run.cmd`，或只对当前进程放开：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run.ps1 --help
```

## 两分钟演示

仓库自带四个演示文件（Word、纯文本、KiriKiri、Ren'Py），不需要准备素材：

```powershell
.\run.cmd index examples\demo --db .textlens\demo.db --rebuild
.\run.cmd search "约定" --db .textlens\demo.db
.\run.cmd context "约定" --db .textlens\demo.db --before 1 --after 2
.\run.cmd stats --db .textlens\demo.db --top 15
.\run.cmd search "约定" --db .textlens\demo.db --speaker 诗音
```

## 解包 galgame 归档

### YU-RIS `.ypf`

```powershell
.\run.cmd unpack "examples\[CLOCKUP team.DYO] Zwei Worter -ツヴァイ・ウォルター- HDリマスター.ypf" -o unpacked\zwei

# 只列条目，不写文件
.\run.cmd unpack "游戏.ypf" --list

# 同时导出解压后的 .ybn 原始脚本
.\run.cmd unpack "游戏.ypf" -o unpacked\game --raw
```

默认会：

- 解析 YPF 目录，解压全部条目；
- 自动从 YSTB 指令/参数结构恢复 4 字节密钥（也可 `--key 9b093929` 手动指定）；
- 解码 V5 编译脚本中的对话和旁白，按角色写成 `说话人：台词`；
- 输出 `text/`（每个脚本一个 UTF-8 `.txt`）、`corpus.jsonl` 和 `manifest.json`。

然后直接索引解出来的文本：

```powershell
.\run.cmd index "unpacked\zwei\text" --db .textlens\zwei.db
.\run.cmd search "イリアス" --db .textlens\zwei.db
.\run.cmd context "ご飯" --db .textlens\zwei.db --before 2 --after 2
```

示例结果：266 个 YPF 条目中导出 83 个文本脚本、83,226 条片段、约 170 万字符；索引后可直接按角色、关键词和上下文检索。完整统计见 [docs/ypf_report.md](docs/ypf_report.md)。

### 其他归档包：待开发

除 `.ypf` 外，以下归档格式的解包功能**暂未实现**：

```text
.arc  .cpz  .dat  .xp3  .nsa  .rpa  .pak  .pck
```

这些文件不会被强行解析，也不会生成乱码；`index` 会跳过并在摘要中提示。请先用 GARbro、krkrextract、nsaextract 或对应引擎工具解包，导出 `.ks` / `.rpy` / `.scn` / `.txt` 后再交给 TextLens。

## 典型工作流

### 1. 建立索引

```powershell
# 可以同时传多个目录/文件；默认数据库为 .textlens\index.db
textlens index "D:\Docs\设定集" "D:\Games\MyGame\script"

# 指定数据库，只处理部分格式
textlens index "D:\Games\MyGame" --db "D:\Corpus\mygame.db" --include .ks,.txt,.docx

# 清空重建 / 强制重解析 / 清理已删除文件
textlens index "D:\Games\MyGame" --rebuild
textlens index "D:\Games\MyGame" --force
textlens index "D:\Games\MyGame" --prune
```

### 2. 检索

```powershell
# 中文、日文、英文都可以
textlens search "约定"
textlens search "约定 车站"                 # 多个词 AND
textlens search "约定 车站" --any           # 多个词 OR
textlens search "我们约定好了" --phrase      # 整句短语

# 限定角色、文件、格式、片段类型
textlens search "明天见" --speaker 诗音
textlens search "黄昏" --ext .ks,.txt
textlens search "黄昏" --file chapter03
textlens search "黄昏" --kind dialogue

# 正则（会扫描候选片段，适合复杂模式）
textlens search "「.{0,20}约定.{0,20}」" --regex

# 给程序消费
textlens search "约定" --json
```

### 3. 看上下文

```powershell
textlens context "约定的地方" --before 3 --after 3
textlens context "诗音" --speaker 诗音 --limit 3 --json
```

输出中 `>` 标记命中片段，行号可用于回到原文件。

### 4. 统计与报告

```powershell
textlens stats
textlens stats --top 40 --include-speakers
textlens stats --file chapter03 --format md --output report.md
textlens stats --format json --output report.json
```

`report.md` 包含总览、角色台词排行、高频词和文件明细，可直接放进项目文档。仓库里的 [docs/demo_report.md](docs/demo_report.md) 就是用演示语料生成的示例。

### 5. 导出合并语料

```powershell
# 全部片段，JSONL（每行一个片段，最适合后续处理）
textlens extract "D:\Games\MyGame" -o corpus.jsonl

# 只导出有角色名的台词
textlens extract "D:\Games\MyGame" -o dialogue.jsonl --dialogue-only

# 人读的合并文本
textlens extract "D:\Docs" "D:\Games\MyGame\script" -o corpus.md --format md

# 管道使用
textlens extract "D:\Docs" -o - --format txt | Select-String "约定"
```

## 搜索语义

- 查询会做 Unicode NFKC、大小写折叠、空白归一化；全角/半角、英文大小写不影响命中。
- 默认按空白拆词并要求全部命中（AND）；中文没有空格时按连续子串匹配。
- 3 个字符及以上走 SQLite FTS5 trigram；1–2 个字自动回退 `LIKE`，结果按文件路径/行号排序。
- `--phrase` 把整个输入当短语；`--regex` 使用 Python 正则，忽略大小写。
- 角色名也参与检索（搜索“诗音”会命中她的台词）；`--speaker` 则是只保留该角色说话的片段。
- `--speaker`、`--file` 是包含匹配；`--ext` 可逗号分隔或重复传。
- 脚本/文本的行号是源文件行号；Word 使用段落、表格行和脚注的块序号，不对应页面行号。
- 返回码：有结果 `0`，无结果 `1`，参数/索引错误 `2`，便于脚本判断。

## 支持格式与解析规则

| 格式 | 解析内容 | 说明 |
| --- | --- | --- |
| `.ypf` | YU-RIS 条目表、YSTB V5 编译脚本 | 用 `unpack` 解包并提取文本；自动恢复 XOR 密钥 |
| `.docx` / `.docm` / `.dotx` / `.dotm` | 正文段落、标题、表格行、脚注、文档属性 | 纯标准库读取 OOXML；跳过修订删除内容；不解析图片 |
| `.doc` / `.dot` | 不支持直接读取 | 用 Word 另存为 `.docx`，或 `soffice --headless --convert-to docx` |
| `.txt` / `.md` | 逐行文本、Markdown 标题 | 自动检测 UTF-8 / UTF-16 / GB18030 / Big5 / CP932 等 |
| `.ks` | KAG 标签剥离后的文本、`[name text="…"]` 角色 | 跳过 `*label`、`@` 命令、`;` 注释 |
| `.rpy` | `Character("…")` 映射、对白、旁白、选项 | 跳过 Python/场景指令；支持 `extend` 与行尾 `with` |
| `.scn` | 文本行、`名字「…」` 角色 | 跳过常见 NScripter 命令和标签 |
| `.tjs` | 字符串字面量中的人类可读文本 | 尽力提取；TJS 是代码，运行时拼接的文本可能漏 |
| `.json` / `.jsonl` | `text/message/content…` + `speaker/name/character…` | 其他字段递归查找 |
| `.csv` / `.tsv` | 自动识别 text/speaker 列 | 无表头时默认首列为角色、末列为文本 |
| `.srt` | 字幕正文 | 保留时间轴对应的源行号 |
| `.ass` / `.ssa` | `Dialogue:` 行、角色字段 | 去除 `{\…}` 样式标签 |

### 容器处理

- **YU-RIS `.ypf`**：`unpack` 可以直接读取；如果文本以 YSTB V5 编译脚本存放，会解密并还原对话/旁白。
- **`.arc` / `.cpz` / `.dat` / `.xp3` / `.nsa` / `.rpa` / `.pak` / `.pck`**：解包功能待开发。索引时会识别并跳过，避免把归档当成文本。

在解包支持完成前，建议先用 [GARbro](https://github.com/morkt/GARbro)、`krkrextract`（KiriKiri XP3）、`nsaextract`（NScripter NSA）或引擎专用工具导出 `.ks` / `.rpy` / `.scn` / `.txt`，再交给 TextLens 索引。

### 角色名是怎么认出来的

TextLens 优先使用格式本身的信号：Ren'Py 的 `Character("…")` 定义、KiriKiri 的 `[name text="…"]`、ASS 的 Name 字段、CSV 的 speaker 列。纯文本则使用 `名字：台词`、`名字「台词」`、`【名字】台词` 等模式。

为了避免把小说里的 `岛村心想：……` 误判成角色，`名字：` 模式会拒绝常见的叙述动词结尾（心想、问、说、写道……）、过长的前缀和“作者/声明/参考资料”等标签。宁可把不确定的角色留空，也不大量制造假角色；`stats` 里的角色排行可以直接反映这一点。

### 编码处理

Galgame 脚本常见 UTF-16LE、Shift-JIS/CP932、GB18030 和 Big5。TextLens 先看 BOM 和合法 UTF-8，再对候选编码做字符合理性评分（假名、常用汉字、控制字符、私用区等），最后选出最可信的编码。检测结果会记录在数据库的 `encoding` 字段里。

## 数据与目录

```text
.textlens/
  index.db        # SQLite：文档元数据 + 片段 + FTS5 索引
  index.db-wal
  index.db-shm
```

数据库默认位于当前工作目录。可以用 `--db` 指向任意位置，例如放到同步盘或项目数据目录。删除数据库不会影响源文件，只是需要重新索引。

### 表结构（简版）

- `documents`：路径、标题、格式、语言、编码、大小、mtime、SHA-1、字数、台词数。
- `segments`：文档 ID、行号、类型（台词/旁白/标题/表格/选项/脚注）、角色、原文、检索归一化文本。
- `segments_fts`：FTS5 外部内容表，trigram 分词。
- `meta`：schema 版本和分词器信息。

## 项目结构

```text
src/textlens/
  cli.py            # argparse 命令与输出
  index.py          # SQLite schema、增量索引、FTS5
  search.py         # FTS + LIKE 回退、过滤、上下文
  analysis.py       # 统计、分词、文本/Markdown 报告
  export.py         # JSONL / TXT / Markdown 导出
  encoding.py       # 编码检测
  text.py           # 清洗、语言识别、对话/角色启发式
  parsers/
    docx.py         # 标准库 OOXML
    engines.py      # KiriKiri / NScripter / Ren'Py
    plain.py        # 文本、字幕、JSON、CSV、TJS
    __init__.py     # 注册表与文件发现
  archives/
    ypf.py          # YU-RIS YPF 解包 + YSTB V5 文本提取
    __init__.py     # 归档识别与错误提示
tests/              # unittest，48 个用例
examples/demo/      # 可直接索引的演示语料
```

## 扩展一个新格式

1. 在 `src/textlens/parsers/` 里写一个函数，返回 `ParsedDocument`（参考 `engines.py`）。
2. 用 `finalize(...)` 统一清洗、语言识别和标题回退。
3. 在 `src/textlens/parsers/__init__.py` 的 `PARSERS` 里注册扩展名。
4. 在 `tests/test_parsers.py` 添加一个最小样例。

示例骨架：

```python
from pathlib import Path
from ..encoding import read_text_file
from ..models import Segment
from .base import finalize

def parse_my_format(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    segments = []
    for line_no, line in enumerate(decoded.text.splitlines(), 1):
        if line.strip():
            segments.append(Segment(line_no=line_no, text=line.strip()))
    return finalize(path, parser="myformat", encoding=decoded.encoding, segments=segments)
```

## 开发与测试

```powershell
# 不安装包直接跑测试
.\run_tests.ps1

# 或手动
$env:PYTHONPATH = "src"
python -m unittest discover -v
```

核心功能不依赖第三方包；`jieba` 只用于中文词频分析，缺失时会退化为正则片段统计。FTS5 在 Python 3.10+ 的官方 Windows 构建中通常已启用；若 SQLite 不含 FTS5，检索会自动退化为 `LIKE`，功能仍可用但大语料会变慢。

## 已知限制

- 旧版二进制 `.doc` 不直接解析；请转 `.docx`。
- TJS 是脚本语言，只能提取字符串字面量；加密/编译后的游戏资源需要先解包或导出文本。
- `.ypf` 已支持解包；`.arc` / `.cpz` / `.dat` / `.xp3` / `.nsa` / `.rpa` 等归档包的解包功能待开发。
- 角色名依赖启发式规则。若脚本把「角色名」和「台词」拆成两条不同命令，可能需要为该引擎补一个专用解析器。
- 不分析图片、语音、字体或游戏资源文件。
- Word 只读取正文、表格、脚注和标题；页眉页脚、批注、图片内文字不参与索引。
- 高频词是统计信号，不等于命名实体或剧情摘要。

## License

MIT
