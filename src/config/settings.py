"""
Configuration module for the ontology-driven data insight application.
Loads environment variables and provides centralized configuration management.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)


class OpenAIConfig:
    """OpenAI-compatible LLM configuration.

    DeepSeek is the default provider. Any OpenAI-compatible endpoint can be
    selected through environment variables without changing application code.
    """

    BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    API_KEY = (
        os.getenv('OPENAI_API_KEY') or os.getenv('DEEPSEEK_API_KEY', '')
    ).strip()
    MODEL = os.getenv('OPENAI_MODEL', 'deepseek-v4-pro').strip() or 'deepseek-v4-pro'
    SMALL_MODEL = (
        os.getenv('OPENAI_SMALL_MODEL', 'deepseek-v4-flash').strip()
        or MODEL
    )

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls.BASE_URL and cls.API_KEY and cls.MODEL and cls.SMALL_MODEL)


_REASONING_EFFORTS = ('none', 'low', 'medium', 'high')


def _read_reasoning_effort(name: str, default: str = 'medium') -> str:
    value = os.getenv(name, default).strip().lower()
    return value if value in _REASONING_EFFORTS else default


class AgentReasoningConfig:
    """Per-agent reasoning effort.

    `gpt-5*` deployments reject `temperature` and `top_p` outright and expose
    `reasoning_effort` instead; `maf_runtime.create_agent` sends this only to those models.
    """

    ALLOWED = _REASONING_EFFORTS

    MASTER = _read_reasoning_effort('MASTER_AGENT_REASONING_EFFORT')
    ONTOLOGY = _read_reasoning_effort('ONTOLOGY_AGENT_REASONING_EFFORT')
    METADATA = _read_reasoning_effort('METADATA_AGENT_REASONING_EFFORT')
    DATA_INSIGHT = _read_reasoning_effort('DATA_INSIGHT_AGENT_REASONING_EFFORT')


class AzureAIFoundryConfig:
    """Azure AI Foundry configuration for monitoring and evaluation."""

    CONNECTION_STRING = os.getenv('AZURE_AI_PROJECT_CONNECTION_STRING')


def _policy_int(generic_name: str, legacy_name: str, default: str) -> int:
    """Read a source-agnostic policy value: DATA_* first, legacy DATABRICKS_* second."""
    return int(os.getenv(generic_name, os.getenv(legacy_name, default)))


class DatabricksConfig:
    """Azure Databricks Unity Catalog configuration for data insight and metadata agents."""
    
    # Workspace connection
    HOST = os.getenv('DATABRICKS_HOST', '')          # e.g. https://adb-xxx.azuredatabricks.net
    TOKEN = os.getenv('DATABRICKS_TOKEN', '')         # PAT or AAD token
    HTTP_PATH = os.getenv('DATABRICKS_HTTP_PATH', '') # SQL warehouse HTTP path
    
    # Default catalog and schema(s) context
    CATALOG = os.getenv('DATABRICKS_CATALOG', 'main')

    # Comma-separated list of schemas to expose to agents; first item is the default.
    # Supports both new DATABRICKS_SCHEMAS and legacy DATABRICKS_SCHEMA env vars.
    _schemas_raw = os.getenv('DATABRICKS_SCHEMAS', os.getenv('DATABRICKS_SCHEMA', 'default'))
    SCHEMAS = [s.strip() for s in _schemas_raw.split(',') if s.strip()] or ['default']
    SCHEMA = SCHEMAS[0]  # primary / default schema (backward-compatible)
    
    # Maximum rows returned for data insight queries.
    # Dual-track alias: DATA_MAX_ROWS takes precedence over the legacy DATABRICKS_MAX_ROWS.
    MAX_ROWS = _policy_int('DATA_MAX_ROWS', 'DATABRICKS_MAX_ROWS', '500')

    # Query timeout in seconds
    QUERY_TIMEOUT = _policy_int('DATA_QUERY_TIMEOUT', 'DATABRICKS_QUERY_TIMEOUT', '120')

    # Process-local, object-scoped UC tool cache. 0 means no expiry.
    METADATA_CACHE_TTL_SECONDS = max(
        0,
        _policy_int(
            'DATA_METADATA_CACHE_TTL_SECONDS',
            'DATABRICKS_METADATA_CACHE_TTL_SECONDS',
            '900',
        ),
    )
    METADATA_AGENT_TIMEOUT_SECONDS = max(
        1,
        int(os.getenv('METADATA_AGENT_TIMEOUT_SECONDS', '120')),
    )
    METADATA_AGENT_MAX_MODEL_ROUNDTRIPS = max(
        1,
        int(os.getenv('METADATA_AGENT_MAX_MODEL_ROUNDTRIPS', '5')),
    )
    METADATA_AGENT_MAX_FUNCTION_CALLS = max(
        1,
        int(os.getenv('METADATA_AGENT_MAX_FUNCTION_CALLS', '8')),
    )

    # Candidate recall bounds. The index scan stays server-side; only candidates reach the prompt.
    METADATA_INDEX_MAX_TABLES = max(
        1,
        int(os.getenv('METADATA_INDEX_MAX_TABLES', '500')),
    )
    METADATA_CANDIDATE_MAX_TABLES = max(
        1,
        int(os.getenv('METADATA_CANDIDATE_MAX_TABLES', '12')),
    )
    # Used only when recall finds no candidate at all, so a small schema still resolves.
    METADATA_SNAPSHOT_MAX_TABLES = max(
        1,
        int(os.getenv('METADATA_SNAPSHOT_MAX_TABLES', '40')),
    )
    
    @classmethod
    def is_configured(cls) -> bool:
        """Return True when the minimum required variables are present."""
        return bool(cls.HOST and cls.TOKEN and cls.HTTP_PATH)


class DataSourceConfig:
    """Which storage backend is active. Exactly one data source runs per process."""

    SUPPORTED_TYPES = ('databricks', 'mysql')
    RAW_TYPE = os.getenv('DATA_SOURCE_TYPE', 'databricks').strip().lower()
    # Unknown values fall back to databricks (backward compatible); the data-source
    # factory logs a warning so the misconfiguration is visible.
    TYPE = RAW_TYPE if RAW_TYPE in SUPPORTED_TYPES else 'databricks'


class MySQLConfig:
    """MySQL connection configuration (active when DATA_SOURCE_TYPE=mysql)."""

    HOST = os.getenv('MYSQL_HOST', '')
    PORT = max(1, int(os.getenv('MYSQL_PORT', '3306')))
    USER = os.getenv('MYSQL_USER', '')
    PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    CHARSET = os.getenv('MYSQL_CHARSET', 'utf8mb4')

    # Comma-separated allowlist of databases exposed to agents; first item is the default.
    _databases_raw = os.getenv('MYSQL_DATABASES', '')
    DATABASES = [d.strip() for d in _databases_raw.split(',') if d.strip()]
    DATABASE = DATABASES[0] if DATABASES else ''

    # SQLAlchemy pool sizing. Total server-side connections are bounded by
    # POOL_SIZE + POOL_MAX_OVERFLOW; keep well below MySQL max_connections (default 151).
    POOL_SIZE = max(1, int(os.getenv('MYSQL_POOL_SIZE', '5')))
    POOL_MAX_OVERFLOW = max(0, int(os.getenv('MYSQL_POOL_MAX_OVERFLOW', '5')))
    POOL_RECYCLE_SECONDS = max(
        60, int(os.getenv('MYSQL_POOL_RECYCLE_SECONDS', '3600'))
    )

    @classmethod
    def is_configured(cls) -> bool:
        """Return True when the minimum required variables are present."""
        return bool(cls.HOST and cls.USER and cls.PASSWORD and cls.DATABASES)


class DataSourcePolicyConfig:
    """Source-agnostic query policy shared by every backend.

    Values intentionally mirror DatabricksConfig.MAX_ROWS / QUERY_TIMEOUT /
    METADATA_CACHE_TTL_SECONDS (both read the same DATA_* → DATABRICKS_* fallback
    chain) so existing deployments keep one source of truth.
    """

    MAX_ROWS = _policy_int('DATA_MAX_ROWS', 'DATABRICKS_MAX_ROWS', '500')
    QUERY_TIMEOUT = _policy_int('DATA_QUERY_TIMEOUT', 'DATABRICKS_QUERY_TIMEOUT', '120')
    METADATA_CACHE_TTL_SECONDS = max(
        0,
        _policy_int(
            'DATA_METADATA_CACHE_TTL_SECONDS',
            'DATABRICKS_METADATA_CACHE_TTL_SECONDS',
            '900',
        ),
    )


class OntologyConfig:
    """Read-only Owlready2 ontology runtime configuration."""

    _project_root = Path(__file__).parent.parent.parent
    _directory_value = Path(
        os.getenv('ONTOLOGY_DIR', str(_project_root / 'Ontology'))
    ).expanduser()
    DIRECTORY = (
        _directory_value
        if _directory_value.is_absolute()
        else (_project_root / _directory_value).resolve()
    )
    FILE_GLOB = os.getenv('ONTOLOGY_FILE_GLOB', '**/*.owl')

    ENABLE_REASONER = os.getenv('ONTOLOGY_ENABLE_REASONER', 'false').lower() == 'true'
    REASONER = os.getenv('ONTOLOGY_REASONER', 'hermit').strip().lower()
    ONLY_LOCAL = os.getenv('ONTOLOGY_ONLY_LOCAL', 'true').lower() == 'true'

    MAX_RESULTS = max(1, int(os.getenv('ONTOLOGY_MAX_RESULTS', '25')))
    MAX_DEPTH = max(1, int(os.getenv('ONTOLOGY_MAX_DEPTH', '5')))
    MAX_PATHS = max(1, int(os.getenv('ONTOLOGY_MAX_PATHS', '10')))
    MAX_NODES = max(10, int(os.getenv('ONTOLOGY_MAX_NODES', '250')))
    FUZZY_THRESHOLD = min(
        1.0,
        max(0.0, float(os.getenv('ONTOLOGY_FUZZY_THRESHOLD', '0.62'))),
    )
    AGENT_TIMEOUT_SECONDS = max(
        1,
        int(os.getenv('ONTOLOGY_AGENT_TIMEOUT_SECONDS', '90')),
    )
    AGENT_MAX_MODEL_ROUNDTRIPS = max(
        1,
        int(os.getenv('ONTOLOGY_AGENT_MAX_MODEL_ROUNDTRIPS', '4')),
    )
    AGENT_MAX_FUNCTION_CALLS = max(
        1,
        int(os.getenv('ONTOLOGY_AGENT_MAX_FUNCTION_CALLS', '6')),
    )
    # Optional safety net for handed-off evidence size. 0 disables any size-based dropping.
    CONTEXT_MAX_CHARS = max(0, int(os.getenv('ONTOLOGY_CONTEXT_MAX_CHARS', '0')))
    # Below this composite confidence the deterministic lookup hands off to the tool-using agent.
    ESCALATION_MIN_CONFIDENCE = min(
        1.0,
        max(0.0, float(os.getenv('ONTOLOGY_ESCALATION_MIN_CONFIDENCE', '0.5'))),
    )


class AppConfig:
    """Application-level configuration."""
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    
    LOG_DIR = Path(__file__).parent.parent.parent / 'logs'
    TMP_DIR = Path(__file__).parent.parent.parent / 'tmp'
    DATA_DIR = Path(__file__).parent.parent.parent / 'data'
    
    # MasterAgent query engine limits. MAF applies these to one function-invocation loop.
    QUERY_ENGINE_MAX_CONSECUTIVE_ERRORS = max(1, int(os.getenv('QUERY_ENGINE_MAX_CONSECUTIVE_ERRORS', '3')))
    QUERY_ENGINE_MAX_MODEL_ROUNDTRIPS = max(
        1,
        int(os.getenv('QUERY_ENGINE_MAX_MODEL_ROUNDTRIPS', '8')),
    )
    QUERY_ENGINE_MAX_FUNCTION_CALLS = max(
        1,
        int(os.getenv('QUERY_ENGINE_MAX_FUNCTION_CALLS', '12')),
    )
    SESSION_RESPONSE_CACHE_ENABLED = os.getenv(
        'SESSION_RESPONSE_CACHE_ENABLED',
        'true',
    ).lower() == 'true'
    SESSION_RESPONSE_CACHE_TTL_SECONDS = max(
        0,
        int(os.getenv('SESSION_RESPONSE_CACHE_TTL_SECONDS', '3600')),
    )
    
    # Feature flags
    DEFAULT_ENABLE_ONTOLOGY = os.getenv('DEFAULT_ENABLE_ONTOLOGY', 'true').lower() == 'true'
    
    # Ensure directories exist
    LOG_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)


def validate_config():
    """Validate that all required configuration values are set."""
    
    required_configs = {
        'OPENAI_BASE_URL': OpenAIConfig.BASE_URL,
    }

    if not OpenAIConfig.API_KEY:
        required_configs['OPENAI_API_KEY'] = OpenAIConfig.API_KEY
    if not OpenAIConfig.MODEL:
        required_configs['OPENAI_MODEL'] = OpenAIConfig.MODEL
    if not OpenAIConfig.SMALL_MODEL:
        required_configs['OPENAI_SMALL_MODEL'] = OpenAIConfig.SMALL_MODEL

    if DataSourceConfig.TYPE == 'mysql':
        if not MySQLConfig.is_configured():
            if not MySQLConfig.HOST:
                required_configs['MYSQL_HOST'] = ''
            if not MySQLConfig.USER:
                required_configs['MYSQL_USER'] = ''
            if not MySQLConfig.PASSWORD:
                required_configs['MYSQL_PASSWORD'] = ''
            if not MySQLConfig.DATABASES:
                required_configs['MYSQL_DATABASES'] = ''
    
    missing_configs = [key for key, value in required_configs.items() if not value]
    
    if missing_configs:
        raise ValueError(
            f"Missing required configuration values: {', '.join(missing_configs)}. "
            f"Please check your .env file."
        )
    
    return True

