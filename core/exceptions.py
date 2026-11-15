class SessionManagerError(Exception):
    """插件可诊断的基础异常。"""

class RouteNotFound(SessionManagerError): pass
class RouteExpired(SessionManagerError): pass
class RouteDisabled(SessionManagerError): pass
class AmbiguousSessionRoute(SessionManagerError): pass
class UnsupportedScene(SessionManagerError): pass
class AdapterCompatibilityError(SessionManagerError): pass
class QQSendFailed(SessionManagerError): pass
