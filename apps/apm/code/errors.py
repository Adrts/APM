class ApmError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigError(ApmError):
    pass


class BucketError(ApmError):
    pass


class ManifestError(ApmError):
    pass


class DependencyError(ApmError):
    pass


class DownloadError(ApmError):
    pass


class VerifyError(ApmError):
    pass


class ExtractError(ApmError):
    pass


class InstallError(ApmError):
    pass


class UninstallError(ApmError):
    pass


class ToolError(ApmError):
    pass
