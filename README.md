# APM

Adrts package manager

兼容 Scoop 软件源的 Windows 包管理器：manifest 格式与 bucket 清单沿用 Scoop 约定，命令入口（shim）布局与 Scoop 对齐。

- 安装 / 更新 / 卸载采用两阶段事务：先全部下载并校验哈希，再按依赖顺序统一提交
- 事务显示参照 DNF：事务表（安装 / 更新 / 安装依赖 / 卸载分节）、事务摘要、
  下载进度条块与执行事务步骤条
- 支持的 manifest 字段：`version`、`architecture`、`bin`、`shortcuts`、
  `env_set`、`env_add_path`、`persist`、`installer` / `uninstaller`、
  `pre_install` / `post_install`、`depends` 等

详细设计见 [docs/design.md](docs/design.md)。
