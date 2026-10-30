"""Command-line interface for TextLens."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from .analysis import analyze, render_markdown, render_text
from .errors import TextLensError
from .export import extract
from .index import DEFAULT_DB, Index, build_index
from .parsers import format_support_table
from .search import context, search
from .text import snippet

_KIND_LABELS = {
    "dialogue": "台词",
    "narration": "旁白",
    "heading": "标题",
    "table": "表格",
    "choice": "选项",
    "footnote": "脚注",
}
_KINDS = tuple(_KIND_LABELS)

_EPILOG = """\
示例：
  textlens index "D:\\Games\\MyGame" "D:\\Docs"
  textlens search "约定" --ext .ks,.txt
  textlens context "约定的地方" --before 2 --after 2
  textlens stats --top 30 --format md --output report.md
  textlens extract "D:\\Games\\MyGame" -o corpus.jsonl --dialogue-only

支持格式：
""" + format_support_table()


def _configure_streams() -> None:
    """Use UTF-8 for redirected output; keep the Windows console untouched."""
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _split_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _open_index(args: argparse.Namespace, *, must_exist: bool) -> Index:
    path = Path(args.db).expanduser()
    if must_exist and not path.exists():
        raise TextLensError(
            f"索引不存在：{path}\n先运行：textlens index <目录或文件> --db \"{path}\""
        )
    return Index(path)


def _display_path(value: str) -> str:
    try:
        return str(Path(value).resolve().relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return value


def _print_errors(
    errors: list[tuple[str, str]],
    limit: int = 10,
    *,
    stream: TextIO | None = None,
) -> None:
    if not errors:
        return
    output = stream or sys.stdout
    print("错误：", file=output)
    for path, message in errors[:limit]:
        print(f"  - {path}", file=output)
        print(f"    {message}", file=output)
    if len(errors) > limit:
        print(f"  … 另有 {len(errors) - limit} 个错误", file=output)


def _query_text(parts: list[str]) -> str:
    return " ".join(parts).strip()


def _search_terms(query: str, phrase: bool) -> list[str]:
    return [query] if phrase else query.split()


def _print_hits(hits: list, query: str, *, phrase: bool) -> None:
    if not hits:
        print(f"没有找到：{query}")
        return
    print(f"找到 {len(hits)} 条结果")
    terms = _search_terms(query, phrase)
    for index, hit in enumerate(hits, start=1):
        label = _KIND_LABELS.get(hit.kind, hit.kind)
        speaker = f"  {hit.speaker}" if hit.speaker else ""
        print(f"\n[{index}] {_display_path(hit.path)}:{hit.line_no}  ({label}){speaker}")
        print(f"    {snippet(hit.text, terms)}")


def _context_dict(blocks) -> list[dict]:
    result = []
    for block in blocks:
        hit = block.hit
        rows = []
        for row in block.segments:
            rows.append(
                {
                    "line": int(row["line_no"]),
                    "kind": row["kind"],
                    "speaker": row["speaker"],
                    "text": row["text"],
                    "is_hit": int(row["line_no"]) == hit.line_no and row["text"] == hit.text,
                }
            )
        result.append(
            {
                "path": hit.path,
                "title": hit.title,
                "line": hit.line_no,
                "speaker": hit.speaker,
                "text": hit.text,
                "segments": rows,
            }
        )
    return result


def _print_context(blocks) -> None:
    if not blocks:
        print("没有找到可展示的上下文。")
        return
    for index, block in enumerate(blocks, start=1):
        hit = block.hit
        print(f"\n[{index}] {_display_path(hit.path)}:{hit.line_no}")
        for row in block.segments:
            marker = ">" if int(row["line_no"]) == hit.line_no and row["text"] == hit.text else " "
            speaker = f" {row['speaker']}：" if row["speaker"] else " "
            print(f" {marker} {int(row['line_no']):>6} {speaker}{row['text']}")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_index(args: argparse.Namespace) -> int:
    report = build_index(
        args.db,
        args.paths,
        includes=_split_values(args.include) or None,
        excludes=_split_values(args.exclude),
        force=args.force,
        rebuild=args.rebuild,
        prune=args.prune,
    )
    if args.json:
        payload = dataclasses.asdict(report)
        payload["duration"] = round(report.duration, 3)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"索引完成：扫描 {report.seen} 个文件，更新 {report.indexed}，"
            f"未变化 {report.skipped}，失败 {report.failed}"
        )
        print(
            f"新增/更新片段 {report.segments:,}，字符 {report.chars:,}，"
            f"用时 {report.duration:.2f}s"
        )
        print(f"数据库：{Path(args.db).expanduser().resolve()}")
        if report.removed:
            print(f"已清理失效记录：{report.removed}")
        if report.skipped_archives:
            print(
                f"跳过 {len(report.skipped_archives)} 个游戏归档"
                "（需要先用 GARbro / krkrextract / nsaextract 等解包）："
            )
            for path, label in report.skipped_archives[:8]:
                print(f"  - {label}  {path}")
            if len(report.skipped_archives) > 8:
                print(f"  … 另有 {len(report.skipped_archives) - 8} 个")
        _print_errors(report.errors)
        if report.seen == 0:
            print("没有发现可索引文件；检查路径，或用 --include 指定扩展名。")
    if report.failed and not report.indexed:
        return 1
    if report.skipped_archives and not report.indexed and not report.failed:
        return 1
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    query = _query_text(args.query)
    with _open_index(args, must_exist=True) as index:
        hits = search(
            index,
            query,
            limit=args.limit,
            offset=args.offset,
            extensions=_split_values(args.ext) or None,
            speaker=args.speaker,
            file_pattern=args.file,
            kind=args.kind,
            any_term=args.any,
            phrase=args.phrase,
            use_regex=args.regex,
        )
    if args.json:
        print(
            json.dumps(
                {
                    "query": query,
                    "count": len(hits),
                    "results": [
                        {
                            **hit.as_dict(),
                            "snippet": snippet(
                                hit.text,
                                _search_terms(query, args.phrase),
                                regex=query if args.regex else None,
                            ),
                        }
                        for hit in hits
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_hits(hits, query, phrase=args.phrase)
    return 0 if hits else 1


def cmd_context(args: argparse.Namespace) -> int:
    query = _query_text(args.query)
    with _open_index(args, must_exist=True) as index:
        blocks = context(
            index,
            query,
            before=args.before,
            after=args.after,
            limit=args.limit,
            extensions=_split_values(args.ext) or None,
            speaker=args.speaker,
            file_pattern=args.file,
            kind=args.kind,
            any_term=args.any,
            phrase=args.phrase,
        )
    if args.json:
        print(
            json.dumps(
                {"query": query, "blocks": _context_dict(blocks)},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_context(blocks)
    return 0 if blocks else 1


def cmd_stats(args: argparse.Namespace) -> int:
    stopwords: set[str] = set()
    if args.stopwords:
        stopword_path = Path(args.stopwords).expanduser()
        if not stopword_path.is_file():
            raise TextLensError(f"停用词文件不存在：{stopword_path}")
        for line in stopword_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                stopwords.add(line.casefold() if line.isascii() else line)
    with _open_index(args, must_exist=True) as index:
        stats = analyze(
            index,
            top=args.top,
            file_pattern=args.file,
            extensions=_split_values(args.ext) or None,
            extra_stopwords=stopwords,
            exclude_speakers=not args.include_speakers,
        )

    chosen = args.format
    if not chosen and args.output and args.output != "-":
        suffix = Path(args.output).suffix.casefold()
        chosen = "md" if suffix in {".md", ".markdown"} else "json" if suffix == ".json" else "text"
    chosen = chosen or "text"

    if chosen == "json":
        rendered = json.dumps(stats.as_dict(), ensure_ascii=False, indent=2)
    elif chosen == "md":
        rendered = render_markdown(stats)
    else:
        rendered = render_text(stats)

    if args.output and args.output != "-":
        target = Path(args.output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(f"报告已写入：{target.resolve()}")
    else:
        print(rendered)
    return 0


def cmd_files(args: argparse.Namespace) -> int:
    with _open_index(args, must_exist=True) as index:
        rows = index.documents()
    payload = [
        {
            "id": int(row["id"]),
            "path": row["path"],
            "title": row["title"],
            "ext": row["ext"],
            "language": row["language"],
            "segments": int(row["segment_count"]),
            "chars": int(row["char_count"]),
            "dialogues": int(row["dialogue_count"]),
        }
        for row in rows
    ]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not payload:
            print("索引为空。先运行：textlens index <目录或文件>")
            return 1
        print(f"共 {len(payload)} 个文件")
        for item in payload:
            print(
                f"  [{item['id']:>4}] {item['ext']:<7} {item['language'] or '?':<3} "
                f"{item['chars']:>9,} 字  {item['path']}"
            )
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    report = extract(
        args.paths,
        args.output,
        fmt=args.format,
        includes=_split_values(args.include) or None,
        excludes=_split_values(args.exclude),
        dialogue_only=args.dialogue_only,
        force=args.force,
    )
    stream = sys.stderr if str(args.output) == "-" else sys.stdout
    print(
        f"导出完成：{report.files} 个文件，{report.segments:,} 个片段，"
        f"{report.chars:,} 字符",
        file=stream,
    )
    if report.output != "-":
        print(f"输出：{Path(report.output).expanduser().resolve()}", file=stream)
    if report.files == 0:
        print("没有发现可导出文本；游戏归档需先解包，检查路径或 --include。", file=stream)
    _print_errors(report.errors, stream=stream)
    if report.files == 0:
        return 1
    return 1 if report.errors and report.files == len(report.errors) else 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="textlens",
        description="TextLens：把 Word、galgame 脚本和文本语料变成可检索、可统计的本地知识库。",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"TextLens {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="命令")

    index = subparsers.add_parser("index", help="建立或更新本地索引", description="建立/更新 SQLite FTS5 索引。")
    index.add_argument("paths", nargs="+", help="文件或目录，可传多个")
    index.add_argument("--db", default=str(DEFAULT_DB), help=f"索引数据库路径（默认 {DEFAULT_DB}）")
    index.add_argument("--include", action="append", metavar="EXT", help="只处理这些扩展名，逗号分隔，如 .docx,.txt")
    index.add_argument("--exclude", action="append", metavar="GLOB", help="排除匹配的文件/目录，可重复")
    index.add_argument("--force", action="store_true", help="忽略 mtime/size 缓存，强制重解析")
    index.add_argument("--rebuild", action="store_true", help="清空索引后重建")
    index.add_argument("--prune", action="store_true", help="删除源文件已不存在的索引记录")
    index.add_argument("--json", action="store_true", help="以 JSON 输出摘要")
    index.set_defaults(func=cmd_index)

    search_parser = subparsers.add_parser(
        "search", help="全文检索", description="全文检索；中文短语按子串匹配，短查询自动回退 LIKE。"
    )
    search_parser.add_argument("query", nargs="+", help="关键词，多个词默认 AND")
    search_parser.add_argument("--db", default=str(DEFAULT_DB), help="索引数据库路径")
    search_parser.add_argument("--limit", type=int, default=20, help="返回条数（默认 20）")
    search_parser.add_argument("--offset", type=int, default=0, help="跳过前 N 条")
    search_parser.add_argument("--ext", action="append", metavar="EXT", help="按扩展名过滤，逗号分隔")
    search_parser.add_argument("--speaker", help="按角色名过滤（包含匹配）")
    search_parser.add_argument("--file", help="按文件路径/文件名过滤（包含匹配）")
    search_parser.add_argument("--kind", choices=_KINDS, help="按片段类型过滤")
    search_parser.add_argument("--any", action="store_true", help="多个关键词改为 OR")
    search_parser.add_argument("--phrase", action="store_true", help="把整个查询当作一个短语")
    search_parser.add_argument("--regex", action="store_true", help="把查询当作正则表达式（扫描较慢）")
    search_parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    search_parser.set_defaults(func=cmd_search)

    context_parser = subparsers.add_parser(
        "context", help="查看命中位置的上下文", description="对前几条命中展示前后文。"
    )
    context_parser.add_argument("query", nargs="+", help="关键词")
    context_parser.add_argument("--db", default=str(DEFAULT_DB), help="索引数据库路径")
    context_parser.add_argument("--before", type=int, default=2, help="向前显示的片段数（默认 2）")
    context_parser.add_argument("--after", type=int, default=2, help="向后显示的片段数（默认 2）")
    context_parser.add_argument("--limit", type=int, default=5, help="展示的命中数（默认 5）")
    context_parser.add_argument("--ext", action="append", metavar="EXT", help="按扩展名过滤")
    context_parser.add_argument("--speaker", help="按角色名过滤")
    context_parser.add_argument("--file", help="按文件路径过滤")
    context_parser.add_argument("--kind", choices=_KINDS, help="按片段类型过滤")
    context_parser.add_argument("--any", action="store_true", help="多个关键词改为 OR")
    context_parser.add_argument("--phrase", action="store_true", help="把整个查询当作一个短语")
    context_parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    context_parser.set_defaults(func=cmd_context)

    stats = subparsers.add_parser("stats", help="统计语料与高频词", description="统计字数、角色台词和高频词。")
    stats.add_argument("--db", default=str(DEFAULT_DB), help="索引数据库路径")
    stats.add_argument("--top", type=int, default=25, help="高频词/角色数量（默认 25）")
    stats.add_argument("--file", help="只统计匹配的文件")
    stats.add_argument("--ext", action="append", metavar="EXT", help="只统计这些扩展名")
    stats.add_argument("--stopwords", help="额外停用词文件，一行一个")
    stats.add_argument("--include-speakers", action="store_true", help="高频词中包含角色名")
    stats.add_argument("--format", choices=("text", "md", "json"), help="输出格式；默认按扩展名推断")
    stats.add_argument("--output", "-o", help="写入文件；- 表示标准输出")
    stats.set_defaults(func=cmd_stats)

    files = subparsers.add_parser("files", help="列出已索引文件", description="查看索引中的文件清单。")
    files.add_argument("--db", default=str(DEFAULT_DB), help="索引数据库路径")
    files.add_argument("--json", action="store_true", help="以 JSON 输出")
    files.set_defaults(func=cmd_files)

    extract_parser = subparsers.add_parser("extract", help="导出合并语料", description="把多个文件解析后导出为 JSONL/TXT/Markdown。")
    extract_parser.add_argument("paths", nargs="+", help="文件或目录")
    extract_parser.add_argument("--output", "-o", required=True, help="输出文件；- 表示标准输出")
    extract_parser.add_argument("--format", choices=("jsonl", "txt", "md"), help="输出格式；默认按扩展名推断")
    extract_parser.add_argument("--include", action="append", metavar="EXT", help="只处理指定扩展名")
    extract_parser.add_argument("--exclude", action="append", metavar="GLOB", help="排除匹配路径")
    extract_parser.add_argument("--dialogue-only", action="store_true", help="只导出有角色标记的台词")
    extract_parser.add_argument("--force", action="store_true", help="覆盖已存在的输出文件")
    extract_parser.set_defaults(func=cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TextLensError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"数据库错误：{exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
