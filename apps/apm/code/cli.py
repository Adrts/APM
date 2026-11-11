"""Command line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__, buckets, cache, install, installed, manifest, query, uninstall
from .errors import ApmError
from .known_buckets import KNOWN_BUCKETS
from .output import console
from .util import display_width, fit_text, pad_text, terminal_columns

# 预定义短别名：仅命令级，第一个参数精确匹配时替换为正式命令名。
SHORT_ALIASES = {
    "in": "install",
    "rm": "uninstall",
    "remove": "uninstall",
    "se": "search",
    "up": "update",
    "ls": "list",
    "?": "help",
}


def _expand_argv(argv: list[str]) -> list[str]:
    """在 argparse 解析前展开预定义短别名。

    规则 1：第一个参数若精确等于别名名，替换为正式命令名，其余参数原样保留。
    规则 2：其余参数中的独立 token ``*`` 一律翻译为 ``--all``。
    """
    if not argv:
        return argv
    head = SHORT_ALIASES.get(argv[0], argv[0])
    rest = ["--all" if token == "*" else token for token in argv[1:]]
    return [head, *rest]


def _reject_all_and_positional(
    parser: argparse.ArgumentParser,
    all_flag: bool,
    positional: object,
    positional_name: str,
) -> None:
    """--all 与位置参数同时指定时报参数错误（exit 2）。"""
    if all_flag and positional:
        parser.error(f"--all 不能与 {positional_name} 同时指定")


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="apm",
        description="APM - Adrts package manager（兼容 Scoop bucket 的 Windows 包管理器）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "预定义短别名（不支持用户自定义）：\n"
            "  in = install    rm = remove = uninstall    se = search\n"
            "  up = update    ls = list    ? = help\n"
            "参数级别名：除命令名外，独立参数 * 一律等价于 --all"
        ),
    )
    parser.add_argument("--version", action="version", version=f"apm {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    parsers: dict[str, argparse.ArgumentParser] = {}

    def add(name: str, **kwargs) -> argparse.ArgumentParser:
        child = sub.add_parser(name, **kwargs)
        parsers[name] = child
        return child

    p = add("install", help="安装应用", description="安装一个或多个应用及其依赖")
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("download", help="只下载并校验", description="执行安装流程的阶段一，不修改已安装内容")
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("uninstall", help="卸载应用", description="卸载一个或多个应用")
    p.add_argument("apps", nargs="+", metavar="app")
    p.add_argument("--purge", action="store_true", help="同时删除 persist 数据")

    p = add("update", help="更新 bucket 或应用", description="不带参数时刷新 bucket 索引")
    p.add_argument("apps", nargs="*", metavar="app", help="应用名；--all 表示全部")
    p.add_argument(
        "--all",
        action="store_true",
        help="先刷新全部 bucket 索引，再更新全部应用，不能与 app 同时指定",
    )

    add("status", help="检查可更新版本")

    p = add("list", help="列出已安装应用")
    p.add_argument("query", nargs="?", metavar="query", help="名称过滤；--all 表示全部")
    p.add_argument(
        "--all", action="store_true", help="列出全部已安装应用，不能与 query 同时指定"
    )

    p = add("search", help="搜索可用应用")
    p.add_argument("query", metavar="query")

    p = add("info", help="显示应用信息")
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("hold", help="锁定应用，更新时跳过", description="锁定一个或多个已安装应用，update/status 跳过它们")
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("unhold", help="解除应用锁定", description="解除一个或多个应用的锁定状态")
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("bucket", help="管理软件源")
    bucket_sub = p.add_subparsers(dest="bucket_command", metavar="<action>")
    b = bucket_sub.add_parser("add", help="添加 bucket")
    b.add_argument("name")
    b.add_argument("repo", nargs="?")
    bucket_sub.add_parser("list", help="列出 bucket")
    bucket_sub.add_parser("known", help="列出已知 bucket")
    b = bucket_sub.add_parser("rm", help="删除 bucket")
    b.add_argument("name")

    p = add("cache", help="管理下载缓存")
    cache_sub = p.add_subparsers(dest="cache_command", metavar="<action>")
    cache_sub.add_parser("show", help="查看缓存")
    b = cache_sub.add_parser("rm", help="删除缓存")
    b.add_argument("target", nargs="?", help="应用名；--all 表示全部")
    b.add_argument("--all", action="store_true", help="删除全部缓存，不能与 target 同时指定")

    p = add("cleanup", help="清理残留版本与临时文件")
    p.add_argument("app", nargs="?", help="应用名；--all 表示全部")
    p.add_argument(
        "--all", action="store_true", help="清理全部应用与残留，不能与 app 同时指定"
    )

    p = add("help", help="显示帮助")
    p.add_argument("topic", nargs="?")

    return parser, parsers


def main(argv: list[str] | None = None) -> int:
    parser, parsers = build_parser()
    raw = list(sys.argv[1:]) if argv is None else list(argv)
    args = parser.parse_args(_expand_argv(raw))
    if not args.command:
        parser.print_help()
        return 0
    try:
        return dispatch(args, parser, parsers)
    except ApmError as exc:
        console.error(str(exc))
        return 1
    except KeyboardInterrupt:
        console.error("已取消")
        return 130


def dispatch(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    parsers: dict[str, argparse.ArgumentParser],
) -> int:
    command = args.command

    if command == "install":
        return install.install_apps(args.apps)
    if command == "download":
        return install.download_apps(args.apps)
    if command == "uninstall":
        return uninstall.uninstall_apps(args.apps, purge=args.purge)
    if command == "update":
        _reject_all_and_positional(parser, args.all, args.apps, "app")
        return _update(args.apps, all_apps=args.all)
    if command == "status":
        return query.status()
    if command == "list":
        _reject_all_and_positional(parser, args.all, args.query, "query")
        return query.list_installed(None if args.all else args.query)
    if command == "search":
        return query.search(args.query)
    if command == "info":
        return query.info(args.apps)
    if command == "hold":
        return _set_hold(args.apps, True)
    if command == "unhold":
        return _set_hold(args.apps, False)
    if command == "bucket":
        return _bucket(args)
    if command == "cache":
        return _cache(args, parser)
    if command == "cleanup":
        _reject_all_and_positional(parser, args.all, args.app, "app")
        return query.cleanup(None if args.all else args.app)
    if command == "help":
        if args.topic and args.topic in parsers:
            parsers[args.topic].print_help()
        elif args.topic:
            console.error(f"未知命令：{args.topic}")
            return 1
        else:
            parser.print_help()
        return 0

    parser.print_help()
    return 1


def _set_hold(apps: list[str], hold: bool) -> int:
    verb = "已锁定" if hold else "已解除锁定"
    for raw in apps:
        _, app, _ = manifest.parse_app_spec(raw)
        changed = installed.set_hold(app, hold)
        if changed:
            console.success(f"{verb} {app}")
        else:
            state = "已处于锁定状态" if hold else "未处于锁定状态"
            console.info(f"{app} {state}")
    return 0


def _update(targets: list[str], all_apps: bool = False) -> int:
    if not all_apps and not targets:
        failures = buckets.update_buckets()
        if failures:
            console.failure_list(failures)
            return 1
        console.success("已更新全部 bucket 索引")
        return 0
    if all_apps:
        bucket_failures = buckets.update_buckets()
        if bucket_failures:
            console.warn("部分 bucket 更新失败，继续使用现有索引")
        result = install.update_apps([])
        return 1 if bucket_failures else result
    return install.update_apps(targets)


def _bucket(args: argparse.Namespace) -> int:
    action = getattr(args, "bucket_command", None)
    if action == "add":
        buckets.add_bucket(args.name, args.repo)
        return 0
    if action == "rm":
        buckets.remove_bucket(args.name)
        return 0
    if action == "known":
        name_width = max(8, max(display_width(name) for name in KNOWN_BUCKETS))
        console.heading(f"{pad_text('Bucket', name_width)}  仓库")
        for name, repo in sorted(KNOWN_BUCKETS.items()):
            console.plain(f"{pad_text(name, name_width)}  {repo}")
        console.plain()
        console.summary(f"共 {len(KNOWN_BUCKETS)} 个已知 bucket")
        return 0
    if action in (None, "list"):
        names = buckets.bucket_names()
        if not names:
            console.info("尚未添加任何 bucket，运行 apm bucket add main")
            return 0
        width = terminal_columns()
        name_width = max(8, max(display_width(name) for name in names))
        console.heading(
            f"{pad_text('Bucket', name_width)}  {pad_text('清单', 6, 'right')}  来源"
        )
        for name in names:
            prefix = (
                f"{pad_text(name, name_width)}  "
                f"{buckets.manifest_count(name):>6}  "
            )
            console.plain(
                prefix + fit_text(buckets.bucket_source(name), width - display_width(prefix))
            )
        console.plain()
        console.summary(f"共 {len(names)} 个 bucket")
        return 0
    console.error(f"未知的 bucket 子命令：{action}")
    return 1


def _cache(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    action = getattr(args, "cache_command", None)
    if action in (None, "show"):
        cache.show()
        return 0
    if action == "rm":
        _reject_all_and_positional(parser, args.all, args.target, "target")
        if not args.all and not args.target:
            console.error("请指定应用名或使用 --all")
            return 1
        cache.remove(None if args.all else args.target)
        return 0
    console.error(f"未知的 cache 子命令：{action}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
