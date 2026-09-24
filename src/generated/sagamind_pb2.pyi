from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class StartSagaRequest(_message.Message):
    __slots__ = ("tenant_id", "goal")
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    GOAL_FIELD_NUMBER: _ClassVar[int]
    tenant_id: str
    goal: str
    def __init__(self, tenant_id: _Optional[str] = ..., goal: _Optional[str] = ...) -> None: ...

class StartSagaResponse(_message.Message):
    __slots__ = ("saga_id", "status")
    SAGA_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    saga_id: str
    status: str
    def __init__(self, saga_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class StepProposal(_message.Message):
    __slots__ = ("saga_id", "step_name", "tool_name", "arguments", "compensation_tool", "compensation_arguments", "invariants")
    class ArgumentsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class CompensationArgumentsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SAGA_ID_FIELD_NUMBER: _ClassVar[int]
    STEP_NAME_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    COMPENSATION_TOOL_FIELD_NUMBER: _ClassVar[int]
    COMPENSATION_ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    INVARIANTS_FIELD_NUMBER: _ClassVar[int]
    saga_id: str
    step_name: str
    tool_name: str
    arguments: _containers.ScalarMap[str, str]
    compensation_tool: str
    compensation_arguments: _containers.ScalarMap[str, str]
    invariants: str
    def __init__(self, saga_id: _Optional[str] = ..., step_name: _Optional[str] = ..., tool_name: _Optional[str] = ..., arguments: _Optional[_Mapping[str, str]] = ..., compensation_tool: _Optional[str] = ..., compensation_arguments: _Optional[_Mapping[str, str]] = ..., invariants: _Optional[str] = ...) -> None: ...

class StepResult(_message.Message):
    __slots__ = ("status", "step_id", "error")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STEP_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status: str
    step_id: str
    error: str
    def __init__(self, status: _Optional[str] = ..., step_id: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class StepEvent(_message.Message):
    __slots__ = ("step_name", "status", "error", "timestamp")
    STEP_NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    step_name: str
    status: str
    error: str
    timestamp: float
    def __init__(self, step_name: _Optional[str] = ..., status: _Optional[str] = ..., error: _Optional[str] = ..., timestamp: _Optional[float] = ...) -> None: ...

class SagaStatusRequest(_message.Message):
    __slots__ = ("saga_id",)
    SAGA_ID_FIELD_NUMBER: _ClassVar[int]
    saga_id: str
    def __init__(self, saga_id: _Optional[str] = ...) -> None: ...

class SagaStatusResponse(_message.Message):
    __slots__ = ("saga_id", "tenant_id", "goal", "status", "completed_steps")
    SAGA_ID_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    GOAL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_STEPS_FIELD_NUMBER: _ClassVar[int]
    saga_id: str
    tenant_id: str
    goal: str
    status: str
    completed_steps: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, saga_id: _Optional[str] = ..., tenant_id: _Optional[str] = ..., goal: _Optional[str] = ..., status: _Optional[str] = ..., completed_steps: _Optional[_Iterable[str]] = ...) -> None: ...
