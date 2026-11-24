"""模型输出的结构化格式：字段、长度和日期格式由 Pydantic 校验。

v0.5 只校验结构和类型，不校验业务约束（日期窗口、每天时长上限、知识点覆盖等）；
这些约束在 v0.6 的 validator 中由程序检查，并为修复提示提供错误清单。
"""

from pydantic import BaseModel, ConfigDict, Field


class PlanTaskOut(BaseModel):
    """模型给出的单个任务。point 为知识点名称，程序再映射为 point_id。"""

    model_config = ConfigDict(extra="ignore")

    point: str = Field(..., min_length=1, max_length=20)
    title: str = Field(..., min_length=1, max_length=60)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    minutes: int = Field(..., ge=5, le=240)
    reason: str = Field("", max_length=240)


class PlanOut(BaseModel):
    """模型一次生成的完整计划。"""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field("", max_length=600)
    tasks: list[PlanTaskOut] = Field(..., min_length=1, max_length=30)
    notes: str = Field("", max_length=400)
