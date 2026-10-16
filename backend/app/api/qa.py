"""资料问答接口：RAG 问答、知识库概况和片段查看。"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.service import kb_service, llm_client, qa_service

router = APIRouter(prefix="/api/qa", tags=["qa"])


class AskIn(BaseModel):
    question: str = Field(..., max_length=200, description="用户问题")
    mode: Literal["rag", "direct"] = Field("rag", description="rag=带资料，direct=直接问模型")
    top_k: int = Field(3, ge=1, le=8, description="检索片段数量")
    prompt_version: Literal["v1", "v2", "v3"] = Field("v3", description="Prompt 版本")
    temperature: float = Field(0.2, ge=0, le=1)


@router.post("/ask")
def ask(payload: AskIn):
    return qa_service.answer_question(
        payload.question,
        mode=payload.mode,
        top_k=payload.top_k,
        prompt_version=payload.prompt_version,
        temperature=payload.temperature,
    )


@router.get("/kb")
def kb_info():
    overview = qa_service.kb_overview()
    overview["model"] = llm_client.DEFAULT_MODEL
    overview["llm_available"] = llm_client.is_available()
    return overview


@router.get("/kb/chunks")
def kb_chunks(source_id: str | None = None, limit: int = 5, offset: int = 0):
    return {"items": qa_service.list_chunks(source_id, limit, offset), "total": len(kb_service.load_chunks())}


@router.post("/kb/rebuild")
def kb_rebuild():
    return kb_service.build_index()
