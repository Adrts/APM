"""Command line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__, buckets, cache, config, install, installed, manifest
from . import messages as msg, query, uninstall
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


def _help_theme() -> dict[str, str]:
    """读取 argparse 当前主题色；不可着色或旧版 Python 时返回空映射。"""
    if not console.color:
        return {}
    try:
        from _colorize import get_theme
    except ImportError:
        return {}
    theme = get_theme(force_color=True).argparse
    return {
        "heading": theme.heading,
        "alias": theme.action,
        "target": theme.summary_action,
        "label": theme.summary_label,
        "option": theme.summary_long_option,
        "reset": theme.reset,
    }


def _print_help_notes() -> None:
    """输出根帮助底部的别名与常用参数说明，配色与 argparse 段落一致。"""
    theme = _help_theme()

    def span(kind: str, text: str) -> str:
        code = theme.get(kind, "")
        return f"{code}{text}{theme['reset']}" if code else text

    console.plain()
    console.plain(span("heading", msg.HELP_ALIAS_TITLE))
    console.plain(
        msg.HELP_ALIAS_LINE_1.format(
            in_=span("alias", "in"),
            install=span("target", "install"),
            rm=span("alias", "rm"),
            remove=span("alias", "remove"),
            uninstall=span("target", "uninstall"),
            se=span("alias", "se"),
            search=span("target", "search"),
        )
    )
    console.plain(
        msg.HELP_ALIAS_LINE_2.format(
            up=span("alias", "up"),
            update=span("target", "update"),
            ls=span("alias", "ls"),
            list=span("target", "list"),
            question=span("alias", "?"),
            help_cmd=span("target", "help"),
        )
    )
    console.plain()
    console.plain(span("heading", msg.HELP_PARAM_ALIAS_TITLE))
    console.plain(
        msg.HELP_PARAM_ALIAS_LINE.format(
            star=span("label", "*"),
            all_flag=span("option", "--all"),
        )
    )
    console.plain()
    console.plain(span("heading", msg.HELP_OPTIONS_TITLE))
    console.plain(span("label", msg.HELP_OPTIONS_LINE))
    console.plain(span("label", msg.HELP_OPTIONS_LINE_2))
    console.plain()


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


class ApmHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """使用中文「用法：」前缀的帮助格式化器。"""

    def add_usage(self, usage, actions, groups, prefix=None):
        if prefix is None:
            prefix = msg.HELP_USAGE_PREFIX
        super().add_usage(usage, actions, groups, prefix)


class ApmArgumentParser(argparse.ArgumentParser):
    """把 argparse 自动生成的错误与帮助标点本地化。"""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        text = msg.argparse_error(message)
        self.exit(2, f"{self.prog}: {msg.ERR_ARG_PREFIX}{text}\n")

    def print_help(self, file=None) -> None:
        text = self.format_help()
        for title in (msg.HELP_SECTION_POSITIONAL, msg.HELP_SECTION_OPTIONS):
            text = text.replace(f"{title}:", f"{title}：")
        print(text, end="", file=file or sys.stdout)


def _configure_parser(parser: argparse.ArgumentParser) -> None:
    parser._positionals.title = msg.HELP_SECTION_POSITIONAL
    parser._optionals.title = msg.HELP_SECTION_OPTIONS
    parser.add_argument("-h", "--help", action="help", help=msg.HELP_OPTION_HELP)


def _add_parser(
    subparsers: argparse._SubParsersAction,
    name: str,
    **kwargs,
) -> argparse.ArgumentParser:
    child = subparsers.add_parser(
        name,
        formatter_class=ApmHelpFormatter,
        add_help=False,
        **kwargs,
    )
    _configure_parser(child)
    return child


def _reject_all_and_positional(
    parser: argparse.ArgumentParser,
    all_flag: bool,
    positional: object,
    positional_name: str,
) -> None:
    """--all 与位置参数同时指定时报参数错误（exit 2）。"""
    if all_flag and positional:
        parser.error(msg.HELP_ARG_ALL_CONFLICT.format(name=positional_name))


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = ApmArgumentParser(
        prog="apm",
        description=msg.HELP_ROOT_DESC,
        formatter_class=ApmHelpFormatter,
        add_help=False,
    )
    _configure_parser(parser)
    parser.add_argument(
        "--version",
        action="version",
        version=msg.HELP_VERSION_TEXT.format(version=__version__),
        help=msg.HELP_OPTION_VERSION,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    parsers: dict[str, argparse.ArgumentParser] = {}

    def add(name: str, **kwargs) -> argparse.ArgumentParser:
        child = _add_parser(sub, name, **kwargs)
        parsers[name] = child
        return child

    p = add(
        "install", help=msg.HELP_INSTALL, description=msg.HELP_INSTALL_DESC
    )
    p.add_argument("apps", nargs="+", metavar="app")
    p.add_argument("--verbose", "-v", action="store_true", help=msg.HELP_INSTALL_VERBOSE)
    p.add_argument("--yes", "-y", action="store_true", help=msg.HELP_INSTALL_YES)

    p = add(
        "download", help=msg.HELP_DOWNLOAD, description=msg.HELP_DOWNLOAD_DESC
    )
    p.add_argument("apps", nargs="+", metavar="app")

    p = add(
        "uninstall", help=msg.HELP_UNINSTALL, description=msg.HELP_UNINSTALL_DESC
    )
    p.add_argument("apps", nargs="*", metavar="app")
    p.add_argument("--purge", action="store_true", help=msg.HELP_UNINSTALL_PURGE)
    p.add_argument("--all", action="store_true", help=msg.HELP_UNINSTALL_ALL)
    p.add_argument("--verbose", "-v", action="store_true", help=msg.HELP_UNINSTALL_VERBOSE)
    p.add_argument("--yes", "-y", action="store_true", help=msg.HELP_UNINSTALL_YES)

    p = add("update", help=msg.HELP_UPDATE, description=msg.HELP_UPDATE_DESC)
    p.add_argument("apps", nargs="*", metavar="app", help=msg.HELP_UPDATE_APPS)
    p.add_argument("--all", action="store_true", help=msg.HELP_UPDATE_ALL)
    p.add_argument("--verbose", "-v", action="store_true", help=msg.HELP_UPDATE_VERBOSE)
    p.add_argument("--yes", "-y", action="store_true", help=msg.HELP_UPDATE_YES)

    add("status", help=msg.HELP_STATUS)

    p = add("list", help=msg.HELP_LIST)
    p.add_argument("query", nargs="?", metavar="query", help=msg.HELP_LIST_QUERY)
    p.add_argument("--all", action="store_true", help=msg.HELP_LIST_ALL)

    p = add("search", help=msg.HELP_SEARCH)
    p.add_argument("query", metavar="query")

    p = add("info", help=msg.HELP_INFO)
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("hold", help=msg.HELP_HOLD, description=msg.HELP_HOLD_DESC)
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("unhold", help=msg.HELP_UNHOLD, description=msg.HELP_UNHOLD_DESC)
    p.add_argument("apps", nargs="+", metavar="app")

    p = add("bucket", help=msg.HELP_BUCKET)
    bucket_sub = p.add_subparsers(dest="bucket_command", metavar="<action>")
    b = _add_parser(bucket_sub, "add", help=msg.HELP_BUCKET_ADD)
    b.add_argument("name")
    b.add_argument("repo", nargs="?")
    _add_parser(bucket_sub, "list", help=msg.HELP_BUCKET_LIST)
    _add_parser(bucket_sub, "known", help=msg.HELP_BUCKET_KNOWN)
    b = _add_parser(bucket_sub, "rm", help=msg.HELP_BUCKET_RM)
    b.add_argument("name")

    p = add("cache", help=msg.HELP_CACHE)
    cache_sub = p.add_subparsers(dest="cache_command", metavar="<action>")
    _add_parser(cache_sub, "show", help=msg.HELP_CACHE_SHOW)
    b = _add_parser(cache_sub, "rm", help=msg.HELP_CACHE_RM)
    b.add_argument("target", nargs="?", help=msg.HELP_CACHE_RM_TARGET)
    b.add_argument("--all", action="store_true", help=msg.HELP_CACHE_RM_ALL)

    p = add("cleanup", help=msg.HELP_CLEANUP)
    p.add_argument("app", nargs="?", help=msg.HELP_CLEANUP_APP)
    p.add_argument("--all", action="store_true", help=msg.HELP_CLEANUP_ALL)

    p = add("help", help=msg.HELP_HELP)
    p.add_argument("topic", nargs="?")

    return parser, parsers


def main(argv: list[str] | None = None) -> int:
    try:
        config.ensure_file()
    except ApmError as exc:
        console.error(str(exc))
        return 1
    parser, parsers = build_parser()
    raw = list(sys.argv[1:]) if argv is None else list(argv)
    args = parser.parse_args(_expand_argv(raw))
    if not args.command:
        parser.print_help()
        _print_help_notes()
        return 0
    try:
        return dispatch(args, parser, parsers)
    except ApmError as exc:
        console.error(str(exc))
        return 1
    except KeyboardInterrupt:
        console.error(msg.MSG_CANCELED)
        return 130


def dispatch(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    parsers: dict[str, argparse.ArgumentParser],
) -> int:
    command = args.command
    # 明细行开关：仅 install / uninstall / update 支持 --verbose。
    console.verbose = bool(getattr(args, "verbose", False))

    if command == "install":
        return install.install_apps(args.apps, assume_yes=args.yes)
    if command == "download":
        return install.download_apps(args.apps)
    if command == "uninstall":
        _reject_all_and_positional(parser, args.all, args.apps, "app")
        if args.all:
            apps = [item.app for item in installed.installed_apps()]
            if not apps:
                console.info(msg.MSG_NO_INSTALLED)
                return 0
        else:
            apps = args.apps
        return uninstall.uninstall_apps(apps, purge=args.purge, assume_yes=args.yes)
    if command == "update":
        _reject_all_and_positional(parser, args.all, args.apps, "app")
        return _update(args.apps, all_apps=args.all, assume_yes=args.yes)
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
            console.error(msg.MSG_UNKNOWN_COMMAND.format(topic=args.topic))
            return 1
        else:
            parser.print_help()
            _print_help_notes()
        return 0

    parser.print_help()
    _print_help_notes()
    return 1


def _set_hold(apps: list[str], hold: bool) -> int:
    for raw in apps:
        _, app, _ = manifest.parse_app_spec(raw)
        changed = installed.set_hold(app, hold)
        if changed:
            template = msg.MSG_HOLD_SET if hold else msg.MSG_UNHOLD_SET
            console.success(template.format(app=app))
        else:
            template = msg.MSG_HOLD_ALREADY if hold else msg.MSG_UNHOLD_ALREADY
            console.info(template.format(app=app))
    return 0


def _update(targets: list[str], all_apps: bool = False, assume_yes: bool = False) -> int:
    if not all_apps and not targets:
        failures = buckets.update_buckets()
        if failures:
            console.failure_list(failures)
            return 1
        console.success(msg.MSG_BUCKET_INDEX_UPDATED)
        return 0
    if all_apps:
        bucket_failures = buckets.update_buckets()
        if bucket_failures:
            console.warn(msg.MSG_BUCKET_PARTIAL_FAILED)
        result = install.update_apps([], assume_yes=assume_yes)
        return 1 if bucket_failures else result
    return install.update_apps(targets, assume_yes=assume_yes)


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
        console.heading(f"{pad_text(msg.TABLE_SOURCE, name_width)}  {msg.TABLE_REPO}")
        for name, repo in sorted(KNOWN_BUCKETS.items()):
            console.plain(f"{pad_text(name, name_width)}  {repo}")
        console.plain()
        console.summary(msg.SUMMARY_KNOWN_BUCKETS.format(count=len(KNOWN_BUCKETS)))
        return 0
    if action in (None, "list"):
        names = buckets.bucket_names()
        if not names:
            console.info(msg.MSG_NO_BUCKETS)
            return 0
        width = terminal_columns()
        name_width = max(8, max(display_width(name) for name in names))
        console.heading(
            f"{pad_text(msg.TABLE_SOURCE, name_width)}  "
            f"{pad_text(msg.TABLE_COUNT, 6, 'right')}  {msg.TABLE_ORIGIN}"
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
        console.summary(msg.SUMMARY_BUCKETS.format(count=len(names)))
        return 0
    console.error(msg.MSG_UNKNOWN_BUCKET_ACTION.format(action=action))
    return 1


def _cache(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    action = getattr(args, "cache_command", None)
    if action in (None, "show"):
        cache.show()
        return 0
    if action == "rm":
        _reject_all_and_positional(parser, args.all, args.target, "target")
        if not args.all and not args.target:
            console.error(msg.MSG_SPECIFY_APP_OR_ALL)
            return 1
        cache.remove(None if args.all else args.target)
        return 0
    console.error(msg.MSG_UNKNOWN_CACHE_ACTION.format(action=action))
    return 1


if __name__ == "__main__":
    sys.exit(main())
