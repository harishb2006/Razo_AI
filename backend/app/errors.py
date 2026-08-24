class RazoError(Exception):
    code: str = "SYSTEM_ERROR"
    http_status: int = 500
    user_message: str = "Something went wrong."
    retryable: bool = False

    def __init__(self, code: str, http_status: int, user_message: str, *, detail: dict | None = None, retryable: bool = False):
        self.code = code
        self.http_status = http_status
        self.user_message = user_message
        self.detail = detail or {}
        self.retryable = retryable
        super().__init__(user_message)


def product_not_found(sku: str) -> RazoError:
    return RazoError(
        "PRODUCT_NOT_FOUND", 404, "I couldn't find that product.",
        detail={"sku": sku},
    )
