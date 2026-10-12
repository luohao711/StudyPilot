"""业务异常：由业务层抛出，接口层统一转成 JSON 错误响应。"""


class BusinessError(Exception):
    """带有用户可读提示的业务异常。"""

    def __init__(self, message: str, status_code: int = 400, code: str = "BAD_REQUEST"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
