"""Safe review infrastructure errors; messages never include provider payloads."""


class LLMConfigurationError(ValueError):
    pass


class LLMReviewError(RuntimeError):
    def __init__(self, code: str = "service") -> None:
        self.code = code
        super().__init__("Independent review infrastructure: " + code)


class LLMProposalError(LLMReviewError):
    def __init__(self) -> None:
        super().__init__("malformed_proposal")


class LLMAuditError(LLMReviewError):
    pass
