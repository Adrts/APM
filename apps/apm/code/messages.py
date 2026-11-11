"""Centralized zh-CN user-facing text.

所有会在终端、帮助、错误信息或默认配置中展示给用户的文本都放在这里。
业务代码只使用常量并通过 ``str.format()`` 填充占位符；新增语言时替换本
模块即可，不需要改动业务逻辑。

命名约定：
- ``TABLE_*`` / ``FIELD_*``：表头与字段视图标签
- ``SUMMARY_*``：表格汇总行模板
- ``MSG_*``：常规状态与提示模板
- ``ERR_*``：异常与失败信息模板
- ``HELP_*``：argparse 帮助与参数描述
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 通用

PREFIX = "[apm]"
YES = "是"
NO = "否"
DASH = "-"
NOTES_TITLE = "说明"

# ---------------------------------------------------------------- 表格与字段

TABLE_APP = "应用"
TABLE_VERSION = "版本"
TABLE_SOURCE = "软件源"
TABLE_LOCK = "锁定"
TABLE_SIZE = "大小"
TABLE_FILE = "文件"
TABLE_INSTALLED = "已安装"
TABLE_LATEST = "最新"
TABLE_COUNT = "清单"
TABLE_REPO = "仓库"
TABLE_ORIGIN = "来源"

FIELD_APP = "应用"
FIELD_VERSION = "版本"
FIELD_BUCKET = "软件源"
FIELD_INSTALLED = "已安装"
FIELD_HOLD = "锁定"
FIELD_HOMEPAGE = "主页"
FIELD_LICENSE = "许可证"
FIELD_DESCRIPTION = "描述"
FIELD_DEPENDS = "依赖"
FIELD_BIN = "命令"
FIELD_SUGGEST = "建议"

SUMMARY_APPS = "共 {count} 个应用"
SUMMARY_RESULTS = "共 {count} 个结果"
SUMMARY_OUTDATED = "共 {count} 个应用可更新，运行 apm update <app> 或 apm update *"
SUMMARY_CACHE = "共 {count} 个缓存文件，合计 {size}"
SUMMARY_BUCKETS = "共 {count} 个软件源"
SUMMARY_KNOWN_BUCKETS = "共 {count} 个已知软件源"

# ---------------------------------------------------------------- 通用状态

MSG_CANCELED = "已取消"
MSG_FAILURE_HEADER = "以下项目失败："
ERR_GIT_FETCH = "git fetch 失败"
ERR_GIT_RESET = "git reset 失败"
ERR_VERIFY_FAILED = "校验失败"
MSG_UNKNOWN_COMMAND = "未知命令：{topic}"
MSG_UNKNOWN_BUCKET_ACTION = "未知的软件源子命令：{action}"
MSG_UNKNOWN_CACHE_ACTION = "未知的缓存子命令：{action}"
ERR_ITEM = "{name}：{error}"
MSG_SPECIFY_APP_OR_ALL = "请指定应用名或使用 --all"
MSG_NO_INSTALLED = "尚未安装任何应用"
MSG_NO_MATCH_INSTALLED = "没有匹配的已安装应用"
MSG_NO_MATCH_SEARCH = "没有匹配 '{query}' 的应用"
MSG_NOTHING_TO_PROCESS = "没有需要处理的应用"
MSG_NOTHING_TO_DOWNLOAD = "没有需要下载的文件"
MSG_NO_CACHE = "没有缓存文件"
MSG_NO_MATCH_CACHE = "没有匹配的缓存文件"
MSG_NO_BUCKETS = "尚未添加任何软件源，运行 apm bucket add main"
MSG_ALL_LATEST = "所有应用均已是最新版本"
MSG_INSTALLED_YES = "是（{version}）"

# ---------------------------------------------------------------- 别名帮助

HELP_ALIAS_TITLE = "预定义短别名（不支持用户自定义）："
HELP_ALIAS_LINE_1 = (
    "  {in_} = {install}    {rm} = {remove} = {uninstall}    {se} = {search}"
)
HELP_ALIAS_LINE_2 = "  {up} = {update}    {ls} = {list}    {question} = {help_cmd}"
HELP_PARAM_ALIAS_TITLE = "参数级别名："
HELP_PARAM_ALIAS_LINE = "  除命令名外，独立参数 {star} 一律等价于 {all_flag}"

# ---------------------------------------------------------------- argparse

HELP_USAGE_PREFIX = "用法："
HELP_SECTION_POSITIONAL = "位置参数"
HELP_SECTION_OPTIONS = "选项"
HELP_ROOT_DESC = "APM - Adrts 包管理器（兼容 Scoop 软件源的 Windows 包管理器）"
HELP_OPTION_HELP = "显示帮助并退出"
HELP_OPTION_VERSION = "显示版本号并退出"
HELP_VERSION_TEXT = "apm {version}"
ERR_ARG_PREFIX = "错误："

HELP_INSTALL = "安装应用"
HELP_INSTALL_DESC = "安装一个或多个应用及其依赖"
HELP_DOWNLOAD = "只下载并校验"
HELP_DOWNLOAD_DESC = "执行安装流程的阶段一，不修改已安装内容"
HELP_UNINSTALL = "卸载应用"
HELP_UNINSTALL_DESC = "卸载一个或多个应用"
HELP_UNINSTALL_PURGE = "同时删除持久化数据"
HELP_UPDATE = "更新软件源或应用"
HELP_UPDATE_DESC = "不带参数时刷新软件源索引"
HELP_UPDATE_APPS = "应用名；--all 表示全部"
HELP_UPDATE_ALL = "先刷新全部软件源索引，再更新全部应用，不能与 app 同时指定"
HELP_STATUS = "检查可更新版本"
HELP_LIST = "列出已安装应用"
HELP_LIST_QUERY = "名称过滤；--all 表示全部"
HELP_LIST_ALL = "列出全部已安装应用，不能与 query 同时指定"
HELP_SEARCH = "搜索可用应用"
HELP_INFO = "显示应用信息"
HELP_HOLD = "锁定应用，更新时跳过"
HELP_HOLD_DESC = "锁定一个或多个已安装应用，update/status 跳过它们"
HELP_UNHOLD = "解除应用锁定"
HELP_UNHOLD_DESC = "解除一个或多个应用的锁定状态"
HELP_BUCKET = "管理软件源"
HELP_BUCKET_ADD = "添加软件源"
HELP_BUCKET_LIST = "列出软件源"
HELP_BUCKET_KNOWN = "列出已知软件源"
HELP_BUCKET_RM = "删除软件源"
HELP_CACHE = "管理下载缓存"
HELP_CACHE_SHOW = "查看缓存"
HELP_CACHE_RM = "删除缓存"
HELP_CACHE_RM_TARGET = "应用名；--all 表示全部"
HELP_CACHE_RM_ALL = "删除全部缓存，不能与 target 同时指定"
HELP_CLEANUP = "清理残留版本与临时文件"
HELP_CLEANUP_APP = "应用名；--all 表示全部"
HELP_CLEANUP_ALL = "清理全部应用与残留，不能与 app 同时指定"
HELP_HELP = "显示帮助"
HELP_ARG_ALL_CONFLICT = "--all 不能与 {name} 同时指定"

# argparse 自动错误信息的中文映射（按顺序匹配）。
_ARGPARSE_ERRORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^the following arguments are required: (?P<names>.+)$"),
        "缺少必需参数：{names}",
    ),
    (
        re.compile(r"^unrecognized arguments: (?P<args>.+)$"),
        "无法识别的参数：{args}",
    ),
    (
        re.compile(r"^argument (?P<name>.+): expected one argument$"),
        "参数 {name} 需要一个值",
    ),
    (
        re.compile(r"^argument (?P<name>.+): expected at least one argument$"),
        "参数 {name} 至少需要一个值",
    ),
    (
        re.compile(r"^argument (?P<name>.+): expected at most one argument$"),
        "参数 {name} 最多只能有一个值",
    ),
    (
        re.compile(
            r"^argument (?P<name>.+): invalid choice: (?P<value>.+) "
            r"\(choose from (?P<choices>.+)\)$"
        ),
        "参数 {name} 的取值无效：{value}（可选：{choices}）",
    ),
    (
        re.compile(r"^argument (?P<name>.+): not allowed with argument (?P<other>.+)$"),
        "参数 {name} 不能与 {other} 同时使用",
    ),
    (
        re.compile(
            r"^ambiguous option: (?P<option>.+) could match (?P<matches>.+)$"
        ),
        "选项 {option} 有歧义，可匹配：{matches}",
    ),
)


def argparse_error(message: str) -> str:
    """把 argparse 生成的英文参数错误转换成中文；无法识别时原样返回。"""
    for pattern, template in _ARGPARSE_ERRORS:
        match = pattern.match(message)
        if match:
            return template.format(**match.groupdict())
    return message


# ---------------------------------------------------------------- 安装与更新

MSG_PLAN = "计划顺序：{apps}"
MSG_APP_LOCKED_SKIP = "{app} 已锁定，跳过"
MSG_APP_LOCKED_CHECK = "{app} 已锁定，跳过检查"
MSG_APP_INSTALLED_SKIP = "{app} {version} 已安装，跳过"
ERR_DEPENDENCY_CYCLE = "检测到循环依赖：{chain}"
ERR_DEPENDENCY_CONFLICT = (
    "依赖版本冲突：{app} 已按 {resolved} 解析，又要求 {version}"
)
MSG_APP_LATEST = "{app} 已是最新（{version}）"
MSG_MANIFEST_UNREADABLE = "无法读取 {app} 的清单，跳过：{error}"
MSG_UPDATE_APP = "更新 {app}：{old} -> {new}"
MSG_INSTALLED = "已安装 {app} {version}"
MSG_ALL_INSTALLED = "已全部安装完成"
MSG_INSTALL_FAILED = "安装 {app} 失败：{error}"
MSG_APM_RESERVED = "应用名 apm 为 APM 自身保留"
MSG_APP_NOT_INSTALLED = "应用未安装：{app}"
MSG_EXTRACT_TO_UNSUPPORTED = "{app} 使用了 extract_to 字段，首版暂不支持，已忽略"
MSG_EXTRACTING = "解压 {file}"
MSG_RUNNING_INSTALLER = "运行安装程序 {file}"
MSG_SWITCH_CURRENT = "切换当前版本 -> {version}"
MSG_CREATING_SHIM = "创建命令入口 {name}"
MSG_OVERWRITE_SHIM = "覆盖已存在的命令入口：{name}"
MSG_ADD_PATH = "加入用户 PATH：{entries}"
MSG_SET_ENV = "写入环境变量：{names}"
MSG_PERSISTING = "持久化 {source}"
MSG_REMOVE_SHIM = "移除命令入口 {name}"
MSG_OLD_VERSION_DELETE_FAILED = "旧版本目录删除失败（可能被占用）：{path}"
MSG_UNINSTALLED = "已卸载 {app}"
MSG_HOLD_SET = "已锁定 {app}"
MSG_UNHOLD_SET = "已解除锁定 {app}"
MSG_HOLD_ALREADY = "{app} 已处于锁定状态"
MSG_UNHOLD_ALREADY = "{app} 未处于锁定状态"
MSG_PERSIST_PURGED = "已删除持久化数据：{app}"
MSG_PERSIST_KEPT = "保留持久化数据：{path}"
MSG_CLEANED = "已清理：删除 {dirs} 个目录、{files} 个临时文件"
MSG_CLEAN_APM = "不能清理 APM 自身"
MSG_UNINSTALL_APM = "不能卸载 APM 自身"

# ---------------------------------------------------------------- 软件源

MSG_BUCKET_ADDING = "添加软件源 '{name}'：{repo}"
MSG_BUCKET_ADDED = "软件源 '{name}' 已添加（{count} 个清单）"
MSG_BUCKET_REMOVED = "软件源 '{name}' 已删除"
MSG_BUCKET_UPDATED = "软件源 '{name}' 已更新"
MSG_BUCKET_UPDATE_FAILED = "软件源 '{name}' 更新失败，保留旧索引"
MSG_BUCKET_INDEX_UPDATED = "已更新全部软件源索引"
MSG_BUCKET_PARTIAL_FAILED = "部分软件源更新失败，继续使用现有索引"
MSG_BUCKET_AMBIGUOUS = (
    "应用 '{app}' 存在于多个软件源（{names}），使用 '{chosen}'"
)
ERR_BUCKET_NAME_INVALID = "软件源名称无效：{name}"
ERR_BUCKET_EXISTS = "软件源 '{name}' 已存在"
ERR_BUCKET_NOT_FOUND = "软件源 '{name}' 不存在"
ERR_BUCKET_NOT_GIT = "软件源 '{name}' 不是 git 仓库，无法更新"
ERR_BUCKET_DELETE_INCOMPLETE = "软件源 '{name}' 删除不完整，请关闭占用后重试"
ERR_BUCKET_ADD_FAILED = "添加软件源失败：{name}\n{detail}"
ERR_BUCKET_UNKNOWN = "未知软件源：{name}，请显式提供仓库地址"
ERR_GIT_MISSING = "未检测到 git，请先安装 git 并加入 PATH"
ERR_APP_NOT_FOUND = "找不到应用 '{app}'，可运行 apm search {app} 查看"
ERR_APP_NOT_FOUND_IN_BUCKET = "软件源 '{bucket}' 中找不到应用 '{app}'"

# ---------------------------------------------------------------- 查询与缓存

MSG_CHECKS_FAILED = "无法检查 {app}：{error}"
MSG_INFO_NOT_FOUND = "找不到应用：{app}"
MSG_CACHE_REMOVED = "已删除 {count} 个缓存文件，释放 {size}"
MSG_DELETE_FAILED = "删除失败（可能被占用）：{path}"
MSG_CACHE_DELETE_FAILED = "删除失败（可能被占用）：{path}（{error}）"

# ---------------------------------------------------------------- 下载

MSG_DOWNLOAD = "下载 {file}"
MSG_DOWNLOAD_MIRROR = "下载 {file}（镜像）"
MSG_USE_CACHE = "使用缓存 {file}"
MSG_DOWNLOADED = "已下载 {file}"
MSG_DOWNLOAD_PROGRESS_LABEL = "下载 {app} {version}（{file}）"
MSG_CACHE_INVALID = "缓存校验失败，重新下载：{file}\n{detail}"
MSG_MIRROR_NOT_CONFIGURED = (
    "未配置 {provider} 镜像前缀，按原始地址下载"
    "（可在 {config} 中设置 download.mirror.{provider}）"
)
MSG_DOWNLOAD_STAGE_FAILED = "下载阶段失败，已终止安装，未修改已安装内容"
MSG_DOWNLOAD_VERIFIED = "下载与校验已完成"
ERR_HTTP = "HTTP 错误 {code}：{url}"
ERR_NETWORK = "网络错误：{reason}（{url}）"
ERR_WRITE_FAILED = "写入失败：{error}"
ERR_DOWNLOAD_EXIT = "下载失败（退出码 {code}）：{url}\n{detail}"
ERR_TLS_CERTIFICATE = (
    "下载失败：TLS 证书校验失败\n{detail}\n"
    "提示：wget 后端需要可用的 CA 证书，请确认 cacert 已安装且 wget.ini 指向 cacert.pem；"
    "也可改用 curl / python / aria2 后端"
)
ERR_CURL_MISSING = "未检测到 curl.exe"
ERR_WGET_MISSING = "未检测到 wget.exe"
ERR_ARIA2_MISSING = "未检测到 aria2c.exe"
ERR_BACKEND_UNKNOWN = "未知的下载后端：{backend}"

# ---------------------------------------------------------------- 校验

MSG_HASH_NOT_PROVIDED = "清单未提供哈希值：{file}\n实际 SHA-256：{actual}"
MSG_SEVEN_ZIP_WARNING = "7-Zip 警告，继续"
MSG_NOT_ARCHIVE = "文件不是压缩包，跳过完整性测试"
ERR_HASH_ALGORITHM = "不支持的哈希算法：{algorithm}"
ERR_HASH_VALUE = "无法识别的哈希值：{value}"
ERR_HASH_LENGTH = "哈希值长度与算法不匹配：{value}"
ERR_HASH_MISMATCH = (
    "哈希校验失败\n应用：{app}\n算法：{algorithm}\n期望：{expected}\n实际：{actual}"
)
ERR_HASH_MISMATCH_NO_ALGORITHM = (
    "哈希校验失败\n应用：{app}\n期望：{expected}\n实际：{actual}"
)
ERR_SEVEN_ZIP_MISSING_VERIFY = "未检测到 7z.exe，无法使用 7zip 校验方式"
ERR_SEVEN_ZIP_HASH = "7-Zip 不支持 {algorithm} 或无法计算哈希值"
ERR_SEVEN_ZIP_TEST = "7-Zip 测试失败（退出码 {code}）"

# ---------------------------------------------------------------- 解压

MSG_STDLIB_FALLBACK = "标准库解压失败，改用 7-Zip：{file}"
ERR_EXTRACT_ABSOLUTE = "拒绝绝对路径条目：{name}"
ERR_EXTRACT_TRAVERSAL = "拒绝越界路径条目：{name}"
ERR_EXTRACT_INVALID_CHAR = "路径包含非法字符：{name}"
ERR_EXTRACT_TRAILING = "路径以空格或点结尾：{name}"
ERR_EXTRACT_RESERVED = "路径包含 Windows 保留名：{name}"
ERR_EXTRACT_EMPTY = "空路径条目：{name}"
ERR_EXTRACT_OUTSIDE = "路径越界：{name}"
ERR_EXTRACT_SYMLINK_OUTSIDE = "拒绝越界符号链接：{name} -> {target}"
ERR_EXTRACT_SYMLINK_FAILED = "无法创建符号链接：{name}（{error}）"
ERR_EXTRACT_LINK_OUTSIDE = "拒绝越界链接：{name} -> {target}"
ERR_EXTRACT_HARDLINK_OUTSIDE = "拒绝越界硬链接：{name} -> {target}"
ERR_EXTRACT_ENTRY_UNREADABLE = "无法读取归档条目：{name}"
ERR_EXTRACT_HARDLINK_MISSING = "硬链接源不存在：{target}"
ERR_EXTRACT_UNSUPPORTED = "不支持的压缩格式：{name}"
ERR_EXTRACT_7Z = "7-Zip 解压失败：{name}（退出码 {code}）\n{detail}"
ERR_EXTRACT_MSI = "MSI 解包失败：{name}（退出码 {code}）\n{detail}"
ERR_EXTRACT_INNOUNP = "innounp 解包失败：{name}（退出码 {code}）\n{detail}"
ERR_EXTRACT_VOLUME = "分卷压缩包缺少分卷：{name}"
ERR_EXTRACT_DIR = "解压目录不存在：{dir}"

# ---------------------------------------------------------------- 清单

ERR_ARCH_UNSUPPORTED = "应用不支持当前架构（{arch}）"
ERR_MANIFEST_NO_URL = "清单中缺少 url 字段"
ERR_MANIFEST_SCHEME = "不支持的下载地址（仅支持 http/https）：{url}"
ERR_MANIFEST_HASH_URL = "哈希值对应的下载地址不在 url 列表中：{url}"
ERR_MANIFEST_HASH_COUNT = "下载地址与哈希值数量不匹配：{url}"
ERR_APP_NAME_EMPTY = "应用名为空"
ERR_VERSION_INVALID = "版本号无效：{version}"
ERR_APP_SPEC_INVALID = "应用标识无效：{spec}"
ERR_BUCKET_NAME_FIELD_INVALID = "软件源名称无效：{bucket}"
ERR_MANIFEST_NOT_OBJECT = "清单必须是 JSON 对象：{app}"
ERR_MANIFEST_VERSION_MISSING = "清单缺少 version 字段：{app}"
ERR_MANIFEST_VERSION_INVALID = "清单的 version 字段格式无效：{version}"
ERR_MANIFEST_HOMEPAGE_MISSING = "清单缺少 homepage 字段：{app}"
ERR_MANIFEST_LICENSE_MISSING = "清单缺少 license 字段：{app}"
ERR_MANIFEST_ARCH_INVALID = "清单的 architecture 字段格式无效：{app}"
ERR_MANIFEST_PARSE = "清单解析失败：{path}\n{error}"
ERR_MANIFEST_PINNED = (
    "软件源只提供当前版本 {current}，无法安装 {app}@{version}"
)
ERR_MANIFEST_WRAPPED = "{app}：{error}"
ERR_DEPENDENCY_FORMAT = "依赖格式无效：{item}（{error}）"
ERR_BIN_ENTRY = "命令入口条目格式无效：{item}"
ERR_BIN_TARGET_EMPTY = "命令入口目标为空"
ERR_BIN_NAME_EMPTY = "命令入口名称为空：{target}"
ERR_BIN_NAME_PATH = "命令入口名称不能包含路径：{name}"
ERR_SHORTCUT_ENTRY = "快捷方式条目格式无效：{item}"
ERR_SHORTCUT_MISSING = "快捷方式条目缺少目标或名称：{item}"
ERR_PERSIST_ENTRY = "持久化条目格式无效：{item}"
ERR_PERSIST_SOURCE_EMPTY = "持久化源路径为空"
ERR_INSTALLER_FORMAT = "安装程序字段格式无效：{app}"
ERR_UNINSTALLER_FORMAT = "卸载程序字段格式无效：{app}"
ERR_ENV_SET_FORMAT = "env_set 字段格式无效：{app}"
ERR_FIELD_OUTSIDE = "{field} 路径越界：{relative}"

# ---------------------------------------------------------------- 安装细节

ERR_MISSING_PACKAGE = "缺少已下载的安装包：{url}"
ERR_INSTALLER_FILE_MISSING = "安装程序文件不存在：{file}"
ERR_INSTALLER_NO_FILE = "安装程序未指定文件，且没有可用的下载文件名"
ERR_UNINSTALLER_NO_FILE = "卸载程序未指定文件，且没有可用的下载文件名"
ERR_BIN_TARGET_MISSING = "命令入口目标不存在：{target}"
ERR_BIN_NAME_APM = "命令入口名称不能为 apm"
ERR_SHIM_MISSING = "内置入口程序不存在：{path}"
ERR_SHIM_CHECKSUM = (
    "内置入口程序校验失败：{path}\n期望：{expected}\n实际：{actual}"
)
ERR_SHIM_CHECKSUM_MISSING = "内置入口程序校验文件不存在：{path}"
ERR_SHIM_CHECKSUM_INVALID = "内置入口程序校验文件格式无效：{path}"

# ---------------------------------------------------------------- 配置与环境

ERR_CONFIG_READ = "无法读取配置文件 {path}：{error}"
ERR_CONFIG_JSON = (
    "配置文件 JSON 格式错误：{path}\n"
    "第 {line} 行第 {column} 列：{error}\n"
    r"提示：Windows 路径中的反斜杠需要写成 \\，例如 C:\\Program Files\\7-Zip\\7z.exe"
)
ERR_CONFIG_TOP_LEVEL = "配置文件格式错误（顶层必须是对象）：{path}"
ERR_CONFIG_CREATE = "无法创建默认配置文件 {path}：{error}"
ERR_CONFIG_SECTION = "配置项 {name} 必须是对象"
ERR_CONFIG_BOOL = "配置项 {path} 必须是布尔值"
ERR_CONFIG_INT = "配置项 {path} 必须是整数"
ERR_CONFIG_POSITIVE = "配置项 {path} 必须大于等于 1"
ERR_CONFIG_STRING = "配置项 {path} 必须是字符串"
ERR_CONFIG_BACKEND = "配置项 download.backend 取值无效：{value}（可选：{choices}）"
ERR_CONFIG_VERIFY = "配置项 verify.method 取值无效：{value}（可选：{choices}）"
ERR_ENV_WINDOWS = "环境变量操作仅在 Windows 上可用"
ERR_ENV_READ = "读取环境变量 {name} 失败：{error}"
ERR_ENV_WRITE = "写入环境变量 {name} 失败：{error}"
ERR_ENV_DELETE = "删除环境变量 {name} 失败：{error}"

# ---------------------------------------------------------------- 工具与脚本

ERR_7Z_MISSING = (
    "未检测到 7z.exe，请安装 7-Zip 并在配置中设置 "
    "{section}.seven_zip，或运行 apm install 7zip"
)
ERR_INNOUNP_MISSING = (
    "未检测到 innounp.exe，请安装 innounp 并在配置中设置 extract.innounp"
)
ERR_POWERSHELL_MISSING = "未检测到 PowerShell，无法执行安装脚本"
MSG_HOOK_RUNNING = "执行{hook}脚本"
ERR_HOOK_FAILED = "{hook}脚本执行失败（退出码 {code}）\n{detail}"
ERR_INSTALLER_MISSING = "安装程序不存在：{path}"
ERR_INSTALLER_FAILED = (
    "安装程序执行失败（退出码 {code}）：{name}\n{detail}"
)
HOOK_NAMES = {
    "pre_install": "安装前",
    "post_install": "安装后",
    "pre_uninstall": "卸载前",
    "post_uninstall": "卸载后",
    "installer": "安装",
    "uninstaller": "卸载",
}

# ---------------------------------------------------------------- 快捷方式与文件

MSG_SHORTCUT_CREATED = "创建快捷方式 {name}"
ERR_SHORTCUT_NAME = "快捷方式名称无效：{name}"
ERR_SHORTCUT_OUTSIDE = "快捷方式路径越界：{name}"
MSG_SHORTCUT_TARGET_MISSING = "创建快捷方式失败（目标不存在）：{path}"
MSG_SHORTCUT_ICON_MISSING = "创建快捷方式失败（图标不存在）：{path}"
ERR_SHORTCUT_FAILED = "创建快捷方式失败：{name}\n{detail}"
ERR_JUNCTION_TARGET = "目录联接目标不存在：{path}"
ERR_JUNCTION_PARENT = "无法创建目录联接父目录：{path}（{error}）"
ERR_JUNCTION_CREATE = "创建目录联接失败：{link} -> {target}\n{detail}"
ERR_HARDLINK_PARENT = "无法创建硬链接父目录：{path}（{error}）"
ERR_HARDLINK = "创建硬链接失败：{link} -> {target}（{error}）"
ERR_PATH_ABSOLUTE = "拒绝绝对路径：{part}"
ERR_PATH_TRAVERSAL = "拒绝越界路径：{part}"
ERR_PATH_OUTSIDE = "路径越界：{path}"
ERR_WRITE_FILE = "写入文件失败：{path}\n{error}"
ERR_KNOWN_BUCKETS_FORMAT = (
    "已知软件源清单格式错误：{path}（应为「名称 → 仓库地址」的字符串映射）"
)
ERR_KNOWN_BUCKETS_MISSING = "已知软件源清单缺失：{path}"
ERR_KNOWN_BUCKETS_UNREADABLE = "已知软件源清单不可读：{path}（{error}）"
ERR_APP_RECORD_MISSING = "{app} 缺少安装记录，无法修改锁定状态"

# ---------------------------------------------------------------- 默认配置说明

CONFIG_DOCS: dict[str, str] = {
    "download.backend": "下载后端，必须为 python / aria2 / curl / wget 之一，其他值直接报错。"
    "aria2 支持多连接分片下载，其余后端为单连接顺序下载",
    "download.concurrency.apps": "同时下载的应用数量上限",
    "download.concurrency.segments": "单个文件的分片并发连接数，仅 aria2 后端生效，其余后端按 1 处理",
    "download.mirror.github": "GitHub 加速前缀，留空表示直连（下载时提示一次）。"
    "填写后 GitHub 下载地址改写为「前缀 + 原始地址」，例如 https://mirror.example.com/ ，"
    "用于绕过网络对 GitHub 的限速",
    "download.mirror.sourceforge": "SourceForge 加速前缀，留空表示直连。填写后改写规则同上",
    "verify.method": "校验方式：hashlib（Python 标准库计算哈希并比对清单的哈希值）/ "
    "7zip（调用 7z.exe 计算哈希，清单无哈希值时顺带做压缩包完整性测试）",
    "verify.seven_zip": "7z.exe 完整路径，留空表示按「本配置项 → PATH → apps 下已安装的 7zip」顺序查找。"
    r"JSON 中反斜杠需写成 \\，例如 C:\\Program Files\\7-Zip\\7z.exe。"
    "仅 verify.method 为 7zip 时使用",
    "extract.seven_zip": "7z.exe 完整路径，留空表示按「本配置项 → PATH → apps 下已安装的 7zip」顺序查找。"
    r"JSON 中反斜杠需写成 \\，例如 C:\\Program Files\\7-Zip\\7z.exe。"
    "zip/7z/rar/iso 等归档解压时优先使用；缺失时 zip/tar 回退标准库解压，其余格式直接报错",
    "extract.innounp": "innounp.exe 完整路径，留空表示按「本配置项 → PATH → apps 下已安装的 innounp」顺序查找。"
    "清单标记 innosetup 的 .exe 安装包解包必需，缺失直接报错",
}
