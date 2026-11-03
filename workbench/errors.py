class WorkbenchError(Exception):
    """所有可预期错误的基类；message 面向使用者，要求能直接看懂。"""


class ConfigError(WorkbenchError):
    """配置缺失或非法。doctor 会汇总成一张清单，不逐条抛。"""


class AdapterUnavailable(WorkbenchError):
    """外部能力不可用（未配置、连不上、被登录墙挡住）。

    强制约定：不可用时必须如实抛出，**不许用模型补写内容**冒充成功。
    """


class GateRejected(WorkbenchError):
    """人工在闸门处否决。"""


class InvariantViolation(WorkbenchError):
    """状态机或数据一致性被破坏（如子形态引入母版没有的论点）。"""
