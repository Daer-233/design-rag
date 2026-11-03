"""Application service wiring."""

from functools import lru_cache

from app.clients.inference import EmbeddingClient, RerankClient
from app.core.config import settings
from app.providers.llm.factory import get_answer_provider, get_vision_provider
from app.providers.parser.factory import get_parser_provider
from app.repositories.agent_repository import AgentRepository
from app.repositories.evaluation_repository import EvaluationRepository
from app.repositories.import_batch_repository import ImportBatchRepository
from app.repositories.knowledge_catalog_repository import KnowledgeCatalogRepository
from app.repositories.material_consistency_repository import MaterialConsistencyRepository
from app.repositories.rag_repository import RagRepository
from app.repositories.search_audit_repository import SearchAuditRepository
from app.services.access_policy import AccessPolicy
from app.services.agent_service import AgentService
from app.services.agent_workflow import AgentLlmClient, DesignAgentWorkflow
from app.services.answer_service import AnswerService
from app.services.bert_intent_service import BertIntentClassifier
from app.services.document_page_renderer import DocumentPageRenderer
from app.services.ingestion_job_service import IngestionJobService
from app.services.import_batch_service import ImportBatchService
from app.services.mcp_knowledge_client import McpKnowledgeClient, McpSearchServiceAdapter
from app.services.material_consistency_service import MaterialConsistencyService
from app.services.rag_evaluation_service import RagEvaluationService
from app.services.rag_service import IngestionService, SearchService
from app.services.search_audit_service import SearchAuditService
from app.services.visual_analysis_service import VisualAnalysisService


@lru_cache
def get_repository() -> RagRepository:
    return RagRepository()


@lru_cache
def get_knowledge_catalog_repository() -> KnowledgeCatalogRepository:
    return KnowledgeCatalogRepository()


@lru_cache
def get_search_audit_repository() -> SearchAuditRepository:
    return SearchAuditRepository()


@lru_cache
def get_search_audit_service() -> SearchAuditService:
    return SearchAuditService(get_search_audit_repository())


@lru_cache
def get_ingestion_service() -> IngestionService:
    return IngestionService(get_repository(), EmbeddingClient())


@lru_cache
def get_ingestion_job_service() -> IngestionJobService:
    return IngestionJobService(get_repository(), get_parser_provider(), get_ingestion_service())


@lru_cache
def get_search_service() -> SearchService:
    return SearchService(get_repository(), EmbeddingClient(), RerankClient(), AccessPolicy())


@lru_cache
def get_answer_service() -> AnswerService:
    return AnswerService(get_search_service(), get_answer_provider())


@lru_cache
def get_evaluation_repository() -> EvaluationRepository:
    return EvaluationRepository()

@lru_cache
def get_material_consistency_repository() -> MaterialConsistencyRepository:
    return MaterialConsistencyRepository()


@lru_cache
def get_rag_evaluation_service() -> RagEvaluationService:
    return RagEvaluationService(
        get_evaluation_repository(),
        get_search_service(),
        get_answer_provider(),
    )

@lru_cache
def get_material_consistency_service() -> MaterialConsistencyService:
    return MaterialConsistencyService(
        get_material_consistency_repository(),
        get_visual_analysis_service(),
        get_answer_provider(),
    )


@lru_cache
def get_agent_repository() -> AgentRepository:
    return AgentRepository()


@lru_cache
def get_mcp_knowledge_client() -> McpKnowledgeClient:
    return McpKnowledgeClient(
        settings.design_rag_mcp_url,
        timeout_seconds=settings.design_rag_mcp_timeout_seconds,
    )


@lru_cache
def get_agent_service() -> AgentService:
    mcp_client = get_mcp_knowledge_client() if settings.design_rag_mcp_enabled else None
    search_service = McpSearchServiceAdapter(mcp_client) if mcp_client else get_search_service()
    workflow = DesignAgentWorkflow(
        search_service=search_service,
        answer_provider=get_answer_provider(),
        classifier=BertIntentClassifier(),
        llm_client=AgentLlmClient(),
    )
    return AgentService(
        get_agent_repository(),
        workflow,
        get_search_audit_service(),
        project_catalog=mcp_client,
    )


@lru_cache
def get_visual_analysis_service() -> VisualAnalysisService:
    return VisualAnalysisService(get_repository(), get_vision_provider(), get_ingestion_service())


@lru_cache
def get_import_batch_repository() -> ImportBatchRepository:
    return ImportBatchRepository()


@lru_cache
def get_import_batch_service() -> ImportBatchService:
    return ImportBatchService(
        get_import_batch_repository(),
        get_ingestion_job_service(),
        get_visual_analysis_service(),
        document_page_renderer=DocumentPageRenderer(
            soffice_path=settings.soffice_path,
            pdftoppm_path=settings.pdftoppm_path,
            dpi=settings.document_page_vision_dpi,
            max_pages=settings.document_page_vision_max_pages,
            timeout_seconds=settings.document_page_render_timeout_seconds,
        ),
    )
