class LabError(Exception):
    """所有可预期实验台错误的基类。"""


class NotFoundError(LabError):
    pass


class AuthorizationDenied(LabError):
    """示证、开席或接手未通过。对外只给概括原因，不泄露校验细节。"""


class InvalidStateError(LabError):
    pass


class BindingMismatch(LabError):
    """持卡器或登记处发现卡片不属于同一主体。"""
