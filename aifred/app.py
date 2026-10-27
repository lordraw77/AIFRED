"""Service composition: builds the shared services and the FastAPI application."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI

from aifred import __version__, logbuffer, web
from aifred.auth import AuthService
from aifred.agent import Agent
from aifred.config import RuntimeConfig, Settings
from aifred.gateways.telegram import TelegramGateway
from aifred.instructions import InstructionService
from aifred.mcp import McpManager, McpService
from aifred.memory import Memory
from aifred.rag import RagService, RagWorker
from aifred.review import ReviewService
from aifred.assist import AssistService
from aifred.agents import AgentService
from aifred.scheduler import SchedulerService
from aifred.skills import SkillStore
from aifred.tools.registry import ToolRegistry
from aifred.tools.web import WebTools
from aifred.providers import ProviderService
from aifred.router import LLMRouter
from aifred.store import Store
from aifred.vault import Box, OpenBaoBox, SecretBox, Vault, VaultError, load_or_create_key, read_key

logger = logging.getLogger(__name__)

DATA_SUBDIRS = ("memory", "skills", "workspace")


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once; later calls only change the level."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logbuffer.install()
    root.setLevel(level)
    # httpx logs the full URL of every request at INFO, and web_fetch URLs can carry secrets in their query string
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def ensure_layout(data_dir: Path) -> None:
    """Create the data directory and its subdirectories."""
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in DATA_SUBDIRS:
        (data_dir / name).mkdir(mode=0o700, exist_ok=True)


def _credential(value: str | None, path: Path | None, what: str) -> str | None:
    """A credential from its file (preferred, for container secrets) or its variable."""
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"cannot read {what} file {path}: {exc.strerror}") from None
        if not text:
            raise ValueError(f"{what} file {path} is empty")
        return text
    return value or None


def build_vault(settings: Settings, store: Store) -> Vault:
    """Choose the secrets backend from the bootstrap environment (spec 4.2).

    With ``AIFRED_SECRETS_BACKEND=openbao`` no master key is created on disk; an existing
    ``.secret.key`` is only read, to migrate older secrets.

    Raises:
        ValueError: If the OpenBao settings are incomplete or a credential file is unreadable.
    """
    key_path = settings.data_dir / ".secret.key"
    bao: OpenBaoBox | None = None
    if settings.openbao_addr:
        token = _credential(
            settings.openbao_token.get_secret_value() if settings.openbao_token else None,
            settings.openbao_token_file, "AIFRED_OPENBAO_TOKEN",
        )
        secret_id = _credential(
            settings.openbao_secret_id.get_secret_value() if settings.openbao_secret_id else None,
            settings.openbao_secret_id_file, "AIFRED_OPENBAO_SECRET_ID",
        )
        verify: bool | str = str(settings.openbao_cacert) if settings.openbao_cacert else settings.openbao_verify_tls
        if verify is False:
            logger.warning("TLS verification is disabled for OpenBao")
        try:
            bao = OpenBaoBox(
                settings.openbao_addr, token=token, role_id=settings.openbao_role_id, secret_id=secret_id,
                mount=settings.openbao_mount, key=settings.openbao_key, verify=verify,
            )
        except VaultError as exc:
            raise ValueError(f"{exc} (AIFRED_OPENBAO_TOKEN, or AIFRED_OPENBAO_ROLE_ID with AIFRED_OPENBAO_SECRET_ID)") from None
    if settings.secrets_backend == "openbao":
        if bao is None:
            raise ValueError("AIFRED_SECRETS_BACKEND=openbao needs AIFRED_OPENBAO_ADDR and credentials")
        legacy: list[Box] = [SecretBox(read_key(key_path))] if key_path.exists() else []
        vault = Vault(store, bao, legacy)
        if legacy:
            logger.warning(
                "%s still exists: run `python -m aifred admin migrate-secrets`, then delete it so no key is left on the volume",
                key_path,
            )
        return vault
    return Vault(store, SecretBox(load_or_create_key(key_path)), [bao] if bao else [])


@dataclass
class Services:
    """Shared services of one AIFRED process.

    Attributes:
        settings: Bootstrap environment.
        store: Database.
        vault: Encrypted secrets.
        config: Runtime settings.
        auth: Admin account, sessions and API keys.
        providers: LLM providers, keys and models.
        llm: Route walker with failover.
        memory: Curated memory files.
        skills: Skill store.
        web: Web fetch and search implementation.
        tools: Tool registry.
        agent: The agent core.
        rag: The local knowledge base.
        rag_worker: Background indexer, created by the server process only.
        review: Background self-improvement review.
        scheduler: Scheduled tasks and the clock that runs them (started by the server only).
        agents: Sub-agent definitions and runs.
        instructions: What MCP servers, toolsets and tools are for.
        mcp: MCP server definitions.
        mcp_manager: Connections to MCP servers (started by the server and the CLI).
    """

    settings: Settings
    store: Store
    vault: Vault
    config: RuntimeConfig
    auth: AuthService
    providers: ProviderService
    llm: LLMRouter
    memory: Memory
    skills: SkillStore
    web: WebTools
    tools: ToolRegistry
    agent: Agent = field(init=False)
    rag: RagService = field(init=False)
    rag_worker: RagWorker | None = field(init=False, default=None)
    review: ReviewService = field(init=False)
    scheduler: SchedulerService = field(init=False)
    agents: AgentService = field(init=False)
    instructions: InstructionService = field(init=False)
    mcp: McpService = field(init=False)
    mcp_manager: McpManager = field(init=False)

    def __post_init__(self) -> None:
        self.review = ReviewService(self)
        self.instructions = InstructionService(self)
        self.agent = Agent(self)
        self.scheduler = SchedulerService(self)
        self.agents = AgentService(self)
        self.assist = AssistService(self)
        self.mcp = McpService(self.store, self.vault, self.config)
        self.mcp_manager = McpManager(self.mcp, self.store)
        self.tools.add_source(self.mcp_manager.tools)
        self.config.subscribe(self._mcp_settings_changed)
        self.rag = RagService(self.store, self.config, self.providers, self.llm, self.web, self.settings.data_dir)

    def _mcp_settings_changed(self, changed: set[str]) -> None:
        if changed & {"MCP_CONNECT_TIMEOUT", "MCP_CALL_TIMEOUT", "MCP_MAX_TOOLS_PER_SERVER"}:
            self.mcp_manager.reload()

    def start_mcp(self) -> None:
        """Import ``mcp.json`` on first start and connect to the servers in the background."""
        self.mcp.import_file(self.settings.data_dir / "mcp.json")
        self.mcp_manager.start()

    @classmethod
    def open(cls, settings: Settings) -> Services:
        """Open the data directory and build every service (no admin bootstrap)."""
        ensure_layout(settings.data_dir)
        store = Store(settings.data_dir / "aifred.db")
        try:
            vault = build_vault(settings, store)
        except Exception:
            store.close()
            raise
        status = vault.status()
        (logger.info if status["ok"] else logger.error)(
            "Secrets backend: %s (%s); %d secret(s) not yet in this backend's format",
            status["backend"], status["detail"], status["legacy_secrets"],
        )
        config = RuntimeConfig(store, vault)
        configure_logging(config.get("LOG_LEVEL"))

        def on_change(keys: set[str]) -> None:
            if "LOG_LEVEL" in keys:
                configure_logging(config.get("LOG_LEVEL"))

        config.subscribe(on_change)
        providers = ProviderService(store, vault)
        from aifred.tools.builtin import registry

        memory = Memory(settings.data_dir)
        return cls(
            settings, store, vault, config, AuthService(store), providers,
            LLMRouter(store, vault, config, providers), memory, SkillStore(settings.data_dir, memory), WebTools(), registry.fork(),
        )

    def bootstrap(self) -> None:
        """Create the admin from the environment when none exists.

        Raises:
            ValueError: If no admin exists and the bootstrap variables are missing or invalid.
        """
        password = self.settings.admin_password.get_secret_value() if self.settings.admin_password else None
        self.auth.bootstrap_admin(self.settings.admin_user, password)

    def close(self) -> None:
        """Release resources."""
        self.scheduler.stop()
        self.review.shutdown()
        self.mcp_manager.stop()
        self.store.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Bootstrap settings; read from the environment when omitted.
    """
    from aifred.gateways import api, api_agents, api_chat, api_llm, api_data, api_instructions, api_mcp, api_openai, api_rag, api_sched, api_system

    services = Services.open(settings or Settings())
    try:
        services.bootstrap()
    except ValueError:
        services.close()
        raise

    def sync_rag_worker(changed: set[str] | None = None) -> None:
        """Run the knowledge-base indexer exactly while ``RAG_ENABLED`` is on (it follows live changes)."""
        if changed is not None and "RAG_ENABLED" not in changed:
            return
        if services.config.get("RAG_ENABLED"):
            if services.rag_worker is None:
                services.rag_worker = RagWorker(services.rag)
            services.rag_worker.start()
        elif services.rag_worker is not None:
            services.rag_worker.stop()

    def sync_scheduler(changed: set[str] | None = None) -> None:
        """Run the scheduler clock exactly while ``SCHEDULER_ENABLED`` is on."""
        if changed is not None and "SCHEDULER_ENABLED" not in changed:
            return
        if services.config.get("SCHEDULER_ENABLED"):
            services.scheduler.start()
        else:
            services.scheduler.stop()

    telegram = TelegramGateway(services)
    app_loop: list[asyncio.AbstractEventLoop] = []

    def sync_telegram(changed: set[str] | None = None) -> None:
        """Restart the Telegram gateway when its token, allowlist or API base change."""
        if changed is not None and not changed & {"TELEGRAM_TOKEN", "TELEGRAM_ALLOWED_IDS", "TELEGRAM_API_BASE"}:
            return
        if app_loop:  # settings can change from a worker thread
            asyncio.run_coroutine_threadsafe(telegram.restart(), app_loop[0])

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("AIFRED %s started, data in %s", __version__, services.settings.data_dir)
        app_loop.append(asyncio.get_running_loop())
        services.config.subscribe(sync_rag_worker)
        services.config.subscribe(sync_telegram)
        services.config.subscribe(sync_scheduler)
        services.agents.ensure_starters()
        services.agents.interrupted()
        sync_rag_worker()
        sync_scheduler()
        services.start_mcp()
        await telegram.start()
        try:
            yield
        finally:
            await telegram.stop()
            if services.rag_worker is not None:
                services.rag_worker.stop()
            services.close()

    app = FastAPI(
        title="AIFRED", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.services = services
    app.state.telegram = telegram
    app.include_router(api.router)
    app.include_router(api_llm.router)
    app.include_router(api_rag.router)
    app.include_router(api_mcp.router)
    app.include_router(api_sched.router)
    app.include_router(api_agents.router)
    app.include_router(api_system.router)
    app.include_router(api_data.router)
    app.include_router(api_instructions.router)
    app.include_router(api_chat.router)
    app.include_router(api_openai.router)
    app.add_exception_handler(api_openai.OpenAIError, api_openai.openai_error_response)
    web.mount(app)
    return app
